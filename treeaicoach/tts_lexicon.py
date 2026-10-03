"""French pronunciation for the coach's voice: League words and champion names, written so a French
voice (Edge neural, Windows OneCore or SAPI) says them the way French players do.

:func:`speakable` is applied to every sentence BEFORE synthesis (``voice.VoiceEngine.say`` and the
pre-generation lists, so the cache keys match). It only changes how a line SOUNDS: the card /
banner / toast keep the real names.

French text-to-speech reads English-looking names with French rules: ``u`` = [y] (Nunu ->
"Nunu" instead of "Nounou"), ``w`` = [v] (Swain -> "Svain"), final ``-en`` nasal (Garen ->
"Garin"), ``ay`` / ``ai`` = [è], ``ee`` = [é], ``y`` = [i]... and spells out all-caps words
("GANK" -> G-A-N-K). The table respells only those cases (community pronunciation on French
streams); names a French voice already says well are left alone.

Pure Python, no dependency, never raises (the input is returned on any error).
"""

from __future__ import annotations

import re
from functools import lru_cache

#: Champion names (French client names) -> spelling for a French voice.
CHAMPIONS: dict[str, str] = {
    "Aatrox": "Atrox",
    "Amumu": "Amoumou",
    "Aphelios": "Afélios",
    "Aurelion Sol": "Aurélion Sol",
    "Bel'Veth": "Bèl Vèth",
    "Braum": "Braoum",
    "Briar": "Braïar",
    "Caitlyn": "Kètline",
    "Cho'Gath": "Cho Gath",
    "Dr. Mundo": "Docteur Moundo",
    "Draven": "Dravène",
    "Elise": "Élise",
    "Evelynn": "Évelyne",
    "Ezreal": "Ézréal",
    "Garen": "Garène",
    "Gwen": "Gouène",
    "Hecarim": "Ékarim",
    "Heimerdinger": "Haïmeur-dinnegueur",
    "Hwei": "Houéï",
    "Illaoi": "Ilaoï",
    "Irelia": "Irélia",
    "Jarvan IV": "Jarvan quatre",
    "Jayce": "Djèïce",
    "Jhin": "Djinne",
    "Jinx": "Djinx",
    "K'Santé": "Késanté",
    "K'Sante": "Késanté",
    "Kai'Sa": "Kaïssa",
    "Kayle": "Kèïle",
    "Kayn": "Kèïne",
    "Kennen": "Kénène",
    "Kha'Zix": "Kazix",
    "Kog'Maw": "Kog Mao",
    "LeBlanc": "Leblanc",
    "Lee Sin": "Li Sine",
    "Leona": "Léona",
    "Lucian": "Loucianne",
    "Malphite": "Malfaïte",
    "Maokai": "Maokaï",
    "Miss Fortune": "Miss Fortchoune",
    "Mordekaiser": "Mordékaïzeur",
    "Naafiri": "Nafiri",
    "Neeko": "Niko",
    "Nidalee": "Nidali",
    "Nunu et Willump": "Nounou et Ouiloump",
    "Nunu & Willump": "Nounou et Ouiloump",
    "Nunu": "Nounou",
    "Poppy": "Popi",
    "Pyke": "Païke",
    "Qiyana": "Kiyana",
    "Quinn": "Kouinne",
    "Rek'Sai": "Rek Saï",
    "Renata Glasc": "Rénata Glasc",
    "Renekton": "Rénekton",
    "Rengar": "Rèngar",
    "Riven": "Rivène",
    "Rumble": "Reumbeul",
    "Ryze": "Raïze",
    "Sejuani": "Séjouani",
    "Senna": "Séna",
    "Sett": "Sète",
    "Shaco": "Chako",
    "Shen": "Chène",
    "Shyvana": "Chivana",
    "Singed": "Sinnged",
    "Smolder": "Smoldeur",
    "Swain": "Souène",
    "Sylas": "Saïlasse",
    "Syndra": "Sindra",
    "Tahm Kench": "Tam Kènch",
    "Teemo": "Timo",
    "Thresh": "Trèche",
    "Trundle": "Treundeul",
    "Tryndamere": "Trinndamir",
    "Twisted Fate": "Touisted Fète",
    "Twitch": "Touitch",
    "Udyr": "Oudir",
    "Varus": "Varusse",
    "Vayne": "Vèïne",
    "Veigar": "Végar",
    "Vel'Koz": "Vèl Koz",
    "Viego": "Viégo",
    "Volibear": "Volibèr",
    "Warwick": "Ouarouik",
    "Wukong": "Woukong",
    "Xayah": "Zaya",
    "Xerath": "Zérath",
    "Xin Zhao": "Chine Zao",
    "Yasuo": "Yassouo",
    "Yone": "Yoné",
    "Yunara": "Younara",
    "Yuumi": "Youmi",
    "Zaahen": "Zaène",
    "Zed": "Zède",
    "Zeri": "Zéri",
    "Zilean": "Ziléane",
    "Zyra": "Zaïra",
}

#: League words (whole words, case-insensitive; the case of the first letter is kept).
WORDS: dict[str, str] = {
    "jungler": "jungleur",
    "junglers": "jungleurs",
    "drake": "drèk",
    "drakes": "drèks",
    "buff": "beuf",
    "buffs": "beufs",
    "ult": "ulti",
    "ults": "ultis",
    "ulti": "ulti",
    "push": "pouche",
    "ward": "ouarde",
    "wards": "ouardes",
    "roam": "rôme",
    "roams": "rômes",
    "flash": "flache",
    "lane": "lène",
    "lanes": "lènes",
    "mid": "midd",
    "bot": "botte",
    "back": "bak",
    "kill": "kil",
    "kills": "kils",
    "teamfight": "tîme-faïte",
    "split": "splite",
    "stack": "stak",
    "stacks": "staks",
    "farm": "farme",
    "smite": "smaïte",
}

#: Acronyms (exact case) -> spoken form.
ACRONYMS: dict[str, str] = {
    "CS": "C.S.",
    "AD": "A.D.",
    "AP": "A.P.",
    "ADC": "A.D.C.",
    "TP": "T.P.",
    "PV": "P.V.",
    "MIA": "M.I.A.",
    "CC": "C.C.",
    "AOE": "zone",
    "GG": "G.G.",
    "XP": "expérience",
    "LP": "L.P.",
    "IA": "I.A.",
    "F9": "F 9",
}

_NUM_FR = ("zéro", "un", "deux", "trois", "quatre", "cinq")

_TIME_RE = re.compile(r"\b(\d{1,2}):([0-5]\d)\b")
_VS_RE = re.compile(r"\b([1-5])\s?[vV]\s?([1-5])\b")
_PCT_RE = re.compile(r"(\d)\s?%")
_PER_MIN_RE = re.compile(r"(?<=[\w%])\s?/\s?min\b")
_CAPS_RE = re.compile(r"\b([A-ZÀ-ÖØ-Ý]{4,})\b")
_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+")
_APOS_RE = re.compile(r"[’ʼ`]")
_SPACE_RE = re.compile(r"\s+")


@lru_cache(maxsize=1)
def _champ_re() -> re.Pattern[str]:
    keys = sorted(CHAMPIONS, key=len, reverse=True)       # "Nunu et Willump" before "Nunu"
    return re.compile(r"(?<![\wÀ-ÿ])(" + "|".join(re.escape(k) for k in keys) + r")(?![\wÀ-ÿ])")


def _time(m: re.Match[str]) -> str:
    mins, secs = int(m.group(1)), int(m.group(2))
    if secs == 0:
        return f"{mins} minute" + ("s" if mins > 1 else "")
    return f"{mins} minute{'s' if mins > 1 else ''} {secs}"


def _caps(m: re.Match[str]) -> str:
    w = m.group(1)
    if w in ACRONYMS:
        return w
    return w[:1] + w[1:].lower()


def _word(m: re.Match[str]) -> str:
    w = m.group(0)
    if w in ACRONYMS:
        return ACRONYMS[w]
    rep = WORDS.get(w.casefold())
    if rep is None:
        return w
    return rep[:1].upper() + rep[1:] if w[:1].isupper() else rep


@lru_cache(maxsize=4096)
def speakable(text: str) -> str:
    """``text`` respelled for a French voice (names, League words, times "8:00", "2v1", caps).
    Idempotent on its own output for the common cases. Never raises."""
    try:
        if not isinstance(text, str) or not text:
            return text if isinstance(text, str) else ""
        s = _APOS_RE.sub("'", text)
        s = _CAPS_RE.sub(_caps, s)                                   # GANK -> Gank (not spelled)
        s = _champ_re().sub(lambda m: CHAMPIONS[m.group(1)], s)
        s = _TIME_RE.sub(_time, s)
        s = _VS_RE.sub(lambda m: f"{_NUM_FR[int(m.group(1))]} contre {_NUM_FR[int(m.group(2))]}", s)
        s = _PCT_RE.sub(r"\1 %", s)
        s = _PER_MIN_RE.sub(" par minute", s)
        s = _WORD_RE.sub(_word, s)
        s = s.replace(" & ", " et ")
        return _SPACE_RE.sub(" ", s).strip()
    except Exception:
        return text


__all__ = ["ACRONYMS", "CHAMPIONS", "WORDS", "speakable"]
