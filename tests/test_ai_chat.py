"""« Parler à l'IA » (4.1): the written chat page (ui_page_aichat.py) and its WoW context (wow/ai_chat.py).

Offscreen Qt; the provider is always a fake (``ai_advisor.call_llm`` patched): no network. Invented names only."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

import test_launcher as tl  # noqa: E402

from treeaicoach import ai_advisor  # noqa: E402
from treeaicoach import ui_common as ui  # noqa: E402
from treeaicoach.wow import ai_chat  # noqa: E402

home = tl.home
KEY = "sk-TEST-very-secret-4242"
ANSWER = "Va au Camp Factice rendre tes quêtes.\n- Rends « Quête 0 »\n- Puis prends la suivante\nEnsuite, camp."


def _summary(now: float, age: float = 180.0, **extra: object) -> dict:
    s = {"class": "HUNTER", "className": "Chasseur", "level": 30, "zone": "Zone Test", "subzone": "Camp Factice",
         "money": 123456, "bagsFree": 6, "bagsTotal": 64, "updated": now - age,
         "quests": {"count": 2, "complete": 1, "updated": now - age,
                    "list": [{"id": 1000, "title": "Quête 0", "level": 29, "done": 1, "total": 1, "complete": True,
                              "zone": "Zone Test"},
                             {"id": 1001, "title": "Quête 1", "level": 30, "done": 0, "total": 8,
                              "zone": "Zone Test"}]}}
    s.update(extra)
    return {"chars": {"Testeur-Royaume": {"summary": s}}}


class FakeLLM:
    """Records every call; answers ``ANSWER`` (or raises ``exc``); can block until released (freeze test)."""

    def __init__(self, answer: str = ANSWER, exc: BaseException | None = None, block: bool = False) -> None:
        self.answer, self.exc = answer, exc
        self.calls: list[dict] = []
        self.gate = threading.Event()
        if not block:
            self.gate.set()

    def __call__(self, provider: str, api_key: str, model: str, system: str, prompt: str, **kw: object) -> str:
        self.calls.append({"provider": provider, "key": api_key, "system": system, "prompt": prompt, **kw})
        self.gate.wait(10)
        if self.exc is not None:
            raise self.exc
        return self.answer


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    f = FakeLLM()
    monkeypatch.setattr(ai_advisor, "call_llm", f)
    monkeypatch.setattr(ai_chat, "data_freshness", lambda engine=None: "Données lues il y a 3 min : fais /reload "
                                                                       "en jeu pour les mettre à jour.")
    return f


def _app(home: Path, provider: str = "groq", key: str = KEY) -> tuple:
    app, _v, _e = tl._build(home, start_backend=False)
    app.set_options(ai_provider=provider, ai_api_key=key)
    app.show_page("aichat")
    return app, app.page_views["aichat"]


def _wait(app: object, cond: object, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        tl._pump(app, 0.02)
        if cond():
            return True
    return False


# ------------------------------------------------------------------------------------------ pure
def test_navigation_has_the_chat_page_next_to_wow() -> None:
    assert ("aichat", "Parler à l'IA", "chat") in ui.PAGES
    assert dict(ui.NAV_GROUPS)["World of Warcraft"] == ("wow", "aichat")
    assert "aichat" in ui.PREBUILD_ORDER and ui.SETTINGS_ANCHORS["IA"] == "ai"
    assert len(ai_chat.SUGGESTIONS) == 6 and "Où je vais maintenant ?" in ai_chat.SUGGESTIONS


def test_ai_status_without_provider_or_key() -> None:
    assert ai_chat.ai_status(SimpleNamespace(ai_provider="off"))[0] is False
    assert ai_chat.notice(SimpleNamespace(ai_provider="off"))[0] == "L'IA n'est pas configurée"
    assert "Réglages > Conseils IA" in ai_chat.notice(SimpleNamespace(ai_provider="off"))[1]
    assert ai_chat.notice(SimpleNamespace(ai_provider="groq", ai_api_key=" "))[0] == "Il manque ta clé API"
    assert ai_chat.ai_status(SimpleNamespace(ai_provider="ollama", ai_api_key=""))[0] is True
    assert ai_chat.notice(SimpleNamespace(ai_provider="gemini", ai_api_key=KEY)) is None


def test_no_key_never_calls_the_provider(fake: FakeLLM) -> None:
    r = ai_chat.ask(SimpleNamespace(ai_provider="off"), "Où je vais ?", db={})
    assert not r.ok and r.error == "noai" and not r.retry and not fake.calls
    r = ai_chat.ask(SimpleNamespace(ai_provider="groq", ai_api_key=""), "Où je vais ?", db={})
    assert r.error == "nokey" and "clé" in r.text and not fake.calls


def test_freshness_line() -> None:
    assert ai_chat.freshness_line(180) == "Données lues il y a 3 min : fais /reload en jeu pour les mettre à jour."
    assert ai_chat.freshness_line(30).startswith("Données lues à l'instant")
    assert "/reload" in ai_chat.freshness_line(None)
    assert ai_chat.freshness_line(5, has_data=False).startswith("Aucune donnée de jeu lue")
    assert ai_chat.freshness_line(200, live=True).endswith("XP, niveau et zone en direct.")


def test_answer_uses_the_wow_context_with_its_freshness(fake: FakeLLM) -> None:
    now = time.time()
    cfg = SimpleNamespace(ai_provider="groq", ai_api_key=KEY, ai_model="")
    r = ai_chat.ask(cfg, "Rends mes quêtes au plus vite", db=_summary(now), live={}, plan={}, facts=[], now=now)
    assert r.ok and r.text.startswith("Va au Camp Factice") and "• Rends « Quête 0 »" in r.text
    assert r.fresh == "Données lues il y a 3 min : fais /reload en jeu pour les mettre à jour."
    call = fake.calls[0]
    assert call["system"] == ai_chat.CHAT_PROMPT and call["long"] is True and call["max_tokens"] >= 500
    assert "Question : Rends mes quêtes au plus vite" in call["prompt"] and "Quête 0" in call["prompt"]
    assert "Testeur" not in call["prompt"]                  # player names never sent
    assert "tutoiement" in ai_chat.CHAT_PROMPT and "« - »" in ai_chat.CHAT_PROMPT


def test_profession_question_gets_the_metiers_block(fake: FakeLLM) -> None:
    now = time.time()
    prof = {"profs": [{"line": 197, "name": "Couture", "rank": 112, "max": 150}], "updated": now - 180,
            "plans": {"Couture": [{"item": "Chemise en lin", "times": 5}]}}
    cfg = SimpleNamespace(ai_provider="groq", ai_api_key=KEY, ai_model="")
    ai_chat.ask(cfg, "Comment monter mon métier ?", db=_summary(now, prof=prof), live={}, plan={}, facts=[], now=now)
    prompt = fake.calls[0]["prompt"]
    assert '"metiers"' in prompt and "Couture" in prompt and "112" in prompt


def test_multi_turn_prompt_keeps_the_last_exchanges() -> None:
    hist = []
    for i in range(6):
        hist += [ai_chat.Message("me", f"question {i}"), ai_chat.Message("coach", f"réponse {i}")]
    hist.append(ai_chat.Message("me", "sans réponse"))
    kept = ai_chat.recent_exchanges(hist)
    assert len(kept) == 2 * ai_chat.HISTORY_TURNS and kept[0].text == f"question {6 - ai_chat.HISTORY_TURNS}"
    prompt = ai_chat.build_chat_prompt("Et après ?", {"fraicheur": "x"}, hist)
    i_q, i_a = prompt.index("Joueur : question 5"), prompt.index("Coach : réponse 5")
    assert i_q < i_a < prompt.index("Question : Et après ?")
    assert "question 0" not in prompt and "sans réponse" not in prompt
    # a follow-up without a subject borrows the previous question's subject (its JSON sections)
    q = ai_chat.context_question("Et pourquoi ?", [ai_chat.Message("me", "Quelle quête prendre ?")])
    assert "Quelle quête prendre" in q
    assert ai_chat.context_question("Mon familier a faim ?", hist) == "Mon familier a faim ?"


def test_format_answer() -> None:
    out = ai_chat.format_answer("**Va** au camp.\n\n* rends A\n* rends B\n## Ensuite\nCamp.<think>x</think>")
    assert out == "Va au camp.\n\n• rends A\n• rends B\n\nEnsuite\n\nCamp."


def test_history_file_round_trip_without_secret(tmp_path: Path) -> None:
    p = tmp_path / "ai_chat.json"
    msgs = [ai_chat.Message("me", "Où je vais ?"), ai_chat.Message("coach", f"Va là. {KEY}", fresh="Données lues")]
    assert ai_chat.save_history(msgs, p, secret=KEY)
    raw = p.read_text(encoding="utf-8")
    assert KEY not in raw and set(json.loads(raw)["messages"][1]) == {"role", "text", "t", "fresh"}
    back = ai_chat.load_history(p)
    assert [(m.role, m.text[:6]) for m in back] == [("me", "Où je "), ("coach", "Va là.")]
    assert ai_chat.save_history([], p) and not p.exists()
    p.write_text("{pas du json", encoding="utf-8")
    assert ai_chat.load_history(p) == []


# ------------------------------------------------------------------------------------------ the page
def test_page_builds_and_sends(home: Path, fake: FakeLLM) -> None:
    app, view = _app(home)
    try:
        assert not app._failed_pages and app.sidebar.buttons["aichat"].text() == "Parler à l'IA"
        assert view.page.title.text() == "Parler à l'IA" and view.notice.isHidden()
        assert [b.text() for b in view.chips] == list(ai_chat.SUGGESTIONS)
        assert _wait(app, lambda: "il y a 3 min" in view.fresh.text())
        view.input.setPlainText("Où je vais ?")
        assert view.send() is True
        assert view.input.toPlainText() == "" and view.typing() and not view.send_btn.isEnabled()
        assert _wait(app, lambda: not view.typing())
        assert view.bubbles()[0] == ("me", "Où je vais ?")
        who, text = view.bubbles()[1]
        assert who == "coach" and text.startswith("Va au Camp Factice") and "\n\n• Rends" in text
        bubble = view.list.rows[1].bubble
        assert bubble.copy_btn is not None and "/reload" in bubble.fresh.text()
        bubble.copy_btn.click()
        assert app.qapp.clipboard().text() == text
        assert view.send_btn.isEnabled() and fake.calls[0]["key"] == KEY
    finally:
        app.close()


def test_enter_sends_and_shift_enter_breaks_the_line(home: Path, fake: FakeLLM) -> None:
    from PySide6 import QtCore
    from PySide6.QtTest import QTest

    app, view = _app(home)
    try:
        QTest.keyClicks(view.input, "Ligne 1")
        QTest.keyClick(view.input, QtCore.Qt.Key_Return, QtCore.Qt.ShiftModifier)
        QTest.keyClicks(view.input, "Ligne 2")
        assert view.input.toPlainText() == "Ligne 1\nLigne 2" and not view.list.rows
        QTest.keyClick(view.input, QtCore.Qt.Key_Return)
        assert view.bubbles()[0] == ("me", "Ligne 1\nLigne 2") and view.input.toPlainText() == ""
        assert _wait(app, lambda: not view.typing())
        QTest.keyClick(view.input, QtCore.Qt.Key_Enter)          # keypad Entrée, empty input: nothing sent
        assert len(fake.calls) == 1
    finally:
        app.close()


def test_chip_sends_its_question(home: Path, fake: FakeLLM) -> None:
    app, view = _app(home)
    try:
        view.chips[5].click()
        assert view.bubbles()[0] == ("me", "Meilleur familier à apprivoiser ici ?")
        assert all(not b.isEnabled() for b in view.chips)        # one question at a time
        view.chips[0].click()
        assert len([b for b in view.bubbles() if b[0] == "me"]) == 1
        assert _wait(app, lambda: not view.typing())
        assert "Question : Meilleur familier à apprivoiser ici ?" in fake.calls[0]["prompt"]
        assert all(b.isEnabled() for b in view.chips)
    finally:
        app.close()


def test_no_key_shows_where_to_set_it(home: Path, fake: FakeLLM) -> None:
    app, view = _app(home, provider="off", key="")
    try:
        assert not view.notice.isHidden() and view.notice.title.text() == "L'IA n'est pas configurée"
        assert not view.input.isEnabled() and not view.send_btn.isEnabled()
        assert view.send("Où je vais ?") is False and not view.list.rows and not fake.calls
        app.set_options(ai_provider="groq")
        assert view.notice.title.text() == "Il manque ta clé API" and not view.input.isEnabled()
        view.notice.button.click()
        assert app._current_page == "settings"
        app.set_options(ai_api_key=KEY)
        app.show_page("aichat")
        assert view.notice.isHidden() and view.input.isEnabled() and view.send_btn.isEnabled()
    finally:
        app.close()


def test_error_is_one_friendly_line_with_retry(home: Path, fake: FakeLLM) -> None:
    app, view = _app(home)
    try:
        fake.exc = ai_advisor.AIError("offline", "URLError")
        view.send("Quelle quête prendre ?")
        assert _wait(app, lambda: not view.typing())
        who, text = view.bubbles()[-1]
        assert who == "error" and text == ai_chat.ERROR_LINES["offline"]
        err = view.list.rows[-1].bubble
        assert err.retry_btn is not None and err.copy_btn is None
        fake.exc = None
        err.retry_btn.click()
        assert _wait(app, lambda: not view.typing() and view.bubbles()[-1][0] == "coach")
        assert [w for w, _t in view.bubbles()] == ["me", "coach"] and len(fake.calls) == 2
        assert fake.calls[1]["prompt"].count("Quelle quête prendre ?") == 1
        fake.exc = ai_advisor.AIError("key", "HTTP 401")         # a refused key: no « Réessayer », go fix it
        view.send("Et le groupe ?")
        assert _wait(app, lambda: not view.typing())
        assert view.list.rows[-1].bubble.retry_btn is None and "clé" in view.bubbles()[-1][1]
    finally:
        app.close()


def test_multi_turn_in_the_page(home: Path, fake: FakeLLM) -> None:
    app, view = _app(home)
    try:
        view.send("Quelle quête prendre ?")
        assert _wait(app, lambda: not view.typing())
        view.send("Et après ?")
        assert _wait(app, lambda: not view.typing() and len(fake.calls) == 2)
        p = fake.calls[1]["prompt"]
        assert "Joueur : Quelle quête prendre ?" in p and "Coach : Va au Camp Factice" in p
        assert "Question : Et après ?" in p
        assert "Joueur : Et après ?" not in p                      # the question itself only once
    finally:
        app.close()


def test_worker_thread_keeps_the_ui_responsive(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slow = FakeLLM(block=True)
    monkeypatch.setattr(ai_advisor, "call_llm", slow)
    app, view = _app(home)
    try:
        t0 = time.perf_counter()
        view.send("Où je vais ?")
        assert time.perf_counter() - t0 < 0.5                     # send() returns at once
        assert _wait(app, lambda: bool(slow.calls))                 # the provider is now « thinking »
        seen = set()
        end = time.monotonic() + 1.5
        while time.monotonic() < end:                               # the UI thread keeps turning meanwhile
            tl._pump(app, 0.05)
            seen.add(view.list.rows[-1].bubble.text.text())
        assert view.typing() and len(seen) >= 2                     # the « réfléchit… » dots animate
        assert view.input.isEnabled()
        view.input.setPlainText("je tape pendant ce temps")
        assert view.input.toPlainText() == "je tape pendant ce temps"
        assert slow.calls and threading.current_thread() is threading.main_thread()
        slow.gate.set()
        assert _wait(app, lambda: not view.typing())
        assert view.bubbles()[-1][0] == "coach" and view.input.toPlainText() == "je tape pendant ce temps"
    finally:
        slow.gate.set()
        app.close()


def test_clear_forgets_and_drops_a_late_answer(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slow = FakeLLM(block=True)
    monkeypatch.setattr(ai_advisor, "call_llm", slow)
    app, view = _app(home)
    try:
        view.send("Où je vais ?")
        assert _wait(app, lambda: bool(slow.calls))
        view.clear_btn.click()
        assert not view.list.rows and not view.messages and not view.empty.isHidden()
        slow.gate.set()
        tl._pump(app, 0.5)
        assert not view.list.rows                                   # the late answer is dropped
    finally:
        slow.gate.set()
        app.close()


def test_history_is_kept_across_restarts(home: Path, fake: FakeLLM) -> None:
    app, view = _app(home)
    try:
        view.send("Quelle quête prendre ?")
        assert _wait(app, lambda: not view.typing())
        assert _wait(app, lambda: ai_chat.history_path().is_file())
    finally:
        app.close()
    app, view = _app(home)
    try:
        assert _wait(app, lambda: len(view.list.rows) == 2)
        assert [w for w, _t in view.bubbles()] == ["me", "coach"]
        view.clear()
        assert _wait(app, lambda: not ai_chat.history_path().exists())
    finally:
        app.close()


def test_key_never_in_logs_screen_or_file(home: Path, fake: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    app, view = _app(home)
    try:
        for exc in (RuntimeError(f"boom with {KEY}"), ai_advisor.AIError("key", "HTTP 401"), None):
            fake.exc = exc
            fake.answer = f"Voilà ({KEY})"
            view.send("Où je vais ?")
            assert _wait(app, lambda: not view.typing())
        assert len(fake.calls) == 3 and all(c["key"] == KEY for c in fake.calls)
        assert all(KEY not in c["prompt"] for c in fake.calls)
        assert KEY not in caplog.text
        assert all(KEY not in t for _w, t in view.bubbles())
        assert _wait(app, lambda: ai_chat.history_path().is_file())
        tl._pump(app, 0.3)
        assert KEY not in ai_chat.history_path().read_text(encoding="utf-8")
    finally:
        app.close()
    assert KEY not in caplog.text


def test_wow_page_has_a_button_to_the_chat(home: Path, fake: FakeLLM) -> None:
    app, _view = _app(home)
    try:
        app.show_page("wow")
        wow = app.page_views["wow"]
        assert wow.ask_btn.text() == "Demander à l'IA"
        wow.ask_btn.click()
        assert app._current_page == "aichat" and app.sidebar.buttons["aichat"].isChecked()
    finally:
        app.close()
