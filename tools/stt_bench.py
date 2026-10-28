"""Rerunnable French benchmark of the voice assistant's speech recognition (local only, zero API).

    python tools/stt_bench.py synth  --cache DIR                 # edge-tts clips (once, ~180 phrases)
    python tools/stt_bench.py run    --cache DIR --engine vosk --vosk-model DIR/vosk-model-small-fr-0.22
    python tools/stt_bench.py run    --cache DIR --engine whisper --model DIR/faster-whisper-small-xxx \\
                                     [--beam 3] [--fallback] [--no-prompt] [--highpass] [--norm] [--gate] \\
                                     [--fix-terms] [--threads 2] [--quick] [--wake-all]

Corpus (tools/stt_corpus.py): 80 questions / commands (70 « Hey coach, … », 10 push-to-talk) with marked
League names (Kai'Sa, Bel'Veth, K'Santé, Lame d'infini, Trinité…) and 100 ordinary sentences (traps: « le
coach », « cocher »…). Each phrase is synthesised by one of 10 French edge-tts voices (fr-FR / fr-CA /
fr-BE / fr-CH, men and women) at -15 % / 0 / +15 % speed, then degraded like real use: headset mic EQ,
Discord Opus round trip at 32-64 kbit/s (libopus via discord.py), synthetic game noise / music at 5-15 dB
SNR, clipping, very low level. Each question is heard in 2 conditions (1 with ``--quick``).

Metrics: WER of the question (wake phrase removed, accents / punctuation ignored), LoL-term accuracy (each
marked name found, apostrophes / spaces / accents ignored), wake rate on the 70 « Hey coach » phrases and
false wakes on the 100 ordinary ones (the app's rule: Vosk wake engine, then Whisper on « coach-like »
utterances; ``--wake-all``: Whisper on every utterance), latency = transcription time of one utterance
(the end-of-speech -> text delay adds the VAD's 1.0 s end silence). The clips stay in ``--cache`` (never
committed)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import ssl
import statistics
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import stt_corpus  # noqa: E402

from treeaicoach import stt_local  # noqa: E402
from treeaicoach import voice_assistant as va  # noqa: E402

VOICES = ("fr-FR-HenriNeural", "fr-FR-DeniseNeural", "fr-CA-AntoineNeural", "fr-CA-SylvieNeural",
          "fr-BE-GerardNeural", "fr-BE-CharlineNeural", "fr-CH-FabriceNeural", "fr-CH-ArianeNeural",
          "fr-FR-VivienneMultilingualNeural", "fr-CA-ThierryNeural")
RATES = ("-15%", "+0%", "+15%")
SR = 48000
CONDITIONS = ("clean", "discord48", "headset_music15", "discord32_game10", "headset_game5", "clip_discord64",
              "low_hiss")


# ------------------------------------------------------------------ corpus
def parse(phrase: str) -> tuple[str, str, list[str]]:
    """``(written, spoken by the TTS, League terms)`` of a corpus phrase with ``{Name|spoken}`` marks."""
    terms: list[str] = []

    def w(m: re.Match) -> str:
        terms.append(m.group(1).split("|")[0])
        return m.group(1).split("|")[0]

    def t(m: re.Match) -> str:
        p = m.group(1).split("|")
        return p[1] if len(p) > 1 else p[0]
    return re.sub(r"\{([^}]*)\}", w, phrase), re.sub(r"\{([^}]*)\}", t, phrase), terms


def items() -> list[dict]:
    out = []
    for i, p in enumerate(stt_corpus.QUESTIONS):
        written, spoken, terms = parse(p)
        out.append({"id": f"q{i:02d}", "text": written, "tts": spoken, "terms": terms,
                    "wake": written.lower().startswith("hey coach"), "kind": "q"})
    for i, p in enumerate(stt_corpus.TALK):
        out.append({"id": f"t{i:02d}", "text": p, "tts": p, "terms": [], "wake": False, "kind": "t"})
    for k, it in enumerate(out):
        it["voice"] = VOICES[k % len(VOICES)]
        it["rate"] = RATES[(k // len(VOICES) + k) % len(RATES)]
    return out


# ------------------------------------------------------------------ synthesis (edge-tts, once)
def synth(cache: Path) -> None:
    import edge_tts  # noqa: PLC0415
    import miniaudio  # noqa: PLC0415
    from edge_tts import communicate  # noqa: PLC0415

    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca:
        communicate._SSL_CTX = ssl.create_default_context(cafile=ca)   # behind a TLS proxy
    cache.mkdir(parents=True, exist_ok=True)

    async def one(it: dict) -> None:
        dst = cache / f"{it['id']}.npy"
        if dst.is_file():
            return
        mp3 = cache / f"{it['id']}.mp3"
        for attempt in range(3):
            try:
                await edge_tts.Communicate(it["tts"], it["voice"], rate=it["rate"]).save(str(mp3))
                break
            except Exception as exc:  # noqa: BLE001
                print("retry", it["id"], exc)
                await asyncio.sleep(2 + attempt)
        dec = miniaudio.decode(mp3.read_bytes(), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                               sample_rate=SR)
        np.save(dst, np.asarray(dec.samples, dtype=np.int16))
        mp3.unlink()

    async def main() -> None:
        its = items()
        for k in range(0, len(its), 8):
            await asyncio.gather(*(one(it) for it in its[k:k + 8]))
    asyncio.run(main())
    (cache / "corpus.json").write_text(json.dumps(items(), ensure_ascii=False, indent=1), encoding="utf-8")
    print("clips:", len(list(cache.glob("*.npy"))))


# ------------------------------------------------------------------ degradations (48 kHz float)
def _eq(x: np.ndarray, curve) -> np.ndarray:  # noqa: ANN001
    f = np.fft.rfft(x)
    fr = np.fft.rfftfreq(x.size, 1.0 / SR)
    return np.fft.irfft(f * curve(fr), n=x.size).astype(np.float32)


def headset(x: np.ndarray) -> np.ndarray:
    """Cheap headset boom mic: 150 Hz high-pass, 6.5 kHz low-pass, +4 dB presence bump at 2.5 kHz."""
    def curve(f: np.ndarray) -> np.ndarray:
        hp = 1.0 / np.sqrt(1.0 + (150.0 / np.maximum(f, 1.0)) ** 4)
        lp = 1.0 / np.sqrt(1.0 + (f / 6500.0) ** 8)
        bump = 1.0 + 0.6 * np.exp(-((f - 2500.0) / 900.0) ** 2)
        return hp * lp * bump
    return _eq(x, curve)


def opus_roundtrip(x: np.ndarray, kbps: int) -> np.ndarray:
    """Discord's path: libopus encode (VOIP, 20 ms frames, stereo 48 kHz) then decode."""
    from discord import opus  # noqa: PLC0415

    if not opus.is_loaded():
        for name in ("opus", "libopus.so.0", "libopus-0.dll"):
            try:
                opus.load_opus(name)
                break
            except Exception:  # noqa: BLE001
                continue
    enc = opus.Encoder(application="voip")
    enc.set_bitrate(kbps)
    try:
        enc.set_expected_packet_loss_percent(0.0)
    except Exception:  # noqa: BLE001
        pass
    dec = opus.Decoder()
    pcm = np.clip(x * 32767, -32768, 32767).astype("<i2")
    n = 960
    pcm = np.concatenate([pcm, np.zeros((-pcm.size) % n, "<i2")])
    out = []
    for k in range(0, pcm.size, n):
        fr = np.repeat(pcm[k:k + n], 2).tobytes()                   # stereo
        out.append(np.frombuffer(dec.decode(enc.encode(fr, n), fec=False), "<i2").reshape(-1, 2)[:, 0])
    return (np.concatenate(out).astype(np.float32) / 32768.0)[: x.size]


def game_noise(n: int, kind: str, seed: int) -> np.ndarray:
    """Synthetic in-game background: ``music`` (chord pads changing every 0.6 s + pink bed) or ``game``
    (pink noise + spell / hit bursts + a low drone)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    w = rng.normal(size=n)
    f = np.fft.rfft(w)
    fr = np.fft.rfftfreq(n, 1.0 / SR)
    pink = np.fft.irfft(f / np.sqrt(np.maximum(fr, 20.0)), n=n)
    pink /= np.std(pink) + 1e-9
    if kind == "music":
        y = 0.4 * pink
        seg = int(0.6 * SR)
        for s0 in range(0, n, seg):
            root = rng.choice([110.0, 130.8, 146.8, 164.8, 196.0])
            idx = slice(s0, min(n, s0 + seg))
            for m in (1.0, 1.26, 1.5, 2.0):
                y[idx] += 0.5 * np.sin(2 * np.pi * root * m * t[idx] + rng.uniform(0, 6))
        y[:] += 0.3 * np.sin(2 * np.pi * 2.0 * t) * pink
    else:
        y = pink + 0.5 * np.sin(2 * np.pi * 55.0 * t)
        for _ in range(int(n / SR * 4)):
            s0 = int(rng.integers(0, max(1, n - SR // 5)))
            ln = int(rng.integers(SR // 50, SR // 6))
            burst = rng.normal(size=ln) * np.exp(-np.arange(ln) / (ln / 4)) * rng.uniform(2, 6)
            y[s0:s0 + ln] += burst[: max(0, min(ln, n - s0))]
    return (y / (np.std(y) + 1e-9)).astype(np.float32)


def _speech_rms(x: np.ndarray) -> float:
    fr = x[: x.size // 480 * 480].reshape(-1, 480)
    r = np.sqrt(np.mean(fr * fr, axis=1))
    return float(np.mean(np.sort(r)[int(len(r) * 0.6):])) if len(r) else 1e-3


def mix(x: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    g = _speech_rms(x) / (10 ** (snr_db / 20.0)) / (np.std(noise) + 1e-9)
    return x + g * noise


def degrade(x: np.ndarray, cond: str, seed: int) -> np.ndarray:
    x = x.astype(np.float32)
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.5
    x = np.concatenate([np.zeros(int(0.3 * SR), np.float32), x, np.zeros(int(0.8 * SR), np.float32)])
    if cond == "clean":
        return x
    if cond == "discord48":
        return opus_roundtrip(headset(x), 48000)
    if cond == "headset_music15":
        return mix(headset(x), game_noise(x.size, "music", seed), 15)
    if cond == "discord32_game10":
        return opus_roundtrip(mix(headset(x), game_noise(x.size, "game", seed), 10), 32000)
    if cond == "headset_game5":
        return mix(headset(x), game_noise(x.size, "game", seed) * 0.5 + game_noise(x.size, "music", seed + 1)
                   * 0.5, 5)
    if cond == "clip_discord64":
        return opus_roundtrip(np.clip(headset(x) * 6.0, -1.0, 1.0) * 0.9, 64000)
    if cond == "low_hiss":
        y = headset(x) * (10 ** (-38 / 20) / max(_speech_rms(headset(x)), 1e-6))
        return y + np.random.default_rng(seed).normal(scale=10 ** (-62 / 20), size=y.size).astype(np.float32)
    raise ValueError(cond)


def to16k(x: np.ndarray) -> bytes:
    pcm = np.clip(x * 32767, -32768, 32767).astype("<i2").tobytes()
    return va.resample_to_16k(pcm, SR, 1)


def utterances(cache: Path, quick: bool) -> list[dict]:
    """Every (phrase, condition) of the run with its 16 kHz PCM (cached on disk)."""
    its = json.loads((cache / "corpus.json").read_text(encoding="utf-8"))
    out = []
    for k, it in enumerate(its):
        conds = [CONDITIONS[k % len(CONDITIONS)]]
        if it["kind"] == "q" and not quick:
            conds.append(CONDITIONS[(k + 3) % len(CONDITIONS)])
        for c in conds:
            f = cache / "deg" / f"{it['id']}_{c}.pcm"
            if not f.is_file():
                f.parent.mkdir(exist_ok=True)
                x = np.load(cache / f"{it['id']}.npy").astype(np.float32) / 32768.0
                f.write_bytes(to16k(degrade(x, c, seed=k)))
            out.append(dict(it, cond=c, pcm=f.read_bytes()))
    return out


# ------------------------------------------------------------------ metrics
_NUM = {"0": "zero", "1": "un", "2": "deux", "3": "trois", "4": "quatre", "5": "cinq", "6": "six", "7": "sept",
        "8": "huit", "9": "neuf", "10": "dix"}


def norm_words(text: str) -> list[str]:
    s = unicodedata.normalize("NFD", str(text).lower().replace("œ", "oe"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[’'\-]", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return [_NUM.get(w, w) for w in s.split()]


def compact(text: str) -> str:
    return "".join(norm_words(text))


def wer(ref: str, hyp: str) -> tuple[int, int]:
    r, h = norm_words(ref), norm_words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)], len(r)


def question_of(text: str) -> str:
    ok, rest = va.whisper_wake(text)
    return (rest if ok else va.strip_wake(text)).strip()


# ------------------------------------------------------------------ engines
class VoskEngine:
    def __init__(self, model_dir: Path) -> None:
        self.a = va.VoiceAssistant(model_path=model_dir)

    def wake_and_text(self, pcm: bytes) -> tuple[bool, str]:
        eng = va.WakeEngine(self.a._make_rec)
        eng.ready()
        n = va.BLOCK_FRAMES * 2
        woke = False
        for k in range(0, len(pcm), n):
            if eng.feed(pcm[k:k + n]):
                woke = True
                break
        woke = woke or eng.flush()
        rec = self.a._make_rec(None)
        parts = []
        for k in range(0, len(pcm), n):
            if rec.accept(pcm[k:k + n]):
                parts.append(rec.result())
        rec.accept(va.comfort_noise(1.0))
        parts.append(rec.final())
        return woke, " ".join(p for p in parts if p)


def game_names(it: dict, k: int, champs: list[str]) -> list[str]:
    """The 10 champions of the simulated game: the ones the phrase names first, then random others."""
    rng = np.random.default_rng(k)
    names = [t for t in it["terms"] if t in champs]
    while len(names) < 10:
        c = champs[int(rng.integers(0, len(champs)))]
        if c not in names:
            names.append(c)
    return names[:10]


def run(args: argparse.Namespace) -> dict:
    cache = Path(args.cache)
    utts = utterances(cache, args.quick)
    vosk_cache = cache / "vosk_results.json"
    vres: dict = json.loads(vosk_cache.read_text(encoding="utf-8")) if vosk_cache.is_file() else {}
    vosk = VoskEngine(Path(args.vosk_model)) if args.vosk_model else None
    champs = [c["name_fr"] for c in json.loads(
        (ROOT / "treeaicoach/assets/icons/champions/index.json").read_text(encoding="utf-8"))["champions"]]
    w = None
    if args.engine == "whisper":
        w = stt_local.WhisperSTT(model_dir=Path(args.model), threads=args.threads)
        w.opts.update(beam=args.beam, fallback=args.fallback, prompt=not args.no_prompt, highpass=args.highpass,
                      norm=args.norm, gate=args.gate, fix_terms=args.fix_terms)
        if not w.load():
            raise SystemExit(w.status)
        w.transcribe(utts[0]["pcm"])                     # warm-up
    rows = []
    for k, u in enumerate(utts):
        key = f"{u['id']}_{u['cond']}"
        if vosk is not None and key not in vres:
            vres[key] = vosk.wake_and_text(u["pcm"])
        v_wake, v_text = vres.get(key, (False, ""))
        if w is not None:
            w.set_names(game_names(u, k, champs))
            t0 = time.perf_counter()
            text = w.transcribe(u["pcm"])
            ms = (time.perf_counter() - t0) * 1000
            wake_w = va.whisper_wake(text)[0]
            app_wake = bool(v_wake) or (va.wakeish(v_text) and wake_w)
            wake = wake_w or bool(v_wake) if args.wake_all else app_wake
        else:
            text, ms, wake = v_text, 0.0, bool(v_wake)
        e, n = wer(question_of(u["text"]), question_of(text))
        hit = [compact(t) in compact(text) for t in u["terms"]]
        rows.append({"id": key, "kind": u["kind"], "cond": u["cond"], "ref": u["text"], "hyp": text, "err": e,
                     "n": n, "terms": len(hit), "hits": sum(hit), "wake_ref": u["wake"], "wake": wake, "ms": ms,
                     "audio_s": len(u["pcm"]) / 32000})
        if args.verbose:
            print(f"{key:24s} {e}/{n} {'W' if wake else '-'} {ms:5.0f}ms | {text}")
    if vosk is not None:
        vosk_cache.write_text(json.dumps(vres, ensure_ascii=False), encoding="utf-8")
    return summarize(rows, args)


def summarize(rows: list[dict], args: argparse.Namespace) -> dict:
    q = [r for r in rows if r["kind"] == "q"]
    t = [r for r in rows if r["kind"] == "t"]
    ms = [r["ms"] for r in rows if r["ms"]]
    res = {
        "config": vars(args) | {"model": Path(args.model).name if args.model else ""},
        "n_utts": len(rows),
        "wer_q": round(100 * sum(r["err"] for r in q) / max(1, sum(r["n"] for r in q)), 1),
        "wer_talk": round(100 * sum(r["err"] for r in t) / max(1, sum(r["n"] for r in t)), 1),
        "term_acc": round(100 * sum(r["hits"] for r in q) / max(1, sum(r["terms"] for r in q)), 1),
        "wake_rate": round(100 * sum(r["wake"] for r in q if r["wake_ref"]) / max(1, sum(r["wake_ref"] for r in q)), 1),
        "false_wakes": sum(r["wake"] for r in t),
        "n_talk": len(t),
        "lat_ms_mean": round(statistics.mean(ms)) if ms else 0,
        "lat_ms_p90": round(sorted(ms)[int(len(ms) * 0.9)]) if ms else 0,
        "by_cond": {},
    }
    for c in CONDITIONS:
        cq = [r for r in q if r["cond"] == c]
        if cq:
            res["by_cond"][c] = {"wer": round(100 * sum(r["err"] for r in cq) / max(1, sum(r["n"] for r in cq)), 1),
                                 "terms": round(100 * sum(r["hits"] for r in cq) / max(1, sum(r["terms"] for r in cq)), 1)}
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": res, "rows": rows}, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("synth", "run"))
    ap.add_argument("--cache", required=True)
    ap.add_argument("--engine", choices=("vosk", "whisper"), default="whisper")
    ap.add_argument("--model", default="")
    ap.add_argument("--vosk-model", default="")
    ap.add_argument("--beam", type=int, default=3)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--fallback", action="store_true")
    ap.add_argument("--no-prompt", action="store_true")
    ap.add_argument("--highpass", action="store_true")
    ap.add_argument("--norm", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--fix-terms", action="store_true")
    ap.add_argument("--wake-all", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.cmd == "synth":
        synth(Path(args.cache))
        return
    res = run(args)
    by = res.pop("by_cond")
    cfg = res.pop("config")
    print(json.dumps({k: cfg[k] for k in ("engine", "model", "beam", "fallback", "no_prompt", "highpass", "norm",
                                          "gate", "fix_terms", "threads", "quick")}))
    print(json.dumps(res))
    print(json.dumps(by))


if __name__ == "__main__":
    main()
