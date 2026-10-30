"""The toolbar's Find box: type any NPC, creature, town, zone, camp, coachman, rare or pin and the map
rings where it is (the same pulsing ring the quest steps use).

Names come from atlas.json (EQOA-Data's map: zones, towns and every point marked on it), the quest
data's NPCs and creatures (quests.json and guides.json), places.json (coachmen, camps) and your pins.
"""
from __future__ import annotations

import json
import os
import re

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QLineEdit

WORLD_NAMES = {0: "Tunaria", 1: "Oggok", 2: "Odus", 3: "Lavastorm", 4: "Plane of Sky", 5: "Secrets"}
WORLD_IDS = {v: k for k, v in WORLD_NAMES.items()}
# kinds that are the same sort of thing, so one name from several sources shows once
SAME = {"Quest NPC": "NPC", "NPC": "NPC", "Point": "NPC"}
ORDER = {"Town": 0, "Zone": 1, "NPC": 2, "Coachman": 2, "Rare": 3, "Raid mob": 3, "Camp": 4, "Pin": 5}


def fold(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower().replace("`", "'").replace("'", "")).strip()


class SearchIndex:
    def __init__(self):
        self.entries: list[dict] = []
        self._by: dict[tuple, dict] = {}

    def add(self, name: str, kind: str, places, detail: str = ""):
        name = re.sub(r"\s+", " ", (name or "")).strip(" .,")
        if len(name) < 2 or not places:
            return
        kind = "NPC" if SAME.get(kind) == "NPC" else kind
        key = (fold(name), kind)
        if kind == "NPC":   # a coachman or a rare the quest data also names: one entry
            key = next((k for k in ((key[0], "Coachman"), (key[0], "Rare"), (key[0], "Raid mob")) if k in self._by), key)
        e = self._by.get(key)
        if e is None:
            e = self._by[key] = {"n": name, "k": kind, "p": [], "d": detail, "f": fold(name)}
            self.entries.append(e)
        for p in places:
            p = [int(p[0]), float(p[1]), float(p[2])]
            if all(abs(p[1] - q[1]) + abs(p[2] - q[2]) > 30 or p[0] != q[0] for q in e["p"]):
                e["p"].append(p)
        if detail and not e["d"]:
            e["d"] = detail

    def find(self, text: str, limit=30) -> list[dict]:
        q = fold(text)
        if not q:
            return []
        toks = q.split()
        out = []
        for e in self.entries:
            n = e["f"]
            if n == q:
                r = 0
            elif n.startswith(q):
                r = 1
            elif any(w.startswith(q) for w in n.split()):
                r = 2
            elif all(t in n for t in toks):
                r = 3
            elif all(t in n or t in fold(e["d"]) for t in toks):
                r = 4
            else:
                continue
            out.append((r, ORDER.get(e["k"], 6), len(n), n, e))
        out.sort(key=lambda t: t[:4])
        return [t[-1] for t in out[:limit]]


def label(e: dict) -> str:
    where = sorted({WORLD_NAMES.get(p[0], "?") for p in e["p"]})
    bits = [e["k"]]
    if e["d"]:
        bits.append(e["d"] if len(e["d"]) <= 60 else e["d"][:57] + "…")
    if where != ["Tunaria"]:
        bits.append("/".join(where))
    if len(e["p"]) > 1:
        bits.append(f"{len(e['p'])} spots")
    return f"{e['n']}   —   " + " · ".join(bits)


def build_index(win, root: str) -> SearchIndex:
    idx = SearchIndex()
    # the map's own places first (best names), then the quest data, then the rest of the map points
    pp = getattr(win, "places_panel", None)
    if pp is not None:
        p = pp.p
        for t in p.towns:
            idx.add(t["name"], "Town", [[t["world"], *t["at"]]], t.get("side", ""))
        for c in p.coaches:
            idx.add(c["coachman"], "Coachman", [[c["world"], *c["at"]]],
                    f"in {c['town']} · to {', '.join(c['to'])}" if c["to"] else f"in {c['town']}")
        for c in p.camps:
            idx.add(c["name"], "Camp", [[c["world"], *c["at"]]], f"levels {c['levels'][0]}-{c['levels'][1]}")
            for r in c["rare"]:
                idx.add(r, "Rare", [[c["world"], *c["at"]]], f"at {c['name']}")
    qp = getattr(win, "quest_panel", None)
    if qp is not None:
        for name, pl in qp.book.npcs:
            idx.add(name, "NPC", pl)
    try:
        with open(os.path.join(root, "atlas.json"), encoding="utf-8") as f:
            for e in json.load(f)["places"]:
                idx.add(e["n"], e["k"], e["p"], e.get("d", ""))
    except Exception:
        pass
    return idx


class FindBox(QLineEdit):
    """A line edit whose drop-down lists matches as you type; picking one rings it on the map."""

    def __init__(self, win, root: str):
        super().__init__()
        self.win, self.root = win, root
        self.index: SearchIndex | None = None
        self.results: list[dict] = []
        self.setPlaceholderText("Find NPC, town, zone, camp…  (Ctrl+F)")
        self.setClearButtonEnabled(True)
        self.setMinimumWidth(230)
        self.setToolTip("Type part of a name: NPCs, creatures, towns, zones, camps, coachmen, rares, your pins.\n"
                        "Pick one to ring it on the map (the nearest spots to you, if it has several).")
        self.model = QStringListModel(self)
        self.comp = QCompleter(self.model, self)
        self.comp.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.comp.setCaseSensitivity(Qt.CaseInsensitive)
        self.comp.setMaxVisibleItems(14)
        self.comp.setWidget(self)
        self.comp.popup().setMinimumWidth(460)
        self.comp.activated[str].connect(self.picked_text)
        self.textEdited.connect(self.update_list)
        self.returnPressed.connect(self.pick_first)

    def ensure_index(self):
        if self.index is None:
            self.index = build_index(self.win, self.root)

    def refresh(self):
        """Pins or data changed: rebuild on the next search."""
        self.index = None

    def update_list(self, text):
        self.ensure_index()
        self.results = self.index.find(text) + self.pin_hits(text)
        self.model.setStringList([label(e) for e in self.results])
        if self.results:
            self.comp.complete()
        else:
            self.comp.popup().hide()

    def pin_hits(self, text) -> list[dict]:
        q = fold(text)
        out = []
        for world, pins in getattr(self.win, "pins", {}).items():
            w = WORLD_IDS.get(world)
            for p in pins:
                if w is not None and q and q in fold(p["name"] + " " + p.get("notes", "")):
                    out.append({"n": p["name"], "k": "Pin", "p": [[w, p["x"], p["z"]]], "d": world, "f": fold(p["name"])})
        return out[:10]

    def picked_text(self, text):
        for e in self.results:
            if label(e) == text:
                self.go(e)
                return

    def pick_first(self):
        if self.comp.popup().isVisible() and self.comp.popup().currentIndex().isValid():
            return   # the completer's own activation handles it
        self.ensure_index()
        hits = self.index.find(self.text()) + self.pin_hits(self.text())
        if hits:
            self.go(hits[0])
        elif self.text().strip():
            self.win.statusBar().showMessage(f"Nothing called “{self.text().strip()}” on the map", 5000)

    def go(self, e: dict):
        self.comp.popup().hide()
        self.setText(e["n"])
        places = e["p"]
        me = None
        c = self.win.chars.get(self.win.follow_name or "")
        if c and c.world in WORLD_IDS:
            me = (WORLD_IDS[c.world], c.x, c.z)
        world = me[0] if me and any(p[0] == me[0] for p in places) else places[0][0]
        here = [p for p in places if p[0] == world]
        if me and me[0] == world:
            here.sort(key=lambda p: (p[1] - me[1]) ** 2 + (p[2] - me[2]) ** 2)
        pts = [(p[1], p[2]) for p in here[:5]]
        more = f"  ({len(places)} spots, showing the nearest {len(pts)})" if len(places) > len(pts) else ""
        self.win.show_places(WORLD_NAMES.get(world, f"World {world}"), pts, f"{e['n']} ({e['k']}){more}")
