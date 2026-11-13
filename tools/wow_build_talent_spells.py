#!/usr/bin/env python3
"""wow_build_talent_spells.py — sorts de talent par arbre, pour la détection de spé de l'addon (GEAR 5.0).

Source : treeaicoach/wow/data/talents.json.gz (tables client Talent / TalentTab de WoW Forever, déjà dans le dépôt,
produites par tools/wow_build_data.py). Aucune requête réseau.

Sortie : wow_addon/TreeAI/Data/TalentSpells.lua
  T.Data.TalentSpells[CLASSE][arbre] = "id,id,…;id,…;…"  — un groupe par talent, dans l'ordre de
  T.Data.Talents.trees[CLASSE][arbre].t (rang, colonne), les rangs 1 → max séparés par des virgules.
L'addon s'en sert pour deux lectures : (1) relier un nœud de l'arbre du jeu (C_Traits : sort de la définition) à
(arbre, rang, colonne) ; (2) déduire la spé des sorts de talent connus quand l'arbre ne se lit pas.

  python tools/wow_build_talent_spells.py            # réécrit le fichier
  python tools/wow_build_talent_spells.py --check    # code 1 si le fichier n'est pas à jour
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "treeaicoach" / "wow" / "data" / "talents.json.gz"
OUT = ROOT / "wow_addon" / "TreeAI" / "Data" / "TalentSpells.lua"


def render() -> str:
    with gzip.open(SRC, "rt", encoding="utf-8") as fh:
        tal = json.load(fh)
    out = ["-- GÉNÉRÉ par tools/wow_build_talent_spells.py — ne pas éditer à la main.\n"
           f"-- Source : tables client Blizzard (WoW Forever, build {tal.get('build', '?')}) : Talent.SpellRank.\n"
           "-- TalentSpells[CLASSE][arbre] = \"rang1,rang2,…;…\" : un groupe par talent, même ordre que\n"
           "-- T.Data.Talents.trees[CLASSE][arbre].t ; sert à lire la spé (nœuds C_Traits, sorts connus).\n"
           "local T = TreeAI\nif type(T) ~= \"table\" then return end\nT.Data = T.Data or {}\n"
           "T.Data.TalentSpells = {\n"]
    for cls, c in sorted(tal["classes"].items()):
        out.append(f"  {cls} = {{\n")
        for tab in c["tabs"]:
            rows = sorted(tab["talents"], key=lambda t: (t["tier"], t["col"]))
            groups = ";".join(",".join(str(s) for s in t["spells"]) for t in rows)
            out.append(f"    \"{groups}\",\n")
        out.append("  },\n")
    out.append("}\n")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = render()
    if "--check" in argv:
        cur = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if cur != text:
            print("Data/TalentSpells.lua n'est pas à jour : relance tools/wow_build_talent_spells.py")
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"{OUT.relative_to(ROOT)} : {len(text)} octets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
