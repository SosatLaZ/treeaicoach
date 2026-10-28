"""False-wake benchmark of « Hey coach » (local only, zero API): how often the coach wakes up by itself
during ordinary chatter, and how often it hears the real wake phrase. PC-mic path and Discord path.

    python tools/wake_bench.py synth --cache DIR                     # edge-tts clips (once)
    python tools/wake_bench.py run   --cache DIR --stt-cache STT_DIR --vosk-model DIR/vosk-model-small-fr-0.22 \\
                                     [--whisper DIR/faster-whisper-small] [--path pc|discord|both] [--quick]

Non-wake set: :data:`CHATTER` (gaming chatter, laughs, swears, « les gars », « vas-y », numbers, champion
names, « coach » inside other sentences: « mon coach dit… », « un coach »…) + the 100 ordinary sentences
and the 10 push-to-talk questions of tools/stt_corpus.py, each said by one of 10 French edge-tts voices, +
music / game noise without speech. They are played as ONE continuous session per path (the clips back to
back with short gaps, like friends talking), degraded like real use (headset EQ, game noise / music at
5-15 dB SNR, very low level for the PC; Discord Opus round trip at 32-64 kbit/s, packets only while
someone speaks, for Discord). Result: false wakes per hour of chatter.

Wake set: the 70 « Hey coach, … » questions of tools/stt_corpus.py (clips of ``stt_bench.py synth``) +
:data:`WAKES` (« Hé coach », « Et coach », « Coach, … » after a silence, « Hey Tree »), 2 conditions each
per path. Result: wake recall.

The app's exact rule runs (voice_assistant.WakeEngine, discord_listen.Speaker, and on the PC the Whisper
check of « coach-like » utterances); ``--whisper`` loads a CTranslate2 Whisper folder for the Whisper
confirmation (without it, the app's behaviour when Whisper is not ready). Clips stay in ``--cache``."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
if os.environ.get("WAKE_BENCH_ROOT"):            # another copy of the package (the « before » numbers)
    sys.path.insert(0, os.environ["WAKE_BENCH_ROOT"])

import stt_bench as sb  # noqa: E402
import stt_corpus  # noqa: E402

from treeaicoach import discord_listen as dl  # noqa: E402
from treeaicoach import voice_assistant as va  # noqa: E402

CHATTER: tuple[str, ...] = (
    # laughs / fillers / swears
    "Ha ha ha ha !", "Hahaha, t'es nul !", "Mdr, il s'est suicidé sous la tour.", "Pfff.", "Hum.", "Hum, tu crois ?",
    "Euh… attends.", "Putain !", "Merde, merde, merde !", "Bordel, il m'a one shot.", "Oh non !", "Oh là là.",
    "Aïe aïe aïe.", "Ah ouais ?", "Hein ?", "Quoi ?", "Ouais.", "Non.", "Ok ok.", "Oui oui.", "Eh bah bravo.",
    "Eh, c'est chaud là.", "Et voilà.", "Et alors ?", "Hé, regarde ça !", "Hé ho, réveille-toi !",
    "Hé les gars, on fait quoi ?", "Eh les gars, regroupez !", "Et là il flash dans le mur.", "Héhé, trop facile.",
    "Wesh, c'est quoi ce build ?", "Sérieux ?", "N'importe quoi.", "Ça va, ça va.", "Tranquille.", "Allez, allez !",
    # « les gars », « vas-y »
    "Les gars, je suis seul en bas.", "Vas-y, vas-y, vas-y !", "Vas-y fonce.", "Vas-y je te couvre.",
    "Les gars, on push mid.", "Venez les gars.", "Les gars, il y a le baron.", "Allez les gars, on y croit.",
    "Vas-y prends le kill.", "Les gars, ils sont trois en haut.", "Vas-y, back.", "Bougez-vous les gars !",
    # numbers / timers
    "Un, deux, trois, go !", "Trente secondes.", "Il reste deux minutes.", "Quatre cents golds.", "Cinq contre quatre.",
    "Dix-huit minutes de jeu.", "Trois kills.", "Zéro, dix, deux.", "Il a sept morts.", "Quinze secondes de dragon.",
    "Mille deux cents golds.", "Niveau six.", "Deux cents PV.", "Encore une minute.", "Quarante-cinq.",
    # champion names
    "Garen en haut.", "Le Darius est fed.", "Attention au Zed.", "Le Yasuo a flash.", "Lux a son ulti.",
    "Jinx se fait dive.", "Swain est là.", "Le Thresh a hook.", "Ezreal en bot.", "Le Lee Sin est passé top.",
    "Leona engage !", "Kai'Sa est seule.", "Le Teemo a mis des champis partout.", "Ahri roam bot.",
    "Le Malphite a son ulti.", "Tahm Kench m'a mangé.", "Miss Fortune ult dans le tas.", "Blitzcrank m'a attrapé.",
    "Le Katarina va reset.", "Seraphine bot, Garen top.", "Le Jax il est trop fort.", "Viego a volé le corps.",
    # « coach » inside other sentences (talk ABOUT the coach / another coach)
    "Mon coach dit qu'il faut farmer.", "Un coach, ça sert à rien.", "C'est quoi ce coach ?", "Le coach a dit de back.",
    "Ton coach il est nul.", "J'ai pris un coach sur internet.", "Le coach de l'équipe est français.",
    "Mon coach de foot crie tout le temps.", "Elle coache une équipe de basket.", "Il fait du coaching le soir.",
    "Ce coach est vraiment bien.", "Tu écoutes encore ton coach ?", "Le coach vocal parle trop.",
    "Demande au coach, lui il sait.", "Un vrai coach t'aurait dit de reculer.", "Coach ou pas, on a perdu.",
    "Les coachs coréens sont durs.", "Je coache mon petit frère.", "Le coach m'a engueulé.", "C'est un bon coach.",
    "Et le coach, il dit quoi ?", "J'ai désactivé le coach.", "Un coach sportif, c'est cher.", "Quel coach tu utilises ?",
    # « et / hé » + something else
    "Et toi, tu joues quoi ?", "Et si on faisait le drake ?", "Et on fait quoi maintenant ?", "Et là, il est où ?",
    "Hé, quelqu'un a une ward ?", "Eh, tu viens ?", "Et tu fais quoi là ?", "Hé, attends-moi !", "Et c'est reparti.",
    "Et cocher la case, c'est où ?", "Et la couche d'ozone alors ?", "Eh, cochon !", "Et quoi encore ?",
    "Hé, t'as vu le score ?", "Et toi, t'as ton flash ?", "Hey, ça va ?", "Hey, c'est moi.", "Hé, salut !",
    # other words close to coach / tree
    "On a fait trois tours.", "Il a pris le trident.", "Trois, deux, un.", "C'est très bien.", "Il triche !",
    "Tri des objets.", "Une poche de golds.", "La cloche a sonné.", "Il est moche ce skin.", "C'est cauchemardesque.",
    "Passe-moi la sauce.", "Il est trop proche.", "Le koala dort.", "Je mange une brioche.", "Trop cool.",
    "La couche est pleine.", "Ce cochon de jungler.", "C'est la fête.", "Ça coince.", "Il coche toutes les cases.",
    # gaming chatter
    "Il arrive par le bas.", "Je suis plein vie.", "Ward la rivière stp.", "On fait le héraut ?",
    "Je n'ai plus de mana.", "Il est low, il est low !", "Focus l'ADC.", "Peel moi, je suis seul.",
    "J'ai pas mon ulti.", "Il a TP bot.", "Ils font le baron !", "Rentre, rentre !", "Je back.", "Je reviens.",
    "Attends mon spike.", "On peut tower dive.", "Il a pas de flash.", "Gank top dans dix secondes.",
    "Ping les sorts.", "C'est un piège.", "Me laisse pas seul.", "Il me focus.", "Défends la base !",
    "On a l'âme du dragon.", "Prends la vague.", "Je freeze la lane.", "On slow push.", "Fais pas le con.",
    "J'ai lag.", "Mon écran a freeze.", "Tu peux mute le chat ?", "Ils ont surrender ?", "GG les gars.",
    "Bien joué.", "Encore une ?", "On relance ?", "Je vais chercher à boire.", "Attends, je reviens.",
    "Ma mère m'appelle.", "T'as vu le dernier patch ?", "Le patch a nerf mon champion.", "Je monte Platine.",
    "On fait une ranked ?", "Je suis en promo.", "Il a smurf ce mec.", "Report le jungler.", "Ce support est AFK.",
    "Il est parti farmer.", "On aurait dû backer.", "Ça sert à rien de chase.", "Laisse tomber.",
    "Il est sous la tour.", "Prends le buff bleu.", "Le rouge est à toi.", "Je prends les krugs.",
    "Le scuttle est là.", "Les larves sont up.", "Le drake infernal pope.", "Il manque un.", "Missing mid !",
    "Il a disparu.", "Je le vois pas.", "Il est invisible.", "C'est quoi cet item ?", "Il a full build.",
    "Il est où le Nashor ?", "On a perdu l'inhib.", "Super minions en bot.", "Fin de partie.", "Victoire !",
    "Défaite.", "Ils vont finir.", "On tient.", "Clean le ward.", "Achète des pinks.", "Je suis à sec.",
    "Il a esquivé.", "Le skillshot est passé.", "Trop de lag.", "J'ai raté le smite.", "Ils ont volé le baron !",
    "Tu me laisses le kill ?", "Je suis le meilleur.", "On est trop forts.", "C'est fini pour eux.",
    "Allez, on se concentre.", "Pas de bêtise.", "Qui a le flash ?", "Moi j'ai TP.", "Il a ignite.",
    "On attend le spawn.", "Les tourelles tombent.", "On split push.", "Je suis à la base.",
)

#: other ways to call the coach (recall), said after a silence
WAKES: tuple[str, ...] = (
    "Hé coach, quel objet je fais ?", "Et coach, il est où le jungler ?", "Eh coach, je back ou pas ?",
    "Coach, on fait le drake ?", "Coach, quel objet contre Zed ?", "Hey Tree, le baron est dispo ?",
    "Hé coach !", "Hey coach.", "Et coach, c'est quoi le timer du dragon ?", "Hé coach, Garen il build quoi ?",
    "Hey coach, je dois farmer ?", "Coach, je fais quoi maintenant ?", "Hé coach, combien de golds ?",
    "Eh coach, le héraut est up ?", "Hey coach, ils ont leur flash ?", "Hé coach, je prends quelle rune ?",
    "Hey coach, on peut gagner ?", "Et coach, où je ward ?", "Hé coach, Swain contre Lux ?", "Hey coach, aide-moi.",
)

PC_CONDS = ("clean", "headset_music15", "headset_game5", "low_hiss")
DISCORD_CONDS = ("discord48", "discord32_game10", "clip_discord64")
SR = sb.SR


def items() -> list[dict]:
    out = []
    for i, p in enumerate(CHATTER):
        out.append({"id": f"c{i:03d}", "text": p, "tts": p, "wake": False})
    for i, p in enumerate(WAKES):
        out.append({"id": f"w{i:02d}", "text": p, "tts": p, "wake": True})
    for k, it in enumerate(out):
        it["voice"] = sb.VOICES[(k * 3) % len(sb.VOICES)]
        it["rate"] = sb.RATES[k % len(sb.RATES)]
        it["terms"] = []
    return out


def synth(cache: Path) -> None:
    sb.items = items                                  # stt_bench's synth, on this corpus
    sb.synth(cache)


def corpus(cache: Path, stt_cache: Path) -> tuple[list[dict], list[dict]]:
    """``(non-wake clips, wake clips)``: dicts with the raw 48 kHz int16 samples (``x``)."""
    talk, wake = [], []
    for it in json.loads((cache / "corpus.json").read_text(encoding="utf-8")):
        d = dict(it, x=np.load(cache / f"{it['id']}.npy"))
        (wake if it["wake"] else talk).append(d)
    for it in json.loads((stt_cache / "corpus.json").read_text(encoding="utf-8")):
        d = dict(it, x=np.load(stt_cache / f"{it['id']}.npy"))
        (wake if it["wake"] else talk).append(d)
    return talk, wake


def _deg16(x: np.ndarray, cond: str, seed: int, cache: Path, key: str) -> bytes:
    f = cache / "deg" / f"{key}_{cond}.pcm"
    if not f.is_file():
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(sb.to16k(sb.degrade(x.astype(np.float32) / 32768.0, cond, seed)))
    return f.read_bytes()


def _noise16(seconds: float, kind: str, seed: int, level: float) -> bytes:
    n = int(seconds * SR)
    y = sb.game_noise(n, kind, seed) * level
    return sb.to16k(y)


def _hiss(seconds: float, seed: int) -> bytes:
    n = int(seconds * va.SAMPLE_RATE)
    return np.random.default_rng(seed).normal(0, 12.0, n).astype("<i2").tobytes()


def _trim(pcm: bytes) -> bytes:
    """What Discord sends: the talk spurt only (its voice activity cuts the silences)."""
    a = np.frombuffer(pcm, "<i2").astype(np.float32)
    fr = a[: a.size // 320 * 320].reshape(-1, 320)
    r = np.sqrt(np.mean(fr * fr, axis=1))
    on = np.where(r > max(200.0, 0.05 * r.max()))[0]
    if not on.size:
        return b""
    s, e = max(0, on[0] - 5), min(len(r), on[-1] + 10)
    return a[s * 320:e * 320].astype("<i2").tobytes()


class Whisper:
    """The Whisper check, timed."""

    def __init__(self, model: str, threads: int) -> None:
        from treeaicoach import stt_local  # noqa: PLC0415

        self.w = stt_local.WhisperSTT(model_dir=Path(model), threads=threads)
        if not self.w.load():
            raise SystemExit(self.w.status)
        self.ms: list[float] = []
        self.ms_confirm: list[float] = []

    def transcribe(self, pcm: bytes, prompt: str | None = None, beam: int | None = None) -> str:
        t0 = time.perf_counter()
        text = self.w.transcribe(pcm, prompt, beam=beam)
        (self.ms_confirm if beam else self.ms).append((time.perf_counter() - t0) * 1000)
        return text


# ------------------------------------------------------------------ PC path
class PcSim:
    """The PC assistant's wake loop (VoiceAssistant._run) on a stream: WakeEngine on every block, the VAD
    and the Whisper check of « coach-like » utterances (when Whisper is loaded)."""

    def __init__(self, make_rec, whisper: Whisper | None) -> None:  # noqa: ANN001
        self.make_rec, self.whisper = make_rec, whisper
        self.eng = self._engine()
        self.seg = None
        if whisper is not None:
            from treeaicoach.vad import Segmenter  # noqa: PLC0415

            self.seg = Segmenter()
        self.t = 0.0
        self.ww: list[float] = []
        self.events: list[tuple[float, str]] = []
        self.skip = 0

    def _engine(self) -> va.WakeEngine:
        eng = va.WakeEngine(self.make_rec)
        if self.whisper is not None and hasattr(eng, "confirm"):
            eng.confirm = lambda pcm: va.whisper_confirm(self.whisper, pcm)
        eng.ready()
        return eng

    def feed(self, pcm: bytes) -> None:
        n = va.BLOCK_FRAMES * 2
        for k in range(0, len(pcm) - n + 1, n):
            self.t += va.BLOCK_S
            blk = pcm[k:k + n]
            if self.skip > 0:                        # the question being recorded after a wake
                self.skip -= 1
                continue
            if self.eng.feed(blk):
                self._woke(f"{self.eng.last_kind},{self.eng.last_source}: {self.eng.last_text}")
                continue
            if self.seg is None:
                continue
            for u in self.seg.feed(blk):
                if not va.wakeish(self.eng.last_text) or len(u) > 8 * va.SAMPLE_RATE * 2:
                    continue
                self.ww = [x for x in self.ww if self.t - x < 60.0]
                if len(self.ww) >= va.WHISPER_WAKE_PER_MIN:
                    continue
                self.ww.append(self.t)
                text = self.whisper.transcribe(u)
                if va.whisper_wake(text)[0]:
                    self._woke("whisper: " + text)
                    break

    def _woke(self, what: str) -> None:
        self.events.append((self.t, what))
        self.skip = int(4.0 / va.BLOCK_S)
        self.eng.reset()
        if self.seg is not None:
            self.seg.reset()

    def end(self) -> None:
        if self.eng.flush():
            self._woke(f"{self.eng.last_kind},{self.eng.last_source}: {self.eng.last_text} (flush)")


# ------------------------------------------------------------------ Discord path
class DiscordSim:
    """One friend in the voice channel (discord_listen.Speaker): 20 ms packets while he talks, nothing in
    the silences (the ticker feeds comfort noise), fake clock."""

    def __init__(self, make_rec, whisper: Whisper | None) -> None:  # noqa: ANN001
        self.t = 0.0
        self.whisper = whisper
        self.sp = dl.Speaker("1", make_rec, lambda: self.t, lambda: None)
        if whisper is not None and hasattr(self.sp.engine, "confirm"):
            self.sp.engine.confirm = lambda pcm: va.whisper_confirm(whisper, pcm)
        self.events: list[tuple[float, str]] = []

    def _ev(self, evs) -> None:  # noqa: ANN001
        for k, _ in evs:
            if k == "wake":
                e = self.sp.engine
                self.events.append((self.t, f"{e.last_kind},{e.last_source}: {e.last_text}"))
                self.sp.engine.reset()
                self.sp.stage = "écoute"

    def talk(self, pcm: bytes) -> None:
        n = 640
        for k in range(0, len(pcm) - n + 1, n):
            self.t += 0.02
            self._ev(self.sp.feed(pcm[k:k + n]))
            if int(self.t / 0.02) % 12 == 0:
                self._ev(self.sp.tick())

    def silence(self, seconds: float) -> None:
        end = self.t + seconds
        while self.t < end:
            self.t += 0.25
            self._ev(self.sp.tick())


def run_path(path: str, talk: list[dict], wake: list[dict], args, make_rec, whisper) -> dict:  # noqa: ANN001
    cache = Path(args.cache)
    rng = np.random.default_rng(1)
    conds = PC_CONDS if path == "pc" else DISCORD_CONDS
    reps = 1 if args.quick else args.reps
    # -- chatter session
    sim = PcSim(make_rec, whisper) if path == "pc" else DiscordSim(make_rec, whisper)
    dur = 0.0
    t0 = time.perf_counter()
    for r in range(reps):
        for k, it in enumerate(talk):
            c = conds[(k + r) % len(conds)]
            pcm = _deg16(it["x"], c, k + 1000 * r, cache, it["id"])
            gap = float(rng.uniform(0.1, 1.5))
            if path == "pc":
                sim.feed(pcm + _hiss(gap, k))
                dur += len(pcm) / 32000 + gap
            else:
                sp = _trim(pcm)
                sim.talk(sp)
                sim.silence(gap + 0.3)
                dur += len(sp) / 32000 + gap + 0.3
            if k % 25 == 0:                          # music / game sounds without speech (PC: the mic hears
                kind = ("music", "game")[(k // 25) % 2]   # the speakers; Discord: a friend's open mic)
                lvl = float(rng.uniform(0.05, 0.3))
                pcm = _noise16(8.0, kind, k + r, lvl)
                if path == "pc":
                    sim.feed(pcm)
                else:
                    sim.talk(pcm)
                    sim.silence(0.5)
                dur += 8.0
        if path == "pc":
            sim.end()
    false = list(sim.events)
    # -- wake clips
    hits, n, missed = 0, 0, []
    for r in range(reps):
        for k, it in enumerate(wake):
            c = conds[(k + 2 * r) % len(conds)]
            pcm = _deg16(it["x"], c, k + 7, cache, it["id"])
            if path == "pc":
                sim.events = []
                sim.skip = 0
                sim.eng.reset()
                sim.feed(_hiss(1.0, k) + pcm + _hiss(1.0, k + 1))
                sim.end()
            else:
                sim.events = []
                sim.silence(3.0)
                sim.talk(_trim(pcm))
                sim.silence(1.5)
            n += 1
            if sim.events:
                hits += 1
            else:
                e = sim.eng if path == "pc" else sim.sp.engine
                missed.append(f"{it['id']}_{c}: {it['text']}  | heard « {e.last_text} »")
    h = dur / 3600.0
    res = {"path": path, "chatter_h": round(h, 3), "chatter_clips": len(talk) * reps, "false_wakes": len(false),
           "false_per_h": round(len(false) / h, 2), "recall": round(hits / max(1, n), 3), "wake_clips": n,
           "false": [f"{t:.0f}s {w}" for t, w in false], "missed": missed,
           "run_s": round(time.perf_counter() - t0, 1)}
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("synth", "run"))
    ap.add_argument("--cache", required=True)
    ap.add_argument("--stt-cache", default="")
    ap.add_argument("--vosk-model", default="")
    ap.add_argument("--whisper", default="")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--path", choices=("pc", "discord", "both"), default="both")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--reps", type=int, default=2, help="each clip played this many times (other conditions)")
    ap.add_argument("--out", default="")
    ap.add_argument("--pylib", default="", help="folder holding ctranslate2 / tokenizers (pip --target)")
    args = ap.parse_args()
    if args.pylib:
        sys.path.insert(0, args.pylib)
    cache = Path(args.cache)
    if args.cmd == "synth":
        synth(cache)
        return
    talk, wake = corpus(cache, Path(args.stt_cache))
    a = va.VoiceAssistant(model_path=Path(args.vosk_model))
    whisper = Whisper(args.whisper, args.threads) if args.whisper else None
    out = []
    for p in (("pc", "discord") if args.path == "both" else (args.path,)):
        r = run_path(p, talk, wake, args, a._make_rec, whisper)
        print(json.dumps({k: v for k, v in r.items() if k not in ("false", "missed")}))
        for f in r["false"]:
            print("  FALSE", f)
        for m in r["missed"][:40]:
            print("  MISS ", m)
        out.append(r)
    for name, ms in (("whisper check", whisper.ms if whisper else []),
                     ("whisper wake confirmation", whisper.ms_confirm if whisper else [])):
        if ms:
            ms = sorted(ms)
            print(f"{name}: {len(ms)} calls, median {ms[len(ms) // 2]:.0f} ms, p90 {ms[int(len(ms) * 0.9)]:.0f} ms")
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
