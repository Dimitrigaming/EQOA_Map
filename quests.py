"""Quest tracker: the class quests of levels 1-20, and every other quest guide (17, 23-47, the
30/40/49 path quests, 53-59, the level 60 epics, exemplars, weres, portals, misc), per character.

Data: quests.json, exported from the EQOA Rust client's quest guide (MIT), which
was built from EQOA-Data (github.com/Jadiction/EQOA-Data, MIT); NPC and creature
places come from go-eqoa-server's spawns and may differ a little on Sandstorm.

guides.json holds the other guides (EQOA-Data's text, split into quests, with its map points).

Progress is kept per character in quest_progress.json next to the app.
"""
from __future__ import annotations

import json
import math
import os
import time

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QFrame, QGraphicsItem, QGridLayout,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox,
                               QTextBrowser, QVBoxLayout, QWidget)

CLASSES = ["Warrior", "Ranger", "Paladin", "Shadowknight", "Monk", "Bard", "Rogue", "Druid", "Shaman",
           "Cleric", "Magician", "Necromancer", "Enchanter", "Wizard", "Alchemist"]
RACES = ["Human", "Elf", "Dark Elf", "Gnome", "Dwarf", "Troll", "Barbarian", "Halfling", "Erudite", "Ogre"]
# world number in the data -> the map's world names (as gamefeed.WORLD_NAMES)
WORLD_NAMES = {0: "Tunaria", 1: "Oggok", 2: "Odus", 3: "Lavastorm", 4: "Plane of Sky", 5: "Secrets"}
WORLD_IDS = {v: k for k, v in WORLD_NAMES.items()}

KIND_WORDS = {"Talk": "Talk to", "Show": "Show to", "Give": "Give to", "Kill": "Kill", "Loot": "Loot from", "Reach": "Go to"}

PLACEHOLDER = "My character"   # until the map sees a real one
GOLD, TEAL, DIM, DONE = "#e6b84a", "#5db6a8", "#8a8170", "#7f8a84"


def city_title(c: str) -> str:
    return c.replace("-", " ").replace("klick anon", "klick'anon").title().replace("Klick'Anon", "Klick'Anon")


class QuestBook:
    """The quest data and the questions the panel asks of it."""

    def __init__(self, path: str, guides_path: str | None = None):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.source = d.get("source", "")
        self.cities = d["cities"]
        self.npcs = d["npcs"]                    # [name, [[world, x, z], ...]]
        self.quests = d["quests"]
        self.homes = {(r, c): cities for r, c, cities in d["homes"]}
        self.sets: list[dict] = []
        if guides_path and os.path.exists(guides_path):
            with open(guides_path, encoding="utf-8") as f:
                g = json.load(f)
            if len(self.npcs) + len(g["npcs"]) and all(
                    q.get("giver") is None or q["giver"] < len(self.npcs) + len(g["npcs"]) for q in g["quests"]):
                self.npcs = self.npcs + g["npcs"]
                self.quests = self.quests + g["quests"]
                self.sets = g["sets"]
        self.by_key = {q["key"]: i for i, q in enumerate(self.quests)}
        for q in self.quests:
            if q.get("requires_key") in self.by_key:
                q["requires"] = [self.by_key[q["requires_key"]]]

    def set_title(self, key) -> str:
        return next((s["title"] for s in self.sets if s["key"] == key), key or "")

    def set_quests(self, key: str, cls: int) -> list[int]:
        """A guide set's quests for this class (those for any class too), by level."""
        out = [i for i, q in enumerate(self.quests) if q.get("set") == key and (not q["classes"] or cls in q["classes"])]
        return sorted(out, key=lambda i: (not self.quests[i].get("has_level", True), self.quests[i]["levels"][0], i))

    def home_cities(self, race: int, cls: int) -> list[int]:
        return self.homes.get((race, cls), [])

    def chain(self, city: int, cls: int, race: int) -> list[int]:
        """The class quests of a city for this class and race, by level."""
        out = []
        for i, q in enumerate(self.quests):
            if q["city"] != city:
                continue
            if q["classes"] and cls not in q["classes"]:
                continue
            if q["races"] and race not in q["races"]:
                continue
            out.append(i)
        return sorted(out, key=lambda i: (self.quests[i]["levels"][0], self.quests[i]["key"]))

    def places(self, step: dict) -> list[tuple[int, float, float]]:
        if step.get("at"):
            return [tuple(step["at"])]
        if step.get("npc") is None:
            return []
        return [tuple(p) for p in self.npcs[step["npc"]][1]]

    def npc_name(self, idx) -> str:
        return self.npcs[idx][0] if idx is not None else ""

    @staticmethod
    def nearest(places, world: int, near: tuple[float, float] | None, n: int):
        """Up to n places in this world, nearest to `near` (or the first ones)."""
        here = [p for p in places if p[0] == world]
        if near is not None:
            here.sort(key=lambda p: (p[1] - near[0]) ** 2 + (p[2] - near[1]) ** 2)
        return here[:n]


class QuestMarkItem(QGraphicsItem):
    """A '!' (quest to take) or '?' (next step) on the map."""

    def __init__(self, glyph: str, color: str, tip: str, on_click):
        super().__init__()
        self.glyph, self.color = glyph, QColor(color)
        self.on_click = on_click
        self.setFlags(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(12)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)

    def boundingRect(self):
        return QRectF(-13, -30, 26, 32)

    def paint(self, p: QPainter, *_):
        # a small banner on a stalk, so it reads as different from the round pins
        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(-3, -8)
        path.addRoundedRect(QRectF(-11, -29, 22, 22), 5, 5)
        p.setPen(QPen(QColor(16, 14, 10, 220), 2))
        p.setBrush(self.color)
        p.drawRoundedRect(QRectF(-11, -29, 22, 22), 5, 5)
        p.drawLine(QPointF(0, -7), QPointF(0, 0))
        f = QFont("Segoe UI", 12)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#15120c"))
        p.drawText(QRectF(-11, -29, 22, 22), Qt.AlignCenter, self.glyph)

    def mousePressEvent(self, e):
        self.on_click()
        e.accept()


class QuestPanel(QWidget):
    """The right-hand panel. `win` is the map window (for positions, centring and marks)."""

    changed = Signal()

    def __init__(self, win, book: QuestBook, progress_path: str):
        super().__init__()
        self.win, self.book, self.path = win, book, progress_path
        try:
            with open(progress_path, encoding="utf-8") as f:
                self.progress = json.load(f)
        except Exception:
            self.progress = {}
        self.char = None
        self.sel = None          # quest index shown in the details
        self.marks: list[QuestMarkItem] = []
        self._loading = False
        self.auto: dict[str, dict] = {}   # per character: the journal's lines as matched, quests whose line left

        v = QVBoxLayout(self)
        # --- the character
        top = QGridLayout()
        top.setHorizontalSpacing(6)
        self.char_box = QComboBox()
        self.char_box.setToolTip("Whose quests to track. Follows the character the map is following.")
        self.char_box.activated.connect(lambda i: self.set_char(self.char_box.itemData(i), by_user=True))
        top.addWidget(QLabel("Character"), 0, 0)
        top.addWidget(self.char_box, 0, 1, 1, 3)
        self.race_box = QComboBox()
        self.race_box.addItems(RACES)
        self.class_box = QComboBox()
        self.class_box.addItems(CLASSES)
        top.addWidget(self.race_box, 1, 0, 1, 2)
        top.addWidget(self.class_box, 1, 2, 1, 2)
        self.level_box = QSpinBox()
        self.level_box.setRange(1, 60)
        self.level_box.setPrefix("Level ")
        self.city_box = QComboBox()
        top.addWidget(self.level_box, 2, 0, 1, 2)
        top.addWidget(self.city_box, 2, 2, 1, 2)
        v.addLayout(top)
        for w in (self.race_box, self.class_box):
            w.currentIndexChanged.connect(self.profile_edited)
        self.level_box.valueChanged.connect(self.profile_edited)
        self.city_box.currentIndexChanged.connect(self.profile_edited)

        # --- which quests: the class chain, or one of the guide sets
        self.set_box = QComboBox()
        self.set_box.addItem("Class quests (levels 1–20)", None)
        for st in self.book.sets:
            self.set_box.addItem(st["title"], st["key"])
            self.set_box.setItemData(self.set_box.count() - 1, st["sub"], Qt.ToolTipRole)
        self.qset = win.settings.get("quest_set") if hasattr(win, "settings") else None
        self.set_box.setCurrentIndex(max(0, self.set_box.findData(self.qset)))
        self.qset = self.set_box.currentData()
        self.set_box.currentIndexChanged.connect(self.set_changed)
        top.addWidget(self.set_box, 3, 0, 1, 4)
        if not self.book.sets:
            self.set_box.setVisible(False)

        # --- the chain
        row = QHBoxLayout()
        self.count_lbl = QLabel()
        row.addWidget(self.count_lbl, 1)
        self.marks_chk = QCheckBox("On map")
        self.marks_chk.setToolTip("Show ! at quests you can take and ? where your active quests' next steps are")
        self.marks_chk.setChecked(True)
        self.marks_chk.toggled.connect(lambda _: self.update_marks())
        row.addWidget(self.marks_chk)
        v.addLayout(row)
        self.list = QListWidget()
        self.list.itemClicked.connect(lambda it: self.select(it.data(Qt.UserRole)))
        v.addWidget(self.list, 3)

        # --- the details
        self.detail = QFrame()
        self.detail.setFrameShape(QFrame.StyledPanel)
        dv = QVBoxLayout(self.detail)
        self.d_title = QLabel()
        self.d_title.setWordWrap(True)
        self.d_title.setTextFormat(Qt.RichText)
        dv.addWidget(self.d_title)
        self.d_giver = QPushButton()
        self.d_giver.setFlat(True)
        self.d_giver.setStyleSheet("text-align: left; color: %s;" % GOLD)
        self.d_giver.setToolTip("Show on the map")
        self.d_giver.clicked.connect(self.locate_giver)
        dv.addWidget(self.d_giver)
        self.d_offer = QLabel()
        self.d_offer.setWordWrap(True)
        self.d_offer.setStyleSheet("color: #b5a888;")
        dv.addWidget(self.d_offer)
        self.steps = QListWidget()
        self.steps.setWordWrap(True)
        self.steps.setToolTip("Tick a step when it's done. Click a step to see where it is on the map.")
        self.steps.itemChanged.connect(self.step_ticked)
        self.steps.itemClicked.connect(self.step_clicked)
        dv.addWidget(self.steps, 1)
        self.d_guide = QTextBrowser()
        self.d_guide.setOpenLinks(False)
        self.d_guide.setToolTip("Gold names are on the map: click one to see where")
        pal = self.d_guide.palette()
        pal.setColor(QPalette.Link, QColor(GOLD))
        self.d_guide.setPalette(pal)
        self.d_guide.anchorClicked.connect(self.guide_link)
        self.d_guide.setVisible(False)
        dv.addWidget(self.d_guide, 2)
        self.d_rewards = QLabel()
        self.d_rewards.setWordWrap(True)
        dv.addWidget(self.d_rewards)
        brow = QHBoxLayout()
        self.b_start = QPushButton("Start")
        self.b_start.clicked.connect(self.start_quest)
        self.b_done = QPushButton("Mark done")
        self.b_done.clicked.connect(self.toggle_done)
        brow.addWidget(self.b_start)
        brow.addStretch()
        brow.addWidget(self.b_done)
        dv.addLayout(brow)
        self.detail.setVisible(False)
        v.addWidget(self.detail, 4)
        credit = QLabel("Quest data: EQOA-Data (Jadiction), with the EQOA Rust client's quest guide")
        credit.setStyleSheet("color: #6f6a5c; font-size: 8pt;")
        v.addWidget(credit)
        self.refresh_chars()

    # ---------------------------------------------------------------- profiles
    def profile(self, name=None) -> dict:
        name = name or self.char
        p = self.progress.setdefault(name, {})
        p.setdefault("race", 1)
        p.setdefault("class", 10)
        p.setdefault("level", 1)
        p.setdefault("city", None)
        p.setdefault("done", [])
        p.setdefault("active", {})   # quest key -> steps ticked [bool, ...]
        return p

    def save(self):
        live = {c.name for c in self.win.chars.values() if not c.manual}
        keep = {n: p for n, p in self.progress.items()   # leave out untouched placeholders
                if n in live or n == self.char or p.get("done") or p.get("active") or p.get("level", 1) > 1}
        keep.pop(PLACEHOLDER, None) if not (keep.get(PLACEHOLDER, {}).get("done") or
                                             keep.get(PLACEHOLDER, {}).get("active")) else None
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(keep, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def refresh_chars(self):
        """Characters: the map's live ones and anyone with saved progress."""
        live = [c.name for c in self.win.chars.values() if not c.manual]
        names = list(dict.fromkeys(live + sorted(self.progress)))
        if not names:
            names = [PLACEHOLDER]
        elif PLACEHOLDER in names and len(names) > 1 and not self.progress.get(PLACEHOLDER, {}).get("done"):
            names.remove(PLACEHOLDER)
        box = self.char_box
        box.blockSignals(True)
        box.clear()
        for n in names:
            box.addItem(n + ("  ●" if n in live else ""), n)
        box.blockSignals(False)
        want = self.char if self.char in names else (self.win.follow_name if self.win.follow_name in names else names[0])
        self.set_char(want)

    def set_char(self, name, by_user=False):
        if name is None:
            return
        self.char = name
        self.char_box.blockSignals(True)
        self.char_box.setCurrentIndex(max(0, self.char_box.findData(name)))
        self.char_box.blockSignals(False)
        p = self.profile()
        self._loading = True
        self.race_box.setCurrentIndex(p["race"])
        self.class_box.setCurrentIndex(p["class"])
        self.level_box.setValue(p["level"])
        self.fill_cities()
        self._loading = False
        self.sel = None
        self.detail.setVisible(False)
        self.refresh_list()

    def fill_cities(self):
        p = self.profile()
        homes = self.book.home_cities(p["race"], p["class"])
        if p["city"] is None or p["city"] not in range(len(self.book.cities)):
            p["city"] = homes[0] if homes else 0
        box = self.city_box
        box.blockSignals(True)
        box.clear()
        order = homes + [i for i in range(len(self.book.cities)) if i not in homes]
        for i in order:
            label = city_title(self.book.cities[i])
            if homes and i not in homes:
                label += "  (not a home city)"
            box.addItem(label, i)
        box.setCurrentIndex(max(0, box.findData(p["city"])))
        box.blockSignals(False)

    def profile_edited(self, *_):
        if self._loading or not self.char:
            return
        p = self.profile()
        race, cls = self.race_box.currentIndex(), self.class_box.currentIndex()
        if (race, cls) != (p["race"], p["class"]):
            p["race"], p["class"], p["city"] = race, cls, None   # pick the new home city
            self.fill_cities()
        else:
            p["city"] = self.city_box.currentData()
        p["level"] = self.level_box.value()
        self.save()
        self.refresh_list()

    def follow_changed(self):
        """The map started following someone (or a new character appeared)."""
        live = {c.name for c in self.win.chars.values() if not c.manual}
        have = {self.char_box.itemData(i) for i in range(self.char_box.count())}
        if live - have:
            self.refresh_chars()
        f = self.win.follow_name
        if f and f != self.char and f in live:
            self.set_char(f)

    # ---------------------------------------------------------------- states
    def state(self, qi: int) -> str:
        p = self.profile()
        q = self.book.quests[qi]
        if q["key"] in p["done"]:
            return "done"
        if q["key"] in p["active"]:
            return "active"
        if any(self.book.quests[r]["key"] not in p["done"] for r in q["requires"]):
            return "locked"
        if q.get("has_level", True) and p["level"] < q["levels"][0]:
            return "later"
        return "available"

    def refresh_list(self):
        p = self.profile()
        chain = self.shown()
        self.list.clear()
        glyph = {"done": "✓", "active": "▶", "available": "○", "later": "·", "locked": "·"}
        color = {"done": DONE, "active": TEAL, "available": GOLD, "later": DIM, "locked": DIM}
        counts = {"done": 0, "active": 0, "available": 0}
        for qi in chain:
            q = self.book.quests[qi]
            st = self.state(qi)
            if st in counts:
                counts[st] += 1
            lv = f"L{q['levels'][0]:<2}" if q.get("has_level", True) else "L– "
            it = QListWidgetItem(f"{glyph[st]}  {lv}  {q['name']}")
            it.setForeground(QBrush(QColor(color[st])))
            if st == "done":
                f = it.font()
                f.setStrikeOut(True)
                it.setFont(f)
            it.setToolTip({"done": "Done", "active": "In progress", "available": "You can take this now",
                           "later": f"From level {q['levels'][0]}",
                           "locked": "Finish the earlier quests in this chain first"}[st])
            it.setData(Qt.UserRole, qi)
            self.list.addItem(it)
            if qi == self.sel:
                it.setSelected(True)
        if not chain and self.qset:
            it = QListWidgetItem(f"No quests here for a {CLASSES[p['class']]}.")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        elif not chain:
            it = QListWidgetItem(f"No class quests found for a {RACES[p['race']]} {CLASSES[p['class']]} "
                                 f"in {city_title(self.book.cities[p['city']])}.\nTry another city.")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        self.count_lbl.setText(f"{counts['active']} active · {counts['available']} to take · "
                               f"{counts['done']}/{len(chain)} done")
        if self.sel is not None:
            self.show_detail(self.sel)
        self.update_marks()

    # ---------------------------------------------------------------- details
    def select(self, qi):
        self.sel = qi
        for i in range(self.list.count()):   # also when picked from a mark on the map
            if self.list.item(i).data(Qt.UserRole) == qi:
                self.list.setCurrentRow(i)
                self.list.scrollToItem(self.list.item(i))
                break
        self.show_detail(qi)
        self.update_marks()

    def show_detail(self, qi):
        q = self.book.quests[qi]
        p = self.profile()
        st = self.state(qi)
        self.detail.setVisible(True)
        lv = q["levels"][0]
        guide = "guide" in q
        if guide:
            bits = [f"Level {lv}" if q.get("has_level", True) else "Any level", self.book.set_title(q["set"])]
            if q.get("cls_text") and q["cls_text"].lower() not in ("all", "all/all", "any"):
                bits.append(q["cls_text"])
            sub = " · ".join(bits)
        else:
            sub = f"Level {lv} · {city_title(self.book.cities[q['city']])}"
        self.d_title.setText(f"<b style='font-size:11pt'>{q['name']}</b><br><span style='color:{DIM}'>{sub}</span>")
        if q["giver"] is not None:
            self.d_giver.setText(f"From {self.book.npc_name(q['giver'])}  ⌖")
        elif q.get("pois"):
            self.d_giver.setText(f"Places in this guide ({len(q['pois'])})  ⌖")
        self.d_giver.setVisible(q["giver"] is not None or bool(q.get("pois")))
        self.d_offer.setText(f"“{q['offer']}”" if q["offer"] else "")
        self.d_offer.setVisible(bool(q["offer"]))
        self.d_guide.setVisible(guide)
        if guide:
            self.d_guide.setMarkdown(q["guide"])
            self.color_links()
        self.steps.setVisible(bool(q["steps"]))
        self.steps.setMaximumHeight(150 if guide else 16777215)
        self.d_rewards.setText(f"<span style='color:{DIM}'>Rewards:</span> {q['rewards']}" if q["rewards"] else "")
        ticks = p["active"].get(q["key"], [])
        self.steps.blockSignals(True)
        self.steps.clear()
        for i, s in enumerate(q["steps"]):
            text = s["text"] or f"{KIND_WORDS.get(s['kind'], s['kind'])} {self.book.npc_name(s['npc'])}"
            if s["kind"] == "Kill" and s.get("count", 0) > 1:
                text += f" (×{s['count']})"
            if s.get("tunar"):
                text += f" ({s['tunar']} tunar)"
            it = QListWidgetItem(f"{i + 1}. {text}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            done = st == "done" or (i < len(ticks) and ticks[i])
            it.setCheckState(Qt.Checked if done else Qt.Unchecked)
            it.setForeground(QBrush(QColor(DONE if done else "#e9dfc6")))
            it.setData(Qt.UserRole, i)
            if self.book.places(s):
                it.setToolTip("Click to see where on the map")
            self.steps.addItem(it)
        self.steps.blockSignals(False)
        self.b_start.setVisible(st in ("available", "later", "locked"))
        self.b_start.setText("Start" if st == "available" else "Start anyway")
        self.b_done.setText("Not done" if st == "done" else "Mark done")

    def start_quest(self):
        q = self.book.quests[self.sel]
        p = self.profile()
        p["active"].setdefault(q["key"], [False] * len(q["steps"]))
        self.save()
        self.refresh_list()

    def toggle_done(self):
        q = self.book.quests[self.sel]
        p = self.profile()
        if q["key"] in p["done"]:
            p["done"].remove(q["key"])
        else:
            self.finish(self.sel)
        self.save()
        self.refresh_list()

    def finish(self, qi, name=None, only_before=False):
        """Done, and so is everything before it in its chain (only those, with only_before)."""
        p = self.profile(name)
        stack, seen = (list(self.book.quests[qi]["requires"]) if only_before else [qi]), set()
        while stack:
            i = stack.pop()
            if i in seen:
                continue
            seen.add(i)
            key = self.book.quests[i]["key"]
            if key not in p["done"]:
                p["done"].append(key)
            p["active"].pop(key, None)
            stack.extend(self.book.quests[i]["requires"])

    def step_ticked(self, it: QListWidgetItem):
        q = self.book.quests[self.sel]
        p = self.profile()
        if q["key"] in p["done"]:
            p["done"].remove(q["key"])
        ticks = p["active"].setdefault(q["key"], [False] * len(q["steps"]))
        ticks += [False] * (len(q["steps"]) - len(ticks))
        ticks[it.data(Qt.UserRole)] = it.checkState() == Qt.Checked
        if all(ticks):
            self.finish(self.sel)
            self.win.statusBar().showMessage(f"Quest done: {q['name']}", 6000)
        self.save()
        self.refresh_list()

    def step_clicked(self, it: QListWidgetItem):
        s = self.book.quests[self.sel]["steps"][it.data(Qt.UserRole)]
        self.locate(self.book.places(s), s["text"] or self.book.npc_name(s["npc"]))

    def locate_giver(self):
        q = self.book.quests[self.sel]
        if q["giver"] is not None:
            self.locate([tuple(p) for p in self.book.npcs[q["giver"]][1]], self.book.npc_name(q["giver"]))
        else:
            places = [tuple(p) for i in q.get("pois", []) for p in self.book.npcs[i][1]]
            self.locate(places, q["name"], n=40)

    def color_links(self):
        """The markdown reader paints links the system's blue; gold reads better and matches the map."""
        from PySide6.QtGui import QTextCharFormat, QTextCursor
        doc = self.d_guide.document()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(GOLD))
        fmt.setFontUnderline(False)
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isAnchor():
                    c = QTextCursor(doc)
                    c.setPosition(frag.position())
                    c.setPosition(frag.position() + frag.length(), QTextCursor.KeepAnchor)
                    c.mergeCharFormat(fmt)
                it += 1
            block = block.next()

    def guide_link(self, url):
        s = url.toString()
        if s.startswith("place:"):
            i = int(s[6:])
            self.locate([tuple(p) for p in self.book.npcs[i][1]], self.book.npc_name(i))

    def set_changed(self, *_):
        self.qset = self.set_box.currentData()
        self.win.settings["quest_set"] = self.qset
        self.win.save_settings()
        self.sel = None
        self.detail.setVisible(False)
        self.refresh_list()

    def shown(self) -> list[int]:
        p = self.profile()
        if self.qset:
            return self.book.set_quests(self.qset, p["class"])
        return self.book.chain(p["city"], p["class"], p["race"])

    # ---------------------------------------------------------------- automatic, from the game
    # The server sends the journal (one line per quest in progress, naming its current step) at
    # login and as it changes, and says "You have finished a quest!" when one is done. Each line
    # is matched to a quest and step (questmatch); the panel's own ticks and buttons stay as they
    # are, so anything it gets wrong can be put right by hand.
    def matcher(self):
        if getattr(self, "_matcher", None) is None:
            import questmatch
            self._matcher = questmatch.Matcher(self.book)
        return self._matcher

    def on_game_event(self, e: dict):
        name = e.get("char") or self.char
        if not name:
            return
        p = self.profile(name)
        st = self.auto.setdefault(name, {"lines": [], "gone": [], "finished_at": 0.0})
        changed = False
        if e["kind"] == "login":
            if 0 <= e.get("class", -1) < len(CLASSES) and 0 <= e.get("race", -1) < len(RACES):
                if (p["race"], p["class"]) != (e["race"], e["class"]):
                    p["race"], p["class"], p["city"] = e["race"], e["class"], None
            if 0 < e.get("level", 0) <= 60:
                p["level"] = e["level"]
            if p["city"] is None:
                homes = self.book.home_cities(p["race"], p["class"])
                p["city"] = homes[0] if homes else 0
            st["lines"] = []
            changed = True
            changed |= self.apply_journal(name, e["journal"])
        elif e["kind"] == "journal":
            changed |= self.apply_journal(name, e["journal"])
        elif e["kind"] == "level":
            p["level"] = e["level"]
            changed = True
        elif e["kind"] == "finished":
            st["finished_at"] = time.monotonic()
            changed |= self.settle_finished(name)
        if changed:
            self.save()
            if name not in [self.char_box.itemData(i) for i in range(self.char_box.count())]:
                self.refresh_chars()
            if name == self.char:
                self.set_char(name) if e["kind"] == "login" else self.refresh_list()

    def apply_journal(self, name, lines) -> bool:
        p = self.profile(name)
        st = self.auto[name]
        m = self.matcher()
        before = {line: qi for line, qi in st["lines"]}
        now = time.monotonic()
        # lines that left: their quests may have just been finished, or moved to their next step
        for line, qi in st["lines"]:
            if qi is not None and line not in lines:
                st["gone"].append((qi, now))
        st["gone"] = [(qi, t) for qi, t in st["gone"] if now - t <= 10]
        after = [qi for qi, _ in st["gone"]]
        homes = self.book.home_cities(p["race"], p["class"])
        cities = list(dict.fromkeys(([p["city"]] if p["city"] is not None else []) + homes))
        new_lines, changed = [], False
        for line in lines:
            if line in before:
                new_lines.append((line, before[line]))
                continue
            hit = m.match_line(line, p["class"], p["race"], p["level"], cities, set(p["done"]), after)
            new_lines.append((line, hit.quest if hit else None))
            if not hit:
                continue
            q = self.book.quests[hit.quest]
            if q["key"] in p["done"]:
                continue   # marked done by hand: leave it
            ticks = p["active"].setdefault(q["key"], [False] * len(q["steps"]))
            ticks += [False] * (len(q["steps"]) - len(ticks))
            for i in range(hit.step):
                ticks[i] = True
            self.finish(hit.quest, name, only_before=True)   # its chain's earlier quests are behind you
            if q["city"] != p["city"]:
                p["city"] = q["city"]                          # this is the character's quest city
            changed = True
            if name == self.char:
                self.win.statusBar().showMessage(f"Quest: {q['name']} (step {hit.step + 1} of {len(q['steps'])})", 6000)
        st["lines"] = new_lines
        if time.monotonic() - st["finished_at"] <= 10:
            changed |= self.settle_finished(name)
        return changed

    def settle_finished(self, name) -> bool:
        """After "You have finished a quest!": a quest whose line just left and has none now is done."""
        p = self.profile(name)
        st = self.auto[name]
        now = time.monotonic()
        current = {qi for _, qi in st["lines"] if qi is not None}
        changed = False
        for qi, t in st["gone"]:
            if now - t <= 10 and qi not in current and self.book.quests[qi]["key"] not in p["done"]:
                self.finish(qi, name)
                changed = True
                if name == self.char:
                    self.win.statusBar().showMessage(f"Quest done: {self.book.quests[qi]['name']}", 8000)
        if changed:
            st["gone"] = [(qi, t) for qi, t in st["gone"] if qi in current]
        return changed

    # ---------------------------------------------------------------- the map
    def player(self):
        """The tracked character's (world id, x, z), if the map knows it."""
        c = self.win.chars.get(self.char or "") or self.win.chars.get(self.win.follow_name or "")
        if c and c.world in WORLD_IDS:
            return WORLD_IDS[c.world], c.x, c.z
        return None

    def locate(self, places, what, n=3):
        if not places:
            self.win.statusBar().showMessage(f"No known place for: {what}", 5000)
            return
        me = self.player()
        world = me[0] if me and any(pl[0] == me[0] for pl in places) else places[0][0]
        near = (me[1], me[2]) if me and me[0] == world else None
        pts = self.book.nearest(places, world, near, n)
        self.win.show_places(WORLD_NAMES.get(world, f"World {world}"), [(x, z) for _, x, z in pts], what)

    def update_marks(self):
        """! at givers of quests you can take now, ? at your active quests' next steps."""
        want = []   # (glyph, color, world, x, z, tip, quest index)
        if self.marks_chk.isChecked():
            p = self.profile()
            me = self.player()
            chain = self.shown()
            active_guides = [i for i, q in enumerate(self.book.quests) if "guide" in q and q["key"] in p["active"]]
            givers: dict[int, list[int]] = {}
            for qi in list(dict.fromkeys(chain + active_guides)):
                st = self.state(qi)
                q = self.book.quests[qi]
                if st == "available" and "guide" in q and not (
                        q["giver"] is not None and q.get("has_level") and p["level"] - 10 <= q["levels"][0]):
                    continue   # a guide's ! only near your level, where it names who gives it
                if st == "available":
                    givers.setdefault(q["giver"], []).append(qi)
                elif st == "active":
                    ticks = p["active"].get(q["key"], [])
                    nxt = next((i for i, s in enumerate(q["steps"]) if not (i < len(ticks) and ticks[i])), None)
                    if nxt is None:
                        continue
                    s = q["steps"][nxt]
                    places = self.book.places(s)
                    for w in sorted({pl[0] for pl in places}):
                        near = (me[1], me[2]) if me and me[0] == w else None
                        for _, x, z in self.book.nearest(places, w, near, 2):
                            want.append(("?", TEAL, w, x, z, f"{q['name']}: {s['text']}", qi))
            for npc, qis in givers.items():
                tip = "Quest to take from " + self.book.npc_name(npc) + ":\n" + "\n".join(
                    f"  L{self.book.quests[i]['levels'][0]} {self.book.quests[i]['name']}" for i in qis)
                for w, x, z in self.book.npcs[npc][1][:2]:
                    want.append(("!", GOLD, w, x, z, tip, qis[0]))
        self.win.set_quest_marks(want)
