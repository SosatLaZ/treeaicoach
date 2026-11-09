"""TreeAI Lite (WeakAuras import string for WoW: Forever on GeForce NOW): encoder round-trip, LibSerialize /
LibDeflate format vectors, Lua 5.1 syntax of the custom code, mock run, release file up to date."""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("build_walite", ROOT / "tools" / "build_walite.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
LUA = shutil.which("lua5.1")
LUAC = shutil.which("luac5.1")


def test_libserialize_vectors():
    # values worked out by hand from LibSerialize.lua's writer (version byte 1 first)
    assert B.serialize(5) == b"\x01\x0b"                       # 7-bit embedded int
    assert B.serialize(300) == bytes([1, (300 * 16 + 4) % 256, (300 * 16 + 4) // 256])
    assert B.serialize(-1) == bytes([1, 16 + 8 + 4, 0])
    assert B.serialize("abc") == b"\x01\x32abc"               # embedded string, count 3
    assert B.serialize(True, False, None) == bytes([1, 96, 104, 0])
    assert B.serialize([1, 2]) == bytes([1, 2 * 16 + 4 * 2 + 2, 3, 5])
    assert B.serialize({"k": 1}) == bytes([1, 16 + 4 + 2, 0x12]) + b"k" + b"\x03"
    assert B.serialize(["abc", "abc"]) == bytes([1, 0x2a, 0x32]) + b"abc" + bytes([26 * 8, 1])   # string ref
    assert B.serialize(0.5) == bytes([1, 80, 3]) + b"0.5"     # NUM_FLOATSTR


def test_libserialize_roundtrip():
    v = {"a": [1, -5000, 70000, 2 ** 40, 0.1, -2.5, "x" * 300], "b": {1: True, "z": None, "y": False}, 3: "é"}
    out = B.deserialize(B.serialize(v))[0]
    assert out["a"] == {1: 1, 2: -5000, 3: 70000, 4: 2 ** 40, 5: 0.1, 6: -2.5, 7: "x" * 300}
    assert out["b"] == {1: True, "y": False} and out[3] == "é"


def test_print_encoding_vectors():
    assert B.encode_for_print(b"") == ""
    assert B.encode_for_print(b"\x00\x00\x00") == "aaaa"
    assert B.encode_for_print(b"\xff\xff\xff") == "))))"
    assert B.encode_for_print(b"\x01") == "ba"     # 8 bits -> 2 chars, LSB first
    for data in (b"a", b"ab", b"abc", bytes(range(256))):
        assert B.decode_for_print(B.encode_for_print(data)) == data


def test_release_string_roundtrip_and_shape():
    s = (ROOT / "release" / "wow" / "TreeAI-Lite-WeakAura.txt").read_text(encoding="utf-8").strip()
    assert s.startswith("!WA:2!") and set(s[6:]) <= set(B.ALPHABET)
    t = B.decode_wa(s)
    assert t["m"] == "d" and t["v"] == 1421
    g = t["d"]
    assert g["regionType"] == "dynamicgroup" and g["id"] == "TreeAI Lite"
    kids = [t["c"][i] for i in sorted(t["c"])]
    assert [k["id"] for k in kids] == [g["controlledChildren"][i] for i in sorted(g["controlledChildren"])]
    for k in kids:
        trig = k["triggers"][1]["trigger"]
        assert trig["type"] == "custom" and trig["custom_type"] == "stateupdate" and trig["check"] == "event"
        assert k["information"] is not None   # WA migration 89 writes into it
        assert "pcall" not in trig["custom"]  # blocked in WA's sandbox
    assert kids[1]["load"]["class"]["single"] == "HUNTER"
    assert B.main(["--check"]) == 0, "release string stale: run python tools/build_walite.py"


@pytest.mark.skipif(not LUAC, reason="luac5.1 missing")
@pytest.mark.parametrize("name", ["quest_init.lua", "quest_trigger.lua", "hunter_trigger.lua"])
def test_custom_code_is_lua51(tmp_path, name):
    code = (ROOT / "walite" / name).read_text(encoding="utf-8")
    if "trigger" in name:          # WA loads triggers as "return " .. code
        code = "return " + code
    for banned in ("pcall", "loadstring", "setfenv", "getfenv", "SendChatMessage", "print(", "RunScript"):
        assert banned not in code, banned
    p = tmp_path / name
    p.write_text(code, encoding="utf-8")
    subprocess.run([LUAC, "-p", str(p)], check=True)


@pytest.mark.skipif(not LUA, reason="lua5.1 missing")
def test_mock_run():
    r = subprocess.run([LUA, str(ROOT / "tests" / "walite" / "mock_run.lua"), str(ROOT)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "MOCK OK" in r.stdout, r.stdout + r.stderr
