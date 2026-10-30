"""Test feed for Norrath Field Map.

Sends made-up positions for one or more characters walking in circles, so
you can try the live markers and the Follow dropdown before a real feed
exists. It only talks to the map on this PC. Nothing goes to the game.

Run:   python feed_demo.py
Then press "Connect feed" in the map app.

Examples:
  python feed_demo.py --chars Dimitri,Alt            two characters, one feed (default)
  python feed_demo.py --chars Dimitri,Alt,Third      three characters
  python feed_demo.py --separate                     each character on its own port (8765, 8766, ...)
                                                     add those addresses under Feed > Feed addresses
  python feed_demo.py --stop Alt --after 30          Alt stops sending after 30 s (to see "not sending")
  python feed_demo.py --x 14000 --z 17000            where the circles are, in game X/Z
"""
import argparse
import asyncio
import json
import math
import time

import websockets

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8765)
ap.add_argument("--chars", default="Dimitri,Alt", help="comma-separated character names")
ap.add_argument("--world", default="Tunaria")
ap.add_argument("--x", type=float, default=14000.0, help="centre of the circles, game X")
ap.add_argument("--z", type=float, default=17000.0, help="centre of the circles, game Z")
ap.add_argument("--radius", type=float, default=400.0)
ap.add_argument("--separate", action="store_true", help="one port per character instead of one shared feed")
ap.add_argument("--stop", default="", help="name of a character that stops sending after --after seconds")
ap.add_argument("--after", type=float, default=30.0)
args = ap.parse_args()

NAMES = [n.strip() for n in args.chars.split(",") if n.strip()] or ["Dimitri"]
START = time.monotonic()


def position(i: int, t: float) -> dict:
    """Character i walks its own circle: different size, speed and starting point."""
    r = args.radius * (1 + 0.45 * i)
    a = t * (0.12 - 0.02 * i) + i * 2.1
    cx = args.x + 250 * i
    return {
        "char": NAMES[i],
        "world": args.world,
        "x": round(cx + r * math.cos(a), 1),
        "z": round(args.z + r * math.sin(a), 1),
        "heading": round(a + math.pi, 3),  # facing along the circle (the arrow points up at 0)
    }


def sending(i: int) -> bool:
    return not (NAMES[i] == args.stop and time.monotonic() - START > args.after)


def make_handler(indexes):
    async def serve(ws):
        who = ", ".join(NAMES[i] for i in indexes)
        print(f"map connected ({who})")
        try:
            while True:
                t = time.monotonic() - START
                for i in indexes:
                    if sending(i):
                        await ws.send(json.dumps(position(i, t)))
                await asyncio.sleep(0.2)
        except websockets.ConnectionClosed:
            print(f"map disconnected ({who})")
    return serve


async def main():
    servers = []
    if args.separate:
        for i, name in enumerate(NAMES):
            servers.append(await websockets.serve(make_handler([i]), "localhost", args.port + i))
            print(f"{name}: ws://localhost:{args.port + i}")
    else:
        servers.append(await websockets.serve(make_handler(range(len(NAMES))), "localhost", args.port))
        print(f"{', '.join(NAMES)}: ws://localhost:{args.port}")
    if args.stop:
        print(f"{args.stop} stops sending after {args.after:.0f} s")
    print("Ctrl+C to stop")
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
