"""Build « TreeAI Lite » as a WeakAuras import string (WoW: Forever on GeForce NOW: no file access, WA only).

Format (WeakAuras/Transmission.lua, TableToString with forChat=true):
    "!WA:2!" .. LibDeflate:EncodeForPrint( LibDeflate:CompressDeflate( LibSerialize:Serialize(transmit) ) )
    transmit = { m = "d", d = <group data>, c = { <child data>, ... }, v = 1421, s = <WA version> }
Implemented here faithfully in Python: LibSerialize v1 writer + reader, raw DEFLATE (RFC 1951, zlib wbits=-15,
which LibDeflate:DecompressDeflate reads), LibDeflate's 6-bit print alphabet (a-z A-Z 0-9 ( )).

Usage: python tools/build_walite.py [--check]   → release/wow/TreeAI-Lite-WeakAura.txt
"""
from __future__ import annotations

import hashlib
import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "walite"
OUT = ROOT / "release" / "wow" / "TreeAI-Lite-WeakAura.txt"
PREFIX = "!WA:2!"
ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789()"
WA_VERSION = "5.22.0"      # base of Musca Auras (the WA port for Forever); value is informational
INTERNAL_VERSION = 82      # WA 5.19 level: newer WAs migrate up (83-90 don't touch our fields)
TICK_EVENT = "TREEAI_LITE_TICK"

# --------------------------------------------------------------------------------------------- LibSerialize
_EMB_STR, _EMB_TABLE, _EMB_ARRAY, _EMB_MIXED = 0, 1, 2, 3
_NIL, _N16P, _N16N, _N24P, _N24N, _N32P, _N32N, _N64P, _N64N, _FLOAT, _FSTR, _FSTRN = range(12)
_BT, _BF, _S8, _S16, _S24, _T8, _T16, _T24, _A8, _A16, _A24, _M8, _M16, _M24 = range(12, 26)
_SR8, _SR16, _SR24, _TR8, _TR16, _TR24 = range(26, 32)


def _req(v: int) -> int:
    for n, lim in ((1, 256), (2, 65536), (3, 16777216)):
        if v < lim:
            return n
    raise ValueError("Object limit exceeded")


def _req_num(v: int) -> int:
    for n, lim in ((1, 256), (2, 65536), (3, 16777216), (4, 4294967296)):
        if v < lim:
            return n
    return 7


def _int(n: int, size: int) -> bytes:
    return int(n).to_bytes(size, "big")


def _lua_g14(x: float) -> str:  # Lua 5.1 tostring(number) = "%.14g"
    return "%.14g" % x


class _Writer:
    def __init__(self):
        self.out = bytearray()
        self.srefs: dict[bytes, int] = {}
        self.trefs: dict[int, int] = {}

    def byte(self, b: int):
        self.out.append(b)

    def obj(self, v):
        if v is None:
            self.byte(8 * _NIL)
        elif isinstance(v, bool):
            self.byte(8 * (_BT if v else _BF))
        elif isinstance(v, (int, float)):
            self.num(v)
        elif isinstance(v, str):
            self.string(v.encode("utf-8"))
        elif isinstance(v, bytes):
            self.string(v)
        elif isinstance(v, (list, tuple, dict)):
            self.table(v)
        else:
            raise TypeError(type(v))

    def num(self, n):
        if isinstance(n, float) and n.is_integer() and abs(n) < 2 ** 53:
            n = int(n)
        if isinstance(n, float):
            sign, a = (8, -n) if n < 0 else (0, n)
            s = _lua_g14(a)
            if len(s) < 7 and float(s) == a and math.isfinite(a):
                self.byte(sign + 8 * _FSTR)
                self.byte(len(s))
                self.out += s.encode()
            else:
                self.byte(8 * _FLOAT)
                self.out += struct.pack(">d", n)
        elif -4096 < n < 4096:
            if 0 <= n < 128:
                self.byte(n * 2 + 1)
            else:
                sign = 8 if n < 0 else 0
                v = abs(n) * 16 + sign + 4
                self.byte(v % 256)
                self.byte(v // 256)
        else:
            sign = 8 if n < 0 else 0
            r = _req_num(abs(n))
            self.byte(sign + 8 * {2: _N16P, 3: _N24P, 4: _N32P, 7: _N64P}[r])
            self.out += _int(abs(n), r)

    def string(self, b: bytes):
        ref = self.srefs.get(b)
        if ref:
            r = _req(ref)
            self.byte(8 * (_SR8 + r - 1))
            self.out += _int(ref, r)
            return
        if len(b) < 16:
            self.byte(16 * len(b) + 4 * _EMB_STR + 2)
        else:
            r = _req(len(b))
            self.byte(8 * (_S8 + r - 1))
            self.out += _int(len(b), r)
        self.out += b
        if len(b) > 2:
            self.srefs[b] = len(self.srefs) + 1

    def table(self, t):
        ref = self.trefs.get(id(t))
        if ref:
            r = _req(ref)
            self.byte(8 * (_TR8 + r - 1))
            self.out += _int(ref, r)
            return
        self.trefs[id(t)] = len(self.trefs) + 1
        if isinstance(t, dict):
            arr, n = [], 1
            while n in t:
                arr.append(t[n])
                n += 1
            rest = [(k, v) for k, v in t.items() if not (isinstance(k, int) and not isinstance(k, bool) and 1 <= k < n)]
        else:
            arr, rest = list(t), []
        rest = [(k, v) for k, v in rest if v is not None]
        rest.sort(key=lambda kv: (type(kv[0]).__name__, str(kv[0])))
        na, nm = len(arr), len(rest)
        if nm == 0:
            if na < 16:
                self.byte(16 * na + 4 * _EMB_ARRAY + 2)
            else:
                r = _req(na)
                self.byte(8 * (_A8 + r - 1))
                self.out += _int(na, r)
        elif na:
            if nm < 5 and na < 5:
                self.byte(16 * ((nm - 1) * 4 + na - 1) + 4 * _EMB_MIXED + 2)
            else:
                r = max(_req(nm), _req(na))
                self.byte(8 * (_M8 + r - 1))
                self.out += _int(na, r) + _int(nm, r)
        else:
            if nm < 16:
                self.byte(16 * nm + 4 * _EMB_TABLE + 2)
            else:
                r = _req(nm)
                self.byte(8 * (_T8 + r - 1))
                self.out += _int(nm, r)
        for v in arr:
            self.obj(v)
        for k, v in rest:
            self.obj(k)
            self.obj(v)


def serialize(*values) -> bytes:
    w = _Writer()
    w.byte(1)  # SERIALIZATION_VERSION
    for v in values:
        w.obj(v)
    return bytes(w.out)


class _Reader:
    def __init__(self, data: bytes):
        self.d, self.i = data, 0
        self.srefs: list = []
        self.trefs: list = []

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.d):
            raise ValueError("truncated")
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def int(self, n: int) -> int:
        return int.from_bytes(self.take(n), "big")

    def string(self, n: int):
        b = self.take(n)
        try:
            s = b.decode("utf-8")
        except UnicodeDecodeError:
            s = b
        if n > 2:
            self.srefs.append(s)
        return s

    def table(self, na: int, nm: int):
        t: dict = {}
        self.trefs.append(t)
        for i in range(1, na + 1):
            t[i] = self.obj()
        for _ in range(nm):
            k = self.obj()
            t[k] = self.obj()
        return t

    def obj(self):
        v = self.int(1)
        if v % 2 == 1:
            return (v - 1) // 2
        if v % 4 == 2:
            typ = (v - 2) // 4
            cnt, typ = typ // 4, typ % 4
            if typ == _EMB_STR:
                return self.string(cnt)
            if typ == _EMB_TABLE:
                return self.table(0, cnt)
            if typ == _EMB_ARRAY:
                return self.table(cnt, 0)
            return self.table(cnt % 4 + 1, cnt // 4 + 1)
        if v % 8 == 4:
            packed = self.int(1) * 256 + v
            return -((packed - 12) // 16) if v % 16 == 12 else (packed - 4) // 16
        typ = v // 8
        if typ == _NIL:
            return None
        if _N16P <= typ <= _N64N:
            size = {0: 2, 1: 3, 2: 4, 3: 7}[(typ - 1) // 2]
            n = self.int(size)
            return -n if typ % 2 == 0 else n
        if typ == _FLOAT:
            return struct.unpack(">d", self.take(8))[0]
        if typ in (_FSTR, _FSTRN):
            f = float(self.take(self.int(1)).decode())
            return -f if typ == _FSTRN else f
        if typ in (_BT, _BF):
            return typ == _BT
        if _S8 <= typ <= _S24:
            return self.string(self.int(typ - _S8 + 1))
        if _T8 <= typ <= _T24:
            return self.table(0, self.int(typ - _T8 + 1))
        if _A8 <= typ <= _A24:
            return self.table(self.int(typ - _A8 + 1), 0)
        if _M8 <= typ <= _M24:
            r = typ - _M8 + 1
            na = self.int(r)
            return self.table(na, self.int(r))
        if _SR8 <= typ <= _SR24:
            return self.srefs[self.int(typ - _SR8 + 1) - 1]
        return self.trefs[self.int(typ - _TR8 + 1) - 1]


def deserialize(data: bytes) -> list:
    r = _Reader(data)
    if r.int(1) > 2:
        raise ValueError("Unknown serialization version!")
    out = []
    while r.i < len(data):
        out.append(r.obj())
    return out


# --------------------------------------------------------------------------------------------- LibDeflate
def compress_deflate(data: bytes) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15, 9)
    return c.compress(data) + c.flush()


def decompress_deflate(data: bytes) -> bytes:
    d = zlib.decompressobj(-15)
    out = d.decompress(data) + d.flush()
    if not d.eof:
        raise ValueError("incomplete deflate stream")
    return out


def encode_for_print(data: bytes) -> str:
    out, i, n = [], 0, len(data)
    while i + 3 <= n:
        c = data[i] | data[i + 1] << 8 | data[i + 2] << 16
        out += [ALPHABET[c & 63], ALPHABET[c >> 6 & 63], ALPHABET[c >> 12 & 63], ALPHABET[c >> 18 & 63]]
        i += 3
    cache, bits = 0, 0
    for b in data[i:]:
        cache |= b << bits
        bits += 8
    while bits > 0:
        out.append(ALPHABET[cache & 63])
        cache >>= 6
        bits -= 6
    return "".join(out)


def decode_for_print(s: str) -> bytes:
    s = s.strip()
    if len(s) == 1:
        raise ValueError("bad length")
    out, cache, bits = bytearray(), 0, 0
    for ch in s:
        v = ALPHABET.find(ch)
        if v < 0:
            raise ValueError("invalid character %r" % ch)
        cache |= v << bits
        bits += 6
        while bits >= 8:
            out.append(cache & 255)
            cache >>= 8
            bits -= 8
    return bytes(out)


def encode_wa(transmit: dict) -> str:
    return PREFIX + encode_for_print(compress_deflate(serialize(transmit)))


def decode_wa(s: str) -> dict:
    s = s.strip()
    if not s.startswith(PREFIX):
        raise ValueError("not a !WA:2! string")
    vals = deserialize(decompress_deflate(decode_for_print(s[len(PREFIX):])))
    if len(vals) != 1 or not isinstance(vals[0], dict):
        raise ValueError("unexpected payload")
    return vals[0]


# --------------------------------------------------------------------------------------------- WA tables
def uid(name: str) -> str:
    h = hashlib.sha256(("TreeAI-Lite:" + name).encode()).digest()
    return "".join(ALPHABET[b % 62] for b in h[:11])


def read_src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


GROUP_ID = "TreeAI Lite"


def text_aura(aid: str, events: str, trigger: str, init: str = "", load: dict | None = None,
              color=(1, 1, 1, 1)) -> dict:
    return {
        "id": aid, "uid": uid(aid), "parent": GROUP_ID, "regionType": "text", "internalVersion": INTERNAL_VERSION,
        "displayText": "%n", "customTextUpdate": "event", "justify": "LEFT", "font": "Friz Quadrata TT",
        "fontSize": 13, "outline": "OUTLINE", "color": list(color), "wordWrap": "WordWrap",
        "automaticWidth": "Fixed", "fixedWidth": 320, "anchorPoint": "CENTER", "selfPoint": "TOPLEFT",
        "anchorFrameType": "SCREEN", "xOffset": 0, "yOffset": 0, "frameStrata": 1,
        "shadowColor": [0, 0, 0, 1], "shadowXOffset": 1, "shadowYOffset": -1,
        "triggers": {
            1: {"trigger": {"type": "custom", "custom_type": "stateupdate", "check": "event", "events": events,
                            "custom": trigger, "debuffType": "HELPFUL", "unit": "player", "names": [],
                            "spellIds": [], "subeventPrefix": "SPELL", "subeventSuffix": "_CAST_START"},
                "untrigger": {}},
            "activeTriggerMode": -10, "disjunctive": "any",
        },
        "actions": {"init": {"do_custom": bool(init), "custom": init}, "start": {}, "finish": {}},
        "load": {"size": {"multi": {}}, "talent": {"multi": {}}, "spec": {"multi": {}}, "class": {"multi": {}},
                 **(load or {})},
        "animation": {k: {"type": "none", "duration_type": "seconds", "easeType": "none", "easeStrength": 3}
                      for k in ("start", "main", "finish")},
        "conditions": [], "config": {}, "authorOptions": [], "subRegions": [],
        "information": {"forceEvents": True}, "desc": "TreeAI Lite (lecture seule, rien dans le chat).",
    }


def build_transmit() -> dict:
    quests = text_aura(
        "TreeAI Lite - Quêtes",
        "QUEST_LOG_UPDATE QUEST_TURNED_IN QUEST_ACCEPTED ZONE_CHANGED_NEW_AREA PLAYER_ENTERING_WORLD "
        "PLAYER_REGEN_ENABLED " + TICK_EVENT,
        read_src("quest_trigger.lua"), read_src("quest_init.lua"))
    hunter = text_aura(
        "TreeAI Lite - Chasseur",
        "UNIT_INVENTORY_CHANGED:player BAG_UPDATE_DELAYED UNIT_HAPPINESS UNIT_PET PET_UI_UPDATE "
        "PLAYER_ENTERING_WORLD PLAYER_REGEN_ENABLED",
        read_src("hunter_trigger.lua"),
        load={"use_class": True, "class": {"single": "HUNTER", "multi": {"HUNTER": True}}})
    children = [quests, hunter]
    group = {
        "id": GROUP_ID, "uid": uid(GROUP_ID), "regionType": "dynamicgroup", "internalVersion": INTERNAL_VERSION,
        "controlledChildren": [c["id"] for c in children], "grow": "DOWN", "align": "LEFT", "space": 4,
        "anchorPoint": "CENTER", "selfPoint": "TOP", "anchorFrameType": "SCREEN", "xOffset": -420,
        "yOffset": 120, "frameStrata": 1, "sort": "none", "border": False, "backdropColor": [0, 0, 0, 0.4],
        "triggers": {1: {"trigger": {"type": "aura2", "unit": "player", "debuffType": "HELPFUL",
                                     "names": [], "spellIds": []}, "untrigger": {}}},
        "load": {"size": {"multi": {}}, "talent": {"multi": {}}, "spec": {"multi": {}}, "class": {"multi": {}}},
        "animation": {k: {"type": "none", "duration_type": "seconds", "easeType": "none", "easeStrength": 3}
                      for k in ("start", "main", "finish")},
        "conditions": [], "config": {}, "authorOptions": [], "subRegions": [], "information": {},
        "desc": "TreeAI Lite : quêtes à rendre + objectif le plus proche, munitions / familier (chasseur). "
                "Déplace le groupe dans /wa. Lecture seule.",
    }
    return {"m": "d", "d": group, "c": children, "v": 1421, "s": WA_VERSION}


def main(argv: list[str]) -> int:
    transmit = build_transmit()
    s = encode_wa(transmit)
    back = decode_wa(s)
    assert back["d"]["id"] == GROUP_ID and len(back["c"]) == 2, "round-trip failed"
    assert back["c"][1]["actions"]["init"]["custom"] == read_src("quest_init.lua"), "round-trip failed"
    if "--check" in argv:
        old = OUT.read_text(encoding="utf-8").strip() if OUT.exists() else ""
        if old != s:
            print("release/wow/TreeAI-Lite-WeakAura.txt is stale: run python tools/build_walite.py")
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(s + "\n", encoding="utf-8")
    print("%s: %d chars" % (OUT.relative_to(ROOT), len(s)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
