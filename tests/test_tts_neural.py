"""tts_neural: rate parsing, voice list, phrase list for a game, cache layout (no network)."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from types import SimpleNamespace as NS

from treeaicoach import tts_neural


def _wav() -> bytes:
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x10" * 4800)
    return bio.getvalue()


def test_rate_percent_accepts_strings_and_legacy_numbers() -> None:
    assert tts_neural.rate_percent("+15%") == 15
    assert tts_neural.rate_percent("-10 %") == -10
    assert tts_neural.rate_percent("+900%") == 100
    assert tts_neural.rate_percent("vite") == 15
    assert tts_neural.rate_percent(2) == 15          # legacy SAPI scale
    assert tts_neural.rate_string("20") == "+20%"


def test_list_neural_voices() -> None:
    voices = tts_neural.list_neural_voices()
    assert voices[0] == ("fr-FR-DeniseNeural", "Denise (femme, France)")
    assert all(tts_neural.clean_voice_id(v) == v for v, _ in voices)
    assert tts_neural.clean_voice_id("../../evil") == tts_neural.DEFAULT_NEURAL_VOICE


def test_build_phrase_list_for_a_game() -> None:
    game = NS(enemies=[NS(champion_name=n) for n in ("Lee Sin", "Ahri", "Darius", "Jinx", "Thresh", "X")],
              allies=[NS(champion_name="Garen"), {"champion_name": "Lux"}])
    phrases = tts_neural.build_phrase_list(game)
    assert phrases[0] == "Gank ! Lee Sin, recule !"
    assert len(phrases) == len(set(phrases)) and len(phrases) <= tts_neural.MAX_PREFETCH
    assert any("Thresh" in p for p in phrases) and not any(" X" in p for p in phrases)
    assert "Dragon dans une minute." in phrases
    assert tts_neural.build_phrase_list(None) == tts_neural.build_phrase_list(NS())
    assert tts_neural.build_phrase_list(object()) == list(dict.fromkeys(tts_neural.static_phrases()))


def test_cache_path_layout_and_rate_in_key(tmp_path: Path) -> None:
    t = tts_neural.NeuralTTS("fr-FR-DeniseNeural", "+15%", cache_root=tmp_path,
                             _synth=lambda text, v, r: _wav())
    p = t.path_for("Bonjour.")
    assert p.parent == tmp_path / "fr-FR-DeniseNeural" and len(p.stem) == 40 and p.suffix == ".wav"
    got = t.get("Bonjour.")
    assert got == p and p.read_bytes()[:4] == b"RIFF"
    t.set_params("fr-FR-DeniseNeural", "+30%")
    assert t.path_for("Bonjour.") != p and t.cached("Bonjour.") is None
