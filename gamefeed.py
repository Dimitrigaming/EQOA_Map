"""Live position from the game's own network traffic.

The game client tells the server where you are about ten times a second
(the "0x40 self-state": world, X/Y/Z, speed and facing). This module
listens to those packets on your own PC, read-only, the way Wireshark
does, and turns them into map positions. It never touches the game,
its memory or its files, and never sends anything.

Works with any client that speaks the game's protocol (the Rust client,
PCSX2), one marker per game window. On Windows it uses Npcap when it is
installed (npcap.com), and otherwise Windows' own capture (a raw socket),
which needs no download but needs the map to run as administrator.

Run on its own to check it:  python gamefeed.py
"""
from __future__ import annotations

import math
import re
import sys
import threading
import time
from dataclasses import dataclass, field

# --- the game's numbers (vi::selfstate in the Rust client, VIClient::SendPosition in the game)
POS = ((-4000.0, -1000.0, -4000.0), (32000.0, 1000.0, 32000.0))
VEL = ((-15.3, -84.5, -15.3), (15.3, 3.0, 15.3))
SELFSTATE_SIZE = 41
SELF_CHANNEL = 0x40

# world id in the self-state -> name. The map app has tiles for the first three
# (eqoa.live calls world 1's map "Oggok"; it is the Rathe Mountains).
WORLD_NAMES = {0: "Tunaria", 1: "Oggok", 2: "Odus", 3: "Lavastorm", 4: "Plane of Sky", 5: "Secrets"}


# ----------------------------------------------------------------- reading packets
class Reader:
    """Little-endian reader that stops cleanly at the end, as drdp's Buffer does."""

    __slots__ = ("d", "p", "end", "bad")

    def __init__(self, data: bytes, start=0, end=None):
        self.d, self.p = data, start
        self.end = len(data) if end is None else min(end, len(data))
        self.bad = False

    def left(self) -> int:
        return -1 if self.bad else self.end - self.p

    def take(self, n: int) -> bytes:
        if self.bad or self.p + n > self.end:
            self.bad = True
            return b"\0" * n
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "little")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "little")

    def u64(self) -> int:
        return int.from_bytes(self.take(8), "little")

    def packed(self) -> int:
        v = shift = 0
        while shift < 64:
            c = self.u8()
            v |= (c & 0x7F) << shift
            if not c & 0x80 or self.bad:
                break
            shift += 7
        return v

    def size(self) -> int:
        c = self.u8()
        return self.u16() if c == 255 else c

    def skip(self, n: int):
        if self.p + n <= self.end and not self.bad:
            self.p += n
        else:
            self.bad = True


def rle_decode(r: Reader, size: int) -> bytes:
    """drdp's run_length_decode: runs of zeros and copied bytes, a zero header ends it."""
    out = bytearray()
    while True:
        c = r.u8()
        if c == 0 or r.bad:
            break
        if c & 0x80:
            nonzeros, zeros = c & 0x7F, r.u8()
        else:
            nonzeros, zeros = c >> 4, c & 0xF
        out += b"\0" * zeros
        n = min(nonzeros, max(0, r.end - r.p))
        out += r.d[r.p:r.p + n]
        r.p += n
    out = bytes(out[:size])
    return out + b"\0" * (size - len(out))


def all_messages(payload: bytes):
    """Every message in one game datagram:
    ("state", channel, seq, delta, bytes) for state-channel updates and
    ("rel", type, seq, bytes) for reliable messages (249 system, 250 a fragment, 251 guaranteed)."""
    if len(payload) < 10:
        return
    b = Reader(payload, 0, len(payload) - 4)  # last 4 bytes are the CRC
    b.u16()
    b.u16()
    while b.left() > 0:
        flags = b.packed()
        size = flags & 0x7FF
        flags &= 0xFFFFF800
        if flags & 0x2000:
            b.u32()
        if flags & 0x40000:
            b.u32()
        if flags & 0x8000:
            b.u64()
        if not flags & 0x1000:
            b.packed()
        if flags & 0x800:
            b.packed()
        if b.bad:
            return
        start = b.p
        seg_end = min(start + size, b.end)
        b.p = seg_end
        if flags & 0x30000 or seg_end <= start:
            continue
        s = Reader(payload, start, seg_end)
        f = s.u8()
        if f & 0x40:
            s.u32()
        s.u16()
        if f & 1:
            s.u16()
            if f & 4:
                s.packed()
        if f & 2:
            s.u16()
            if f & 8:
                s.packed()
        if f & 0x10:
            while s.u8() != 248 and not s.bad:
                s.u16()
        while s.left() > 0:
            ty = s.u8()
            n = s.size()
            if 249 <= ty <= 251:      # reliable: in order, a long one in 1,300-byte fragments
                seq = s.u16()
                data = s.take(n)
                if s.bad:
                    return
                yield "rel", ty, seq, data
            elif ty == 252:           # unreliable messages
                s.skip(n)
            elif ty < 248:            # a state channel
                seq = s.u16()
                delta = s.u8()
                data = rle_decode(s, n)
                if s.bad:
                    return
                yield "state", ty, seq, delta, data
            else:
                return


def state_messages(payload: bytes):
    """Every state-channel update in one game datagram: (channel, seq, delta, bytes)."""
    for m in all_messages(payload):
        if m[0] == "state":
            yield m[1:]


class ReliableStream:
    """The server's reliable messages to one game window, in order, fragments joined:
    what the game itself is handed (drdp's move_delayed_to_arrivals)."""

    def __init__(self):
        self.next = None
        self.pending: dict[int, tuple[int, bytes]] = {}
        self.parts: list[bytes] = []
        self.skipped = 0
        self.behind = 0

    def feed(self, payload: bytes) -> list[bytes]:
        out = []
        for m in all_messages(payload):
            if m[0] != "rel":
                continue
            _, ty, seq, data = m
            if self.next is None:
                self.next = seq
            ahead = (seq - self.next) & 0xFFFF
            if ahead >= 0x8000:          # already delivered (a resend)...
                self.behind += 1
                if ahead < 0xFFFF - 2000 or self.behind > 40:   # ...or a new connection starting over
                    self.next, self.pending, self.parts, self.behind = seq, {}, [], 0
                else:
                    continue
            else:
                self.behind = 0
            self.pending.setdefault(seq, (ty, data))
        while True:
            got = self.pending.pop(self.next, None)
            if got is None:
                if len(self.pending) > 200:  # a gap the capture missed: give up on it and go on
                    self.skipped += 1
                    self.next = min(self.pending, key=lambda q: (q - self.next) & 0xFFFF)
                    self.parts = []
                    continue
                break
            ty, data = got
            self.next = (self.next + 1) & 0xFFFF
            if ty == 250:
                self.parts.append(data)
            else:
                whole = b"".join(self.parts) + data
                self.parts = []
                if ty == 251:
                    out.append(whole)
        return out


# ---- the game's own messages (the Rust client's net::stream, net::dump, net::session)
class MsgReader(Reader):
    def signed(self) -> int:
        v = self.packed()
        return (v >> 1) ^ -(v & 1)

    def narrow(self, maxlen: int) -> str:
        n = self.u32()
        b = self.take(n) if n < 1 << 20 else b""
        return "" if n >= maxlen else b.decode("latin-1", "replace")

    def wide(self, maxchars: int) -> str:
        n = self.u32()
        b = self.take(n * 2) if n < 1 << 20 else b""
        return "" if n >= maxchars else b.decode("utf-16-le", "replace")

    def f32(self) -> float:
        import struct
        return struct.unpack("<f", self.take(4))[0]


FINISHED = "You have finished a quest!"


def read_dump(body: bytes) -> dict | None:
    """The login dump (0x000D) as far as the quest journal: name, class, race, level, world, journal."""
    r = MsgReader(body)
    r.u8()                       # the world isn't on the disc
    r.narrow(256)                # the world's file
    pid = r.signed()
    name = r.narrow(24)
    cls, race, level = r.signed(), r.signed(), r.signed()
    r.signed(); r.signed(); r.u8()                       # experience, ?, ?
    for _ in range(4):
        r.signed()
    world = r.signed()
    for _ in range(6):
        r.f32()                  # position and facing
    n = max(0, r.signed())       # quick-chat sets
    for _ in range(min(n, 4096)):
        if r.bad:
            break
        r.signed()
        for _ in range(4):
            r.signed(); r.wide(128); r.wide(64)
    r.u32(); r.u32()
    q = r.u32()
    journal = []
    for _ in range(min(q, 64)):
        if r.bad:
            break
        journal.append(r.wide(128))
    if r.bad or not name:
        return None
    return {"name": name, "class": cls, "race": race, "level": level, "world": world, "journal": journal}


class QuestWatch:
    """One game window's journal, level and quest-finished notices, from the server's messages."""

    def __init__(self):
        self.stream = ReliableStream()
        self.name = ""
        self.journal: list[str] = []
        self.level = None
        self.seen_dump = False

    def feed(self, payload: bytes) -> list[dict]:
        events = []
        for m in self.stream.feed(payload):
            if len(m) < 2:
                continue
            op = int.from_bytes(m[:2], "little")
            body = m[2:]
            if op == 0x000D:
                d = read_dump(body)
                if d:
                    self.name, self.level, self.seen_dump = d["name"], d["level"], True
                    self.journal = [j for j in d["journal"] if j][:8]
                    events.append({"kind": "login", "char": d["name"], "class": d["class"], "race": d["race"],
                                   "level": d["level"], "journal": list(self.journal)})
            elif op == 0x007C:                       # a journal line added
                r = MsgReader(body)
                r.u32()
                line = r.wide(128)
                if not r.bad and len(self.journal) < 8:
                    self.journal.append(line)
                    events.append({"kind": "journal", "journal": list(self.journal), "added": line})
            elif op == 0x007D:                       # a journal line gone
                r = MsgReader(body)
                r.u32()
                i = r.signed()
                if not r.bad and i < 8:
                    gone = None
                    if 0 <= i < len(self.journal):
                        gone = self.journal.pop(i)
                    elif self.journal:
                        gone = self.journal.pop()
                    events.append({"kind": "journal", "journal": list(self.journal), "removed": gone})
            elif op == 0x0046:                       # something the game says in a box
                r = MsgReader(body)
                r.u32()
                n = r.u32()
                text = r.take(n * 2).decode("utf-16-le", "replace") if n < 512 else ""
                if text.strip() == FINISHED:
                    events.append({"kind": "finished"})
            elif op == 0x0020:                       # level and experience
                r = MsgReader(body)
                lv = r.signed()
                if not r.bad and 0 < lv <= 60 and lv != self.level:
                    self.level = lv
                    events.append({"kind": "level", "level": lv})
        for e in events:
            e.setdefault("char", self.name)
        return events


def _quant(data: bytes, at: int, n: int, lo: float, hi: float) -> float:
    q = int.from_bytes(data[at:at + n], "big")
    return lo + (q + 0.5) * (hi - lo) / (1 << (8 * n))


def decode_selfstate(d: bytes) -> dict:
    """The 41-byte self-state: world, position (3 bytes an axis), velocity (2), facing (1)."""
    world = d[0]
    x = _quant(d, 1, 3, POS[0][0], POS[1][0])
    y = _quant(d, 4, 3, POS[0][1], POS[1][1])
    z = _quant(d, 7, 3, POS[0][2], POS[1][2])
    vx = _quant(d, 10, 2, VEL[0][0], VEL[1][0])
    vy = _quant(d, 12, 2, VEL[0][1], VEL[1][1])
    vz = _quant(d, 14, 2, VEL[0][2], VEL[1][2])
    # 16..21 force; 22 heading, 23 pitch (roll's range is empty, so it takes no byte)
    heading = _quant(d, 22, 1, -math.pi, math.pi)
    return {"world": world, "x": x, "y": y, "z": z, "vx": vx, "vy": vy, "vz": vz, "heading": heading}


def plausible(s: dict) -> bool:
    """A real world number (the speeds can't be wrong: the game caps them to their range)."""
    return s["world"] < 16


class Flow:
    """One game connection (one game window): rebuilds full self-states from the
    compressed ones, which are XOR'd against an earlier update by its number."""

    def __init__(self):
        self.hist: dict[int, bytes] = {}
        self.order: list[int] = []
        self.latest_seq = None
        self.missing_ref = 0      # updates whose earlier update was never seen (missed packets)

    def feed(self, payload: bytes):
        """Every full self-state in this datagram, oldest first."""
        out = []
        for ty, seq, delta, data in state_messages(payload):
            if ty != SELF_CHANNEL or len(data) != SELFSTATE_SIZE:
                continue
            if delta:
                ref = self.hist.get((seq - delta) & 0xFFFF)
                if ref is None:
                    self.missing_ref += 1
                    continue  # its reference was never seen (started mid-stream, or a missed packet)
                data = bytes(a ^ b for a, b in zip(data, ref))
            if seq in self.hist:
                continue
            self.hist[seq] = data
            self.order.append(seq)
            if len(self.order) > 600:
                self.hist.pop(self.order.pop(0), None)
            out.append((seq, data))
        return out


# ----------------------------------------------------------------- heading on the map
class HeadingFit:
    """Learns how the game's facing angle maps onto the map from the direction you
    actually walk, so the arrow points the right way without any setup."""

    def __init__(self):
        self.samples: list[tuple[float, float]] = []
        self.sign = None
        self.offset = 0.0

    def add(self, raw: float, vx: float, vz: float):
        if vx * vx + vz * vz < 2.0:
            return
        move = math.atan2(vx, -vz)  # map angle: 0 = up (towards -Z), clockwise
        self.samples.append((raw, move))
        self.samples = self.samples[-120:]
        if len(self.samples) >= 12:
            best = None
            for sign in (1, -1):
                cs = sum(math.cos(m - sign * r) for r, m in self.samples)
                sn = sum(math.sin(m - sign * r) for r, m in self.samples)
                strength = math.hypot(cs, sn) / len(self.samples)
                if best is None or strength > best[0]:
                    best = (strength, sign, math.atan2(sn, cs))
            if best[0] > 0.8:
                _, self.sign, self.offset = best

    def map_heading(self, raw: float, vx: float, vz: float):
        if self.sign is not None:
            return self.sign * raw + self.offset
        if vx * vx + vz * vz >= 2.0:
            return math.atan2(vx, -vz)
        return None


# ----------------------------------------------------------------- which window is which
def _window_titles() -> dict[int, str]:
    """pid -> title of its visible windows (Windows only; elsewhere empty)."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    titles: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                titles.setdefault(pid.value, buf.value)
        return True

    user32.EnumWindows(each, 0)
    return titles


TITLE_RE = re.compile(r"^EQOA\s*(\d+)?\s*(?:[-–—]\s*(.+))?$")


def name_from_title(title: str | None, proc: str | None, port: int) -> str:
    """'EQOA 1 - Dimitri' -> 'Dimitri'; 'EQOA 2' -> 'EQOA 2'; PCSX2 -> 'PCSX2'."""
    if title:
        m = TITLE_RE.match(title.strip())
        if m:
            return (m.group(2) or f"EQOA {m.group(1) or ''}").strip()
        if "pcsx2" in title.lower():
            return "PCSX2"
    if proc:
        p = proc.lower()
        if "pcsx2" in p:
            return "PCSX2"
        if p.startswith("eqoa"):
            return "EQOA"
    return f"Game (port {port})"


class Namer:
    """Local UDP port -> character name, from the owning process's window title."""

    def __init__(self):
        self.ports: dict[int, tuple[int, str]] = {}
        self.titles: dict[int, str] = {}
        self.when = 0.0

    def refresh(self):
        self.when = time.monotonic()
        try:
            import psutil
            self.ports = {}
            for c in psutil.net_connections(kind="udp"):
                if c.laddr and c.pid:
                    try:
                        name = psutil.Process(c.pid).name()
                    except Exception:
                        name = ""
                    self.ports[c.laddr.port] = (c.pid, name)
        except Exception:
            pass
        try:
            self.titles = _window_titles()
        except Exception:
            self.titles = {}

    def name(self, port: int) -> str:
        if time.monotonic() - self.when > 3:
            self.refresh()
        pid, proc = self.ports.get(port, (None, None))
        return name_from_title(self.titles.get(pid), proc, port)


# ----------------------------------------------------------------- Windows' own capture
NEEDS_ADMIN = ("To follow your character, the map reads the game's network traffic with Windows' own "
               "packet capture. Windows only allows that while the map runs as administrator.")


def is_admin() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def npcap_ready() -> bool:
    """Npcap is installed and scapy can use it."""
    try:
        import scapy.all  # noqa: F401
        from scapy.config import conf
        return bool(getattr(conf, "use_pcap", False))
    except Exception:
        return False


def parse_udp(pkt: bytes):
    """An IPv4 packet -> (src ip, src port, dst ip, dst port, payload), or None unless it is whole UDP."""
    if len(pkt) < 28 or pkt[0] >> 4 != 4 or pkt[9] != 17:
        return None
    if int.from_bytes(pkt[6:8], "big") & 0x3FFF:   # a fragment: the game's datagrams never are
        return None
    ihl = (pkt[0] & 15) * 4
    total = int.from_bytes(pkt[2:4], "big")
    end = min(total, len(pkt)) if total else len(pkt)
    if ihl < 20 or end < ihl + 8:
        return None
    u = pkt[ihl:end]
    ulen = int.from_bytes(u[4:6], "big")
    body = u[8:min(ulen, len(u))] if ulen >= 8 else u[8:]
    return (".".join(map(str, pkt[12:16])), int.from_bytes(u[0:2], "big"),
            ".".join(map(str, pkt[16:20])), int.from_bytes(u[2:4], "big"), bytes(body))


class RawCapture:
    """Windows' built-in capture: a raw socket per network address with SIO_RCVALL, which sees what
    this PC sends and receives on that adapter. Nothing to install, but it needs administrator."""

    def __init__(self, ips, on_packet, want):
        self.ips = [ip for ip in ips if not ip.startswith(("127.", "169.254."))]
        self.on_packet, self.want = on_packet, want
        self.socks: list = []
        self.errors: list[str] = []
        self.exception = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return bool(self.socks) and not self._stop.is_set()

    def start(self):
        import socket
        for ip in self.ips:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                s.bind((ip, 0))
                s.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
                s.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
                s.settimeout(0.5)
            except Exception as e:
                self.errors.append(f"{ip}: {e}")
                continue
            self.socks.append((ip, s))
            threading.Thread(target=self._loop, args=(s,), daemon=True).start()
        if not self.socks:
            raise RuntimeError("; ".join(self.errors) or "no network address to listen on")

    def _loop(self, s):
        import socket
        while not self._stop.is_set():
            try:
                pkt = s.recv(65535)
            except socket.timeout:
                continue
            except Exception as e:
                if not self._stop.is_set():
                    self.exception = e
                break
            u = parse_udp(pkt)
            if u and self.want(u[1], u[3]):
                self.on_packet(*u)

    def stop(self):
        import socket
        self._stop.set()
        for _, s in self.socks:
            try:
                s.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass


# ----------------------------------------------------------------- the listener
@dataclass
class Track:
    flow: Flow = field(default_factory=Flow)
    fit: HeadingFit = field(default_factory=HeadingFit)
    last_sent: float = 0.0
    name: str = ""
    locked: bool = False        # has had a full update to build on
    last_waiting_note: float = 0.0
    last_alive: float = 0.0     # last "still here" sent while the character stands still


class GameFeed:
    """Listens for the game's self-states and calls on_position(dict) for each
    character, at most ten times a second each."""

    def __init__(self, on_position, on_status=None, server_ports=None, on_quest=None):
        self.on_position = on_position
        self.on_quest = on_quest or (lambda e: None)
        self.watches: dict[tuple, QuestWatch] = {}     # (local port, server ip, server port)
        self.servers: set[tuple] = set()               # where the game windows send their traffic
        self.port_names: dict[int, str] = {}           # local port -> character name from the login dump
        self.on_status = on_status or (lambda s: None)
        self.server_ports = set(server_ports or [])   # empty: any port (found from the packets)
        self.tracks: dict[tuple, Track] = {}
        self.namer = Namer()
        self.local_ips: set[str] = set()
        self.sniffer = None
        self.lock = threading.Lock()
        self.listening_on = ""
        self.started = 0.0
        # what the capture has seen, for Feed > Tracking details and `python gamefeed.py`
        self.stats = {"udp": 0, "udp_sent": 0, "game_packets": 0, "self_full": 0, "self_delta": 0,
                      "positions": 0, "implausible": 0, "errors": 0}
        self.last_error = ""
        self.last_position = None
        self.save = None   # open file: every self-state seen (seq, delta, bytes); positions only, no account data
        self.exe_hint = ""           # the game program's file name, from Settings
        self.watch_pids: set[int] = set()   # games the map launched itself
        self.games: dict[int, tuple[str, set]] = {}
        self.filter_ports: set[int] = set()  # listening to only these ports (the game's), or everything
        self.can_filter = True
        self._ifaces = None
        self._stop = threading.Event()
        self.backend = ""            # "npcap", or "windows" (the raw socket) when Npcap isn't there
        self.needs_admin = False     # start() failed only for want of administrator

    def check(self) -> str | None:
        """None if packet capture can start, else what's missing, in plain words. Picks the backend."""
        if sys.platform == "win32" and not npcap_ready():
            if not is_admin():
                self.needs_admin = True
                return NEEDS_ADMIN
            self.backend = "windows"
            return None
        self.backend = "npcap"
        try:
            import scapy.all  # noqa: F401
        except Exception:
            return "The packet library (scapy) isn't installed. Run run.bat again, or: pip install scapy psutil"
        if sys.platform == "win32":
            try:
                from scapy.arch.windows import get_windows_if_list  # noqa: F401
                from scapy.config import conf
                if not getattr(conf, "use_pcap", False):
                    return "The packet capture couldn't start. Restart the map."
            except Exception:
                return "The packet capture couldn't start. Restart the map."
        return None

    def _local_ips(self):
        ips = {"127.0.0.1"}
        try:
            import psutil
            for addrs in psutil.net_if_addrs().values():
                for a in addrs:
                    if a.address and a.address.count(".") == 3:
                        ips.add(a.address)
        except Exception:
            pass
        return ips

    def handle(self, src_ip: str, sport: int, dst_ip: str, dport: int, payload: bytes):
        st = self.stats
        st["udp"] += 1
        if src_ip not in self.local_ips:
            self.handle_incoming(src_ip, sport, dport, payload)
            return  # positions come only from what this PC sends: the server's own 0x40 is the group
        st["udp_sent"] += 1
        if self.server_ports and dport not in self.server_ports:
            return
        key = (sport, dst_ip, dport)
        allm = list(all_messages(payload))
        if allm:
            self.servers.add((sport, dst_ip, dport))   # this window talks the game's protocol to that server
        msgs = [m[1:] for m in allm if m[0] == "state" and m[1] == SELF_CHANNEL and len(m[4]) == SELFSTATE_SIZE]
        if not msgs:
            # Standing still sends no position (nothing changed), but the game keeps talking to the
            # server. Any packet on a tracked connection means the character is still there.
            tr = self.tracks.get(key)
            if tr and tr.locked and tr.name:
                now = time.monotonic()
                if now - tr.last_alive > 2:
                    tr.last_alive = now
                    self.on_position({"char": tr.name, "alive": True})
            return
        st["game_packets"] += 1
        for _, seq, delta, data in msgs:
            st["self_delta" if delta else "self_full"] += 1
            if self.save:
                try:
                    self.save.write(f"{time.time():.3f} {sport} {dst_ip}:{dport} {seq} {delta} {data.hex()}\n")
                    self.save.flush()
                except Exception:
                    pass
        with self.lock:
            tr = self.tracks.get(key)
            if tr is None:
                tr = self.tracks[key] = Track()
            states = tr.flow.feed(payload)
            if not states:
                if not tr.locked and any(ty == SELF_CHANNEL and len(d) == SELFSTATE_SIZE
                                         for ty, _, _, d in state_messages(payload)):
                    now = time.monotonic()
                    if now - tr.last_waiting_note > 10:
                        tr.last_waiting_note = now
                        name = self.namer.name(sport)
                        self.on_status(f"Found {name}, but it was already in the world when the map started. "
                                       "Quit to the server list and log back in to start tracking it.")
                return
            tr.locked = True
            _, data = states[-1]
            s = decode_selfstate(data)
            if not plausible(s):
                st["implausible"] += 1
                return
            st["positions"] += 1
            tr.fit.add(s["heading"], s["vx"], s["vz"])
            now = time.monotonic()
            if now - tr.last_sent < 0.1:
                return
            tr.last_sent = now
            tr.name = self.port_names.get(sport) or self.namer.name(sport)
            msg = {
                "char": tr.name,
                "world": WORLD_NAMES.get(s["world"], f"World {s['world']}"),
                "x": round(s["x"], 2),
                "y": round(s["y"], 2),
                "z": round(s["z"], 2),
            }
            h = tr.fit.map_heading(s["heading"], s["vx"], s["vz"])
            if h is not None:
                msg["heading"] = round(h, 3)
                msg["heading_is_map"] = True
            self.last_position = (time.monotonic(), dict(msg))
        self.on_position(msg)

    # ------------------------------------------------------------- capture
    def _prn(self, pkt):
        try:
            from scapy.all import IP, UDP
            if IP in pkt and UDP in pkt:
                ip, udp = pkt[IP], pkt[UDP]
                self.handle(ip.src, udp.sport, ip.dst, udp.dport, bytes(udp.payload))
        except Exception as e:
            self.stats["errors"] += 1
            self.last_error = repr(e)

    def _want(self, sport: int, dport: int) -> bool:
        """The raw capture's filter: the game's ports once known, else the server's, else everything."""
        fp = self.filter_ports
        if fp:
            return sport in fp or dport in fp
        if self.server_ports:
            return dport in self.server_ports or sport in self.server_ports
        return True

    def _handle_safe(self, *u):
        try:
            self.handle(*u)
        except Exception as e:
            self.stats["errors"] += 1
            self.last_error = repr(e)

    def _open_windows(self):
        cap = RawCapture(sorted(self.local_ips), self._handle_safe, self._want)
        cap.start()
        return cap, "Windows' own capture on " + ", ".join(ip for ip, _ in cap.socks)

    def _open(self, flt):
        """Start a capture with this filter on every adapter (else the default one); raises if it can't."""
        from scapy.all import AsyncSniffer
        if self._ifaces is None:
            try:
                from scapy.interfaces import get_working_ifaces
                self._ifaces = [i for i in get_working_ifaces() if getattr(i, "ip", None)] or []
            except Exception:
                self._ifaces = []
        last = None
        for iface in ([self._ifaces, None] if self._ifaces else [None]):
            sn = None
            try:
                sn = AsyncSniffer(iface=iface, filter=flt, prn=self._prn, store=False)
                sn.start()
                time.sleep(0.6)  # a capture that fails does so in its own thread, just after starting
                if not sn.running or getattr(sn, "exception", None):
                    raise RuntimeError(getattr(sn, "exception", None) or "capture stopped")
                names = [getattr(i, "description", None) or getattr(i, "name", str(i)) for i in iface] if iface else []
                return sn, (", ".join(names) if names else "the default network adapter")
            except Exception as e:
                last = e
                try:
                    sn and sn.stop()
                except Exception:
                    pass
        raise RuntimeError(last or "no capture")

    def _is_game(self, name: str) -> bool:
        n = (name or "").lower()
        hint = (self.exe_hint or "").lower()
        return n.startswith("eqoa") or "pcsx2" in n or (hint and n == hint)

    def find_games(self) -> dict[int, tuple[str, set]]:
        """Running game programs: pid -> (program name, its UDP ports)."""
        games: dict[int, tuple[str, set]] = {}
        try:
            import psutil
            names: dict[int, str] = {}
            for c in psutil.net_connections(kind="udp"):
                if not (c.laddr and c.pid):
                    continue
                if c.pid not in names:
                    try:
                        names[c.pid] = psutil.Process(c.pid).name()
                    except Exception:
                        names[c.pid] = ""
                if self._is_game(names[c.pid]) or c.pid in self.watch_pids:
                    games.setdefault(c.pid, (names[c.pid], set()))[1].add(c.laddr.port)
        except Exception:
            pass
        return games

    def _watch(self):
        """Once a second: which game programs are running and on which ports; listen to just those."""
        while not self._stop.wait(1.0):
            games = self.find_games()
            self.games = games
            ports = set().union(*(p for _, p in games.values())) if games else set()
            if self.backend == "windows":
                if ports:
                    self.filter_ports = ports   # the raw capture filters in Python: nothing to reopen
                continue
            if not ports or ports == self.filter_ports or not self.can_filter:
                continue
            flt = "udp and (" + " or ".join(f"port {p}" for p in sorted(ports)) + ")"
            try:
                sn, where = self._open(flt)   # the new capture starts before the old one stops: nothing missed
            except Exception as e:
                self.last_error = f"narrowing to the game's ports: {e}"
                continue
            old, self.sniffer = self.sniffer, sn
            self.filter_ports, self.listening_on = ports, where
            try:
                old and old.stop()
            except Exception:
                pass

    def start(self) -> str | None:
        problem = self.check()
        if problem:
            return problem
        self.local_ips = self._local_ips()
        self.namer.refresh()
        games = self.find_games()
        ports = set().union(*(p for _, p in games.values())) if games else set()
        if self.backend == "windows":
            try:
                self.sniffer, self.listening_on = self._open_windows()
            except Exception as e:
                return (f"Couldn't start Windows' packet capture: {e}. Make sure the map is running as "
                        "administrator, then turn tracking on again.")
            self.filter_ports = ports
            self.games = games
            self.started = time.monotonic()
            self._stop.clear()
            threading.Thread(target=self._watch, daemon=True).start()
            self.on_status("Listening for the game")
            return None
        base ="udp and (" + " or ".join(f"dst port {p}" for p in sorted(self.server_ports)) + ")" if self.server_ports else "udp"
        first = "udp and (" + " or ".join(f"port {p}" for p in sorted(ports)) + ")" if ports else base
        last = None
        for flt in (first, base, None):   # without a filter if the system can't compile one
            try:
                self.sniffer, self.listening_on = self._open(flt)
                self.can_filter = flt is not None
                self.filter_ports = ports if flt == first and ports else set()
                last = None
                break
            except Exception as e:
                last = e
        if last is not None or self.sniffer is None:
            hint = ""
            return f"Couldn't start listening to network traffic: {last}.{hint}"
        self.games = games
        self.started = time.monotonic()
        self._stop.clear()
        threading.Thread(target=self._watch, daemon=True).start()
        self.on_status("Listening for the game")
        return None

    def handle_incoming(self, src_ip: str, sport: int, dport: int, payload: bytes):
        """What the server tells a game window: the journal, level and quest-finished notices."""
        key = (dport, src_ip, sport)
        if key not in self.servers and dport not in self.filter_ports:
            return
        w = self.watches.get(key)
        if w is None:
            w = self.watches[key] = QuestWatch()
        try:
            events = w.feed(payload)
        except Exception as e:
            self.stats["errors"] += 1
            self.last_error = f"reading quest messages: {e!r}"
            return
        for e in events:
            if e["kind"] == "login":
                self.port_names[dport] = e["char"]
                for (lp, _, _), tr in self.tracks.items():
                    if lp == dport:
                        tr.name = e["char"]
            if not e.get("char"):
                e["char"] = self.port_names.get(dport) or self.namer.name(dport)
            self.stats["quest_events"] = self.stats.get("quest_events", 0) + 1
            self.on_quest(e)

    def describe(self) -> str:
        """What the capture has seen so far, in plain lines (also what to read when it doesn't work)."""
        st = self.stats
        up = time.monotonic() - self.started if self.started else 0
        lines = [
            f"Listening for {up:.0f} s on: {self.listening_on or 'nothing'}",
            ("Listening to: only the game's traffic (port " + ", ".join(map(str, sorted(self.filter_ports))) + ")")
            if self.filter_ports else "Listening to: all network traffic (no game program found yet)",
            ("Game programs running: " + "; ".join(f"{n or '?'} (pid {pid}, port {', '.join(map(str, sorted(p)))})"
                                                for pid, (n, p) in self.games.items()))
            if self.games else "Game programs running: none found (looking for programs named eqoa… or PCSX2)",
            f"This PC's addresses: {', '.join(sorted(self.local_ips - {'127.0.0.1'})) or 'none found'}",
            f"Network packets seen: {st['udp']:,} (sent by this PC: {st['udp_sent']:,})",
            f"Game position packets: {st['game_packets']:,}  (full: {st['self_full']:,}, changes only: {st['self_delta']:,})",
            f"Positions read: {st['positions']:,}" + (f"  (rejected as impossible: {st['implausible']:,})" if st['implausible'] else ""),
        ]
        for (sport, dip, dport), tr in self.tracks.items():
            state = "tracking" if tr.locked else "found, waiting for a full update (log in again)"
            lines.append(f"  Game connection from port {sport} to {dip}:{dport}: {tr.name or self.namer.name(sport)}, {state}"
                         f"; updates it couldn't place: {tr.flow.missing_ref:,}")
        for (lp, sip, spt), w in self.watches.items():
            if w.seen_dump or w.journal:
                lines.append(f"  Quest journal for {w.name or lp}: {len(w.journal)} line(s)" +
                             "".join(f"\n      - {j}" for j in w.journal))
        if self.last_position:
            age = time.monotonic() - self.last_position[0]
            m = self.last_position[1]
            lines.append(f"Last position {age:.0f} s ago: {m['char']} in {m['world']} at X {m['x']:,.1f}, Z {m['z']:,.1f}")
        if st["errors"]:
            lines.append(f"Errors while reading: {st['errors']} (last: {self.last_error})")
        # what the numbers point to
        if not self.games and not st["game_packets"]:
            lines.append("→ The map can't see the game running. Start it with ▶ Launch game, or set its location "
                         "in Settings so the map knows which program it is.")
        elif not st["udp"] and self.backend == "windows":
            lines.append("→ No network traffic is reaching the map. Windows Firewall may be blocking it: allow the "
                         "map through (Windows Security ▸ Firewall ▸ Allow an app), then restart the map.")
        elif not st["udp"]:
            lines.append("→ No network traffic at all is reaching the map. The capture may not be on the adapter "
                         "your internet uses; restart the map, and send this report if it keeps happening.")
        elif not st["udp_sent"]:
            lines.append("→ Traffic is seen, but none of it as sent by this PC. Something unusual about your network "
                         "addresses; please send this report.")
        elif not st["game_packets"]:
            lines.append("→ No game position packets yet. Are you in the world (not at character select) and moving?")
        elif any(tr.locked and tr.flow.missing_ref > 20 for tr in self.tracks.values()) and \
                self.last_position and time.monotonic() - self.last_position[0] > 5:
            lines.append("→ Tracking started, then lost its place: packets were missed. Close apps sending a lot of "
                         "network traffic (video, voice), then quit to the server list and log back in.")
        elif not st["positions"]:
            lines.append("→ Game packets are seen, but no full position yet: quit to the server list and log back in "
                         "with the map open.")
        return "\n".join(lines)

    def stop(self):
        self._stop.set()
        if self.sniffer:
            try:
                self.sniffer.stop()
            except Exception:
                pass
            self.sniffer = None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Check that the map can read your position from the game's traffic.")
    ap.add_argument("--save", metavar="FILE", help="also write every position update seen to FILE "
                    "(positions only, nothing about your account) so it can be checked")
    ap.add_argument("--quiet", action="store_true", help="don't print each position")
    a = ap.parse_args()
    print("Listening for the game. Start the game now (or quit to the server list and log back in), "
          "then walk around. Ctrl+C to stop.\n")
    feed = GameFeed(lambda m: None if a.quiet else print("  position:", m), lambda s: print("  note:", s))
    if a.save:
        feed.save = open(a.save, "w", encoding="utf-8")
    err = feed.start()
    if err:
        print(err)
        sys.exit(1)
    try:
        while True:
            time.sleep(5)
            print("\n" + feed.describe() + "\n")
    except KeyboardInterrupt:
        feed.stop()
        print("\nFinal report:\n" + feed.describe())
