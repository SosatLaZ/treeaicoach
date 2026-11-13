#!/usr/bin/env python3
"""PROF (5.0) — correctness audit of the profession data (wow_addon/TreeAI/Data/Recipes.lua) against the client tables.

Rebuilds the recipes from the cached DB2 tables (same code as tools/wow_build_data.py), compares with what is shipped,
then lists, per profession, every field that is exact (read from a client table), estimated, or impossible to know
from the client (server data: trainer requirements, who sells what). Output = Markdown for research/prof5.md.

    python tools/wow_audit_recipes.py --cache <dir with the CSVs> [--era <dir with Classic Era SkillLineAbility.csv>]

Exit code 1 when the shipped file differs from a rebuild or when a hard anomaly is found (unknown reagent, a recipe
that can never give a point, a vendor reagent without a price)."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import wow_build_data as B  # noqa: E402

RANK_TIER_EFFECT = "118"  # SPELL_EFFECT_SKILL: EffectBasePoints = tier (1..4), EffectMiscValue_0 = skill line


def load(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def audit(cache: str, era: str | None) -> tuple[list[str], int]:
    T = {t: B.fetch(cache, t, B.BUILD, True) for t in B.PROF_TABLES}
    rec = B.build_recipes(T)
    out: list[str] = []
    hard = 0
    shipped = json.loads(gzip.open(os.path.join(B.OUT_DIR, "recipes.json.gz"), "rt", encoding="utf-8").read())
    same = json.loads(json.dumps({"build": B.BUILD, **rec}, ensure_ascii=False, sort_keys=True)) == shipped
    out.append(f"- Reconstruction depuis les tables ({B.BUILD}) = fichier livré : **{'oui' if same else 'NON'}**")
    hard += 0 if same else 1
    sparse = {r["ID"]: r for r in T["ItemSparse"]}
    mats = rec["mats"]
    out.append("")
    out.append("| Métier | Recettes | Apprises avec le métier | Exigence de la table | Patron (exigence de l'objet) |"
               " Maître : exigence ESTIMÉE (= seuil jaune) |")
    out.append("|---|---|---|---|---|---|")
    notes: list[str] = []
    for line, rows in sorted(rec["recipes"].items()):
        if not rows:
            continue
        name = rec["lines"][line][1]
        auto = sum(1 for r in rows if r[10] & 1)
        est = sum(1 for r in rows if r[10] & 2)
        pat = sum(1 for r in rows if r[7])
        exact = len(rows) - auto - est - pat
        out.append(f"| {name} | {len(rows)} | {auto} | {exact} | {pat} | {est} |")
        for r in rows:
            sid, item, cnt, learn, yellow, grey, pts, ritem, rname, flat, flags = r
            if grey <= 0 or grey < yellow:
                notes.append(f"- **{name} · {rname}** ({sid}) : seuils incohérents (jaune {yellow}, gris {grey}) — jamais planifiée.")
            if learn >= grey > 0 and not flags & 1:
                notes.append(f"- {name} · {rname} ({sid}) : s'apprend à {learn}, déjà grise à {grey} : aucun point possible (jamais planifiée).")
            if item and str(item) not in sparse:
                notes.append(f"- {name} · {rname} ({sid}) : objet créé {item} absent d'ItemSparse.")
            for k in range(0, len(flat), 2):
                if flat[k] not in mats:
                    notes.append(f"- **{name} · {rname}** ({sid}) : composant {flat[k]} inconnu.")
                    hard += 1
            if ritem and (str(ritem) not in sparse or B.i(sparse[str(ritem)].get("RequiredSkillRank")) <= 0):
                notes.append(f"- {name} · {rname} ({sid}) : patron {ritem} sans compétence requise dans la table "
                             f"(exigence = {learn}, à lire sur l'objet en jeu).")
    out.append("")
    out.append("### Anomalies relevées")
    out.extend(notes or ["- aucune"])
    # vendor reagents
    out.append("")
    out.append("### Composants vendus par un marchand (liste tenue à la main : le client ne dit pas qui vend quoi)")
    out.append("| Objet | Nom | Prix du lot (table) | Lot | Prix à l'unité |")
    out.append("|---|---|---|---|---|")
    for m in B.VENDOR_MATS:
        sp = sparse.get(str(m))
        if not sp:
            out.append(f"| {m} | absent de la version | | | |")
            continue
        lot = max(1, B.i(sp.get("VendorStackCount"), 1))
        buy = B.i(sp["BuyPrice"])
        if buy <= 0:
            hard += 1
        used = m in mats
        out.append(f"| {m} | {sp['Display_lang']}{'' if used else ' (aucune recette ne s’en sert)'} | {buy} | {lot} | {-(-buy // lot)} |")
    # rank spells
    out.append("")
    out.append("### Rangs (sorts de rang : effet 118, palier 1 à 4 → plafond 75 × palier)")
    eff = {}
    for r in T["SpellEffect"]:
        if r["Effect"] == RANK_TIER_EFFECT and B.i(r["EffectMiscValue_0"]) in B.PROF_LINES:
            eff[r["SpellID"]] = (B.i(r["EffectMiscValue_0"]), B.i(r["EffectBasePointsF"]))
    per: dict[int, set[int]] = {}
    for sid, (line, tier) in eff.items():
        per.setdefault(line, set()).add(tier)
    for line in sorted(per):
        tiers = sorted(t for t in per[line] if 1 <= t <= 4)
        out.append(f"- {B.PROF_LINES[line][1]} : plafonds {', '.join(str(75 * t) for t in tiers)} (client) ; niveau et compétence "
                   "pour passer le rang : absents du client → valeurs Classic, **à confirmer chez le maître**.")
    if era and os.path.isfile(os.path.join(era, "SkillLineAbility.csv")):
        e = {r["Spell"]: r for r in load(os.path.join(era, "SkillLineAbility.csv"))}
        diff = same_n = hint = 0
        for r in T["SkillLineAbility"]:
            if B.i(r["SkillLine"]) in B.PROF_LINES and r["Spell"] in e:
                x = e[r["Spell"]]
                if (x["TrivialSkillLineRankLow"], x["TrivialSkillLineRankHigh"]) != (r["TrivialSkillLineRankLow"], r["TrivialSkillLineRankHigh"]):
                    diff += 1
                else:
                    same_n += 1
                if B.i(x["MinSkillLineRank"]) > 1 and B.i(r["MinSkillLineRank"]) <= 1:
                    hint += 1
        out.append("")
        out.append(f"### Comparaison Classic Era (information) : {same_n} recettes aux mêmes seuils, {diff} aux seuils changés par "
                   f"Forever ; {hint} recettes où Era porte une exigence de maître que Forever n'a pas (Era n'est donc pas une "
                   "source fiable pour Forever : non utilisée).")
    return out, hard


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", required=True)
    ap.add_argument("--era")
    a = ap.parse_args(argv)
    out, hard = audit(a.cache, a.era)
    print("\n".join(out))
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
