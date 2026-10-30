"""Which quest and step a journal line is, the way the EQOA Rust client's quest guide
decided it (crates/eqoa/src/questguide, MIT), ported to Python.

Sandstorm words its journal its own way ("Return to Nothard.", "Bring Norgram 2 slashed
orc pawn belts ."), so a line is matched by the people and things it names, among the
quests a character of that class, race and level could have.
"""
from __future__ import annotations

from dataclasses import dataclass

RARE = 3        # a word in this many names or fewer names one well enough alone
LEAST = 2.0     # the least a step must score (with a rare word)
SURE = 4.0      # a score that needs no rare word

TITLES = [
    "acolyte", "alchemist", "apprentice", "archivist", "armorer", "baron", "bartender", "banker", "blacksmith", "brother",
    "captain", "chief", "cleric", "clerk", "coachman", "commander", "constable", "corporal", "deputy", "dragoon", "druid",
    "elder", "enchanter", "father", "fisherman", "fletcher", "foreman", "gatekeeper", "general", "grand", "guard",
    "guardsman", "guildmaster", "harbormaster", "high", "initiate", "innkeeper", "jeweler", "keeper", "king", "knight",
    "lady", "lieutenant", "lord", "mage", "magistrate", "magus", "marshal", "master", "mayor", "merchant", "mistress",
    "monk", "mother", "necrologist", "necromancer", "officer", "overseer", "paladin", "priest", "priestess", "prince",
    "princess", "queen", "ranger", "rogue", "sage", "scholar", "scribe", "sentry", "sergeant", "shaman", "sheriff", "sir",
    "sister", "smith", "spiritmaster", "tailor", "templar", "trainer", "warden", "warrior", "watchman", "waysender",
    "weaponsmith", "wizard",
]
NAME_STOP = ["a", "an", "the", "of"]
STOP = [
    "a", "an", "the", "of", "to", "and", "in", "on", "at", "by", "for", "from", "with", "into", "onto", "up", "out", "back",
    "me", "my", "your", "you", "him", "her", "his", "them", "their", "it", "its", "this", "that", "these", "those", "some",
    "is", "are", "be", "who", "what", "where", "has", "have", "i", "he", "she", "we", "they", "go", "get", "see", "ask",
    "tell", "speak", "talk", "return", "bring", "take", "give", "show", "find", "kill", "slay", "collect", "gather",
    "deliver", "pick", "meet", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
]


def stem(w: str) -> str:
    n = len(w)
    if n > 4 and w.endswith("ies"):
        s = w[:-3] + "y"
    elif n > 4 and w.endswith("lves"):
        s = w[:-4] + "lf"
    elif n > 3 and w.endswith("es"):
        s = w[:-2]
    elif n > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        s = w[:-1]
    else:
        s = w
    if len(s) > 3 and s.endswith("e"):
        s = s[:-1]
    return s


def words(text: str) -> list[str]:
    out, cur = [], ""
    for c in text + " ":
        if c in "`‘’":
            c = "'"
        if c.isalnum() or (c == "'" and cur):
            cur += c.lower()
        elif cur:
            w = cur[:-2] if cur.endswith("'s") else cur
            w = w.rstrip("'")
            if w:
                out.append(stem(w))
            cur = ""
    return out


def one_apart(a: str, b: str) -> bool:
    s, l = (a, b) if len(a) <= len(b) else (b, a)
    if len(l) - len(s) > 1:
        return False
    p = 0
    while p < len(s) and s[p] == l[p]:
        p += 1
    return s[p + 1:] == l[p + 1:] if len(s) == len(l) else s[p:] == l[p + 1:]


def same(a: str, b: str) -> bool:
    return a == b or (len(a) >= 6 and len(b) >= 6 and a[0] == b[0] and one_apart(a, b))


_STOP = {stem(w) for w in STOP}


def content(ws: list[str]) -> list[str]:
    return [w for w in ws if w not in _STOP]


@dataclass
class Match:
    quest: int
    named: int    # the step the line names
    step: int     # the step to do now
    score: float


class Matcher:
    def __init__(self, book):
        self.book = book
        q, npcs = book.quests, book.npcs
        name_stop, titles = {stem(w) for w in NAME_STOP}, {stem(w) for w in TITLES}
        toks = [[w for w in words(n[0]) if w not in name_stop] for n in npcs]
        first: dict[str, int] = {}
        for t in toks:
            if len(t) > 1:
                first[t[0]] = first.get(t[0], 0) + 1
        is_title = lambda w: w in titles or first.get(w, 0) >= 2
        self.names = []
        for t in toks:
            key = [w for w in t if not is_title(w)]
            tit = [w for w in t if is_title(w)]
            if not key and tit:
                key = [tit.pop()]
            self.names.append((key, tit))
        self.items: dict[str, list[str]] = {}
        for quest in q:
            for s in quest["steps"]:
                for n in [i[0] for i in s["items"]] + list(s["gets"]):
                    self.items.setdefault(n, content(words(n)))

        def df(keys):
            out: dict[str, int] = {}
            for key in keys:
                for w in set(key):
                    out[w] = out.get(w, 0) + 1
            return out
        self.npc_df = df(k for k, _ in self.names)
        self.item_df = df(self.items.values())
        self.journals = [[(words(s["text"]), content(words(s["text"]))) for s in quest["steps"]] for quest in q]

    # --- scoring (questguide's Index)
    def name_score(self, n, line):
        key, tit = n
        has = lambda w: any(same(w, l) for l in line)
        hit = [w for w in key if has(w)]
        all_ = bool(hit) and len(hit) == len(key)
        own = any(self.npc_df.get(w, 0) <= 1 for w in hit)
        if not all_ and not own:
            return 0.0, False
        titles = sum(1 for w in tit if has(w))
        return 2.0 * len(hit) + 0.5 * titles, any(self.npc_df.get(w, 0) <= RARE for w in hit)

    def item_score(self, key, line):
        has = lambda w: any(same(w, l) for l in line)
        hit = sum(1 for w in key if has(w))
        head = bool(key) and has(key[-1])
        if hit > 0 and hit == len(key):
            v = 2.0 * hit
        elif hit >= 2 and head:
            v = float(hit)
        else:
            v = 0.0
        return v, v > 0 and (hit >= 2 or any(has(w) and self.item_df.get(w, 0) <= RARE for w in key))

    def step_score(self, qi, si, all_, line):
        s = self.book.quests[qi]["steps"][si]
        v, rare = 0.0, False
        if s["npc"] is not None:
            n = self.names[s["npc"]]
            a, b = self.name_score(n, line)
            v += a
            if s["kind"] in ("Kill", "Loot"):
                key, tit = n
                rare |= a > 0 and len(key) + len(tit) >= 2 and all(any(same(w, l) for l in line) for w in tit)
            else:
                rare |= b
        for name in [i[0] for i in s["items"]] + list(s["gets"]):
            a, b = self.item_score(self.items[name], line)
            v += a
            rare |= b
        jall, jcontent = self.journals[qi][si]
        if jall and jall == all_:
            return 20.0 + v
        if v >= SURE or (v >= LEAST and rare):
            return v + min(sum(0.25 for w in jcontent if w in line), 1.0)
        return 0.0

    def steps_of(self, qi, all_, named):
        q = self.book.quests[qi]
        scores = [self.step_score(qi, si, all_, named) for si in range(len(q["steps"]))]
        top = max(scores) if scores else 0.0
        if top <= 0:
            return None
        tied = [k for k, v in enumerate(scores) if v >= top - 1e-3]
        # without the inventory nothing is known to be behind: the first of the best-named steps
        first = tied[0]
        return first, first, top

    def match_line(self, text: str, cls: int, race: int, level: int, cities: list[int],
                   finished: set[str], after: list[int] = ()) -> Match | None:
        all_ = words(text)
        named = content(all_)
        best = None
        for qi, q in enumerate(self.book.quests):
            if "guide" in q:
                continue   # the other guides' steps are prose: only the class quests are matched
            if q["classes"] and cls not in q["classes"]:
                continue
            if q["races"] and race not in q["races"]:
                continue
            if level < q["levels"][0]:
                continue
            got = self.steps_of(qi, all_, named)
            if not got:
                continue
            nm, step, score = got
            rank = score
            if q["city"] in cities:
                rank += 1.0
            if qi in after:
                rank += 4.0
            if q["key"] in finished:
                rank -= 6.0
            elif q["requires"] and all(self.book.quests[r]["key"] in finished for r in q["requires"]):
                rank += 0.5
            rank += q["levels"][0] * 0.01
            if best is None or rank > best[0]:
                best = (rank, Match(qi, nm, step, score))
        return best[1] if best else None
