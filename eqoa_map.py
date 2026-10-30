"""Norrath Field Map: an offline EQOA companion map for the Sandstorm server.

- Deep zoom over the Sandstorm map tiles kept in ./maps (Map > Download).
- Pins with notes, stored in pins.json next to the app (share the file).
- Game X/Z under the cursor, "go to" coordinates, and a live position
  marker fed from ws://localhost:8765 (see feed_demo.py for the format).

Map art: eqoa.live/map by Xylof / Sandstorm. This app only reads it.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import math
import os
import sys
import time
import uuid
from collections import OrderedDict

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont, QPainter, QPainterPath, QPalette, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtCore import QEvent
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QAbstractSpinBox, QDialog, QDialogButtonBox
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog, QFormLayout,
                               QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
                               QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressDialog, QPushButton, QSizePolicy, QTabWidget, QToolBar, QVBoxLayout, QWidget)
from PySide6.QtWebSockets import QWebSocket

import tiles

try:
    import gamefeed
except Exception:  # the map still works without packet capture
    gamefeed = None
import quests
import places
import search


class FeedBridge(QObject):
    """Carries positions from the capture thread to the window."""
    position = Signal(dict)
    status = Signal(str)
    quest = Signal(dict)

APP_NAME = "Norrath Field Map"
ROOT = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
PINS_FILE = os.path.join(ROOT, "pins.json")
QUESTS_FILE = os.path.join(ROOT, "quests.json")
GUIDES_FILE = os.path.join(ROOT, "guides.json")
PROGRESS_FILE = os.path.join(ROOT, "quest_progress.json")
PLACES_FILE = os.path.join(ROOT, "places.json")
SETTINGS_FILE = os.path.join(ROOT, "settings.json")
S = tiles.GAME_TO_PX  # game units -> map pixels

CATS = [
    ("quest", "Quest", "!", "#b0821c"),
    ("npc", "NPC", "N", "#1d6a63"),
    ("merchant", "Merchant", "$", "#3f7a2c"),
    ("coach", "Coach", "C", "#2f5d9a"),
    ("bind", "Spiritmaster", "B", "#6d4a9a"),
    ("hunt", "Hunting", "×", "#9b3324"),
    ("rare", "Rare", "★", "#c2571a"),
    ("note", "Note", "•", "#5f5a50"),
]
CAT = {c[0]: c for c in CATS}

# one colour per character, in the order they first appear
CHAR_COLORS = ["#4dc3a8", "#e6b84a", "#d877c9", "#6fa8ff", "#f08a5d", "#9ad35b"]
MANUAL = "Marker"          # the marker placed by hand with "I'm here" or Go
STALE_AFTER = 10           # seconds without an update before a character shows as "not sending"
FORGET_AFTER = 300         # seconds before a silent character drops off the list


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def fmt(v: float) -> str:
    return f"{v:,.1f}"


# ---------------------------------------------------------------- map view
class TileView(QGraphicsView):
    sceneClicked = Signal(QPointF)
    cursorMoved = Signal(QPointF)

    def __init__(self, scene: QGraphicsScene):
        super().__init__(scene)
        self.dzi: tiles.Dzi | None = None
        self.world = "Tunaria"
        self.cache: OrderedDict[str, QPixmap | None] = OrderedDict()
        self.mode = "pan"
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor("#101315"))

    # --- tiles
    def set_world(self, world: str, dzi: tiles.Dzi | None):
        self.world, self.dzi = world, dzi
        self.cache.clear()
        w, h = (dzi.width, dzi.height) if dzi else (4096, 4096)
        self.scene().setSceneRect(QRectF(-w * 0.25, -h * 0.25, w * 1.5, h * 1.5))
        self.resetTransform()
        self.fit()

    def fit(self):
        w, h = (self.dzi.width, self.dzi.height) if self.dzi else (4096, 4096)
        self.fitInView(QRectF(0, 0, w, h), Qt.KeepAspectRatio)

    def pixmap(self, lv, c, r):
        key = f"{lv}/{c}_{r}"
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        p = tiles.tile_path(ROOT, self.world, self.dzi, lv, c, r)
        pm = QPixmap(p) if os.path.exists(p) else None
        if pm is not None and pm.isNull():
            pm = None
        self.cache[key] = pm
        if len(self.cache) > 900:
            self.cache.popitem(last=False)
        return pm

    def zoom(self) -> float:
        return self.transform().m11()

    def draw_level(self, painter: QPainter, rect: QRectF, lv: int):
        d = self.dzi
        f = 2 ** (d.max_level - lv)
        cols, rows = d.grid(lv)
        span = d.tile * f
        c0, c1 = max(0, int(rect.left() // span)), min(cols - 1, int(rect.right() // span))
        r0, r1 = max(0, int(rect.top() // span)), min(rows - 1, int(rect.bottom() // span))
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                pm = self.pixmap(lv, c, r)
                if pm is None:
                    continue
                x, y, w, h = d.tile_rect(lv, c, r)
                painter.drawPixmap(QRectF(x * f, y * f, w * f, h * f), pm, QRectF(0, 0, pm.width(), pm.height()))

    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, QColor("#101315"))
        if not self.dzi:
            painter.save()
            painter.resetTransform()
            painter.setPen(QColor("#b5a888"))
            painter.setFont(QFont("Segoe UI", 13))
            painter.drawText(self.viewport().rect(), Qt.AlignCenter,
                             f"No {self.world} map on this PC yet.\nUse Map ▸ Download {self.world} map.\n\n"
                             "Pins and the position marker still work without it.")
            painter.restore()
            return
        d = self.dzi
        z = self.zoom() * self.devicePixelRatioF()
        lv = d.max_level if z >= 1 else max(0, d.max_level - math.floor(math.log2(1 / z)))
        img = rect.intersected(QRectF(0, 0, d.width, d.height))
        if img.isEmpty():
            return
        # coarse layer first so missing or not-yet-downloaded tiles still show something
        self.draw_level(painter, img, max(0, lv - 4))
        self.draw_level(painter, img, lv)

    # --- input
    def wheelEvent(self, e):
        factor = 1.0015 ** e.angleDelta().y()
        new = self.zoom() * factor
        if 0.002 < new < 8:
            self.scale(factor, factor)

    def set_mode(self, mode):
        self.mode = mode
        self.setDragMode(QGraphicsView.ScrollHandDrag if mode == "pan" else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.OpenHandCursor if mode == "pan" else Qt.CrossCursor)

    def mousePressEvent(self, e):
        if self.mode != "pan" and e.button() == Qt.LeftButton:
            self.sceneClicked.emit(self.mapToScene(e.position().toPoint()))
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self.cursorMoved.emit(self.mapToScene(e.position().toPoint()))
        super().mouseMoveEvent(e)


class PinItem(QGraphicsItem):
    R = 11

    def __init__(self, pin: dict, win: "MainWindow"):
        super().__init__()
        self.pin, self.win = pin, win
        self.selected = False
        self.setFlags(QGraphicsItem.ItemIgnoresTransformations | QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setCursor(Qt.PointingHandCursor)
        self.setZValue(10)
        self.setPos(pin["x"] * S, pin["z"] * S)
        self.setToolTip(pin["name"] + (("\n" + pin["notes"]) if pin.get("notes") else ""))

    def boundingRect(self):
        return QRectF(-14, -14, 240, 28)

    def paint(self, p: QPainter, *_):
        cid, _, glyph, color = CAT.get(self.pin["cat"], CAT["note"])
        r = self.R * (1.25 if self.selected else 1)
        if self.selected:
            p.setPen(QPen(QColor("#5db6a8"), 3))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(0, 0), r + 3, r + 3)
        p.setPen(QPen(QColor(255, 255, 255, 220), 2))
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(0, 0), r, r)
        p.setPen(QColor("white"))
        f = QFont("Segoe UI", 9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(-r, -r, 2 * r, 2 * r), Qt.AlignCenter, glyph)
        if self.win.labels:
            f.setPointSize(9)
            p.setFont(f)
            path = QPainterPath()
            path.addText(QPointF(r + 5, 4), f, self.pin["name"])
            p.setPen(QPen(QColor(16, 14, 10, 220), 3))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#f2e8cf"))
            p.drawPath(path)

    def mousePressEvent(self, e):
        self.win.select_pin(self.pin["id"], center=False)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        x, z = self.pos().x() / S, self.pos().y() / S
        if abs(x - self.pin["x"]) > 0.01 or abs(z - self.pin["z"]) > 0.01:
            self.pin["x"], self.pin["z"] = x, z
            self.win.save_pins()
            self.win.refresh_list()
            self.win.show_editor()


class CharItem(QGraphicsItem):
    """A character's position: coloured arrow (or dot without a heading) and its name."""

    def __init__(self, name: str, color: str):
        super().__init__()
        self.name, self.color = name, QColor(color)
        self.setFlags(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(20)
        self.heading = None      # radians, or None
        self.followed = False
        self.stale = False

    def boundingRect(self):
        return QRectF(-22, -22, 260, 44)

    def paint(self, p: QPainter, *_):
        col = QColor("#8a8f93") if self.stale else self.color
        halo = QColor(col)
        halo.setAlpha(90 if self.followed else 55)
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(0, 0), 19 if self.followed else 15, 19 if self.followed else 15)
        if self.followed:
            p.setPen(QPen(col, 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(0, 0), 20, 20)
        p.setPen(QPen(QColor("white"), 2))
        p.setBrush(col)
        if self.heading is not None:
            p.save()
            p.rotate(math.degrees(self.heading))
            p.drawPolygon(QPolygonF([QPointF(0, -11), QPointF(8, 9), QPointF(0, 5), QPointF(-8, 9)]))
            p.restore()
        else:
            p.drawEllipse(QPointF(0, 0), 7, 7)
        # name, outlined so it reads on any map colour
        f = QFont("Segoe UI", 9)
        f.setBold(True)
        path = QPainterPath()
        label = self.name + ("  (not sending)" if self.stale else "")
        path.addText(QPointF(24, 4), f, label)
        p.setPen(QPen(QColor(16, 14, 10, 230), 3))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#d0d3d4") if self.stale else self.color.lighter(135))
        p.drawPath(path)


class LocateItem(QGraphicsItem):
    """A pulsing ring where something from the quest panel is."""

    def __init__(self):
        super().__init__()
        self.setFlags(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(25)
        self.t = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(60)

    def _tick(self):
        self.t = (self.t + 1) % 20
        self.update()

    def boundingRect(self):
        return QRectF(-30, -30, 60, 60)

    def paint(self, p: QPainter, *_):
        r = 10 + self.t
        c = QColor("#5db6a8")
        c.setAlpha(255 - self.t * 11)
        p.setPen(QPen(c, 3))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r, r)
        p.setPen(QPen(QColor("#5db6a8"), 2))
        p.drawEllipse(QPointF(0, 0), 6, 6)


class Character:
    def __init__(self, name: str, color: str, scene: QGraphicsScene, manual=False):
        self.name, self.color, self.manual = name, color, manual
        self.world = None
        self.x = self.z = 0.0
        self.seen = time.monotonic()
        self.item = CharItem(name, color)
        self.item.setVisible(False)
        scene.addItem(self.item)
        self.pts: list[QPointF] = []
        self.trail = QGraphicsPathItem()
        pen = QPen(QColor(color), 2.5, Qt.DashLine)
        pen.setCosmetic(True)
        self.trail.setPen(pen)
        self.trail.setZValue(15)
        self.trail.setOpacity(0.85)
        scene.addItem(self.trail)

    def stale(self) -> bool:
        return not self.manual and time.monotonic() - self.seen > STALE_AFTER

    def clear_trail(self):
        self.pts = []
        self.trail.setPath(QPainterPath())

    def remove(self, scene: QGraphicsScene):
        scene.removeItem(self.item)
        scene.removeItem(self.trail)


# ---------------------------------------------------------------- download thread
class DownloadWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(bool, int)

    def __init__(self, dl: tiles.Downloader):
        super().__init__()
        self.dl = dl

    def run(self):
        try:
            ok = self.dl.run(lambda d, t: self.progress.emit(d, t))
        except Exception:
            ok = False
        self.finished.emit(ok, len(self.dl.failed))


# ---------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.settings = load_json(SETTINGS_FILE, {})
        self.pins: dict[str, list] = load_json(PINS_FILE, {})
        self.world = self.settings.get("world", "Tunaria")
        if self.world not in tiles.WORLDS:
            self.world = "Tunaria"
        self.labels = self.settings.get("labels", True)
        self.hidden_cats: set[str] = set()
        self.sel_id = None
        self.pin_items: dict[str, PinItem] = {}

        self.scene = QGraphicsScene(self)
        self.view = TileView(self.scene)
        self.setCentralWidget(self.view)
        self.chars: dict[str, Character] = {}
        self.follow_name = None
        self.remembered = self.settings.get("follow_name")  # followed last time; re-picked when it appears
        self.auto_follow = True                              # off once you pick from the dropdown
        self._follow_sig = None

        self._build_toolbar()
        self._build_dock()
        self.places_panel = None
        self._build_places_dock()
        self._build_status()
        self.quest_want = []
        self.quest_items: list = []
        self.locate_items: list = []
        self.follow_pause_until = 0.0
        self.quest_panel = None
        self._build_quest_dock()
        self._build_menu()

        self.view.sceneClicked.connect(self.on_scene_click)
        self.view.cursorMoved.connect(self.on_cursor)

        self.sockets: dict[str, QWebSocket] = {}
        self.live: set[str] = set()
        self.want_feed = False
        self.stale_timer = QTimer(self)
        self.stale_timer.timeout.connect(self.check_stale)
        self.stale_timer.start(2000)

        geo = self.settings.get("geometry")
        if geo:
            self.setGeometry(*geo)
        else:
            self.resize(1280, 820)
        if self.settings.get("on_top"):
            self.top_act.setChecked(True)
            self.toggle_on_top(True)
        self.switch_world(self.world)
        self.game = None
        self.game_note = ""
        self.bridge = FeedBridge()
        self.bridge.position.connect(self.handle_position)
        self.bridge.status.connect(self.on_game_status)
        self.bridge.quest.connect(lambda e: self.quest_panel and self.quest_panel.on_game_event(e))
        self.launched_pids: set[int] = set()
        if self.settings.get("track_game", True):
            QTimer.singleShot(300, lambda: self.set_track_game(True))
        if self.settings.get("launch_on_start") and self.game_exe():
            QTimer.singleShot(1800, self.launch_game)   # after tracking is listening

    # --- building
    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.world_box = QComboBox()
        self.world_box.addItems(list(tiles.WORLDS))
        self.world_box.setCurrentText(self.world)
        self.world_box.currentTextChanged.connect(lambda w: self.switch_world(w, by_user=True))
        tb.addWidget(self.world_box)
        tb.addSeparator()
        grp = QActionGroup(self)
        self.mode_acts = {}
        for mode, label in (("pan", "Pan"), ("add", "Add pin"), ("me", "I'm here")):
            a = QAction(label, self, checkable=True)
            a.triggered.connect(lambda _=False, m=mode: self.set_mode(m))
            grp.addAction(a)
            tb.addAction(a)
            self.mode_acts[mode] = a
        self.mode_acts["pan"].setChecked(True)
        tb.addSeparator()
        tb.addWidget(QLabel(" X "))
        self.gx = QLineEdit()
        self.gx.setFixedWidth(90)
        tb.addWidget(self.gx)
        tb.addWidget(QLabel(" Z "))
        self.gz = QLineEdit()
        self.gz.setFixedWidth(90)
        tb.addWidget(self.gz)
        go = QPushButton("Go")
        go.clicked.connect(self.go_to)
        self.gz.returnPressed.connect(self.go_to)
        tb.addWidget(go)
        tb.addSeparator()
        self.find_box = search.FindBox(self, ROOT)
        tb.addWidget(self.find_box)
        tb.addSeparator()
        tb.addWidget(QLabel(" Follow "))
        self.follow_box = QComboBox()
        self.follow_box.setMinimumWidth(210)
        self.follow_box.setToolTip("Characters the feed is sending. The map keeps the one you pick in view.")
        self.follow_box.addItem("Nobody (no characters yet)", None)
        self.follow_box.activated.connect(self.on_follow_picked)
        tb.addWidget(self.follow_box)
        self.trail_act = QAction("Trail", self, checkable=True, checked=self.settings.get("trail", True))
        self.labels_act = QAction("Labels", self, checkable=True, checked=self.labels)
        self.labels_act.toggled.connect(self.toggle_labels)
        self.trail_act.toggled.connect(lambda on: (None if on else self.clear_trail()))
        for a in (self.trail_act, self.labels_act):
            tb.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.launch_act = QAction("▶ Launch game", self)
        self.launch_act.setToolTip("Start the game client set in Settings. Click again for a second window.")
        self.launch_act.triggered.connect(self.launch_game)
        tb.addAction(self.launch_act)
        self.feed_act = QAction("Connect feed", self)
        self.feed_act.setToolTip("Connect to a feed program (feed_demo.py, or another tool) at the Feed addresses")
        self.feed_act.triggered.connect(self.toggle_feed)   # in the Feed menu
        self.top_act = QAction("On top", self, checkable=True)
        self.top_act.toggled.connect(self.toggle_on_top)
        tb.addAction(self.top_act)

    def _build_dock(self):
        dock = QDockWidget("Pins", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        w = QWidget()
        v = QVBoxLayout(w)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search pins and notes")
        self.search.textChanged.connect(self.refresh_list)
        v.addWidget(self.search)
        chips = QWidget()
        grid = QHBoxLayout(chips)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        self.cat_boxes = {}
        for cid, name, glyph, color in CATS:
            cb = QCheckBox(glyph)
            cb.setChecked(True)
            cb.setToolTip(name)
            cb.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            cb.toggled.connect(lambda on, c=cid: self.toggle_cat(c, on))
            grid.addWidget(cb)
            self.cat_boxes[cid] = cb
        v.addWidget(chips)
        self.count_lbl = QLabel()
        v.addWidget(self.count_lbl)
        self.list = QListWidget()
        self.list.itemClicked.connect(lambda it: self.select_pin(it.data(Qt.UserRole), center=True))
        v.addWidget(self.list, 1)

        self.editor = QWidget()
        f = QFormLayout(self.editor)
        self.e_name = QLineEdit()
        self.e_cat = QComboBox()
        for cid, name, glyph, _ in CATS:
            self.e_cat.addItem(f"{glyph}  {name}", cid)
        self.e_notes = QPlainTextEdit()
        self.e_notes.setFixedHeight(90)
        self.e_pos = QLabel()
        row = QHBoxLayout()
        delb = QPushButton("Delete")
        delb.clicked.connect(self.delete_pin)
        done = QPushButton("Done")
        done.clicked.connect(lambda: (self.select_pin(None), self.end_typing()))
        row.addWidget(delb)
        row.addStretch()
        row.addWidget(done)
        f.addRow("Name", self.e_name)
        f.addRow("Type", self.e_cat)
        f.addRow("Notes", self.e_notes)
        f.addRow("Where", self.e_pos)
        f.addRow(row)
        self.e_name.textEdited.connect(self.edit_changed)
        self.e_cat.currentIndexChanged.connect(self.edit_changed)
        self.e_notes.textChanged.connect(self.edit_changed)
        self.editor.setVisible(False)
        v.addWidget(self.editor)

        io = QHBoxLayout()
        b1 = QPushButton("Export pins…")
        b1.clicked.connect(self.export_pins)
        b2 = QPushButton("Import pins…")
        b2.clicked.connect(self.import_pins)
        io.addWidget(b1)
        io.addWidget(b2)
        v.addLayout(io)
        dock.setWidget(w)
        dock.setMinimumWidth(290)
        dock.setObjectName("pins")
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.pins_dock = dock

    def _build_places_dock(self):
        dock = QDockWidget("Places", self)
        dock.setObjectName("places")
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        try:
            self.places_panel = places.PlacesPanel(self, places.Places(PLACES_FILE))
            dock.setWidget(self.places_panel)
        except Exception as e:
            lbl = QLabel(f"Map places couldn't be loaded:\n{e}\n\nplaces.json should be next to the app.")
            lbl.setWordWrap(True)
            dock.setWidget(lbl)
        dock.setMinimumWidth(290)
        self.setTabPosition(Qt.LeftDockWidgetArea, QTabWidget.North)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.tabifyDockWidget(self.pins_dock, dock)
        self.pins_dock.raise_()
        self.places_dock = dock

    def _build_status(self):
        sb = self.statusBar()
        self.cursor_lbl = QLabel("X —  Z —")
        self.cursor_lbl.setFont(QFont("Consolas", 10))
        self.feed_lbl = QLabel("Feed off")
        self.feed_lbl.setTextFormat(Qt.PlainText)
        sb.addWidget(self.cursor_lbl)
        sb.addPermanentWidget(self.feed_lbl)

    def _build_menu(self):
        m = self.menuBar().addMenu("&Map")
        for world in tiles.WORLDS:
            a = QAction(f"Download {world} map…", self)
            a.triggered.connect(lambda _=False, w=world: self.download(w))
            m.addAction(a)
        m.addSeparator()
        a = QAction("Fit whole map", self)
        a.setShortcut("Home")
        a.triggered.connect(self.view.fit)
        m.addAction(a)
        a = QAction("Clear trails", self)
        a.triggered.connect(self.clear_trail)
        m.addAction(a)
        m.addSeparator()
        self.keep_focus_act = QAction("Clicking the map leaves the game focused", self, checkable=True)
        self.keep_focus_act.setChecked(self.settings.get("keep_game_focus", True))
        self.keep_focus_act.setToolTip("Pan, zoom and click pins without the game losing focus. "
                                       "Typing in a box (pin names, search) borrows focus and gives it back.")
        self.keep_focus_act.toggled.connect(self.set_keep_focus)
        m.addAction(self.keep_focus_act)
        if sys.platform != "win32":
            self.keep_focus_act.setVisible(False)
        settings_act = QAction("&Settings", self)
        settings_act.triggered.connect(self.open_settings)
        self.menuBar().addAction(settings_act)   # a button on the menu bar, next to Map
        fm = self.menuBar().addMenu("&Feed")
        self.track_act = QAction("Track the game automatically", self, checkable=True)
        self.track_act.setToolTip("Reads your position from the game's own network traffic on this PC (Npcap, or run as administrator)")
        self.track_act.toggled.connect(self.set_track_game)
        fm.addAction(self.track_act)
        a = QAction("Tracking details…", self)
        a.triggered.connect(self.show_tracking_details)
        fm.addAction(a)
        self.admin_act = QAction("Start as administrator when Npcap isn't installed", self, checkable=True)
        self.admin_act.setToolTip("Without Npcap the map reads the game's traffic with Windows' own capture, "
                                  "which needs administrator")
        self.admin_act.setChecked(self.settings.get("run_as_admin", False))
        self.admin_act.toggled.connect(lambda on: (self.settings.__setitem__("run_as_admin", on), self.save_settings()))
        fm.addAction(self.admin_act)
        if sys.platform != "win32":
            self.admin_act.setVisible(False)
        fm.addSeparator()
        fm.addAction(self.feed_act)
        a = QAction("Feed addresses…", self)
        a.triggered.connect(self.edit_feed_urls)
        fm.addAction(a)
        a = QAction("Forget characters that stopped sending", self)
        a.triggered.connect(lambda: self.check_stale(forget_all_stale=True))
        fm.addAction(a)
        find = QAction("Find", self)
        find.setShortcut("Ctrl+F")
        find.triggered.connect(self.focus_find)
        self.addAction(find)
        esc = QAction("Cancel tool", self)
        esc.setShortcut("Esc")
        esc.triggered.connect(lambda: self.set_mode("pan"))
        self.addAction(esc)
        h = self.menuBar().addMenu("&Help")
        a = QAction("About", self)
        a.triggered.connect(lambda: QMessageBox.information(
            self, APP_NAME,
            "Offline companion map for EverQuest Online Adventures on Sandstorm.\n\n"
            "Map art: eqoa.live/map by Xylof / Sandstorm.\n"
            "Pins are saved in pins.json next to this app. Copy that file to share them.\n\n"
            "Live position: a feed program sends JSON to ws://localhost:8765, one line per update:\n"
            '{"char": "Dimitri", "world": "Tunaria", "x": 1234.5, "z": -567.8, "heading": 1.57}\n\n'
            "Several characters can share one feed (different \"char\" names), or each can have its own "
            "address under Feed ▸ Feed addresses."))
        h.addAction(a)

    # --- worlds and modes
    def switch_world(self, world, by_user=False):
        if by_user and world != self.world:
            f = self.chars.get(self.follow_name or "")
            if f and f.world and f.world != world:
                self.auto_follow = False
                self.set_follow(None)  # picking another world by hand stops following someone elsewhere
        self.world = world
        if self.world_box.currentText() != world:
            self.world_box.blockSignals(True)
            self.world_box.setCurrentText(world)
            self.world_box.blockSignals(False)
        self.view.set_world(world, tiles.load_local(ROOT, world))
        for c in self.chars.values():
            self.show_char(c)
        f = self.chars.get(self.follow_name or "")
        if f and f.world == world:
            self.view.centerOn(f.item.pos())
        self.sel_id = None
        self.editor.setVisible(False)
        self.rebuild_pins()
        self.render_quest_marks()
        if self.places_panel:
            self.places_panel.refresh()
        self.save_settings()

    def set_mode(self, mode):
        self.mode_acts[mode].setChecked(True)
        self.view.set_mode(mode)
        msg = {"add": "Click the map to place a pin. Esc to cancel.", "me": "Click where you are standing. Esc to cancel."}
        self.statusBar().showMessage(msg.get(mode, ""), 0 if mode != "pan" else 1)

    def on_scene_click(self, pt: QPointF):
        x, z = pt.x() / S, pt.y() / S
        if self.view.mode == "add":
            pin = {"id": uuid.uuid4().hex[:10], "name": "New pin", "cat": "note", "x": x, "z": z, "notes": ""}
            self.world_pins().append(pin)
            self.save_pins()
            self.rebuild_pins()
            self.select_pin(pin["id"])
            if self.keep_focus_act.isChecked():
                self.begin_typing(self.e_name)
            self.e_name.setFocus()
            self.e_name.selectAll()
        elif self.view.mode == "me":
            self.place_manual(x, z)
        self.set_mode("pan")

    def on_cursor(self, pt: QPointF):
        self.cursor_lbl.setText(f"X {fmt(pt.x() / S):>10}   Z {fmt(pt.y() / S):>10}")

    # --- pins
    def world_pins(self) -> list:
        return self.pins.setdefault(self.world, [])

    def save_pins(self):
        save_json(PINS_FILE, self.pins)

    def focus_find(self):
        if self.keep_focus_act.isChecked():
            self.begin_typing(self.find_box)
        self.find_box.setFocus()
        self.find_box.selectAll()

    def visible_pins(self):
        q = self.search.text().strip().lower()
        out = [p for p in self.world_pins() if p["cat"] not in self.hidden_cats and
               (not q or q in (p["name"] + " " + p.get("notes", "")).lower())]
        return sorted(out, key=lambda p: p["name"].lower())

    def rebuild_pins(self):
        for it in self.pin_items.values():
            self.scene.removeItem(it)
        self.pin_items.clear()
        for p in self.visible_pins():
            it = PinItem(p, self)
            it.selected = p["id"] == self.sel_id
            self.scene.addItem(it)
            self.pin_items[p["id"]] = it
        self.refresh_list()

    def refresh_list(self):
        pins = self.visible_pins()
        if set(p["id"] for p in pins) != set(self.pin_items):
            return self.rebuild_pins()
        self.list.clear()
        total = len(self.world_pins())
        self.count_lbl.setText(f"{len(pins)} of {total} pins · {self.world}")
        if not total:
            it = QListWidgetItem("No pins here yet.\nPick “Add pin”, then click the map.")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        for p in pins:
            _, cname, glyph, color = CAT.get(p["cat"], CAT["note"])
            it = QListWidgetItem(f"{glyph}  {p['name']}\n     {cname} · {fmt(p['x'])}, {fmt(p['z'])}")
            it.setForeground(QBrush(QColor("#e9dfc6")))
            it.setData(Qt.UserRole, p["id"])
            self.list.addItem(it)
            if p["id"] == self.sel_id:
                it.setSelected(True)

    def toggle_cat(self, cid, on):
        (self.hidden_cats.discard if on else self.hidden_cats.add)(cid)
        self.rebuild_pins()

    def toggle_labels(self, on):
        self.labels = on
        self.save_settings()
        self.scene.update()

    def current_pin(self):
        return next((p for p in self.world_pins() if p["id"] == self.sel_id), None)

    def select_pin(self, pid, center=False):
        self.sel_id = pid
        for i, it in self.pin_items.items():
            it.selected = i == pid
            it.update()
        p = self.current_pin()
        self.editor.setVisible(p is not None)
        if p:
            self.show_editor()
            if center:
                self.view.centerOn(p["x"] * S, p["z"] * S)
        self.refresh_list()

    def show_editor(self):
        p = self.current_pin()
        if not p:
            return
        for w in (self.e_name, self.e_cat, self.e_notes):
            w.blockSignals(True)
        self.e_name.setText(p["name"])
        self.e_cat.setCurrentIndex(max(0, self.e_cat.findData(p["cat"])))
        self.e_notes.setPlainText(p.get("notes", ""))
        self.e_pos.setText(f"X {fmt(p['x'])}   Z {fmt(p['z'])}  (drag the pin to move it)")
        for w in (self.e_name, self.e_cat, self.e_notes):
            w.blockSignals(False)

    def edit_changed(self, *_):
        p = self.current_pin()
        if not p:
            return
        p["name"] = self.e_name.text().strip() or "Untitled"
        p["cat"] = self.e_cat.currentData()
        p["notes"] = self.e_notes.toPlainText()
        self.save_pins()
        it = self.pin_items.get(p["id"])
        if it:
            it.setToolTip(p["name"] + (("\n" + p["notes"]) if p["notes"] else ""))
            it.update()
        self.refresh_list()

    def delete_pin(self):
        p = self.current_pin()
        if not p:
            return
        self.pins[self.world] = [q for q in self.world_pins() if q["id"] != p["id"]]
        self.sel_id = None
        self.editor.setVisible(False)
        self.save_pins()
        self.rebuild_pins()
        self.statusBar().showMessage(f"Deleted “{p['name']}”", 3000)

    def export_pins(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export pins", os.path.join(ROOT, "my-pins.json"), "Pins (*.json)")
        if path:
            save_json(path, {"app": "norrath-field-map", "version": 2, "pins": self.pins})
            self.statusBar().showMessage(f"Saved {sum(len(v) for v in self.pins.values())} pins to {path}", 5000)

    def import_pins(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import pins", ROOT, "Pins (*.json)")
        if not path:
            return
        data = load_json(path, None)
        src = data.get("pins", data) if isinstance(data, dict) else None
        if not isinstance(src, dict):
            QMessageBox.warning(self, "Import pins", "That file doesn't contain pins from this app.")
            return
        n = 0
        for world, plist in src.items():
            mine = self.pins.setdefault(world, [])
            for p in plist if isinstance(plist, list) else []:
                try:
                    x, z = float(p["x"]), float(p["z"])
                except Exception:
                    continue
                if any(q["name"] == p.get("name") and abs(q["x"] - x) < 1 and abs(q["z"] - z) < 1 for q in mine):
                    continue
                mine.append({"id": uuid.uuid4().hex[:10], "name": str(p.get("name", "Pin")),
                             "cat": p.get("cat") if p.get("cat") in CAT else "note", "x": x, "z": z,
                             "notes": str(p.get("notes", ""))})
                n += 1
        self.save_pins()
        self.rebuild_pins()
        self.statusBar().showMessage(f"Imported {n} pins", 5000)

    # --- characters
    def get_char(self, name, manual=False) -> Character:
        c = self.chars.get(name)
        if c is None:
            used = {ch.color for ch in self.chars.values()}
            color = "#ffffff" if manual else next((k for k in CHAR_COLORS if k not in used),
                                                  CHAR_COLORS[len(self.chars) % len(CHAR_COLORS)])
            c = Character(name, color, self.scene, manual)
            self.chars[name] = c
        return c

    def show_char(self, c: Character):
        here = c.world == self.world
        c.item.setVisible(here)
        c.trail.setVisible(here)
        c.item.followed = c.name == self.follow_name
        c.item.stale = c.stale()
        # followed on top, then live characters, then ones that stopped sending
        c.item.setZValue(22 if c.item.followed else 18 if c.item.stale else 20)
        c.trail.setOpacity(0.35 if c.item.stale else 0.85)
        c.item.update()

    def move_char(self, c: Character, x, z, heading, world):
        if world != c.world:
            c.clear_trail()
        c.world, c.x, c.z = world, x, z
        pt = QPointF(x * S, z * S)
        c.item.heading = heading
        c.item.setPos(pt)
        if self.trail_act.isChecked():
            if not c.pts or (c.pts[-1] - pt).manhattanLength() > 1.5:
                c.pts.append(pt)
                c.pts = c.pts[-3000:]
                path = QPainterPath(c.pts[0])
                for q in c.pts[1:]:
                    path.lineTo(q)
                c.trail.setPath(path)
        self.show_char(c)

    def place_manual(self, x, z):
        c = self.get_char(MANUAL, manual=True)
        self.move_char(c, x, z, None, self.world)
        self.view.centerOn(c.item.pos())

    def clear_trail(self):
        for c in self.chars.values():
            c.clear_trail()

    def go_to(self):
        try:
            x = float(self.gx.text().replace(",", ""))
            z = float(self.gz.text().replace(",", ""))
        except ValueError:
            self.statusBar().showMessage("Type both X and Z as numbers, like 25123.4", 4000)
            return
        self.place_manual(x, z)

    # --- follow dropdown
    def refresh_follow_box(self, force=False):
        names = sorted((c for c in self.chars.values() if not c.manual), key=lambda c: c.name.lower())
        sig = (self.follow_name, tuple((c.name, c.world, c.stale()) for c in names))
        if sig == self._follow_sig and not force:
            return
        self._follow_sig = sig
        box = self.follow_box
        box.blockSignals(True)
        box.clear()
        box.addItem("Nobody" if names else "Nobody (no characters yet)", None)
        for c in names:
            status = "not sending" if c.stale() else c.world
            box.addItem(f"●  {c.name}  ·  {status}", c.name)
            box.setItemData(box.count() - 1, QColor("#8a8f93" if c.stale() else c.color), Qt.ForegroundRole)
        i = box.findData(self.follow_name) if self.follow_name in self.chars else 0
        box.setCurrentIndex(max(0, i))
        box.blockSignals(False)
        self.update_feed_label()

    def update_feed_label(self):
        n_live = sum(1 for c in self.chars.values() if not c.manual and not c.stale())
        parts = []
        if self.game:
            parts.append("Tracking the game")
        if self.want_feed:
            parts.append("Feed connected" if self.live else "Feed: retrying")
        if not parts:
            self.feed_lbl.setText("Not tracking")
            self.feed_lbl.setStyleSheet("")
            return
        if n_live:
            parts.append(f"{n_live} character{'s' if n_live != 1 else ''}")
            self.feed_lbl.setStyleSheet("color: #4cc38a;")
        else:
            parts.append("waiting for the game" if self.game else "no characters yet")
            self.feed_lbl.setStyleSheet("")
        self.feed_lbl.setText("● " + " · ".join(parts))
        self.feed_lbl.setToolTip(self.game_note or "")

    # --- tracking the game from its network traffic
    def set_track_game(self, on):
        if self.track_act.isChecked() != on:
            self.track_act.blockSignals(True)
            self.track_act.setChecked(on)
            self.track_act.blockSignals(False)
        self.settings["track_game"] = on
        self.save_settings()
        if not on:
            if self.game:
                self.game.stop()
            self.game = None
            self.update_feed_label()
            return
        if self.game:
            return
        if gamefeed is None:
            problem = "The packet library isn't installed. Run run.bat again, or: pip install scapy psutil"
        else:
            g = gamefeed.GameFeed(self.bridge.position.emit, self.bridge.status.emit,
                                  self.settings.get("server_ports") or None, self.bridge.quest.emit)
            g.exe_hint = os.path.basename(self.game_exe() or "")
            g.watch_pids = self.launched_pids
            problem = g.start()
            if not problem:
                self.game = g
            elif g.needs_admin and self.offer_admin():
                return
        if problem:
            self.track_act.blockSignals(True)
            self.track_act.setChecked(False)
            self.track_act.blockSignals(False)
            self.settings["track_game"] = False  # don't ask again every start; Feed menu turns it back on
            self.save_settings()
            self.on_game_status(problem + "\n\nOnce that's fixed, turn on Feed ▸ Track the game automatically.", warn=True)
        else:
            self.statusBar().showMessage("Tracking the game. Log in with the map open so it can find you.", 8000)
        self.update_feed_label()

    def offer_admin(self) -> bool:
        """No Npcap and not administrator: offer to restart elevated. True if the map is restarting."""
        box = QMessageBox(self)
        box.setWindowTitle("Track the game")
        box.setIcon(QMessageBox.Information)
        box.setText(gamefeed.NEEDS_ADMIN)
        box.setInformativeText("Restart the map as administrator now? Windows asks each time it starts that way.\n\n"
                               "Or install Npcap (free from npcap.com, default options) and the map won't need it.")
        always = QCheckBox("Always start the map as administrator (when Npcap isn't installed)")
        always.setChecked(True)
        box.setCheckBox(always)
        go = box.addButton("Restart as administrator", QMessageBox.AcceptRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not go:
            return False
        self.settings["run_as_admin"] = always.isChecked()
        self.settings["track_game"] = True
        self.save_settings()
        if hasattr(self, "admin_act"):
            self.admin_act.setChecked(always.isChecked())
        if not relaunch_as_admin():
            QMessageBox.information(self, "Track the game", "Windows didn't start the map as administrator.")
            return False
        self._restarting = True
        self.close()
        QApplication.instance().quit()
        return True

    def on_game_status(self, text, warn=False):
        self.game_note = text
        self.statusBar().showMessage(text, 15000)
        if warn:
            QMessageBox.information(self, "Track the game", text)
        self.update_feed_label()

    def on_follow_picked(self, index):
        self.auto_follow = False
        self.remembered = self.follow_box.itemData(index)
        self.set_follow(self.remembered)

    def set_follow(self, name):
        self.follow_name = name
        self.save_settings()
        c = self.chars.get(name or "")
        if c and c.world:
            if c.world != self.world:
                self.switch_world(c.world)
            self.view.centerOn(c.item.pos())
        for ch in self.chars.values():
            self.show_char(ch)
        self.refresh_follow_box()
        if self.quest_panel:
            self.quest_panel.follow_changed()

    def check_stale(self, forget_all_stale=False):
        now = time.monotonic()
        for name, c in list(self.chars.items()):
            if c.manual:
                continue
            if (forget_all_stale and c.stale()) or now - c.seen > FORGET_AFTER:
                c.remove(self.scene)
                del self.chars[name]
            else:
                self.show_char(c)
        self.refresh_follow_box()

    # --- live feed
    def feed_urls(self) -> list[str]:
        urls = self.settings.get("feed_urls") or [self.settings.get("feed_url", "ws://localhost:8765")]
        return [u for u in urls if u.strip()]

    def edit_feed_urls(self):
        text, ok = QInputDialog.getMultiLineText(
            self, "Feed addresses",
            "One address per line. Use one if all characters come through a single feed,\n"
            "or one per game window if each window sends on its own port.",
            "\n".join(self.feed_urls()))
        if not ok:
            return
        urls = [u.strip() for u in text.splitlines() if u.strip()] or ["ws://localhost:8765"]
        self.settings["feed_urls"] = urls
        self.save_settings()
        if self.want_feed:  # reconnect with the new list
            self.toggle_feed()
            self.toggle_feed()

    def open_socket(self, url):
        ws = self.sockets.get(url)
        if ws is None:
            ws = QWebSocket()
            ws.connected.connect(lambda u=url: self.on_ws_open(u))
            ws.disconnected.connect(lambda u=url: self.on_ws_close(u))
            ws.textMessageReceived.connect(self.on_ws_msg)
            self.sockets[url] = ws
        ws.open(QUrl(url))

    def toggle_feed(self):
        if self.want_feed:
            self.want_feed = False
            for ws in self.sockets.values():
                ws.close()
            self.sockets.clear()
            self.live.clear()
            self.feed_act.setText("Connect feed")
            self.update_feed_label()
        else:
            self.want_feed = True
            self.feed_act.setText("Disconnect feed")
            urls = self.feed_urls()
            self.feed_lbl.setText(f"Connecting to {urls[0]}…" if len(urls) == 1 else f"Connecting to {len(urls)} feeds…")
            for u in urls:
                self.open_socket(u)

    def on_ws_open(self, url):
        self.live.add(url)
        self.refresh_follow_box(force=True)

    def on_ws_close(self, url):
        self.live.discard(url)
        self.update_feed_label()
        if self.want_feed and url in self.feed_urls():
            self.refresh_follow_box(force=True)
            QTimer.singleShot(2000, lambda: self.want_feed and url in self.sockets and self.sockets[url].open(QUrl(url)))

    def on_ws_msg(self, text):
        try:
            m = json.loads(text)
        except Exception:
            return
        for msg in m if isinstance(m, list) else [m]:  # a feed may send one character or a list
            self.handle_position(msg)

    def handle_position(self, m):
        if m.get("alive"):  # standing still: no new position, but the game is still there
            c = self.chars.get(str(m.get("char")))
            if c:
                c.seen = time.monotonic()
                self.show_char(c)
                self.refresh_follow_box()
            return
        try:
            x = float(m["x"])
            z = float(m["z"] if "z" in m else m["y"])
        except Exception:
            return
        name = str(m.get("char") or m.get("name") or "Character")[:40]
        if name == MANUAL:
            name = "Character"
        new = name not in self.chars
        c = self.get_char(name)
        c.seen = time.monotonic()
        w = m.get("world")
        world = str(w) if w else (c.world or self.world)   # a world without a map keeps the marker off-screen
        h = m.get("heading")
        heading = float(h) + math.radians(self.settings.get("heading_offset_deg", 0)) if isinstance(h, (int, float)) else None
        if new and self.auto_follow:
            if name == self.remembered:
                self.follow_name, self.auto_follow = name, False   # the one you followed last time
            elif self.follow_name is None:
                self.follow_name = name                            # first to appear, until yours shows up
        if name == self.follow_name and world != self.world and world in tiles.WORLDS:
            self.switch_world(world)
        self.move_char(c, x, z, heading, world)
        if name == self.follow_name and time.monotonic() > self.follow_pause_until:
            self.view.centerOn(c.item.pos())
        self.refresh_follow_box()
        if new and self.quest_panel:
            self.quest_panel.follow_changed()

    # --- quests
    def _build_quest_dock(self):
        dock = QDockWidget("Quests", self)
        dock.setObjectName("quests")
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        try:
            book = quests.QuestBook(QUESTS_FILE, GUIDES_FILE)
            self.quest_panel = quests.QuestPanel(self, book, PROGRESS_FILE)
            dock.setWidget(self.quest_panel)
        except Exception as e:
            lbl = QLabel(f"Quest data couldn't be loaded:\n{e}\n\nquests.json should be next to the app.")
            lbl.setWordWrap(True)
            dock.setWidget(lbl)
        dock.setMinimumWidth(310)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.quest_dock = dock

    def set_quest_marks(self, want):
        self.quest_want = want
        self.render_quest_marks()

    def render_quest_marks(self):
        for it in self.quest_items:
            self.scene.removeItem(it)
        self.quest_items = []
        here = quests.WORLD_IDS.get(self.world)
        for glyph, color, w, x, z, tip, qi in self.quest_want:
            if w != here:
                continue
            it = quests.QuestMarkItem(glyph, color, tip, lambda qi=qi: self.quest_panel and self.quest_panel.select(qi))
            it.setPos(x * S, z * S)
            self.scene.addItem(it)
            self.quest_items.append(it)

    def show_places(self, world, pts, what):
        """Point at places from the quest panel: switch world, fit them (and you) in view, ring them for a while."""
        self.clear_locate()
        if world != self.world:
            if world not in tiles.WORLDS:
                self.statusBar().showMessage(f"{what}: in {world}, which has no map here", 6000)
                return
            self.switch_world(world)
        self.follow_pause_until = time.monotonic() + 20   # don't snap straight back to you
        rect = QRectF()
        for x, z in pts:
            r = QRectF(x * S - 1, z * S - 1, 2, 2)
            rect = r if rect.isNull() else rect.united(r)
            ring = LocateItem()
            ring.setPos(x * S, z * S)
            self.scene.addItem(ring)
            self.locate_items.append(ring)
        me = self.chars.get(self.follow_name or "")
        if me and me.world == world and me.item.isVisible():
            rect = rect.united(QRectF(me.item.pos(), me.item.pos()).adjusted(-1, -1, 1, 1))
        pad = max(rect.width(), rect.height()) * 0.25 + 120
        self.view.fitInView(rect.adjusted(-pad, -pad, pad, pad), Qt.KeepAspectRatio)
        self.statusBar().showMessage(f"{what}  (following pauses for 20 s)", 8000)
        QTimer.singleShot(12000, self.clear_locate)

    def clear_locate(self):
        for it in self.locate_items:
            it.timer.stop()
            self.scene.removeItem(it)
        self.locate_items = []

    # --- download
    def download(self, world):
        dl = tiles.Downloader(ROOT, world)
        try:
            dzi = dl.fetch_index()
        except Exception as e:
            QMessageBox.warning(self, "Download", f"Couldn't reach eqoa.live: {e}")
            return
        have = sum(1 for t in dzi.all_tiles() if os.path.exists(tiles.tile_path(ROOT, world, dzi, *t)))
        total = dzi.count()
        ask = QMessageBox.question(
            self, f"Download {world} map",
            f"{world}: {dzi.width:,} × {dzi.height:,} pixels, {total:,} tiles "
            f"({have:,} already on this PC).\n\n"
            "This copies the Sandstorm map (by Xylof) for offline use. It downloads 4 tiles at a time to "
            "go easy on their server, so the full Tunaria map takes a while and uses a few hundred MB.\n\n"
            "You can cancel and resume later. Start now?")
        if ask != QMessageBox.Yes:
            return
        prog = QProgressDialog(f"Downloading {world}…", "Cancel", 0, total, self)
        prog.setWindowTitle("Download map")
        prog.setMinimumDuration(0)
        prog.setValue(have)
        self._thread = QThread(self)
        self._worker = DownloadWorker(dl)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(lambda d, t: (prog.setValue(d), prog.setLabelText(f"Downloading {world}… {d:,} / {t:,} tiles")))
        prog.canceled.connect(dl.cancel.set)

        def done(ok, failed):
            self._thread.quit()
            prog.close()
            if world == self.world:
                self.view.set_world(world, tiles.load_local(ROOT, world))
            if ok:
                note = f"\n{failed} tiles failed; run the download again to retry them." if failed else ""
                QMessageBox.information(self, "Download map", f"{world} map is ready.{note}")
            else:
                self.statusBar().showMessage("Download paused. Run it again to resume.", 6000)

        self._worker.finished.connect(done)
        self._thread.start()

    # --- leaving the game focused (Windows)
    # The map's window is made "no-activate": clicks, drags and the wheel still reach it, but
    # Windows keeps the game as the active window, so its keyboard and controller input never
    # move here. Typing needs the keyboard, so clicking a text box borrows the focus and the
    # map hands it back to the game when you press Enter or Esc, or move the mouse off the map.
    def _user32(self):
        if sys.platform != "win32":
            return None
        import ctypes
        u = ctypes.windll.user32
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        u.GetForegroundWindow.restype = ctypes.c_void_p
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.IsWindow.argtypes = [ctypes.c_void_p]
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        return u

    def apply_no_activate(self, on: bool):
        u = self._user32()
        if not u:
            return
        GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_APPWINDOW = -20, 0x08000000, 0x00040000
        hwnd = int(self.winId())
        style = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        # A no-activate window loses its taskbar button (minimized, it shrinks to a little caption
        # bar above the taskbar). WS_EX_APPWINDOW keeps the button; it stays on once set.
        new = (style | WS_EX_NOACTIVATE | WS_EX_APPWINDOW) if on else (style & ~WS_EX_NOACTIVATE)
        if new != style:
            u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
        if on and not style & WS_EX_APPWINDOW and self.isVisible() and not self.isMinimized():
            # the taskbar only looks at the style when a window is shown: show it again, without activating
            SW_HIDE, SW_SHOWNA = 0, 8
            u.ShowWindow(hwnd, SW_HIDE)
            u.ShowWindow(hwnd, SW_SHOWNA)

    def set_keep_focus(self, on):
        self.settings["keep_game_focus"] = on
        self.save_settings()
        if not getattr(self, "_typing", False):
            self.apply_no_activate(on)

    def is_text_box(self, obj) -> bool:
        for w in (obj, getattr(obj, "parent", lambda: None)()):
            if isinstance(w, (QLineEdit, QPlainTextEdit, QAbstractSpinBox)) and w.window() is self:
                return True
        return False

    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.MouseButtonPress and self.keep_focus_act.isChecked()
                and not getattr(self, "_typing", False) and self.is_text_box(obj)):
            target = obj if isinstance(obj, (QLineEdit, QPlainTextEdit, QAbstractSpinBox)) else obj.parent()
            self.begin_typing(target)
        elif ev.type() == QEvent.KeyPress and getattr(self, "_typing", False) and \
                ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape) and not isinstance(obj, QPlainTextEdit):
            QTimer.singleShot(0, self.end_typing)
        return False

    def begin_typing(self, widget=None):
        u = self._user32()
        if not u or getattr(self, "_typing", False):
            return
        self._typing = True
        self._prev_fg = u.GetForegroundWindow()
        self.apply_no_activate(False)
        self.raise_()
        self.activateWindow()
        u.SetForegroundWindow(int(self.winId()))
        if widget is not None:
            QTimer.singleShot(0, widget.setFocus)
        self._away = 0
        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._typing_tick)
        self._typing_timer.start(250)

    def _typing_tick(self):
        inside = self.frameGeometry().contains(QCursor.pos())
        self._away = 0 if inside else self._away + 1
        if self._away >= 6:          # off the map for 1.5 s: give the keyboard back
            self.end_typing()

    def end_typing(self):
        if not getattr(self, "_typing", False):
            return
        self._typing = False
        self._typing_timer.stop()
        if self.keep_focus_act.isChecked():
            self.apply_no_activate(True)
        u = self._user32()
        prev = getattr(self, "_prev_fg", None)
        if u and prev and prev != int(self.winId()) and u.IsWindow(prev):
            u.SetForegroundWindow(prev)

    # --- the game client: settings and launching
    def game_exe(self) -> str:
        return self.settings.get("game_exe", "")

    def open_settings(self, note: str = ""):
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.setMinimumWidth(560)
        v = QVBoxLayout(dlg)
        if note:
            lbl = QLabel(note)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #e6b84a;")
            v.addWidget(lbl)
        form = QFormLayout()
        row = QHBoxLayout()
        path = QLineEdit(self.game_exe())
        path.setPlaceholderText(r"for example  C:\Games\EQOA\eqoa.exe")
        browse = QPushButton("Browse…")

        def pick():
            start = os.path.dirname(path.text()) if path.text() else os.path.expanduser("~")
            f, _ = QFileDialog.getOpenFileName(dlg, "Find the game client", start,
                                               "Programs (*.exe);;All files (*)" if sys.platform == "win32" else "All files (*)")
            if f:
                path.setText(os.path.normpath(f))
        browse.clicked.connect(pick)
        row.addWidget(path, 1)
        row.addWidget(browse)
        form.addRow("Game client", row)
        args = QLineEdit(self.settings.get("game_args", ""))
        args.setPlaceholderText("optional, e.g. the path to your disc image if the game asks every time")
        form.addRow("Start options", args)
        auto = QCheckBox("Launch the game when the map opens")
        auto.setChecked(self.settings.get("launch_on_start", False))
        form.addRow("", auto)
        track = QCheckBox("Track the game automatically (Npcap, or run as administrator)")
        track.setChecked(self.track_act.isChecked())
        form.addRow("", track)
        v.addLayout(form)
        help_ = QLabel("The Rust client is usually eqoa.exe (or eqoa-vanilla-windows-x86_64.exe) in the folder you "
                       "unzipped it to. Launching from here makes sure the map is listening before you log in, and "
                       "tells the map which program is the game so it listens to nothing else.")
        help_.setWordWrap(True)
        help_.setStyleSheet("color: #9aa29b;")
        v.addWidget(help_)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return False
        exe = path.text().strip().strip('"')
        if exe and not os.path.isfile(exe):
            QMessageBox.warning(self, "Settings", f"Couldn't find that program:\n{exe}")
            return self.open_settings(note)
        self.settings.update(game_exe=exe, game_args=args.text().strip(), launch_on_start=auto.isChecked())
        self.save_settings()
        if self.game:
            self.game.exe_hint = os.path.basename(exe)
        if track.isChecked() != self.track_act.isChecked():
            self.set_track_game(track.isChecked())
        return True

    def launch_game(self):
        exe = self.game_exe()
        if not exe or not os.path.isfile(exe):
            if not self.open_settings("Point the map to your game client first, then press ▶ Launch game again."
                                      if not exe else f"The game client isn't at {exe} any more. Where is it now?"):
                return
            exe = self.game_exe()
            if not exe:
                return
        try:
            extra = shlex.split(self.settings.get("game_args", ""), posix=(sys.platform != "win32"))
        except ValueError:
            extra = []
        before = set(self.game_windows())
        u = self._user32()
        if u:  # let the new game take the front: we just had the click, so Windows allows handing it on
            try:
                u.AllowSetForegroundWindow(-1)   # ASFW_ANY
            except Exception:
                pass
        kw = {"cwd": os.path.dirname(exe) or None}
        if sys.platform == "win32":   # the game keeps running if the map closes
            kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        try:
            proc = subprocess.Popen([exe] + extra, **kw)
        except OSError as e:
            QMessageBox.warning(self, "Launch game", f"Couldn't start the game:\n{e}")
            return
        self.launched_pids.add(proc.pid)
        self._front_tries = 0
        self._front_before = before
        if u:
            QTimer.singleShot(500, self._bring_game_front)
        if not self.game and self.settings.get("track_game", True):
            self.set_track_game(True)
        note = "Started the game. Log in and the map will find you." if self.game else \
            "Started the game. Tracking is off (Feed ▸ Track the game automatically)."
        self.statusBar().showMessage(note, 10000)

    def game_windows(self) -> dict[int, int]:
        """Visible game windows: hwnd -> pid (Windows only). Titles 'EQOA…' or the launched programs."""
        if sys.platform != "win32":
            return {}
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found: dict[int, int] = {}

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in self.launched_pids or buf.value.startswith("EQOA"):
                    found[int(hwnd)] = pid.value
            return True
        user32.EnumWindows(each, 0)
        return found

    def _bring_game_front(self):
        """Once the launched game's window appears, put it in front so it logs in with the focus."""
        self._front_tries += 1
        new = [h for h in self.game_windows() if h not in self._front_before]
        u = self._user32()
        if new and u:
            hwnd = new[-1]
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
            u.SetForegroundWindow(hwnd)
            return
        if self._front_tries < 60:     # up to 30 s (the client may update itself and restart first)
            QTimer.singleShot(500, self._bring_game_front)

    # --- tracking details
    def show_tracking_details(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Tracking details")
        dlg.resize(720, 360)
        v = QVBoxLayout(dlg)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Consolas", 10))
        v.addWidget(text)
        hint = QLabel("Updates every second. Copy this and paste it in chat if tracking isn't working.")
        v.addWidget(hint)
        bb = QDialogButtonBox()
        copy = bb.addButton("Copy", QDialogButtonBox.ActionRole)
        bb.addButton(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        def refresh():
            if self.game:
                body = self.game.describe()
            else:
                body = ("Tracking is off. Turn on Feed ▸ Track the game automatically.\n" +
                        (f"Last message: {self.game_note}" if self.game_note else ""))
            if text.toPlainText() != body:
                text.setPlainText(body)

        def do_copy():
            QApplication.clipboard().setText(text.toPlainText())
            copy.setText("Copied")
        copy.clicked.connect(do_copy)
        t = QTimer(dlg)
        t.timeout.connect(refresh)
        t.start(1000)
        refresh()
        dlg.exec()

    # --- misc
    def toggle_on_top(self, on):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()
        if self.keep_focus_act.isChecked() and not getattr(self, "_typing", False):
            self.apply_no_activate(True)  # changing the flag makes a new native window
        self.save_settings()

    def save_settings(self):
        g = self.geometry()
        self.settings.update(world=self.world, labels=self.labels, follow_name=self.remembered,
                             trail=self.trail_act.isChecked(), on_top=self.top_act.isChecked(),
                             geometry=[g.x(), g.y(), g.width(), g.height()])
        try:
            save_json(SETTINGS_FILE, self.settings)
        except OSError:
            pass

    def closeEvent(self, e):
        if self.game:
            self.game.stop()
        if getattr(self, "_worker", None):
            self._worker.dl.cancel.set()
        self.save_settings()
        super().closeEvent(e)


def dark_palette(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    for role, c in ((QPalette.Window, "#1f2327"), (QPalette.WindowText, "#e9dfc6"), (QPalette.Base, "#24282c"),
                    (QPalette.AlternateBase, "#2d3236"), (QPalette.Text, "#e9dfc6"), (QPalette.Button, "#2d3236"),
                    (QPalette.ButtonText, "#e9dfc6"), (QPalette.Highlight, "#1d6a63"),
                    (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#2d3236"),
                    (QPalette.ToolTipText, "#e9dfc6"), (QPalette.PlaceholderText, "#8a8170")):
        p.setColor(role, QColor(c))
    app.setPalette(p)


def relaunch_as_admin() -> bool:
    """Start this app again elevated (Windows asks first). True if the new one is on its way."""
    if sys.platform != "win32":
        return False
    import ctypes
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, sys.argv[1:]
    else:
        exe = sys.executable
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            exe = w   # no console window
        args = [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    params = subprocess.list2cmdline(args)
    try:
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, ROOT, 1)
    except Exception:
        return False
    return r > 32


def main():
    if sys.platform == "win32" and gamefeed is not None and load_json(SETTINGS_FILE, {}).get("run_as_admin") \
            and load_json(SETTINGS_FILE, {}).get("track_game", True) \
            and not gamefeed.is_admin() and not gamefeed.npcap_ready():
        if relaunch_as_admin():
            return   # the elevated copy takes over
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    dark_palette(app)
    w = MainWindow()
    w.show()
    app.installEventFilter(w)
    if w.keep_focus_act.isChecked():
        w.apply_no_activate(True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
