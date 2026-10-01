"""Tests for treeaicoach.alerts: French phrases and the anti-spam throttler."""

from __future__ import annotations

import itertools
import math
import re
import threading

import pytest

from treeaicoach import alerts
from treeaicoach.alerts import Alert, AlertKind, AlertThrottler, Level, alert_key, make_alert, phrase

GANK_KINDS = (AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE)
CHAMPS = (None, "Lee Sin", "Kha'Zix", "Nunu et Willump", "Maître Yi")
ZONES = (None, "en haut", "au milieu", "dans la rivière du bas", "dans la jungle ennemie du haut")

_VOWELS = "aeiouyàâäéèêëîïôöùûüœæ"
_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)


def _syllables(text: str) -> int:
    """Rough French syllable count (vowel groups, mute final -e/-es/-ent, digits)."""
    total = 0
    for word in _WORD_RE.findall(text.lower()):
        for part in re.split(r"['’-]", word):
            if not part:
                continue
            if part.isdigit():
                total += 1 if len(part) == 1 else 2 * len(part) - 1
                continue
            groups = len(re.findall(f"[{_VOWELS}]+", part))
            if groups > 1 and (part.endswith("e") or part.endswith("es") or part.endswith("ent")):
                groups -= 1
            total += max(groups, 1 if any(c.isalpha() for c in part) else 0)
    return total


# ----------------------------------------------------------------------------- phrases

def test_spec_examples() -> None:
    J, R, C = AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE
    assert phrase(J, Level.WARNING, "Lee Sin", "par la rivière") == "Lee Sin arrive par la rivière !"
    assert phrase(J, Level.WARNING, "Lee Sin") == "Lee Sin arrive !"
    assert phrase(J, Level.DANGER, "Lee Sin") == "Gank ! Lee Sin, recule !"
    assert phrase(R, Level.WARNING, "Ahri", "par ta jungle") == "Ahri vient vers toi par ta jungle !"
    assert phrase(R, Level.DANGER, "Ahri") == "Ahri arrive, recule !"
    assert phrase(C, Level.WARNING, None, "bot", count=2, names=["Lee Sin", "Ahri"]) == (
        "Gank bot : Lee Sin et Ahri !")
    assert phrase(C, Level.DANGER, None, "top", count=3, names=["Lee Sin", "Ahri"]) == (
        "Gank top : Lee Sin, Ahri et un ennemi, recule !")
    assert phrase(C, Level.DANGER, None, None, count=2, names=["Vi", "Zed"]) == "Gank : Vi et Zed, recule !"
    assert phrase(C, Level.DANGER, None, count=3) == "Danger, 3 ennemis arrivent, recule !"
    assert phrase(AlertKind.JUNGLER_SPOTTED, Level.INFO, "Lee Sin", "en haut") == "Jungler ennemi vu en haut."
    assert (phrase(AlertKind.JUNGLER_SPOTTED, Level.INFO, None, "dans la rivière du bas")
            == "Jungler ennemi vu dans la rivière du bas.")
    assert phrase(AlertKind.LANER_MIA, Level.INFO, "Darius") == "Darius a disparu, prudence."


def test_every_kind_level_combination_is_clean() -> None:
    for kind, level, champ, zone, count in itertools.product(
            AlertKind, Level, CHAMPS, ZONES, (0, 1, 2, 5)):
        s = phrase(kind, level, champ, zone, count)
        assert isinstance(s, str) and s, (kind, level, champ)
        assert s[0].isupper() or s[0].isdigit(), s
        assert s[-1] in ".!", s
        assert "None" not in s and "{" not in s and "  " not in s, s
        assert len(s) <= 90, s


def test_unknown_champion_is_un_ennemi() -> None:
    assert phrase(AlertKind.ROAM_APPROACH, Level.WARNING, None) == "Un ennemi arrive !"
    assert phrase(AlertKind.ROAM_APPROACH, Level.DANGER, None) == "Gank ! Un ennemi arrive, recule !"
    assert phrase(AlertKind.COLLAPSE, Level.DANGER, None, count=1) == "Danger, un ennemi arrive, recule !"
    assert phrase(AlertKind.JUNGLER_APPROACH, Level.DANGER, None) == "Gank du jungler, recule !"
    assert phrase(AlertKind.LANER_MIA, Level.INFO, "") == "Ton adversaire a disparu, prudence."
    # garbage champion values are treated as unknown
    for bad in ("   ", "None", float("nan"), True):
        assert phrase(AlertKind.ROAM_APPROACH, Level.WARNING, bad) == "Un ennemi arrive !"  # type: ignore[arg-type]


def test_plural_and_counts() -> None:
    C = AlertKind.COLLAPSE
    assert phrase(C, Level.DANGER, "Vi", count=1) == "Danger, Vi arrive, recule !"
    assert phrase(C, Level.DANGER, None, count=2) == "Danger, 2 ennemis arrivent, recule !"
    assert phrase(C, Level.WARNING, None, count=4) == "Attention, 4 ennemis approchent."
    assert phrase(C, Level.DANGER, None, count=9) == "Danger, 5 ennemis arrivent, recule !"   # clamped
    assert phrase(C, Level.DANGER, None) == "Danger, des ennemis arrivent, recule !"
    assert phrase(C, Level.DANGER, None, count="abc") == "Danger, des ennemis arrivent, recule !"  # type: ignore[arg-type]
    assert phrase(C, Level.DANGER, None, count=2.7) == "Danger, 2 ennemis arrivent, recule !"
    assert phrase(AlertKind.DEATH_RECAP, Level.INFO, None, count=2) == "Mort face à 2 ennemis."
    assert phrase(AlertKind.DEATH_RECAP, Level.INFO, "Zed", count=1) == "Mort face à Zed."


def test_v11_kinds_default_phrases() -> None:
    O = AlertKind.OBJECTIVE_SOON
    assert phrase(O, Level.INFO, "Dragon", count=60) == "Dragon dans une minute."
    assert phrase(O, Level.INFO, "Baron", count=20) == "Baron dans 20 secondes."
    assert phrase(O, Level.INFO, "Larves", count=90) == "Larves dans 1 minute 30."
    assert phrase(O, Level.INFO, None) == "Objectif bientôt."
    assert phrase(AlertKind.RECALL_GOLD, Level.INFO, None, count=1473) == (
        "Tu as 1450 pièces d'or, pense à rentrer.")
    assert phrase(AlertKind.CONTROL_WARD, Level.INFO, None) == "Pense à acheter une balise de contrôle."
    W = AlertKind.JUNGLER_WHERE
    assert phrase(W, Level.INFO, "Lee Sin", "dans la rivière du haut", 23) == (
        "Lee Sin vu dans la rivière du haut il y a 23 secondes.")
    assert phrase(W, Level.INFO, "Lee Sin", "en bas", 0) == "Lee Sin est visible en bas."
    assert phrase(W, Level.INFO, "Lee Sin", "en bas", 75) == "Lee Sin vu en bas il y a plus d'une minute."
    assert phrase(W, Level.INFO, "Lee Sin", None, 5) == "Position de Lee Sin inconnue."
    assert phrase(W, Level.INFO, None, None) == "Position du jungler ennemi inconnue."


def test_free_text_for_v11_kinds() -> None:
    recap = "Mort face à 2 ennemis, dont le jungler.   L'alerte avait été donnée\n5 secondes avant."
    assert phrase(AlertKind.DEATH_RECAP, Level.INFO, None, text=recap) == (
        "Mort face à 2 ennemis, dont le jungler. L'alerte avait été donnée 5 secondes avant.")
    assert phrase(AlertKind.JUNGLER_WHERE, Level.INFO, None, text="Lee Sin vu en haut il y a 12 s") == (
        "Lee Sin vu en haut il y a 12 s.")
    assert phrase(AlertKind.OBJECTIVE_SOON, Level.INFO, "Dragon", count=60, text="Dragon ancestral dans 20 secondes !") == (
        "Dragon ancestral dans 20 secondes !")
    # empty / garbage text falls back to the built phrase
    assert phrase(AlertKind.CONTROL_WARD, Level.INFO, None, text="  ") == "Pense à acheter une balise de contrôle."
    assert phrase(AlertKind.DEATH_RECAP, Level.INFO, None, text=None) == "Tu es mort."
    # long text bounded, still ends with punctuation
    long = phrase(AlertKind.DEATH_RECAP, Level.INFO, None, text="mot " * 200)
    assert len(long) <= alerts.FREE_TEXT_MAX_LEN + 1 and long.endswith(".")
    # control characters removed
    assert "\x00" not in phrase(AlertKind.RECALL_GOLD, Level.INFO, None, text="Rentre\x00 vite")
    # ignored for gank kinds (they must stay short)
    assert phrase(AlertKind.JUNGLER_APPROACH, Level.DANGER, "Vi", text="bla bla bla") == "Gank ! Vi, recule !"


def test_gank_phrases_are_short() -> None:
    """Gank alerts must be spoken in about 1.8 s (≈ 10 syllables at SAPI rate +2)."""
    worst = 0
    for kind, level, champ in itertools.product(GANK_KINDS, (Level.WARNING, Level.DANGER),
                                                (None, "Lee Sin", "Kha'Zix", "Nocturne", "Hecarim")):
        for count in (0, 1, 2, 3, 5):
            s = phrase(kind, level, champ, count=count)
            worst = max(worst, _syllables(s))
            assert _syllables(s) <= 10, (s, _syllables(s))
    assert worst >= 5          # sanity check of the estimator
    assert _syllables("Danger, 3 ennemis arrivent, recule !") <= 10
    for level in Level:
        for zone in ZONES:
            s = phrase(AlertKind.JUNGLER_SPOTTED, level, "Lee Sin", zone)
            assert _syllables(s) <= 17, s
            s = phrase(AlertKind.LANER_MIA, level, "Lee Sin")
            assert _syllables(s) <= 10, s


def test_zone_labels_from_geometry() -> None:
    geometry = pytest.importorskip("treeaicoach.geometry")
    for zone in geometry.Zone:
        for team in ("ORDER", "CHAOS", None):
            label = geometry.zone_label_fr(zone, team)
            s = phrase(AlertKind.JUNGLER_SPOTTED, Level.INFO, None, label)
            assert s == f"Jungler ennemi vu {label}." if label else s == "Jungler ennemi repéré.", s


def test_phrase_never_raises() -> None:
    class Evil:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    assert phrase("bogus", Level.DANGER, "X") == "Danger, recule !"  # type: ignore[arg-type]
    assert phrase("collapse", "danger", None, count=2) == "Danger, 2 ennemis arrivent, recule !"  # type: ignore[arg-type]
    assert phrase(AlertKind.ROAM_APPROACH, 99, Evil()) == "Gank ! Un ennemi arrive, recule !"  # type: ignore[arg-type]
    assert phrase(AlertKind.ROAM_APPROACH, None, "Zed") == "Zed arrive !"  # type: ignore[arg-type]
    assert phrase(AlertKind.COLLAPSE, Level.DANGER, None, count=float("inf")) == (
        "Danger, 5 ennemis arrivent, recule !")
    assert phrase(AlertKind.ROAM_APPROACH, Level.WARNING, "X" * 500).endswith(" arrive !")
    assert len(phrase(AlertKind.ROAM_APPROACH, Level.WARNING, "X" * 500)) < 60


def test_level_coerce_and_kind_lookup() -> None:
    assert Level.coerce("danger") is Level.DANGER
    assert Level.coerce(1.4) is Level.WARNING
    assert Level.coerce(7) is Level.DANGER and Level.coerce(-3) is Level.INFO
    assert Level.coerce(float("nan")) is Level.WARNING and Level.coerce(None) is Level.WARNING
    assert AlertKind("COLLAPSE") is AlertKind.COLLAPSE and AlertKind("Death_Recap") is AlertKind.DEATH_RECAP
    with pytest.raises(ValueError):
        AlertKind("nope")


def test_alert_dataclass_and_helpers() -> None:
    a = Alert(kind="roam_approach", level=2, text="x", key="k", t=1.0)  # type: ignore[arg-type]
    assert a.kind is AlertKind.ROAM_APPROACH and a.level is Level.DANGER
    assert a.to_dict() == {"kind": "roam_approach", "level": 2, "text": "x", "key": "k", "t": 1.0, "alias": None}
    b = Alert(kind=AlertKind.COLLAPSE, level=Level.INFO, text=None, key=None, t=0.0)  # type: ignore[arg-type]
    assert b.text == "" and b.key == ""
    assert alert_key(AlertKind.JUNGLER_APPROACH, "LeeSin") == "jungler_approach:LeeSin"
    assert alert_key("collapse") == "collapse"
    m = make_alert(AlertKind.JUNGLER_APPROACH, Level.DANGER, 5.0, champ="Lee Sin", alias="LeeSin")
    assert m.text == "Gank ! Lee Sin, recule !" and m.key == "jungler_approach:LeeSin" and m.alias == "LeeSin"
    d = make_alert(AlertKind.DEATH_RECAP, Level.INFO, 5.0, text="Mort sans alerte.")
    assert d.text == "Mort sans alerte." and d.key == "death_recap"


# ----------------------------------------------------------------------------- throttler

def A(kind: AlertKind, level: Level, t: float, who: str | None = "LeeSin", key: str | None = None,
      alias: str | None = "") -> Alert:
    """Small alert factory: alias defaults to ``who``."""
    return Alert(kind=kind, level=level, text=phrase(kind, level, who), key=key or alert_key(kind, who),
                 t=t, alias=who if alias == "" else alias)


JA, RA, CO, JS = AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE, AlertKind.JUNGLER_SPOTTED


def _run(th: AlertThrottler, raw: list[Alert], t: float) -> list[str]:
    return [a.key for a in th.filter(raw, t)]


@pytest.mark.parametrize("level,cooldown", [(Level.INFO, 30.0), (Level.WARNING, 8.0), (Level.DANGER, 6.0)])
def test_cooldown_per_key_by_level(level: Level, cooldown: float) -> None:
    th = AlertThrottler()
    a = lambda t: A(AlertKind.LANER_MIA, level, t)  # noqa: E731
    assert th.filter([a(0.0)], 0.0)
    for t in (0.5, 2.0, cooldown - 0.2):
        assert th.filter([a(t)], t) == []
    assert th.filter([a(cooldown + 0.01)], cooldown + 0.01)
    # another key is independent
    assert th.filter([A(RA, level, cooldown + 5, who="Ahri")], cooldown + 5)


@pytest.mark.parametrize("kind", [JA, RA, CO])
@pytest.mark.parametrize("level", [Level.WARNING, Level.DANGER])
def test_gank_kinds_not_repeated_for_12s(kind: AlertKind, level: Level) -> None:
    th = AlertThrottler()
    assert th.filter([A(kind, level, 0.0)], 0.0)
    repeat = 25.0 if kind == RA else 12.0      # a roam of the same laner: once per roam (25 s)
    for t in (1.0, 6.5, 8.5, 11.9, repeat - 0.1):
        assert th.filter([A(kind, level, t)], t) == []
    assert th.filter([A(kind, level, repeat)], repeat)


def test_personal_danger_recule_never_twice_within_20s() -> None:
    th = AlertThrottler()
    pd = AlertKind.PERSONAL_DANGER
    assert phrase(pd, Level.DANGER, None) == "Recule !"
    assert th.filter([A(pd, Level.DANGER, 0.0)], 0.0)
    for t in (2.0, 10.0, 19.9):
        assert th.filter([A(pd, Level.DANGER, t)], t) == []
    assert th.filter([A(pd, Level.DANGER, 20.0)], 20.0)


def test_same_gank_under_another_key_is_not_repeated() -> None:
    th = AlertThrottler()
    merged = Alert(kind=CO, level=Level.WARNING, text="Gank bot : Lee Sin et Ahri !",
                   key="collapse:Ahri+LeeSin", t=0.0, members=("Ahri", "LeeSin"))
    assert th.filter([merged], 0.0) == [merged]
    single = make_alert(JA, Level.WARNING, 3.0, "Lee Sin", alias="LeeSin", zone_label="par la rivière")
    assert single.members == ("LeeSin",) and single.text == "Lee Sin arrive par la rivière !"
    assert th.filter([single], 3.0) == []                        # already announced in the merged one
    # escalation to DANGER passes
    danger = make_alert(JA, Level.DANGER, 4.0, "Lee Sin", alias="LeeSin")
    assert th.filter([danger], 4.0) == [danger]
    # a new champion joining the gank passes
    bigger = Alert(kind=CO, level=Level.DANGER, text="Gank bot : Lee Sin, Ahri et Vi, recule !",
                   key="collapse:Ahri+LeeSin+Vi", t=6.0, members=("Ahri", "LeeSin", "Vi"))
    assert th.filter([bigger], 6.0) == [bigger]
    # the same DANGER gank is quiet for 12 s, then may be said again
    again = Alert(kind=CO, level=Level.DANGER, text="x !", key="collapse:Ahri+LeeSin", t=10.0,
                  members=("Ahri", "LeeSin"))
    assert th.filter([again], 10.0) == []
    assert th.filter([again], 18.1) == [again]


def test_jungler_spotted_at_most_every_45s() -> None:
    th = AlertThrottler()
    assert th.filter([A(JS, Level.INFO, 0.0)], 0.0)
    assert th.filter([A(JS, Level.INFO, 40.0)], 40.0) == []
    assert th.filter([A(JS, Level.INFO, 45.5)], 45.5)


def test_jungler_where_cooldown_and_gap_exemption() -> None:
    th = AlertThrottler()
    w = lambda t: A(AlertKind.JUNGLER_WHERE, Level.INFO, t, who=None)  # noqa: E731
    assert th.filter([A(RA, Level.WARNING, 0.0, who="Ahri")], 0.0)
    assert th.filter([w(0.1)], 0.1)                  # not delayed by the 1.2 s global gap
    assert th.filter([w(2.0)], 2.0) == []            # 3 s cooldown
    assert th.filter([w(3.2)], 3.2)
    assert th.filter([A(AlertKind.DEATH_RECAP, Level.INFO, 3.3, who=None)], 3.3)   # gap-exempt too


def test_escalation_same_key_is_immediate() -> None:
    th = AlertThrottler()
    assert _run(th, [A(JA, Level.WARNING, 0.0)], 0.0)
    out = th.filter([A(JA, Level.DANGER, 0.3)], 0.3)          # within the 1.2 s gap
    assert out and out[0].level is Level.DANGER
    # no de-escalation spam: the WARNING stays silent during its cooldown
    for t in (1.0, 4.0, 7.9):
        assert th.filter([A(JA, Level.WARNING, t)], t) == []
    # INFO -> WARNING escalation also passes the key cooldown (subject to the global gap)
    th2 = AlertThrottler()
    assert th2.filter([A(JA, Level.INFO, 0.0)], 0.0)
    assert th2.filter([A(JA, Level.WARNING, 1.3)], 1.3)


def test_escalation_same_champion_across_keys() -> None:
    th = AlertThrottler()
    assert th.filter([A(JA, Level.DANGER, 0.0)], 0.0)
    # later a WARNING about the same champion (other key) is said...
    assert th.filter([A(RA, Level.WARNING, 2.0, key="roam_approach:LeeSin")], 2.0)
    # ...so a DANGER about him passes although its own key is in cooldown
    out = th.filter([A(JA, Level.DANGER, 2.5)], 2.5)
    assert out and out[0].key == "jungler_approach:LeeSin"
    # but not for another champion whose key is in cooldown
    assert th.filter([A(JA, Level.DANGER, 4.1, who="Vi")], 4.1)
    assert th.filter([A(JA, Level.DANGER, 6.0, who="Vi")], 6.0) == []


def test_one_alert_per_tick_highest_level_then_most_recent() -> None:
    th = AlertThrottler()
    raw = [A(JS, Level.INFO, 1.0), A(RA, Level.WARNING, 1.0, who="Ahri"), A(JA, Level.DANGER, 0.9)]
    out = th.filter(raw, 1.0)
    assert len(out) == 1 and out[0].kind is JA and out[0].level is Level.DANGER
    th = AlertThrottler()
    raw = [A(RA, Level.WARNING, 0.8, who="Ahri"), A(RA, Level.WARNING, 1.0, who="Zed")]
    assert _run(th, raw, 1.0) == ["roam_approach:Zed"]
    # same level and time: kind priority (COLLAPSE first)
    th = AlertThrottler()
    raw = [A(JA, Level.DANGER, 1.0), A(CO, Level.DANGER, 1.0, who=None)]
    assert _run(th, raw, 1.0) == ["collapse"]


def test_global_min_gap_except_danger() -> None:
    th = AlertThrottler(min_gap_s=1.2)
    mia = AlertKind.LANER_MIA
    assert th.filter([A(RA, Level.WARNING, 0.0, who="Ahri")], 0.0)
    assert th.filter([A(mia, Level.WARNING, 0.5, who="Zed")], 0.5) == []
    assert _run(th, [A(mia, Level.WARNING, 1.3, who="Zed")], 1.3) == ["laner_mia:Zed"]
    # a gank WARNING is never delayed by the global gap (latency first)
    assert _run(th, [A(RA, Level.WARNING, 1.35, who="Kayn")], 1.35) == ["roam_approach:Kayn"]
    # ... and wins over another kind of the same level
    assert _run(AlertThrottler(), [A(mia, Level.WARNING, 2.0, who="Zed"), A(RA, Level.WARNING, 1.9, who="Ahri")],
                2.0) == ["roam_approach:Ahri"]
    # DANGER is never delayed by the global gap
    assert _run(th, [A(JA, Level.DANGER, 1.4)], 1.4) == ["jungler_approach:LeeSin"]
    th0 = AlertThrottler(min_gap_s=0.0)
    assert th0.filter([A(RA, Level.WARNING, 0.0, who="Ahri")], 0.0)
    assert th0.filter([A(RA, Level.WARNING, 0.0, who="Zed")], 0.0)


def test_danger_after_danger_gap() -> None:
    th = AlertThrottler()
    assert _run(th, [A(CO, Level.DANGER, 0.0, who=None)], 0.0) == ["collapse"]
    assert th.filter([A(JA, Level.DANGER, 0.2)], 0.2) == []           # does not cut the first one
    assert th.pending_count() == 0                                   # DANGER is re-raised, not kept
    assert _run(th, [A(JA, Level.DANGER, 1.6)], 1.6) == ["jungler_approach:LeeSin"]
    literal = AlertThrottler(danger_gap_s=0.0)                       # spec-literal behaviour
    assert literal.filter([A(CO, Level.DANGER, 0.0, who=None)], 0.0)
    assert literal.filter([A(JA, Level.DANGER, 0.1)], 0.1)


def test_one_shot_alerts_held_by_the_gap_are_not_lost() -> None:
    th = AlertThrottler()
    obj = A(AlertKind.OBJECTIVE_SOON, Level.INFO, 0.5, who="Dragon")
    spotted = A(JS, Level.INFO, 0.5)
    assert th.filter([A(RA, Level.WARNING, 0.0, who="Ahri")], 0.0)
    assert th.filter([obj, spotted], 0.5) == []                      # gap: both held
    assert th.pending_count() == 2
    first = th.filter([], 1.25)
    second = th.filter([], 1.9)
    third = th.filter([], 2.5)
    said = [a.key for a in first + second + third]
    assert sorted(said) == sorted([obj.key, spotted.key])
    assert th.pending_count() == 0
    assert th.filter([], 10.0) == []


def test_pending_expiry() -> None:
    th = AlertThrottler(min_gap_s=10.0)
    assert th.filter([A(RA, Level.WARNING, 0.0, who="Ahri")], 0.0)
    th.filter([A(JS, Level.INFO, 1.0), A(AlertKind.LANER_MIA, Level.WARNING, 1.0, who="Zed")], 1.0)
    assert th.pending_count() == 2
    th.filter([], 3.0)                                               # WARNING kept 1.5 s only
    assert th.pending_count() == 1
    assert th.filter([], 10.5) == []                                 # INFO kept 6 s only
    assert th.pending_count() == 0


def test_time_going_backwards_and_jumps() -> None:
    th = AlertThrottler()
    assert th.filter([A(JA, Level.WARNING, 100.0)], 100.0)
    assert th.filter([A(JA, Level.WARNING, 99.7)], 99.7) == []       # jitter: clamped, still in cooldown
    assert th.filter([A(JA, Level.WARNING, 50.0)], 50.0)             # new timeline: state reset
    assert th.filter([A(JA, Level.WARNING, 1e9)], 1e9)               # huge jump: cooldowns expired
    assert th.filter([A(JA, Level.WARNING, 1e9 + 1)], 1e9 + 1) == []
    for bad in (float("nan"), float("inf"), None, "x"):           # invalid tick time: previous tick used
        out = th.filter([A(JA, Level.DANGER, 0.0, who=f"Vi{bad}")], bad)  # type: ignore[arg-type]
        assert isinstance(out, list) and len(out) <= 1
    th2 = AlertThrottler()
    out = th2.filter([A(JA, Level.WARNING, float("nan"))], float("nan"))
    assert len(out) == 1


def test_reset() -> None:
    th = AlertThrottler()
    assert th.filter([A(JA, Level.WARNING, 0.0)], 0.0)
    th.filter([A(JS, Level.INFO, 0.1)], 0.1)
    assert th.filter([A(JA, Level.WARNING, 1.0)], 1.0) == []
    th.reset()
    assert th.pending_count() == 0
    assert th.filter([A(JA, Level.WARNING, 1.0)], 1.0)


def test_invalid_inputs_are_ignored() -> None:
    th = AlertThrottler(min_gap_s=float("nan"))
    assert th.min_gap_s == alerts.DEFAULT_MIN_GAP_S
    assert th.filter(None, 0.0) == []
    assert th.filter([None, "x", 3], 0.0) == []  # type: ignore[list-item]
    empty = Alert(kind=JA, level=Level.WARNING, text="", key="k", t=0.0)
    assert th.filter([empty], 0.0) == []
    gen = (a for a in [A(JA, Level.WARNING, 0.0)])
    assert len(th.filter(gen, 0.0)) == 1
    assert AlertThrottler(min_gap_s=1e9).min_gap_s == alerts.MAX_MIN_GAP_S


def test_gank_sequence_at_8_fps() -> None:
    """Continuous re-emission by the analyser gives WARNING, then DANGER, then silence."""
    th = AlertThrottler()
    said: list[tuple[float, Level]] = []
    for i in range(int(16 * 8)):
        t = i / 8.0
        raw = []
        if 0.0 <= t < 2.0:
            raw.append(A(JA, Level.WARNING, t))
        elif t < 16.0:
            raw.append(A(JA, Level.DANGER, t))
        for a in th.filter(raw, t):
            said.append((t, a.level))
    assert said[0] == (0.0, Level.WARNING)
    assert said[1] == (2.0, Level.DANGER)                          # escalation immediate
    assert [s for s in said if s[0] < 14.0] == said[:2]            # no spam in between
    assert said[2] == (14.0, Level.DANGER)                         # same gank: 12 s
    assert len(said) == 3


def test_memory_stays_bounded() -> None:
    th = AlertThrottler(min_gap_s=0.0, danger_gap_s=0.0)
    for i in range(4000):
        t = i * 0.125
        th.filter([A(JS, Level.INFO, t, who=f"c{i}"), A(RA, Level.WARNING, t, who=f"w{i}")], t)
    keep = int(alerts._KEEP_S) + 10
    assert len(th._by_key) <= 8 * keep and len(th._by_alias) <= 8 * keep
    assert th.pending_count() <= alerts.MAX_PENDING


def test_thread_safety() -> None:
    th = AlertThrottler()
    errors: list[BaseException] = []
    emitted: list[Alert] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        try:
            for i in range(300):
                t = i * 0.05
                out = th.filter([A(RA, Level(i % 3), t, who=f"p{n}-{i % 7}")], t)
                with lock:
                    emitted.extend(out)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors
    assert emitted and all(isinstance(a, Alert) for a in emitted)
    assert not math.isnan(sum(a.t for a in emitted))
