"""Short diagnosis (<= 40 lines) of a « Rapport d'analyse » zip (treeaicoach/analysis_report.py) or of an
« Envoyer à TreeAI » zip (treeaicoach/export.py, recognised by its ``info.json``).

    python tools/analyze_report.py rapport_20261028_181500.zip
    python tools/analyze_report.py TreeAI_envoi_20261028_181500.zip

Export zip: per game, detection health against the LCU truth (tools/record_vs_truth.py: samples checked,
errors per 100), gank precision (tools/gank_alerts_truth.py: came / stayed), claims audit
(tools/claims_audit.py: wrong / checked), spam (alerts per minute, repeats); voice (wakes, false wakes,
transcripts from the filtered logs), performance per thread, errors (top messages), and one line per
analysis report inside.

Reads frames.json + state.json only (images are not decoded): detection anomalies against the Live
Client roster (allies never / rarely seen, duplicates, jumps, unknown ids, champions not in the game),
shop / item card state, rank sanity checks, voice engine state, spam rates (coach lines, threat gate)
and slow threads. Everything is a heuristic flag for a human: "!" = worth a look.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

MAX_LINES = 40
JUMP = 0.25                 # a champion moving more than this (minimap units) between two samples <= 2 s apart
ALLY_SEEN_MIN = 0.7         # allies are always on our minimap: seen in fewer frames than this is a problem
TIERS = {"IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"}


def _num(x: Any) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def load(path: str | Path) -> tuple[list[dict], dict, dict]:
    """(frames, state, meta {size, names})."""
    p = Path(path)
    with zipfile.ZipFile(p) as zf:
        names = zf.namelist()
        frames = json.loads(zf.read("frames.json")) if "frames.json" in names else []
        state = json.loads(zf.read("state.json")) if "state.json" in names else {}
    return frames, state, {"size": p.stat().st_size, "names": names}


def _seen(fr: dict) -> list[tuple[str, str, Any]]:
    """(alias, relation, pos) of the champions shown in a frame: visible tracks, else identified detections."""
    out = [(t.get("alias"), t.get("relation"), t.get("pos")) for t in fr.get("tracks") or ()
           if t.get("visible") and t.get("alias")]
    if not out:
        out = [(d.get("alias"), d.get("relation"), [d.get("u"), d.get("v")]) for d in fr.get("detections") or ()
               if d.get("alias")]
    return out


def detection(frames: list[dict], state: dict) -> list[str]:
    live = state.get("live_client") or {}
    players = live.get("players") or []
    me = (live.get("me") or {}).get("champion")
    my_team = live.get("my_team")
    roster = {p.get("champion"): p for p in players if p.get("champion")}
    allies = {a for a, p in roster.items() if my_team is not None and p.get("team") == my_team}
    enemies = set(roster) - allies
    n = len(frames)
    out = [f"DÉTECTION ({n} images, roster Live Client : {len(roster)} champions)"]
    if not n:
        return out + ["! aucune image dans le tampon (partie pas en cours ?)"]
    if not roster:
        out.append("! pas de roster Live Client : comparaisons impossibles")
    counts: Counter = Counter()
    dup_frames: Counter = Counter()
    foreign: Counter = Counter()
    unknown = 0
    scores = []
    last: dict[str, tuple[float, Any]] = {}
    jumps: Counter = Counter()
    for fr in frames:
        seen = _seen(fr)
        c = Counter(a for a, _r, _p in seen)
        for a, k in c.items():
            counts[a] += 1
            if k > 1:
                dup_frames[a] += 1
            if roster and a not in roster:
                foreign[a] += 1
        unknown += sum(1 for d in fr.get("detections") or () if not d.get("alias"))
        scores += [s for s in (_num(d.get("score")) for d in fr.get("detections") or ()) if s is not None]
        t = _num(fr.get("game_time")) if fr.get("game_time") is not None else _num(fr.get("t"))
        for a, _r, pos in seen:
            if not pos or pos[0] is None or t is None or c[a] > 1:
                continue
            prev = last.get(a)
            if prev is not None and 0 < t - prev[0] <= 2.0:
                d = ((pos[0] - prev[1][0]) ** 2 + (pos[1] - prev[1][1]) ** 2) ** 0.5
                if d > JUMP:
                    jumps[a] += 1
            last[a] = (t, pos)
    low = sorted(a for a in allies | ({me} if me else set()) if counts[a] / n < ALLY_SEEN_MIN)
    if low:
        out.append("! alliés / moi peu vus : " + ", ".join(f"{a} {100 * counts[a] // n} %" for a in low))
    if allies:
        out.append(f"  alliés vus en moyenne {round(100 * sum(counts[a] for a in allies) / (n * len(allies)))} % "
                   f"des images ; ennemis {', '.join(f'{a} {100 * counts[a] // n} %' for a in sorted(enemies)) or '-'}")
    if dup_frames:
        out.append("! doublons (même champion 2 fois) : " + ", ".join(f"{a} ×{k}" for a, k in dup_frames.most_common(4)))
    if foreign:
        out.append("! champions hors partie détectés : " + ", ".join(f"{a} ×{k}" for a, k in foreign.most_common(4)))
    if jumps:
        out.append(f"! sauts > {JUMP} carte en <= 2 s : " + ", ".join(f"{a} ×{k}" for a, k in jumps.most_common(4)))
    fps = [f for f in (_num(fr.get("fps")) for fr in frames) if f is not None]
    out.append(f"  détections sans identité : {unknown / n:.1f} / image ; score moyen "
               f"{statistics.mean(scores):.2f}" if scores else f"  détections sans identité : {unknown / n:.1f} / image")
    if fps:
        out[-1] += f" ; analyse {statistics.median(fps):.0f} img/s"
    return out


def shop(state: dict) -> list[str]:
    s = state.get("shop") or {}
    sw, card, iset = s.get("shop_watcher"), s.get("item_card"), s.get("item_set")
    bits = []
    if sw:
        bits.append(f"boutique {'ouverte' if sw.get('shop_open') else 'fermée'} (vues {sw.get('looks')})")
    else:
        bits.append("boutique jamais analysée")
    if card:
        bits.append("carte d'objet : " + ", ".join(f"{k}={v}" for k, v in card.items() if k != "type")[:80])
    if iset:
        bits.append("set d'objets : " + ", ".join(f"{k}={v}" for k, v in iset.items() if k != "type")[:60])
    screen = (state.get("report") or {}).get("screen")
    return ["BOUTIQUE : " + " ; ".join(bits) + ("" if screen else " ; ! pas de capture plein écran")]


def ranks(state: dict) -> list[str]:
    r = state.get("ranks") or {}
    ps = r.get("players") or []
    if not r.get("enabled"):
        return ["RANGS : désactivés"]
    if not ps:
        return ["RANGS : activés, rien lu (client LoL ? partie pas commencée ?)"]
    live = {p.get("champion") for p in (state.get("live_client") or {}).get("players") or ()}
    issues = []
    allies = sum(1 for p in ps if p.get("ally"))
    if len(ps) > 9 or allies > 4 or len(ps) - allies > 5:
        issues.append(f"{len(ps)} joueurs dont {allies} alliés (attendu 9 : 4 + 5)")
    for p in ps:
        a, tier = p.get("alias"), str(p.get("tier") or "")
        n = int(p.get("wins") or 0) + int(p.get("losses") or 0)
        if live and a not in live:
            issues.append(f"{a} n'est pas dans la partie")
        if tier and tier not in TIERS:
            issues.append(f"{a} palier inconnu {tier}")
        if not tier and n:
            issues.append(f"{a} non classé mais {n} parties")
        if n > 1500:
            issues.append(f"{a} {n} parties classées (invraisemblable ?)")
        if p.get("history_loaded") and int(p.get("games") or 0) > 20:
            issues.append(f"{a} historique {p.get('games')} > 20")
    head = f"RANGS (file {r.get('queue_id')}) : {len(ps)} joueurs, {sum(1 for p in ps if p.get('ranked_loaded'))} lus"
    out = [head] + [f"! {i}" for i in issues[:4]]
    out += [f"  {p.get('shown')}" for p in ps if not p.get("ally")][:5]
    return out


def voice(state: dict) -> list[str]:
    v = state.get("voice") or {}
    w = v.get("whisper") or {}
    tr = v.get("transcripts") or []
    empty = sum(1 for t in tr if not str(t.get("text") or "").strip())
    head = (f"VOIX : Whisper {'prêt' if w.get('ready') else 'NON chargé' if w.get('created') else 'jamais démarré'}"
            f" ({w.get('model') or '?'}{', ' + str(w.get('device')) if w.get('device') else ''})")
    out = [head]
    if w.get("engine_error"):
        out.append(f"! moteur : {str(w['engine_error'])[:90]}")
    if w.get("status"):
        out.append(f"  état : {str(w['status'])[:90]}")
    if tr:
        ms = [t.get("ms") for t in tr if isinstance(t.get("ms"), (int, float))]
        srcs = Counter(t.get("source") for t in tr)
        out.append(f"  {len(tr)} transcriptions ({empty} vides ; " + ", ".join(f"{k} {c}" for k, c in srcs.items())
                   + (f" ; {statistics.median(ms):.0f} ms" if ms else "") + f") ; dernière : « {str(tr[-1].get('text'))[:50]} »")
    return out


def spam(state: dict) -> list[str]:
    lines = state.get("coach_lines") or []
    gate = state.get("threat_gate") or []
    out = []
    gts = [g for g in (_num(x.get("game_time")) for x in lines) if g is not None]
    span = (max(gts) - min(gts)) / 60.0 if len(gts) >= 2 else 0.0
    rate = len(lines) / span if span > 0.5 else None
    rules = Counter(x.get("rule") for x in lines)
    rep = Counter(x.get("text") for x in lines)
    out.append(f"CONSEILS : {len(lines)} lignes" + (f" en {span:.1f} min ({rate:.1f}/min)" if rate else "")
               + " ; règles " + ", ".join(f"{k} {c}" for k, c in rules.most_common(4)))
    if rate and rate > 6:
        out.append(f"! débit élevé : {rate:.1f} lignes/min")
    reps = [(t, c) for t, c in rep.most_common(3) if c >= 3]
    for t, c in reps:
        out.append(f"! répété ×{c} : « {str(t)[:60]} »")
    if gate:
        ok = sum(1 for g in gate if g.get("ok"))
        why = Counter(g.get("why") for g in gate if not g.get("ok"))
        out.append(f"  porte des menaces : {ok}/{len(gate)} dites ; " + ", ".join(f"{k} {c}" for k, c in why.most_common(2)))
    return out


def perf(state: dict) -> list[str]:
    p = state.get("perf") or {}
    samples = p.get("samples") or []
    out = []
    fps = [f for f in (_num(s.get("fps")) for s in samples) if f is not None]
    tick = [f for f in (_num(s.get("tick_ms")) for s in samples) if f is not None]
    threads: dict[str, list[float]] = {}
    for s in samples:
        for k, v in (s.get("threads") or {}).items():
            if _num(v) is not None:
                threads.setdefault(k, []).append(float(v))
    head = "PERF :"
    if fps:
        head += f" analyse {statistics.median(fps):.1f} img/s (min {min(fps):.1f})"
    if tick:
        head += f" ; tick {statistics.median(tick):.0f} ms (max {max(tick):.0f})"
    perf_s = p.get("performance") or {}
    if perf_s.get("text"):
        head += f" ; {perf_s['text']}"[:100]
    out.append(head)
    slow = sorted(((statistics.mean(v), k) for k, v in threads.items() if statistics.mean(v) >= 25.0), reverse=True)
    if slow:
        out.append("! threads lourds : " + ", ".join(f"{k} {m:.0f} %" for m, k in slow[:4]))
    if tick and max(tick) > 80:
        out.append(f"! tick lent : {max(tick):.0f} ms")
    return out


def _tool(name: str) -> Any:
    import importlib.util
    root = Path(__file__).resolve().parent
    if str(root.parent) not in sys.path:
        sys.path.insert(0, str(root.parent))
    spec = importlib.util.spec_from_file_location(f"_ar_{name}", root / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _game_lines(stem: str, rec: dict, truth: dict | None) -> list[str]:
    summ = rec.get("summary") or {}
    dur = float(rec.get("duration") or summ.get("duration") or 0)
    alerts = [a for a in rec.get("alerts") or [] if isinstance(a, list) and len(a) >= 4]
    head = (f"PARTIE {stem[:16]} {summ.get('champion', '?')} {summ.get('position') or ''} {summ.get('result') or '?'} "
            f"{int(dur) // 60} min · v{(rec.get('meta') or {}).get('app_version', '?')} · "
            f"{len(alerts)} alertes ({len(alerts) * 60 / dur:.1f}/min)" if dur else f"PARTIE {stem[:16]}")
    out = [head]
    rep = Counter(str(a[3]) for a in alerts)
    reps = [(t, c) for t, c in rep.most_common(2) if c >= 5]
    if reps:
        out.append("  ! répétés : " + " ; ".join(f"×{c} « {t[:40]} »" for t, c in reps))
    if truth is None:
        out.append("  (pas de vérité LCU : détection / ganks / affirmations non vérifiables)")
        return out
    try:
        r = _tool("record_vs_truth").analyse(rec, truth)
        ck, cn = r["checked"], r["counts"]
        n = max(1, sum(ck.values()))
        top = sorted(((v, k) for k, v in cn.items() if v), reverse=True)[:4]
        gaps = cn.get("gap_enemy", 0)
        out.append(f"  détection : {ck.get('enemy', 0)} ennemis / {ck.get('ally', 0)} alliés vérifiés ; "
                   f"erreurs {100 * sum(cn.values()) / n:.0f}/100 ({', '.join(f'{k} {v}' for v, k in top) or 'aucune'})"
                   + f" ; ennemis visibles manqués {gaps}" + (f" ; moi {r['me_err50']:.3f}" if r.get("me_err50") else ""))
    except Exception as exc:
        out.append(f"  détection : vérification impossible ({type(exc).__name__})")
    try:
        g = _tool("gank_alerts_truth")
        res = g.score([(stem, g.Game(rec, truth), g.alerts_of(rec))])
        c, de = res["per"].get("ALL") or Counter(), res["deaths"]
        sc = c["came"] + c["stayed"]
        out.append(f"  ganks : {sum(c.values())} alertes, précision {c['came'] / sc:.0%} ({c['came']}/{sc})" if sc else
                   f"  ganks : {sum(c.values())} alertes, aucune vérifiable")
        out[-1] += f" ; morts prévenues {de['warned']}/{de['deaths']}"
    except Exception as exc:
        out.append(f"  ganks : calcul impossible ({type(exc).__name__})")
    try:
        rows = _tool("claims_audit").audit(rec, truth)
        v = Counter(r["verdict"] for r in rows)
        bad = Counter(r["rule"] for r in rows if r["verdict"] == "wrong")
        out.append(f"  affirmations : {v['wrong']} fausses / {v['ok'] + v['wrong']} vérifiées"
                   + (" (" + ", ".join(f"{k} {n}" for k, n in bad.most_common(3)) + ")" if bad else ""))
    except Exception as exc:
        out.append(f"  affirmations : audit impossible ({type(exc).__name__})")
    return out


def _log_lines(logs: dict[str, str]) -> list[str]:
    text = "\n".join(logs.values())
    wakes = text.count("mot-clé entendu")
    near = text.count("n'est pas confirmé, ignoré")
    short = len(re.findall(r"question de 0 mot|\(trop courte\)", text))
    heard = text.count("Écoute : entendu")
    dq = text.count("Discord écoute : question")
    out = [f"VOIX (journaux) : {wakes} réveils, {short} sans question (faux réveils probables), {near} quasi-réveils "
           f"ignorés, {heard} phrases entendues, {dq} questions Discord"]
    if wakes and short / wakes > 0.3:
        out.append(f"! faux réveils : {short}/{wakes}")
    errs = [ln for ln in text.splitlines() if re.search(r"\b(ERROR|CRITICAL)\b", ln)]
    warns = sum(1 for ln in text.splitlines() if re.search(r"\bWARNING\b", ln))
    msgs = Counter(re.sub(r"\d+", "N", ln.split(":", 3)[-1].strip())[:70] for ln in errs)
    out.append(f"ERREURS : {len(errs)} erreurs, {warns} avertissements")
    out += [f"! ×{c} {m}" for m, c in msgs.most_common(3)]
    return out


def analyze_export(path: str | Path) -> list[str]:
    p = Path(path)
    with zipfile.ZipFile(p) as zf:
        names = zf.namelist()
        info = json.loads(zf.read("info.json"))
        recs = {n: json.loads(zf.read(n)) for n in names if n.startswith("games/") and n.count("/") == 1
                and n.endswith(".json")}
        truths = {n: json.loads(zf.read(n)) for n in names if n.startswith("games/truth/")}
        logs = {n: zf.read(n).decode("utf-8", "replace") for n in names if n.startswith("logs/")}
        reports = [(n, zf.read(n)) for n in names if n.startswith("reports/") and n.endswith(".zip")]
    caps = Counter((n.rsplit("_", 1)[-1].split(".")[0] if n.endswith(".jpg") else "png") for n in names
                   if n.startswith("collect/"))
    pc, game, app = info.get("pc") or {}, info.get("game") or {}, info.get("app") or {}
    out = [f"Envoi TreeAI {app.get('version', '?')} · {info.get('created', '?')} · {p.stat().st_size / 1e6:.1f} Mo · "
           + ("partie EN COURS" if game.get("running") else "aucune partie en cours"),
           f"PC : {pc.get('cpu', '?')} ({pc.get('cores', '?')} cœurs) · {pc.get('ram_gb', '?')} Go · "
           f"GPU {', '.join(pc.get('gpu') or []) or '?'} · Windows {pc.get('windows_build', '?')} · écran "
           f"{'x'.join(map(str, pc.get('screen') or [])) or '?'} {pc.get('scale_percent', '?')} % · LoL "
           f"{'x'.join(map(str, pc.get('lol_window') or [])) or 'fermé'}",
           f"CAPTURES : {sum(caps.values())} (" + ", ".join(f"{k} {v}" for k, v in caps.most_common()) + ")"]
    for n, rec in sorted(recs.items()):
        stem = Path(n).name[:-len(".json")]
        truth = next((t for tn, t in truths.items() if tn.endswith(stem + ".truth.json")), None)
        out += _game_lines(stem, rec, truth) if isinstance(rec, dict) else []
    v = info.get("voice") or {}
    w = v.get("whisper") or {}
    out.append(f"MOTEUR VOIX : Whisper {'prêt' if w.get('ready') else 'non chargé'} ({w.get('model') or '?'}) ; "
               f"assistant {((v.get('assistant') or {}).get('stage')) or '?'}")
    out += _log_lines(logs)
    pf = info.get("perf") or {}
    th = sorted(((d.get("mean", 0), k) for k, d in (pf.get("threads") or {}).items() if d), reverse=True)[:4]
    out.append(f"PERF ({pf.get('samples', 0)} mesures) : " + (", ".join(f"{k} {m:.0f} %" for m, k in th) or "aucune")
               + (f" ; {pf['fps'].get('mean')} img/s" if (pf.get("fps") or {}).get("mean") else ""))
    for n, data in reports[:3]:
        try:
            import io
            with zipfile.ZipFile(io.BytesIO(data)) as rz:
                st = json.loads(rz.read("state.json"))
                fr = json.loads(rz.read("frames.json")) if "frames.json" in rz.namelist() else []
            det = detection(fr, st)
            flags = [x for x in det if x.startswith("!")]
            out.append(f"RAPPORT {Path(n).stem[-15:]} : {len(fr)} images"
                       + (" (hors partie)" if st.get("no_game") else "") + (f" ; {flags[0][2:70]}" if flags else ""))
        except Exception as exc:
            out.append(f"RAPPORT {n} illisible ({type(exc).__name__})")
    if len(out) > MAX_LINES:
        out = out[:MAX_LINES - 1] + [f"… ({len(out) - MAX_LINES + 1} lignes coupées)"]
    return out


def analyze(path: str | Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        if "info.json" in zf.namelist():
            return analyze_export(path)
    frames, state, meta = load(path)
    app = state.get("app") or {}
    rep = state.get("report") or {}
    live = state.get("live_client") or {}
    me = live.get("me") or {}
    gts = [g for g in (_num(f.get("game_time")) for f in frames) if g is not None]
    span = f"{int(min(gts)) // 60}:{int(min(gts)) % 60:02d}-{int(max(gts)) // 60}:{int(max(gts)) % 60:02d}" if gts else "?"
    out = [f"Rapport TreeAI {app.get('version', '?')} · {state.get('created', '?')} · {meta['size'] / 1e6:.1f} Mo · "
           f"{rep.get('frames', len(frames))} images (pas {rep.get('frame_step', 1)}) · temps de jeu {span}",
           f"Partie : {live.get('mode', '?')} · moi {me.get('champion', '?')} ({me.get('position') or '?'})"]
    for part in (detection(frames, state), shop(state), ranks(state), voice(state), spam(state), perf(state)):
        out += part
    if len(out) > MAX_LINES:
        out = out[:MAX_LINES - 1] + [f"… ({len(out) - MAX_LINES + 1} lignes coupées)"]
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    for a in args:
        try:
            print("\n".join(analyze(a)))
        except Exception as exc:  # a broken zip: say it, keep going
            print(f"{a} : illisible ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
