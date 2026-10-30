"""Copies the map tiles into the release folder for make_release.bat.

For each world it takes whichever copy is more complete, the "maps" folder next to the code or
dist\\maps (where the exe downloads them), and says if any tiles are missing.

    python release_maps.py <release folder>
"""
import os
import shutil
import sys

import tiles

HERE = os.path.dirname(os.path.abspath(__file__))


def have(root, world):
    """(tiles present, tiles expected) for this world's copy under root."""
    dzi = tiles.load_local(root, world)
    if dzi is None:
        return 0, 0
    n = sum(1 for lv, c, r in dzi.all_tiles() if os.path.exists(tiles.tile_path(root, world, dzi, lv, c, r)))
    return n, dzi.count()


def main(out):
    copied = 0
    for world in tiles.WORLDS:
        best = max((have(root, world) + (root,) for root in (HERE, os.path.join(HERE, "dist"))),
                   key=lambda t: t[0])
        n, total, root = best
        if not n:
            print(f"  {world}: not downloaded, left out (download it in the app first to include it)")
            continue
        src = tiles.world_dir(root, world)
        shutil.copytree(src, os.path.join(out, "maps", world), dirs_exist_ok=True)
        copied += 1
        miss = total - n
        print(f"  {world}: {n:,} of {total:,} tiles" + (f"  ({miss:,} MISSING: finish its download in the app)" if miss else ""))
    if not copied:
        print("No maps found to include.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
