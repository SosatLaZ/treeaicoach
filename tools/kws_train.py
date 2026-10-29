"""Train the « Hey coach » keyword spotter head (treeaicoach/kws.py) on synthetic French speech only.

    python tools/kws_train.py synth --work DIR        # edge-tts mp3 (13 French voices, rate / pitch), once
    python tools/kws_train.py feats --work DIR --features DIR_WITH_OWW_ONNX [--jobs 3]
    python tools/kws_train.py train --work DIR --out treeaicoach/assets/voice/kws_hey_coach.onnx

Positives: « Hey coach » / « Hé coach » / « Et coach » / « Eh coach » / « Hey Tree » (+ « , » and a question
in the same breath, or alone), every fr-FR / fr-CA / fr-BE / fr-CH edge-tts voice, rate -20..+20 %, pitch
-8..+8 Hz; the end of the wake word comes from edge-tts' word boundaries. Negatives: French sentences that
are NOT in the wake bench (tools/wake_bench.py CHATTER, tools/stt_corpus.py) — near-wake traps written here
(« coach » / « coque » / « écoute » / « et co… » / « hé + someone else »), the app's own French text read as
paragraphs, laughs, game noise / music without speech.

Augmentations (each clip several times): room reverb (RT60 0.1-0.7 s), headset EQ, level -45..-6 dBFS,
game noise / music / babble at 0-15 dB SNR, clipping, Discord Opus round trip 24-64 kbit/s with Discord's
spurt trimming and comfort noise. The noise generators are NOT the bench's (different recipes / seeds).

Features = :meth:`treeaicoach.kws.KwsModel.clip_embeddings` (the exact streaming numbers). Labels per 80 ms
frame: 1 from the end of the wake word to +0.32 s; 0 before its last 0.25 s and once it left the 1.28 s
window; ignored in between. Head: 16x96 -> MLP 64 -> 1 (sigmoid), ONNX ~0.4 MB."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import ssl
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VOICES = ("fr-FR-DeniseNeural", "fr-FR-EloiseNeural", "fr-FR-HenriNeural", "fr-FR-RemyMultilingualNeural",
          "fr-FR-VivienneMultilingualNeural", "fr-CA-SylvieNeural", "fr-CA-AntoineNeural", "fr-CA-JeanNeural",
          "fr-CA-ThierryNeural", "fr-BE-CharlineNeural", "fr-BE-GerardNeural", "fr-CH-ArianeNeural",
          "fr-CH-FabriceNeural")
WAKE_FORMS = ("Hey coach", "Hé coach", "Et coach", "Eh coach", "Hey, coach", "Hé, coach", "Hey Tree", "Hé Tree")
#: training-only questions (the bench's are in stt_corpus.QUESTIONS / wake_bench.WAKES)
FOLLOW = (
    "", "", "", "!", "?", "t'es là ?", "tu m'entends ?", "j'ai une question.", "on fait quoi ?",
    "je monte quoi après mes bottes ?", "c'est quand le prochain dragon ?", "il a flash le Darius ?",
    "on peut prendre la tour ?", "je dois aider mon jungler ?", "qui je focus dans le combat ?",
    "le Nashor est vivant ?", "je recule ou j'attaque ?", "combien de temps avant le drake ?",
    "quel sort je monte en premier ?", "qu'est-ce que je build contre des tanks ?", "je pose une balise où ?",
    "mon support est où ?", "ils ont combien de golds d'avance ?", "j'achète des potions ?",
    "la jungle ennemie est où ?", "je vais top ou bot ?", "on a l'avantage ?", "rappelle-moi le timer du baron.",
    "je dois rentrer à la base ?", "c'est bon pour dive ?", "explique-moi la phase de lane.",
    "dis-moi quoi faire.", "on attaque le héraut ?", "il me reste combien de temps ?", "Sion il est où ?",
    "Garen il bat Teemo ?", "aide-moi pour la team fight.", "je prends l'exhaust ?", "c'est quoi mon objectif ?",
)
#: near-wake traps (not in the bench): must never wake
TRAPS = (
    "Hé Kevin, tu viens ?", "Hey Paul, t'es où ?", "Et Coco, il fait quoi ?", "Hé cocotte !", "Eh coquin !",
    "Et cochon, ça va ?", "Hé copain, aide-moi.", "Hey cousin, bien joué.", "Et Corentin, il joue quoi ?",
    "Hé Thomas, regarde la map.", "Hey toi, t'as vu ?", "Et quoi, c'est tout ?", "Hé ho, y'a quelqu'un ?",
    "Et coucou les gars !", "Hé, coucou !", "Et co-op, ça marche ?", "Hey, cool ton skin.", "Hé, cool !",
    "Et puis le coach est parti.", "On a viré le coach.", "Le coach adverse est malin.", "Mon coach me saoule.",
    "J'ai parlé au coach hier.", "Sans coach on y arrive.", "Le coach de Karmine est là.", "C'était un coach pro.",
    "Coach Tom est en live.", "Son coach l'a recadré.", "Ce coach-là est bizarre.", "Le coaching en ligne coûte cher.",
    "La coque de mon téléphone est cassée.", "Une coque de noix.", "Il a la coqueluche.", "Coquille vide.",
    "La coquille Saint-Jacques.", "Écoute bien ce que je dis.", "Écoute, j'en sais rien du tout.",
    "Écoutez, on se calme.", "Tu m'écoutes ou pas ?", "Personne n'écoute jamais.", "Écoute-le, il a raison.",
    "J'écoute de la musique.", "Écoute-moi deux secondes.", "Et écoute ça !", "Hé, écoute-moi !",
    "Et cocorico !", "Le coq chante.", "Un coq au vin.", "On a fait coucou au baron.", "C'est cocasse.",
    "Il a un look de cocker.", "Une couche de plus.", "Il se couche tôt.", "Couché, le chien !", "Accroche-toi.",
    "Approche, approche.", "Il est très costaud.", "Trois cocktails.", "Trop cool ce coup.", "Tric trac.",
    "Hé, très bien joué !", "Et très vite !", "Hey, très drôle.", "Trilogie complète.", "Tribunal du jeu.",
    "Et tricher, c'est mal.", "Hé, tristesse.", "Le triangle de la jungle.", "Une triple kill !", "Tri-force.",
    "Et toc !", "Et hop !", "Hey, stop !", "Hé, coach-moi ça.", "Ça s'est joué à un poil.", "Les coachs parlent.",
    "J'aime pas ce coach.", "Ce coach en carton.", "Un coach Challenger.", "Le coach ça sert à quoi ?",
    "C'est le coach qui l'a dit.", "Quoi, le coach ?", "Le coach, le coach, toujours le coach.",
    "Hé, cocher la case.", "Et choper le kill.", "Et choquer tout le monde.", "Hé Toto !", "Hey Timothée !",
    "Et Kaisa ?", "Eh Garen, tu fais quoi ?", "Hé Swain, ulti !", "Et Teemo, il est où ?", "Hé Trundle !",
    "Hey Tristana, focus !", "Et Thresh, il a hook ?", "Hé Tryndamere arrive !", "Et Taric ?", "Hé Corki !",
)
LAUGHS = ("Hahahaha !", "Hihihi.", "Ahahah, mais non !", "Pfff, ahah.", "Mouahaha !", "Hahaha, trop bien.",
          "Ohhh non !", "Ouaaais !", "Rhooo.", "Bah alors ?", "Mmmh.", "Héhéhé.", "Ha !", "Oups.", "Aïe !")
#: openers that sound like « Hé coach » / start like it (+ a FOLLOW question): the hardest negatives
OPENERS = ("Écoute", "Écoutez", "Et écoute", "Hé, écoute", "Eh, écoute", "Écoute-moi", "Écris", "Et cocher",
           "Hé, coucou", "Et coco", "Hé cocotte", "Eh coquin", "Et cochon", "Hé copain", "Eh, coince", "Et coche",
           "Le coach", "Et le coach", "Mon coach", "Hé le coach", "Ton coach", "Ce coach", "Les coachs",
           "Coach", "Et toi", "Hé toi", "Et quoi", "Hé ho", "Euh", "Et euh", "Eh bah", "Hé bah", "Et alors",
           "Attends", "Regarde", "Et tiens", "Hé Kevin", "Eh Thomas", "Et Corki", "Hé Teemo", "Et très bien",
           "Hé, trop fort", "Et trois", "Hé, tric", "Et Trist", "Hey Paul", "Hey toi", "Hey, attends",
           "Eh cou", "Et co", "Hé, comment", "Et comment", "Et combien", "Hé, combien", "Ok, coach")
#: v4 hard negatives (prefix « h »): phonetic neighbours of the bench's last false wakes (« Eh, cochon ! »,
#: « Écoute, c'est quoi… », « On split push. ») — never those exact sentences (the bench stays unseen):
#: « Eh / Hé / Et » + a « co- / -oche / -ouch » word, « écoute, c'est… », « … push / … pouche »
HARD = (
    "Eh, sale cochon !", "Hé, gros cochon !", "Eh, cochonne !", "Et cochonnerie !", "Eh, petit cochon.",
    "Ah, le cochon !", "Oh, cochon va !", "Eh, cocon.", "Hé, coquine !", "Eh, coco !", "Hé, coq !", "Eh, cocorico !",
    "Eh, coussin.", "Hé, couchant.", "Eh, coche-la.", "Et coche ça.", "Hé, cogne !", "Eh, costaud !", "Eh, cool !",
    "Hé, coquillage.", "Eh, cocasse.", "Hé, cocker !", "Eh, coucou !", "Et coucher là.", "Eh, couché !",
    "Hé, coche !", "Eh, côte !", "Eh, colle !", "Hé, cops !", "Eh, corde !", "Et cocotier.", "Eh, Kochi !",
    "Eh, touche !", "Hé, la mouche !", "Eh, louche !", "Et la poche ?", "Hé, proche !", "Eh, moche !",
    "Hé, la cloche.", "Eh, pioche !", "Et la roche.", "Hé, approche !", "Eh, bouge !", "Et douche-toi.",
    "Écoute, c'est quand le baron ?", "Écoute, c'est qui le jungler ?", "Écoute, c'est quoi ce build ?",
    "Écoute, c'est où le héraut ?", "Écoute, c'est combien les golds ?", "Écoute, c'est bon pour dive ?",
    "Écoute, c'est quoi la rune ?", "Écoute, c'est quand le drake ?", "Écoute, c'est quoi son sort ?",
    "Écoute, t'as vu le timer ?", "Écoute, le timer du baron ?", "Écoute, quoi le dragon ?",
    "Écoute, c'est chaud.", "Écoute, c'est mort.", "Écoute, ça passe.", "Écoutez, c'est quoi le plan ?",
    "On split.", "On split bot.", "Je split push top.", "Il split push en bas.", "On fait du split push ?",
    "Split push, les gars.", "On push.", "On push bot.", "Push top.", "On fast push.", "On freeze.",
    "On rush.", "On splitte.", "Ils split push.", "Split et push.", "Je push et je back.", "On pousse.",
    "Pouche.", "On stack.", "On crush.", "On flash.", "Smash.", "On switch.", "On touche.", "Ils couchent.",
)
#: v5 (prefix « k »): « Et le coach, … » / « Et ton coach, … » (an article between « et » and « coach »: not the
#: wake) — v4's only false wake on the bench was such a sentence (not this list's)
HARD_LE = (
    "Et le coach, il en pense quoi ?", "Et le coach, il est où ?", "Et le coach, il a dit ça ?", "Et le coach alors ?",
    "Et le coach, tu l'as vu ?", "Hé le coach, il parle trop.", "Et le coach, quoi ?", "Et le coach, il raconte quoi ?",
    "Et ton coach, il dit rien ?", "Et mon coach me dit de farmer.", "Et leur coach, il est fort ?",
    "Et ce coach, il sert à quoi ?", "Et la coach, elle en pense quoi ?", "Et les coachs, ils pensent quoi ?",
    "Et le coaching, ça donne quoi ?", "Et le coach adverse ?", "Et le coach de l'équipe ?", "Eh le coach, il veut quoi ?",
    "Et le coach, il veut qu'on push ?", "Et le coach, il a raison.", "Et le coach, il te dit quoi de faire ?",
    "Et un coach, ça coûte combien ?", "Et notre coach, il est parti ?", "Et le coach du top ?",
    "Et le coach, il a vu le drake ?", "Hé, le coach, il pense quoi ?", "Et le coach, on l'écoute ?",
    "Et le coach, il dit rien ?", "Et le coach, il dort ?", "Et ton coach, il pense quoi du build ?",
)
_WAKE_RE = re.compile(r"\b(hey|hé|he|eh|et|ey)\W*(coach|tree)\b", re.I)


def app_paragraphs(max_par: int = 140) -> list[str]:
    """French sentences of the app itself (string literals of treeaicoach/*.py), grouped 5 by 5."""
    import ast  # noqa: PLC0415

    sents: list[str] = []
    for f in sorted((ROOT / "treeaicoach").glob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = " ".join(node.value.split())
                if (25 <= len(s) <= 160 and re.search(r"[éèàùçê]", s) and not re.search(r"[{}%<>_/\\|=]", s)
                        and not _WAKE_RE.search(s) and len(s.split()) >= 5):
                    sents.append(s if s[-1] in ".!?…" else s + ".")
    sents = sorted(set(sents))
    random.Random(3).shuffle(sents)
    return [" ".join(sents[k:k + 5]) for k in range(0, min(len(sents), 5 * max_par), 5)]


def items() -> list[dict]:
    rnd = random.Random(7)
    out: list[dict] = []
    k = 0
    for v in VOICES:
        for form in WAKE_FORMS:
            for _ in range(8):
                f = rnd.choice(FOLLOW)
                text = form + ("" if not f else (" " + f if f in "!?" else ", " + f))
                if not f:
                    text += rnd.choice((".", " !", ""))
                out.append({"id": f"p{k:05d}", "text": text, "voice": v, "kind": "pos",
                            "rate": f"{rnd.randint(-20, 20):+d}%", "pitch": f"{rnd.randint(-8, 8):+d}Hz"})
                k += 1
    for v in VOICES:
        for t in TRAPS + LAUGHS:
            if rnd.random() < 0.75:
                out.append({"id": f"t{k:05d}", "text": t, "voice": v, "kind": "neg",
                            "rate": f"{rnd.randint(-20, 20):+d}%", "pitch": f"{rnd.randint(-8, 8):+d}Hz"})
                k += 1
    for i, par in enumerate(app_paragraphs()):
        for v in rnd.sample(VOICES, 3):
            out.append({"id": f"a{k:05d}", "text": par, "voice": v, "kind": "neg",
                        "rate": f"{rnd.randint(-15, 25):+d}%", "pitch": f"{rnd.randint(-6, 6):+d}Hz"})
            k += 1
    for v in VOICES:                      # (after the paragraphs: their ids stay)
        for o in rnd.sample(OPENERS, 30):
            f = rnd.choice([x for x in FOLLOW if len(x) > 2])
            out.append({"id": f"u{k:05d}", "text": f"{o}, {f}", "voice": v, "kind": "neg",
                        "rate": f"{rnd.randint(-20, 20):+d}%", "pitch": f"{rnd.randint(-8, 8):+d}Hz"})
            k += 1
    ecoute = ("Écoute", "Écoute", "Écoute bien", "Écoutez", "Écoute-moi", "Et écoute", "Hé, écoute", "Bon, écoute",
              "Écoute mec", "Écoute frère", "Mais écoute", "Ah écoute", "Écoutez les gars", "Écoute écoute")
    for v in VOICES:                          # « écoute » IS « hé coach » for a weak model: many of them
        for _ in range(26):
            f = rnd.choice([x for x in FOLLOW if len(x) > 2] + [x for x in TRAPS if not x.startswith("É")])
            out.append({"id": f"v{k:05d}", "text": f"{rnd.choice(ecoute)}, {f[0].lower() + f[1:]}", "voice": v,
                        "kind": "neg", "rate": f"{rnd.randint(-20, 20):+d}%", "pitch": f"{rnd.randint(-8, 8):+d}Hz"})
            k += 1
    for v in VOICES:                          # (last: the ids above stay)
        for t in HARD:
            out.append({"id": f"h{k:05d}", "text": t, "voice": v, "kind": "neg",
                        "rate": f"{rnd.randint(-20, 20):+d}%", "pitch": f"{rnd.randint(-8, 8):+d}Hz"})
            k += 1
    for v in VOICES:
        for t in HARD_LE:
            out.append({"id": f"k{k:05d}", "text": t, "voice": v, "kind": "neg",
                        "rate": f"{rnd.randint(-20, 20):+d}%", "pitch": f"{rnd.randint(-8, 8):+d}Hz"})
            k += 1
    return out


# ------------------------------------------------------------------ synth
def synth(work: Path, conc: int = 10) -> None:
    import edge_tts  # noqa: PLC0415
    from edge_tts import communicate  # noqa: PLC0415

    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca:
        communicate._SSL_CTX = ssl.create_default_context(cafile=ca)
    d = work / "tts"
    d.mkdir(parents=True, exist_ok=True)
    its = items()

    async def one(it: dict) -> None:
        mp3, meta = d / f"{it['id']}.mp3", d / f"{it['id']}.json"
        if meta.is_file():
            return
        for attempt in range(4):
            try:
                c = edge_tts.Communicate(it["text"], it["voice"], rate=it["rate"], pitch=it["pitch"],
                                         boundary="WordBoundary")
                audio, words = bytearray(), []
                async for ch in c.stream():
                    if ch["type"] == "audio":
                        audio += ch["data"]
                    elif ch["type"] == "WordBoundary":
                        words.append((ch["text"], ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7))
                mp3.write_bytes(bytes(audio))
                meta.write_text(json.dumps(dict(it, words=words), ensure_ascii=False), encoding="utf-8")
                return
            except Exception as exc:  # noqa: BLE001
                print("retry", it["id"], exc, flush=True)
                await asyncio.sleep(2 + 2 * attempt)

    async def main() -> None:
        sem = asyncio.Semaphore(conc)

        async def lim(it: dict) -> None:
            async with sem:
                await one(it)
        await asyncio.gather(*(lim(it) for it in its))
    asyncio.run(main())
    print("tts clips:", len(list(d.glob("*.json"))), "of", len(its))


# ------------------------------------------------------------------ augment (16 kHz float, -1..1)
SR = 16000


def _decode(mp3: Path) -> np.ndarray:
    import miniaudio  # noqa: PLC0415

    dec = miniaudio.decode(mp3.read_bytes(), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                           sample_rate=SR)
    return np.asarray(dec.samples, dtype=np.float32) / 32768.0


def _fft_eq(x: np.ndarray, curve) -> np.ndarray:  # noqa: ANN001
    f = np.fft.rfft(x)
    return np.fft.irfft(f * curve(np.fft.rfftfreq(x.size, 1.0 / SR)), n=x.size).astype(np.float32)


def _headset(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    hp, lp = rng.uniform(80, 300), rng.uniform(3200, 7600)
    fc, g = rng.uniform(1500, 3500), rng.uniform(-0.3, 0.9)
    return _fft_eq(x, lambda f: (1 / np.sqrt(1 + (hp / np.maximum(f, 1)) ** 4)) / np.sqrt(1 + (f / lp) ** 8)
                   * (1 + g * np.exp(-((f - fc) / 800) ** 2)))


def _reverb(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    rt = rng.uniform(0.1, 0.7)
    n = int(rt * SR)
    t = np.arange(n) / SR
    rir = rng.normal(size=n) * np.exp(-6.9 * t / rt)
    rir[0] = 1.0 / rng.uniform(0.3, 1.0)
    rir /= np.sqrt(np.sum(rir ** 2))
    y = np.fft.irfft(np.fft.rfft(x, x.size + n) * np.fft.rfft(rir, x.size + n))[: x.size]
    return y.astype(np.float32)


def _noise(n: int, rng: np.random.Generator, babble: list[np.ndarray]) -> np.ndarray:
    kind = rng.choice(["brown", "music", "clicks", "babble", "hum"])
    w = rng.normal(size=n)
    if kind == "brown":
        y = np.cumsum(w)
        y -= np.convolve(y, np.ones(400) / 400, "same")
    elif kind == "music":
        t = np.arange(n) / SR
        y = 0.3 * w
        step = int(rng.uniform(0.25, 0.8) * SR)
        for s in range(0, n, step):
            f0 = rng.choice([98.0, 123.5, 155.6, 174.6, 220.0, 261.6])
            sl = slice(s, min(n, s + step))
            for h, a in ((1, 1.0), (2, 0.5), (3, 0.3), (1.498, 0.6)):
                y[sl] += a * np.sin(2 * np.pi * f0 * h * t[sl]) * np.exp(-(t[sl] - t[s]) * rng.uniform(1, 6))
        y[: n] += 0.8 * (np.sin(2 * np.pi * rng.uniform(1.5, 2.5) * t) > 0.97) * rng.normal(size=n)   # kick/hat
    elif kind == "clicks":
        y = 0.2 * w
        for _ in range(int(n / SR * rng.uniform(3, 12))):
            s = int(rng.integers(0, max(1, n - 1600)))
            ln = int(rng.integers(80, 1600))
            y[s:s + ln] += rng.normal(size=ln) * np.exp(-np.arange(ln) / (ln / 5)) * rng.uniform(2, 8)
            if rng.random() < 0.3:   # a game « ding »
                tt = np.arange(ln) / SR
                y[s:s + ln] += 3 * np.sin(2 * np.pi * rng.uniform(600, 2000) * tt) * np.exp(-tt * 20)
    elif kind == "babble" and babble:
        y = np.zeros(n)
        for _ in range(3):
            b = babble[int(rng.integers(len(babble)))]
            o = int(rng.integers(0, max(1, b.size - n))) if b.size > n else 0
            seg = b[o:o + n]
            y[: seg.size] += seg / (np.std(seg) + 1e-6)
    else:
        t = np.arange(n) / SR
        y = np.sin(2 * np.pi * rng.choice([50.0, 60.0, 100.0]) * t) + 0.2 * w
    return (y / (np.std(y) + 1e-9)).astype(np.float32)


def _speech_rms(x: np.ndarray) -> float:
    fr = x[: x.size // 320 * 320].reshape(-1, 320)
    r = np.sqrt(np.mean(fr * fr, axis=1))
    return float(np.mean(np.sort(r)[int(len(r) * 0.6):])) if len(r) else 1e-4


def _opus(x: np.ndarray, kbps: int) -> np.ndarray:
    sys.path.insert(0, str(ROOT / "tools"))
    import stt_bench as sb  # noqa: PLC0415

    up = np.interp(np.arange(x.size * 3) / 3.0, np.arange(x.size), x).astype(np.float32)
    y = sb.opus_roundtrip(up, kbps)
    from treeaicoach import voice_assistant as va  # noqa: PLC0415

    pcm = np.clip(y * 32767, -32768, 32767).astype("<i2").tobytes()
    return np.frombuffer(va.resample_to_16k(pcm, 48000, 1), "<i2").astype(np.float32) / 32768.0


def augment(x: np.ndarray, rng: np.random.Generator, babble: list[np.ndarray]) -> tuple[np.ndarray, int]:
    """One degraded copy, with silence in front; returns (audio, samples added in front)."""
    path = rng.choice(["pc", "discord", "clean"], p=[0.5, 0.4, 0.1])
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.5
    if path != "clean":
        if rng.random() < 0.45:
            x = _reverb(x, rng)
        x = _headset(x, rng)
    pre = int(rng.uniform(0.3, 1.6) * SR)
    post = int(rng.uniform(0.4, 1.2) * SR)
    x = np.concatenate([np.zeros(pre, np.float32), x, np.zeros(post, np.float32)])
    lvl = 10 ** (rng.uniform(-42, -8) / 20)
    x = x * (lvl / max(_speech_rms(x), 1e-6))
    if path != "clean" and rng.random() < 0.75:
        nz = _noise(x.size, rng, babble)
        snr = rng.uniform(0, 15) if rng.random() < 0.7 else rng.uniform(15, 30)
        x = x + nz * (_speech_rms(x) / 10 ** (snr / 20))
    if rng.random() < 0.12:
        x = np.clip(x * rng.uniform(2, 8), -0.9, 0.9)
    if path == "discord":
        x = _opus(np.clip(x, -1, 1), int(rng.choice([24000, 32000, 48000, 64000])))
        if rng.random() < 0.6:          # Discord's voice activity: the spurt only, comfort noise around
            fr = x[: x.size // 320 * 320].reshape(-1, 320)
            r = np.sqrt(np.mean(fr * fr, axis=1))
            on = np.where(r > max(0.006, 0.05 * r.max()))[0]
            if on.size:
                s, e = max(0, on[0] - 5) * 320, min(len(r), on[-1] + 10) * 320
                cn = rng.normal(0, 10 / 32768, x.size).astype(np.float32)
                cn[s:e] = x[s:e]
                x = cn
    else:
        x = x + rng.normal(0, 10 ** (rng.uniform(-80, -60) / 20), x.size).astype(np.float32)
    return np.clip(x, -1, 1).astype(np.float32), pre


def _wake_end(meta: dict) -> float | None:
    for w, _s, e in meta.get("words", []):
        if re.match(r"(?i)^(coach|tree)\W*$", w):
            return e
    return None


def labels_for(n: int, wake_end: float | None) -> np.ndarray:
    """Per 80 ms frame: 1 / 0 / -1 (ignored). Frame i covers audio up to (i+1)*0.08 s."""
    lab = np.zeros(n, np.int8)
    if wake_end is None:
        return lab
    t = (np.arange(n) + 1) * 0.08
    lab[(t > wake_end - 0.25) & (t < wake_end + 1.3)] = -1
    lab[(t >= wake_end) & (t <= wake_end + 0.32)] = 1
    return lab


def feats(work: Path, features: Path, jobs: int, job: int, reps_pos: int, reps_neg: int, prefix: str = "pta") -> None:
    from treeaicoach import kws  # noqa: PLC0415

    m = kws.KwsModel(folder=features, head=Path("/nonexistent"))
    allm = sorted((work / "tts").glob("*.json"))
    babble = [_decode(p.with_suffix(".mp3")) for p in allm if p.name.startswith("a")][:30]
    metas = [p for p in allm if p.name[0] in prefix]
    E, L, G = [], [], []        # embeddings, labels, group (clip id number: train / val split by clip)
    for k, mp in enumerate(metas):
        if k % jobs != job:
            continue
        meta = json.loads(mp.read_text(encoding="utf-8"))
        x = _decode(mp.with_suffix(".mp3"))
        pos = meta["kind"] == "pos"
        we = _wake_end(meta) if pos else None
        if pos and we is None:
            continue
        rng = np.random.default_rng(k * 7919 + 13)
        for r in range(reps_pos if pos else reps_neg):
            y, pre = augment(x, rng, babble)
            e = m.clip_embeddings(np.clip(y * 32767, -32768, 32767).astype(np.int16))
            E.append(e.astype(np.float16))
            L.append(labels_for(len(e), None if we is None else we + pre / SR))
            G.append(np.full(len(e), k, np.int32))
        if k % 200 == job:
            print(job, k, len(metas), flush=True)
    # noise / music without speech
    for k in range(job, 240 if "p" in prefix else 0, jobs):
        rng = np.random.default_rng(100000 + k)
        n = int(rng.uniform(4, 10) * SR)
        y = _noise(n, rng, babble) * 10 ** (rng.uniform(-45, -12) / 20)
        if rng.random() < 0.4:
            y = _opus(np.clip(y, -1, 1), 32000)
        e = m.clip_embeddings(np.clip(y * 32767, -32768, 32767).astype(np.int16))
        E.append(e.astype(np.float16))
        L.append(np.zeros(len(e), np.int8))
        G.append(np.full(len(e), 900000 + k, np.int32))
    np.savez(work / f"feats_{prefix}{job}.npz", e=np.concatenate(E), l=np.concatenate(L), g=np.concatenate(G),
             starts=np.cumsum([0] + [len(x) for x in E])[:-1])
    print("job", job, "frames", sum(len(x) for x in E))


# ------------------------------------------------------------------ train
def windows(e: np.ndarray, starts: np.ndarray, quiet: np.ndarray) -> np.ndarray:
    """Index matrix (n, 16) into ``e`` padded with the quiet embedding (index -1 -> last row)."""
    n = len(e)
    idx = np.arange(n)[:, None] + np.arange(-15, 1)[None, :]
    clip_start = np.zeros(n, np.int64)
    clip_start[starts] = starts
    clip_start = np.maximum.accumulate(clip_start)
    idx[idx < clip_start[:, None]] = n            # before the clip: quiet (row n)
    return idx


def train(work: Path, out: Path, features: Path, epochs: int = 30) -> None:
    import torch  # noqa: PLC0415
    from torch import nn  # noqa: PLC0415

    from treeaicoach import kws  # noqa: PLC0415

    quiet = kws.KwsModel(folder=features, head=Path("/nonexistent")).quiet.astype(np.float32)
    parts = [np.load(f) for f in sorted(work.glob("feats_*.npz"))]
    E, L, G, I = [], [], [], []
    off = 0
    for p in parts:
        e = p["e"].astype(np.float32)
        E.append(e)
        L.append(p["l"])
        G.append(p["g"])
        I.append(windows(e, p["starts"], quiet) + off)
        I[-1][I[-1] == off + len(e)] = -1
        off += len(e)
    e = np.concatenate(E + [quiet[None]])
    lab = np.concatenate(L)
    grp = np.concatenate(G)
    idx = np.concatenate(I)
    idx[idx < 0] = len(e) - 1
    keep = lab >= 0
    val = (grp % 10) == 3
    tr = np.where(keep & ~val)[0]
    va_ = np.where(keep & val)[0]
    print("frames", len(lab), "pos", int((lab == 1).sum()), "neg", int((lab == 0).sum()), "train", len(tr), "val", len(va_))
    torch.manual_seed(0)
    et = torch.from_numpy(e)
    yt = torch.from_numpy((lab == 1).astype(np.float32))
    it = torch.from_numpy(idx)

    class Head(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.net = nn.Sequential(nn.Flatten(), nn.Linear(16 * 96, 96), nn.LayerNorm(96), nn.ReLU(),
                                     nn.Dropout(0.2), nn.Linear(96, 64), nn.ReLU(), nn.Linear(64, 1))

        def forward(self, x):  # noqa: ANN001, ANN202
            return torch.sigmoid(self.net(x))

    net = Head()
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-3)
    pos_tr = tr[lab[tr] == 1]
    neg_tr = tr[lab[tr] == 0]
    bs = 1024
    steps = max(1, min(600, len(tr) // bs))
    torch.set_num_threads(2)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=2e-3, total_steps=epochs * steps)
    rng = np.random.default_rng(0)
    hard = neg_tr
    best, best_state = None, None

    def scores(rows: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            return torch.cat([net(et[it[rows[k:k + 16384]]]).squeeze(1)
                              for k in range(0, len(rows), 16384)]).numpy()
    for ep in range(epochs):
        if ep >= 3 and ep % 2 == 1:              # hard-negative mining: the negatives the head likes most
            net.eval()
            sub = rng.choice(neg_tr, min(len(neg_tr), 200_000), replace=False)
            sn = scores(sub)
            hard = sub[np.argsort(-sn)[: max(2048, len(sub) // 25)]]
        net.train()
        tot = 0.0
        for _ in range(steps):
            nh = (bs - bs // 4) // 3 if ep >= 3 else 0
            b = np.concatenate([rng.choice(pos_tr, bs // 4), rng.choice(neg_tr, bs - bs // 4 - nh),
                                rng.choice(hard, nh)])
            x = et[it[b]]
            x = x + 0.05 * torch.randn_like(x) * (ep < epochs - 3)
            p = net(x).squeeze(1)
            y = yt[b]
            w = torch.where(y > 0, torch.tensor(1.0), torch.tensor(3.0))     # false wakes cost more
            loss = (nn.functional.binary_cross_entropy(p.clamp(1e-6, 1 - 1e-6), y, reduction="none") * w).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss)
        net.eval()
        with torch.no_grad():
            pv = torch.cat([net(et[it[va_[k:k + 8192]]]).squeeze(1) for k in range(0, len(va_), 8192)]).numpy()
        yv = lab[va_]
        msg = []
        for th in (0.3, 0.5, 0.7, 0.9):
            fp = int(((pv >= th) & (yv == 0)).sum())
            tp_clips = len({grp[va_[i]] for i in np.where((pv >= th) & (yv == 1))[0]})
            pos_clips = len({grp[va_[i]] for i in np.where(yv == 1)[0]})
            msg.append(f"th{th}: fp_frames {fp}, clip recall {tp_clips}/{pos_clips}")
        print(f"ep {ep} loss {tot / steps:.4f} | " + " | ".join(msg), flush=True)
        pos_clips = {grp[va_[i]] for i in np.where(yv == 1)[0]}
        for th in (0.95, 0.9, 0.8, 0.7, 0.5):        # best epoch: fewest false frames at >= 98 % clip recall
            ok = len({grp[va_[i]] for i in np.where((pv >= th) & (yv == 1))[0]}) >= 0.98 * len(pos_clips)
            if ok:
                key = int(((pv >= th) & (yv == 0)).sum())
                if ep >= 3 and (best is None or key < best[0]):
                    import copy  # noqa: PLC0415

                    best, best_state = (key, ep, th), copy.deepcopy(net.state_dict())
                break
    if best_state is not None:
        net.load_state_dict(best_state)
        print("best epoch", best)
    net.eval()
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(net, torch.zeros(1, 16, 96), str(out), input_names=["feats"], output_names=["p"],
                      dynamic_axes={"feats": {0: "n"}, "p": {0: "n"}}, opset_version=13, dynamo=False)
    print("saved", out, out.stat().st_size, "bytes")


def latency(work: Path, features: Path, head: Path, n: int = 150) -> None:
    """Wake -> earcon delay on positive clips (augmented like training; group % 10 == 3 = validation
    clips): time from the end of « coach » / « Tree » (edge-tts word boundary) to the wake, audio arriving
    in 0.25 s mic blocks (PC) or 20 ms packets (Discord), + the measured compute of the frame."""
    import time  # noqa: PLC0415

    from treeaicoach import kws  # noqa: PLC0415

    m = kws.KwsModel(folder=features, head=head)
    metas = [p for p in sorted((work / "tts").glob("p*.json"))]
    metas = [p for k, p in enumerate(metas) if k % 10 == 3][:n]
    for path, blk in (("pc", 4000), ("discord", 320)):
        d, miss, unsure = [], 0, 0
        for k, mp in enumerate(metas):
            meta = json.loads(mp.read_text(encoding="utf-8"))
            we = _wake_end(meta)
            rng = np.random.default_rng(k + 5)
            y, pre = augment(_decode(mp.with_suffix(".mp3")), rng, [])
            pcm = np.clip(y * 32767, -32768, 32767).astype("<i2")
            sp = kws.Spotter(m, kws.THRESHOLDS[path])
            fired = None
            for i in range(0, pcm.size - blk + 1, blk):
                t0 = time.perf_counter()
                if sp.feed(pcm[i:i + blk].tobytes()):
                    fired = (i + blk) / SR + (time.perf_counter() - t0)
                    unsure += sp.last_score < kws.SURE[path]
                    break
            if fired is None:
                miss += 1
                continue
            d.append(fired - (we + pre / SR))
        d = np.array(d) * 1000
        print(f"{path}: {len(d)} wakes, {miss} missed; wake->earcon median {np.median(d):.0f} ms, "
              f"p90 {np.percentile(d, 90):.0f} ms, mean {d.mean():.0f} ms; uncertain (Whisper check, + its "
              f"time) {unsure}/{len(d)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("synth", "feats", "train", "count", "latency"))
    ap.add_argument("--work", required=True)
    ap.add_argument("--features", default="")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--job", type=int, default=0)
    ap.add_argument("--reps-pos", type=int, default=5)
    ap.add_argument("--reps-neg", type=int, default=2)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--prefix", default="ptau", help="clip kinds: p(ositives) t(raps) a(pp paragraphs) u (openers)")
    ap.add_argument("--out", default=str(ROOT / "treeaicoach" / "assets" / "voice" / "kws_hey_coach.onnx"))
    a = ap.parse_args()
    work = Path(a.work)
    if a.cmd == "count":
        its = items()
        print(len(its), {k: sum(1 for i in its if i["id"][0] == k) for k in "ptauvhk"})
    elif a.cmd == "synth":
        synth(work)
    elif a.cmd == "latency":
        latency(work, Path(a.features), Path(a.out))
    elif a.cmd == "feats":
        feats(work, Path(a.features), a.jobs, a.job, a.reps_pos, a.reps_neg, a.prefix)
    else:
        train(work, Path(a.out), Path(a.features), a.epochs)


if __name__ == "__main__":
    main()
