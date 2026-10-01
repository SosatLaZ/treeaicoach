"""Build ``treeaicoach/assets/champion_meta.json``: a compact, offline champion "meta" table.

Free, allowed sources only (no op.gg / u.gg scraping):

* Meraki Analytics champion data (https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US/
  champions.json, free to use): positions, class roles (VANGUARD, CATCHER, ARTILLERY...),
  damage type, range, attribute ratings (damage / toughness / control / mobility / utility);
* Data Dragon ``champion.json`` (Riot's official static data): tags, info ratings and base stats -
  used as the fallback for champions Meraki does not know yet.

Output (one entry per champion alias, a few hundred bytes each)::

    {"version": "16.19.1", "source": "meraki+ddragon",
     "champions": {"Ahri": {"pos": ["MIDDLE"], "cls": ["ASSASSIN", "BURST", "MAGE"], "dmg": "M",
                            "rng": "R", "ar": 550, "hp": 590, "hpl": 104,
                            "r": [3, 1, 2, 3, 1], "style": ["pick", "burst"], "curve": "mid"}}}

``dmg``: "P" physical / "M" magic / "X" mixed; ``rng``: "M" melee / "R" ranged; ``r``: damage,
toughness, control, mobility, utility (1..3); ``style``: engage / pick / poke / burst / dive /
sustain / peel / splitpush; ``curve``: early / mid / late (power curve).

Usage:  python tools/fetch_meta.py [--out path]    (network access only when run by hand)
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "treeaicoach" / "assets" / "champion_meta.json"
MERAKI = "https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US/champions.json"
DDRAGON = "https://ddragon.leagueoflegends.com"
UA = {"User-Agent": "TreeAICoach-assets/1.0"}

#: Mixed damage champions (Meraki only gives the adaptive type).
MIXED = set("Corki Kaisa KogMaw Varus Jax Shaco Volibear Udyr Warwick Shyvana Ornn Skarner Shen Kayle "
            "Gwen Belveth Nilah Akshan Ezreal Katarina".split())
#: Power curve (common knowledge of the game: strong early / scaling late).
EARLY = set("Renekton Pantheon LeeSin Elise Draven Lucian Darius Olaf XinZhao Nidalee Kalista Leblanc "
            "Graves Rengar Talon Qiyana Kled Sett Jayce Rumble Pyke Blitzcrank Leona Nautilus Rell Samira "
            "Tristana Caitlyn Ambessa Briar Naafiri Reksai JarvanIV Taliyah Gragas Irelia Riven Urgot "
            "Shaco Evelynn Udyr Volibear Trundle Warwick".split())
LATE = set("Kassadin Kayle Veigar Nasus Vayne KogMaw Jinx AurelionSol Smolder Senna Azir Cassiopeia Ryze "
           "Viktor Vladimir Gangplank Jax Kaisa Twitch Zeri Aphelios Kindred MasterYi Belveth Karthus "
           "Sona Seraphine Ornn Chogath Sion Mordekaiser Fiora Gwen Hwei Mel Yuumi Asol Anivia Orianna "
           "Ezreal Kayn Viego Lillia Sylas Corki Xayah".split())
POKE = set("Xerath Velkoz Ziggs Lux Jayce Varus Ezreal Nidalee Zoe Hwei Karma Corki Caitlyn Jhin "
           "Seraphine Mel Kogmaw Senna Sivir Zeri Heimerdinger Brand Syndra Viktor Taliyah".split())
SPLIT = set("Fiora Jax Tryndamere Camille Yorick Nasus Shen Gwen Trundle Kayle Jayce Riven Irelia "
            "Gangplank Quinn Sion Kled Udyr Ambessa".split())
SUSTAIN = set("Aatrox Soraka Yuumi Vladimir DrMundo Sylas Warwick Swain Briar Illaoi Fiddlesticks "
              "Olaf Volibear Nami Sona Milio Darius Samira Aphelios Kayn".split())


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:   # noqa: S310 - fixed https hosts
        return json.loads(resp.read().decode("utf-8"))


def _style(cls: set[str], r: list[int], alias: str, ranged: bool) -> list[str]:
    s: list[str] = []
    dmg, tough, ctrl, mob, util = r
    if cls & {"VANGUARD", "DIVER"} or (ctrl >= 3 and tough >= 2):
        s.append("engage")
    if cls & {"CATCHER"} or (ctrl >= 3 and not ranged and "ASSASSIN" in cls):
        s.append("pick")
    if cls & {"ARTILLERY"} or alias in POKE:
        s.append("poke")
    if cls & {"BURST", "ASSASSIN"}:
        s.append("burst")
    if cls & {"DIVER", "ASSASSIN", "SKIRMISHER"} and mob >= 2:
        s.append("dive")
    if alias in SUSTAIN:
        s.append("sustain")
    if cls & {"ENCHANTER", "WARDEN"}:
        s.append("peel")
    if alias in SPLIT:
        s.append("splitpush")
    return s


def build(meraki: dict, ddragon: dict) -> dict:
    out: dict[str, dict] = {}
    dd = ddragon.get("data") or {}
    for alias, c in sorted(meraki.items()):
        try:
            ar = c.get("attributeRatings") or {}
            r = [int(ar.get(k) or 1) for k in ("damage", "toughness", "control", "mobility", "utility")]
            st = c.get("stats") or {}
            rng_val = int((st.get("attackRange") or {}).get("flat") or 0)
            ranged = str(c.get("attackType") or "").upper() == "RANGED" or rng_val >= 300
            cls = [str(x) for x in c.get("roles") or []]
            dmg = "X" if alias in MIXED else ("M" if "MAGIC" in str(c.get("adaptiveType") or "") else "P")
            out[alias] = {
                "pos": [str(p) for p in c.get("positions") or []], "cls": cls, "dmg": dmg,
                "rng": "R" if ranged else "M", "ar": rng_val,
                "hp": int((st.get("health") or {}).get("flat") or 0),
                "hpl": int((st.get("health") or {}).get("perLevel") or 0),
                "r": r, "style": _style(set(cls), r, alias, ranged),
                "curve": "early" if alias in EARLY else "late" if alias in LATE else "mid",
            }
        except Exception as exc:  # pragma: no cover - data glitch
            print(f"skip {alias}: {exc}", file=sys.stderr)
    tag_cls = {"Tank": ["TANK", "VANGUARD"], "Fighter": ["FIGHTER", "JUGGERNAUT"], "Mage": ["MAGE", "BURST"],
               "Assassin": ["ASSASSIN"], "Marksman": ["MARKSMAN"], "Support": ["SUPPORT", "ENCHANTER"]}
    for alias, c in sorted(dd.items()):
        if alias in out:
            continue
        info, st, tags = c.get("info") or {}, c.get("stats") or {}, c.get("tags") or []
        cls = [x for t in tags for x in tag_cls.get(t, [])]
        ranged = float(st.get("attackrange") or 0) >= 300
        r = [max(1, min(3, round(float(info.get(k) or 5) / 3.4))) for k in ("attack", "defense")]
        r = [max(r[0], max(1, min(3, round(float(info.get("magic") or 5) / 3.4)))), r[1], 2, 2, 1]
        out[alias] = {
            "pos": [], "cls": cls, "dmg": "M" if float(info.get("magic") or 0) > float(info.get("attack") or 0) else "P",
            "rng": "R" if ranged else "M", "ar": int(float(st.get("attackrange") or 0)),
            "hp": int(float(st.get("hp") or 0)), "hpl": int(float(st.get("hpperlevel") or 0)),
            "r": r, "style": _style(set(cls), r, alias, ranged),
            "curve": "early" if alias in EARLY else "late" if alias in LATE else "mid",
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--meraki-file", default=None, help="use a local copy of the Meraki champions.json")
    args = ap.parse_args(argv)
    version = _get(f"{DDRAGON}/api/versions.json")[0]
    ddragon = _get(f"{DDRAGON}/cdn/{version}/data/en_US/champion.json")
    if args.meraki_file:
        meraki = json.loads(Path(args.meraki_file).read_text(encoding="utf-8"))
    else:
        meraki = _get(MERAKI)
    champs = build(meraki, ddragon)
    data = {"version": version, "source": "meraki+ddragon", "champions": champs}
    out = Path(args.out)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"{len(champs)} champions -> {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
