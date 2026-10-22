import json
import struct

from treeaicoach import tts_eleven as te
from treeaicoach.config import _as_neural_voice, _INVALID

VID = "abcDEF1234567890xyZ1"
PCM = struct.pack("<h", 3000) * 6000


def _tts(tmp_path, http, key="k"):
    return te.ElevenTTS(te.PREFIX + VID, "+15%", api_key=key, cache_root=tmp_path, _http=http)


def test_synth_caches_and_request_shape(tmp_path):
    calls = []

    def http(m, url, h, body):
        calls.append((m, url, h, json.loads(body)))
        return 200, PCM

    t = _tts(tmp_path, http)
    p = t.get("Bot gank", 2)
    assert p and p.read_bytes()[:4] == b"RIFF" and VID in str(p)
    assert t.get("Bot gank", 2) == p and len(calls) == 1            # second one from the cache
    m, url, h, body = calls[0]
    assert VID in url and "pcm_24000" in url and h["xi-api-key"] == "k"
    assert body["language_code"] == "fr" and abs(body["voice_settings"]["speed"] - 1.15) < 1e-9


def test_prefetch_fills_cache(tmp_path):
    t = _tts(tmp_path, lambda *a: (200, PCM))
    t.prefetch(["Un", "Deux"])
    import time
    for _ in range(100):
        if t.prefetch_stats:
            break
        time.sleep(0.05)
    assert t.cached("Un") and t.cached("Deux")


def test_errors_fall_back(tmp_path):
    t = _tts(tmp_path, lambda *a: (402, b""))
    assert t.get("Salut", 2) is None and not t.online()               # backend then uses the Windows voice
    t2 = _tts(tmp_path, lambda *a: (_ for _ in ()).throw(OSError("net")))
    assert t2.get("Salut", 2) is None
    assert _tts(tmp_path, lambda *a: (200, PCM), key="").get("Salut", 2) is None


def test_list_african_voices():
    body = json.dumps({"voices": [{"voice_id": VID, "name": "Amadou - Bright", "gender": "male"},
                                  {"voice_id": "bad!", "name": "x"}]}).encode()
    seen = []
    out = te.list_african_voices("k", _http=lambda m, u, h, b: (seen.append(u), (200, body))[1])
    assert out == [(te.PREFIX + VID, "Amadou (homme, accent africain)")]
    assert "language=fr" in seen[0] and "accent=african" in seen[0]
    assert te.list_african_voices("k", _http=lambda *a: (401, b"")) == []
    assert te.list_african_voices("", _http=lambda *a: (200, body)) == []


def test_config_accepts_eleven_voice():
    assert _as_neural_voice("eleven:" + VID) == "eleven:" + VID
    assert _as_neural_voice("eleven:x") is _INVALID
