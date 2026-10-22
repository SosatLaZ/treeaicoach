"""Optional ElevenLabs voice (online, PAID key needed for library voices): African-accent French.

Microsoft Edge has no West / Central African French voice. ElevenLabs' Voice Library does
(language ``fr`` + accent ``african``), but the public pages show no voice ids: the ids are
read at run time from ``GET /v1/shared-voices`` with the user's own key (:func:`list_african_voices`),
nothing is hard-coded. The free ElevenLabs plan cannot use Voice Library voices through the API
(HTTP 402): a paid plan (Starter, a few $/month) is needed. Output is raw PCM 24 kHz, wrapped in a WAV.

:class:`ElevenTTS` is a :class:`tts_neural.NeuralTTS` (same WAV cache, same prefetch of the coach's
fixed phrases, same fallback: on any error ``get`` returns None and the caller says the line
with the Windows voice). The configured voice is ``"eleven:<voice id>"`` (``cfg.neural_voice``).
Nothing here raises to the caller.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from treeaicoach import tts_neural

log = logging.getLogger(__name__)

PREFIX = "eleven:"
API = "https://api.elevenlabs.io"
MODEL = "eleven_flash_v2_5"          # low latency (~75 ms), French supported
HTTP_TIMEOUT_S = 8.0
BAD_KEY_RETRY_S = 600.0              # 401 / 402: do not hammer the service
VOICE_ID_RE = re.compile(r"[A-Za-z0-9]{10,40}")

#: http(method, url, headers, body) -> (status, bytes); tests inject a fake.
Http = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def is_eleven(voice: Any) -> bool:
    return isinstance(voice, str) and voice.startswith(PREFIX) and bool(VOICE_ID_RE.fullmatch(voice[len(PREFIX):]))


def voice_id(voice: str) -> str:
    return voice[len(PREFIX):] if voice.startswith(PREFIX) else voice


def _urllib_http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:    # noqa: S310 (https constant)
            return int(r.status), r.read()
    except urllib.error.HTTPError as e:
        return int(e.code), b""


def list_african_voices(api_key: str, *, _http: Http | None = None, limit: int = 12) -> list[tuple[str, str]]:
    """``[("eleven:<id>", label), ...]``: French voices of the Voice Library tagged African accent."""
    if not api_key.strip():
        return []
    url = f"{API}/v1/shared-voices?language=fr&accent=african&page_size={int(limit)}&sort=usage_character_count_7d"
    try:
        status, data = (_http or _urllib_http)("GET", url, {"xi-api-key": api_key.strip()}, None)
        if status != 200:
            return []
        out = []
        for v in json.loads(data).get("voices", []):
            vid = str(v.get("voice_id", ""))
            if not VOICE_ID_RE.fullmatch(vid):
                continue
            who = {"male": "homme", "female": "femme"}.get(str(v.get("gender", "")).lower(), "")
            label = f"{str(v.get('name', vid)).split(' - ')[0][:24]} ({who + ', ' if who else ''}accent africain)"
            out.append((PREFIX + vid, label))
        return out
    except Exception as exc:
        log.info("Liste des voix ElevenLabs indisponible : %s", exc)
        return []


class ElevenTTS(tts_neural.NeuralTTS):
    """ElevenLabs voice with the Edge voice's WAV cache. ``voice`` is stored as ``eleven-<id>`` (path-safe)."""

    def __init__(self, voice: str, rate: int = 2, *, api_key: str = "", cache_root: Path | None = None,
                 _http: Http | None = None, **kw: Any) -> None:
        super().__init__(tts_neural.DEFAULT_NEURAL_VOICE, rate, cache_root=cache_root, **kw)
        self._key = api_key.strip()
        self._http = _http or _urllib_http
        self.voice = self._norm_voice(voice)

    @staticmethod
    def _norm_voice(voice: str) -> str:
        v = voice_id(voice.replace("eleven-", PREFIX, 1))
        return "eleven-" + v if VOICE_ID_RE.fullmatch(v) else "eleven-invalid"

    def set_params(self, voice: str, rate: int) -> None:
        with self._lock:
            self.voice = self._norm_voice(voice)
            self.rate_pct = tts_neural.rate_percent(rate)

    def _produce(self, text: str, voice: str, rate_pct: int, dest: Path) -> Path | None:
        try:
            vid = voice.replace("eleven-", "", 1)
            if not self._key or not VOICE_ID_RE.fullmatch(vid):
                raise RuntimeError("clé ou voix ElevenLabs manquante")
            body = json.dumps({"text": text, "model_id": MODEL, "language_code": "fr",
                               "voice_settings": {"speed": max(0.7, min(1.2, 1 + rate_pct / 100))}}).encode()
            status, data = self._http(
                "POST", f"{API}/v1/text-to-speech/{vid}?output_format=pcm_24000",
                {"xi-api-key": self._key, "Content-Type": "application/json", "Accept": "audio/pcm"}, body)
            if status in (401, 402, 403):
                with self._lock:
                    self.failures += 1
                    self._offline_until = float(self._clock()) + BAD_KEY_RETRY_S
                log.warning("ElevenLabs refuse (HTTP %d : clé invalide ou voix de la bibliothèque réservée "
                            "aux offres payantes) : voix Microsoft / Windows utilisée.", status)
                return None
            if status != 200 or len(data) < 2000:
                raise RuntimeError(f"HTTP {status}")
            wav = tts_neural.pcm_to_wav(tts_neural._trim_silence(data))
            if not tts_neural._write_atomic(dest, wav):
                raise RuntimeError("écriture impossible")
            with self._lock:
                self.failures = 0
                self._offline_until = 0.0
            return dest
        except Exception as exc:
            with self._lock:
                self.failures += 1
                self._offline_until = float(self._clock()) + tts_neural.OFFLINE_RETRY_S
            log.warning("Voix ElevenLabs indisponible (%s) : voix Windows utilisée.", exc)
            return None
