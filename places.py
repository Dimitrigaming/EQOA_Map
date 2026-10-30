"""Map layers you can switch on and off: coachmen and the routes between them, towns, XP camps
and rare spawns. Data: places.json (EQOA-Data by Jadiction, MIT, as the EQOA Rust client's map
markers had it; places from go-eqoa-server).
"""
from __future__ import annotations

import json
import math

import tiles

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QGraphicsItem, QGraphicsLineItem, QHBoxLayout, QLabel, QSpinBox,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

WORLD_NAMES = {0: "Tunaria", 1: "Oggok", 2: "Odus"}
WORLD_IDS = {v: k for k, v in WORLD_NAMES.items()}

ROUTE = "#e0b44c"
COACH = "#e0b44c"
CAMP = "#8fcf6a"
RARE = "#d46ad4"
SIDE = {"Good": "#7fb4e6", "Evil": "#e06a5a", "Neutral": "#d8cfb8"}

LAYERS = [  # key, label, colour, tooltip
    ("coaches", "Coachmen", COACH, "Coach stops. Click one to light up where it goes."),
    ("routes", "Coach routes", ROUTE, "Lines between towns a coach runs between"),
    ("towns", "Towns", SIDE["Neutral"], "Town names (blue good, red evil, sand neutral)"),
    ("camps", "XP camps", CAMP, "Experience camps with their level range"),
    ("rares", "Rare spawns", RARE, "Named and rare creatures at camps"),
]


def outlined(p: QPainter, pos: QPointF, text: str, color: str, size=9, bold=True):
    f = QFont("Segoe UI", size)
    f.setBold(bold)
    path = QPainterPath()
    path.addText(pos, f, text)
    p.setPen(QPen(QColor(16, 14, 10, 225), 3))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)


class Places:
    def __init__(self, path: str):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.camps, self.coaches, self.towns, self.routes = d["camps"], d["coaches"], d["towns"], d["routes"]
        self.source = d.get("source", "")

    def coach_tip(self, c) -> str:
        return f"{c['coachman']} — {c['town']}\nGoes to: " + (", ".join(c["to"]) or "nowhere listed")

    def camp_tip(self, c) -> str:
        lo, hi = c["levels"]
        lines = [f"{c['name']}  (levels {lo}–{hi})"]
        if c["note"]:
            lines.append(c["note"])
        if c["rare"]:
            lines.append("Rare: " + ", ".join(c["rare"]))
        if c["own"]:
            lines.append("Own loot: " + ", ".join(c["own"]))
        if c["drops"]:
            lines.append(f"Drops ({c['table']} table): " + ", ".join(c["drops"]))
        return "\n".join(lines)


class CoachItem(QGraphicsItem):
    def __init__(self, coach, tip, on_click):
        super().__init__()
        self.coach, self.on_click = coach, on_click
        self.lit = False
        self.setFlags(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(8)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)

    def boundingRect(self):
        return QRectF(-12, -12, 24, 24)

    def paint(self, p: QPainter, *_):
        # a little wagon wheel
        p.setPen(QPen(QColor(16, 14, 10, 220), 2))
        p.setBrush(QColor("#fff2c4") if self.lit else QColor(COACH))
        p.drawEllipse(QPointF(0, 0), 9, 9)
        p.setPen(QPen(QColor("#3a2c10"), 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), 5.5, 5.5)
        for a, b in ((QPointF(-5.5, 0), QPointF(5.5, 0)), (QPointF(0, -5.5), QPointF(0, 5.5)),
                     (QPointF(-3.9, -3.9), QPointF(3.9, 3.9)), (QPointF(-3.9, 3.9), QPointF(3.9, -3.9))):
            p.drawLine(a, b)

    def mousePressEvent(self, e):
        self.on_click(self.coach)
        e.accept()


class TownItem(QGraphicsItem):
    def __init__(self, town):
        super().__init__()
        self.town = town
        self.setFlags(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(7)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self):
        return QRectF(-6, -24, 260, 30)

    def paint(self, p: QPainter, *_):
        col = SIDE.get(self.town["side"], SIDE["Neutral"])
        p.setPen(QPen(QColor(16, 14, 10, 220), 1.5))
        p.setBrush(QColor(col))
        p.drawRect(QRectF(-3.5, -3.5, 7, 7))
        outlined(p, QPointF(-4, -9), self.town["name"], col, 10)


class CampItem(QGraphicsItem):
    def __init__(self, camp, tip, win, rare_only=False, dy=0):
        super().__init__()
        self.dy = dy   # screen pixels down, so a rare star sits under its camp's badge
        self.camp, self.win, self.rare_only = camp, win, rare_only
        self.setFlags(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(9)
        self.setToolTip(tip)
        lo, hi = camp["levels"]
        self.lv = f"{lo}–{hi}"

    def boundingRect(self):
        return QRectF(-18, -10 + self.dy, 300, 22)

    def paint(self, p: QPainter, *_):
        p.translate(0, self.dy)
        if self.rare_only:
            star = QPolygonF()
            for i in range(10):
                r = 8 if i % 2 == 0 else 3.5
                a = -math.pi / 2 + i * math.pi / 5
                star.append(QPointF(r * math.cos(a), r * math.sin(a)))
            p.setPen(QPen(QColor(16, 14, 10, 220), 1.5))
            p.setBrush(QColor(RARE))
            p.drawPolygon(star)
            if self.win.labels:
                outlined(p, QPointF(11, 4), ", ".join(self.camp["rare"]), "#f0c8f0")
            return
        w = 12 + 6.2 * len(self.lv)
        r = QRectF(-w / 2, -8, w, 16)
        p.setPen(QPen(QColor(16, 14, 10, 220), 1.5))
        p.setBrush(QColor(CAMP))
        p.drawRoundedRect(r, 4, 4)
        f = QFont("Segoe UI", 8)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#13200c"))
        p.drawText(r, Qt.AlignCenter, self.lv)
        if self.win.labels:
            outlined(p, QPointF(w / 2 + 4, 4), self.camp["name"], "#dcefcf")


class PlacesPanel(QWidget):
    """The left-hand Places list: a switch per layer, and under each what's in this world."""

    def __init__(self, win, places: Places):
        super().__init__()
        self.win, self.p = win, places
        self.on = dict({k: True for k, *_ in LAYERS}, **win.settings.get("layers", {}))
        self.items: list = []
        self.lit_coach = None
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.lv_on = QCheckBox("Camps for level")
        self.lv_on.setToolTip("Only show camps whose range is within 3 levels of this")
        self.lv = QSpinBox()
        self.lv.setRange(1, 60)
        self.lv_on.setChecked(win.settings.get("camp_level_on", False))
        self.lv.setValue(win.settings.get("camp_level", 10))
        self.lv_on.toggled.connect(self.changed)
        self.lv.valueChanged.connect(self.changed)
        row.addWidget(self.lv_on)
        row.addWidget(self.lv)
        row.addStretch()
        v.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self.on_item_changed)
        self.tree.itemClicked.connect(self.on_click)
        v.addWidget(self.tree, 1)
        hint = QLabel("Tick a layer to show it. Click an entry to find it on the map. Click a coachman "
                      "on the map to light up the towns it runs to.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9a9384;")
        v.addWidget(hint)
        credit = QLabel("Data: EQOA-Data (Jadiction), go-eqoa-server")
        credit.setStyleSheet("color: #7a7466; font-size: 8pt;")
        v.addWidget(credit)

    def world_id(self):
        return WORLD_IDS.get(self.win.world)

    def camp_ok(self, c):
        if not self.lv_on.isChecked():
            return True
        L = self.lv.value()
        lo, hi = c["levels"]
        return lo - 3 <= L <= hi + 3

    def changed(self, *_):
        self.win.settings["camp_level_on"] = self.lv_on.isChecked()
        self.win.settings["camp_level"] = self.lv.value()
        self.win.save_settings()
        self.refresh()

    # --- list
    def rebuild_list(self):
        w = self.world_id()
        open_ = {self.tree.topLevelItem(i).data(0, Qt.UserRole)[1] for i in range(self.tree.topLevelItemCount())
                 if self.tree.topLevelItem(i).isExpanded()}
        self.tree.blockSignals(True)
        self.tree.clear()
        groups = {
            "coaches": [(f"{c['town']}  —  {c['coachman']}", c["at"], self.p.coach_tip(c), c)
                        for c in sorted(self.p.coaches, key=lambda c: c["town"]) if c["world"] == w],
            "routes": [(f"{r['from']}  ↔  {r['to']}", None, "", r) for r in self.p.routes if r["world"] == w],
            "towns": [(t["name"], t["at"], t["side"], t) for t in sorted(self.p.towns, key=lambda t: t["name"])
                      if t["world"] == w],
            "camps": [(f"{c['levels'][0]:>2}–{c['levels'][1]:<2}  {c['name']}", c["at"], self.p.camp_tip(c), c)
                      for c in sorted(self.p.camps, key=lambda c: tuple(c["levels"]))
                      if c["world"] == w and self.camp_ok(c)],
            "rares": [(f"{', '.join(c['rare'])}  ({c['name']})", c["at"], self.p.camp_tip(c), c)
                      for c in self.p.camps if c["world"] == w and c["rare"]],
        }
        for key, label, color, tip in LAYERS:
            rows = groups[key]
            top = QTreeWidgetItem([f"{label}  ({len(rows)})"])
            top.setData(0, Qt.UserRole, ("layer", key))
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable)
            top.setCheckState(0, Qt.Checked if self.on.get(key, True) else Qt.Unchecked)
            top.setForeground(0, QColor(color))
            top.setToolTip(0, tip)
            for text, at, ttip, obj in rows:
                ch = QTreeWidgetItem([text])
                ch.setData(0, Qt.UserRole, ("place", key, at, obj))
                if ttip:
                    ch.setToolTip(0, ttip)
                top.addChild(ch)
            self.tree.addTopLevelItem(top)
            top.setExpanded(key in open_)
        self.tree.blockSignals(False)

    def on_item_changed(self, it, _col):
        d = it.data(0, Qt.UserRole)
        if d and d[0] == "layer":
            self.on[d[1]] = it.checkState(0) == Qt.Checked
            self.win.settings["layers"] = self.on
            self.win.save_settings()
            self.render()

    def on_click(self, it, _col):
        d = it.data(0, Qt.UserRole)
        if not d or d[0] != "place":
            return
        _, key, at, obj = d
        if key == "routes":
            self.win.show_places(self.win.world, [obj["a"], obj["b"]], f"{obj['from']} ↔ {obj['to']}")
            return
        if key == "coaches":
            self.light(obj)
        self.win.show_places(self.win.world, [at], it.text(0).strip())

    # --- map
    def clear(self):
        for it in self.items:
            self.win.scene.removeItem(it)
        self.items = []

    def refresh(self):
        """After a world change: the list and the map."""
        self.rebuild_list()
        self.render()

    def render(self):
        """Draw the switched-on layers for the world on show."""
        self.clear()
        w = self.world_id()
        if w is None:
            return
        S, sc = tiles.GAME_TO_PX, self.win.scene
        lit = self.lit_coach if self.lit_coach and self.lit_coach["world"] == w else None
        if self.on.get("routes"):
            for r in self.p.routes:
                if r["world"] != w:
                    continue
                hot = lit is not None and ((r["from"] == lit["town"] and r["to"] in lit["to"]) or
                                           (r["to"] == lit["town"] and r["from"] in lit["to"]))
                pen = QPen(QColor("#fff2c4" if hot else ROUTE), 4 if hot else 2.2,
                           Qt.SolidLine if hot else Qt.DashLine)
                pen.setCosmetic(True)
                ln = QGraphicsLineItem(r["a"][0] * S, r["a"][1] * S, r["b"][0] * S, r["b"][1] * S)
                ln.setPen(pen)
                ln.setZValue(6 if hot else 5)
                ln.setOpacity(1.0 if hot or lit is None else 0.45)
                ln.setToolTip(f"Coach: {r['from']} ↔ {r['to']}")
                sc.addItem(ln)
                self.items.append(ln)
        if self.on.get("towns"):
            for t in self.p.towns:
                if t["world"] == w:
                    it = TownItem(t)
                    it.setPos(t["at"][0] * S, t["at"][1] * S)
                    sc.addItem(it)
                    self.items.append(it)
        if self.on.get("coaches"):
            for c in self.p.coaches:
                if c["world"] == w:
                    it = CoachItem(c, self.p.coach_tip(c), self.light)
                    it.lit = lit is not None and (c is lit or c["town"] in lit["to"])
                    it.setPos(c["at"][0] * S, c["at"][1] * S)
                    sc.addItem(it)
                    self.items.append(it)
        for key, rare in (("camps", False), ("rares", True)):
            if not self.on.get(key):
                continue
            for c in self.p.camps:
                if c["world"] != w or (rare and not c["rare"]) or (not rare and not self.camp_ok(c)):
                    continue
                below = rare and self.on.get("camps") and self.camp_ok(c)
                it = CampItem(c, self.p.camp_tip(c), self.win, rare_only=rare, dy=18 if below else 0)
                it.setPos(c["at"][0] * S, c["at"][1] * S)
                sc.addItem(it)
                self.items.append(it)

    def light(self, coach):
        """Highlight a coachman's routes; clicking the same one again turns it off."""
        self.lit_coach = None if self.lit_coach is coach else coach
        self.render()
        if self.lit_coach:
            self.win.statusBar().showMessage(self.p.coach_tip(coach).replace("\n", "   "), 10000)
