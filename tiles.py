"""Deep Zoom (DZI) map tiles: layout math and a polite, resumable downloader.

The Sandstorm map at eqoa.live/map/ is a Deep Zoom image pyramid made by
Xylof / Sandstorm. This module copies it to a local folder so the desktop
app can show it offline. Please keep the download gentle on their server.
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

BASE_URL = "https://eqoa.live/map"
USER_AGENT = "NorrathFieldMap/1.0 (personal offline copy of the Sandstorm map)"

# world name -> folder on eqoa.live (Tunaria | Oggok | Odus, as on the site's links)
WORLDS = {"Tunaria": "dzi0", "Oggok": "dzi1", "Odus": "dzi2"}

# eqoa.live/map/map.js: image pixel = 1024 * game / 2000, image x = game X, image y = game Z
GAME_TO_PX = 1024 / 2000


@dataclass
class Dzi:
    width: int
    height: int
    tile: int = 128
    overlap: int = 2
    fmt: str = "jpg"

    @property
    def max_level(self) -> int:
        return math.ceil(math.log2(max(self.width, self.height)))

    def level_size(self, level: int) -> tuple[int, int]:
        f = 2 ** (self.max_level - level)
        return math.ceil(self.width / f), math.ceil(self.height / f)

    def grid(self, level: int) -> tuple[int, int]:
        w, h = self.level_size(level)
        return math.ceil(w / self.tile), math.ceil(h / self.tile)

    def tile_rect(self, level: int, col: int, row: int) -> tuple[int, int, int, int]:
        """Pixel rect (x, y, w, h) a tile file covers, in that level's pixels."""
        w, h = self.level_size(level)
        x0 = col * self.tile - (self.overlap if col else 0)
        y0 = row * self.tile - (self.overlap if row else 0)
        x1 = min(w, (col + 1) * self.tile + self.overlap)
        y1 = min(h, (row + 1) * self.tile + self.overlap)
        return x0, y0, x1 - x0, y1 - y0

    def all_tiles(self, min_level: int = 0, max_level: int | None = None):
        top = self.max_level if max_level is None else max_level
        for lv in range(min_level, top + 1):
            cols, rows = self.grid(lv)
            for r in range(rows):
                for c in range(cols):
                    yield lv, c, r

    def count(self, min_level: int = 0, max_level: int | None = None) -> int:
        top = self.max_level if max_level is None else max_level
        n = 0
        for lv in range(min_level, top + 1):
            c, r = self.grid(lv)
            n += c * r
        return n


def parse_dzi(text: str) -> Dzi:
    def attr(name):
        m = re.search(rf'{name}="([^"]+)"', text)
        if not m:
            raise ValueError(f"index.dzi has no {name}")
        return m.group(1)

    return Dzi(int(attr("Width")), int(attr("Height")), int(attr("TileSize")), int(attr("Overlap")), attr("Format"))


def world_dir(root: str, world: str) -> str:
    return os.path.join(root, "maps", world)


def load_local(root: str, world: str) -> Dzi | None:
    p = os.path.join(world_dir(root, world), "index.dzi")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return parse_dzi(f.read())


def tile_path(root: str, world: str, dzi: Dzi, level: int, col: int, row: int) -> str:
    return os.path.join(world_dir(root, world), "index_files", str(level), f"{col}_{row}.{dzi.fmt}")


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class Downloader:
    """Copies one world's tile pyramid. Skips tiles already on disk, so it resumes."""

    def __init__(self, root: str, world: str, workers: int = 4, delay: float = 0.05):
        self.root, self.world = root, world
        self.workers, self.delay = max(1, min(workers, 6)), delay
        self.cancel = threading.Event()
        self.done = 0
        self.total = 0
        self.failed: list[str] = []
        self.dzi: Dzi | None = None

    def fetch_index(self) -> Dzi:
        folder = WORLDS[self.world]
        text = _get(f"{BASE_URL}/{folder}/index.dzi").decode("utf-8")
        self.dzi = parse_dzi(text)
        os.makedirs(world_dir(self.root, self.world), exist_ok=True)
        with open(os.path.join(world_dir(self.root, self.world), "index.dzi"), "w", encoding="utf-8") as f:
            f.write(text)
        return self.dzi

    def _one(self, lv: int, c: int, r: int):
        if self.cancel.is_set():
            return
        dzi = self.dzi
        out = tile_path(self.root, self.world, dzi, lv, c, r)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            self.done += 1
            return
        url = f"{BASE_URL}/{WORLDS[self.world]}/index_files/{lv}/{c}_{r}.{dzi.fmt}"
        for attempt in range(3):
            if self.cancel.is_set():
                return
            try:
                data = _get(url)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                tmp = out + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, out)
                break
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        else:
            self.failed.append(f"{lv}/{c}_{r}")
        self.done += 1
        time.sleep(self.delay)

    def run(self, progress=None):
        dzi = self.dzi or self.fetch_index()
        tiles = list(dzi.all_tiles())
        self.total, self.done = len(tiles), 0
        with ThreadPoolExecutor(self.workers) as ex:
            futs = [ex.submit(self._one, *t) for t in tiles]
            for f in futs:
                f.result()
                if progress:
                    progress(self.done, self.total)
                if self.cancel.is_set():
                    break
        return not self.cancel.is_set()


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Download a Sandstorm map for offline use.")
    ap.add_argument("world", choices=list(WORLDS))
    ap.add_argument("--workers", type=int, default=4, help="parallel requests (max 6)")
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    d = Downloader(here, a.world, a.workers)
    dz = d.fetch_index()
    print(f"{a.world}: {dz.width} x {dz.height}, {dz.count()} tiles. Ctrl+C stops; run again to resume.")
    last = [0.0]

    def prog(done, total):
        if time.time() - last[0] > 1:
            last[0] = time.time()
            print(f"\r{done}/{total}  ({100 * done / total:.1f}%)", end="", flush=True)

    try:
        d.run(prog)
    except KeyboardInterrupt:
        d.cancel.set()
    print(f"\nDone. {len(d.failed)} tiles failed (run again to retry).")
