"""Record the bot Discord's fixed sentences (discord_audio.RECORDED) with the natural voice (Edge,
Denise, the app's default rate) into ``treeaicoach/assets/sounds/voice/<clip_name>.wav``: 24 kHz mono
16-bit, loudness-normalised (discord_audio.TARGET_RMS_DB). They are the bot's last resort when no
voice engine works on the player's PC (offline, Edge refused, no Windows voice).

    python tools/make_bot_voice_clips.py [--voice fr-FR-DeniseNeural] [--rate +15%]

Needs edge-tts >= 7.3.1 (Microsoft refuses the 7.2.x handshake with HTTP 403 since 2026) and miniaudio.
Prints each clip's duration and the level of the raw voice (the 2.9.0 bot played it unscaled).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from treeaicoach import discord_audio as da  # noqa: E402
from treeaicoach import tts_neural  # noqa: E402
from treeaicoach.tts_lexicon import speakable  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default=tts_neural.DEFAULT_NEURAL_VOICE)
    ap.add_argument("--rate", default=tts_neural.DEFAULT_NEURAL_RATE)
    ap.add_argument("--out", default=str(ROOT / "treeaicoach" / "assets" / "sounds" / "voice"))
    args = ap.parse_args()
    tts_neural._patch_ssl()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    import numpy as np

    for text in da.RECORDED:
        mp3 = asyncio.run(tts_neural._edge_mp3(speakable(text), args.voice, tts_neural.rate_percent(args.rate)))
        wav = tts_neural.mp3_to_wav(mp3)
        parsed = da.parse_wav(wav)
        if parsed is None:
            print(f"FAILED: {text}")
            return 1
        x, rate = parsed
        raw_db = da.voiced_rms_db(x, rate)
        y = da.soft_limit(x[:, 0] * 10.0 ** ((da.TARGET_RMS_DB - raw_db) / 20.0))
        pcm = np.clip(np.round(y * 32767.0), -32768, 32767).astype("<i2").tobytes()
        bio = io.BytesIO()
        with wave.open(bio, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        path = out / f"{da.clip_name(text)}.wav"
        path.write_bytes(bio.getvalue())
        print(f"{text:28s} {len(y) / rate:5.2f} s  raw voice {raw_db:6.1f} dBFS RMS  -> {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
