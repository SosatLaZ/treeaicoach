"""« Parler à l'IA » (4.1): a written chat with the coach, next to the WoW Forever page.

The player types a question (Entrée envoie, Maj+Entrée va à la ligne) or clicks a suggestion; the answer comes from
the same WoW context as « Hey coach » (wow/ai_chat.py on wow/ai_context.py), longer and structured since it is
read, with how old the game data is under each answer. The conversation stays on screen for the session and is
kept in ``ai_chat.json`` (never the key); « Effacer » forgets it.

Zero freeze: the game files, the prompt and the provider call run on a worker thread (``app.run_job``); the UI
thread only adds a bubble. While the provider answers (no streaming in the provider client), a « Le coach
réfléchit… » bubble animates. Without a provider or a key, the page says where to set it (Réglages > Conseils IA)
instead of failing; an error is one friendly line with « Réessayer »."""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

TITLE = "Parler à l'IA"
SUBTITLE = "Écris ta question au coach : il répond avec les données de ton jeu (quêtes, trajet, groupe, familier)."
INPUT_HINT = "Entrée pour envoyer · Maj+Entrée pour aller à la ligne"
PLACEHOLDER = "Pose ta question au coach…"
TYPING_TEXT = "Le coach réfléchit"
TYPING_MS = 450
BUBBLE_MAX_RATIO = 0.82          # a bubble never takes more than this share of the conversation width
INPUT_MIN_LINES, INPUT_MAX_LINES = 2, 6

_QT: dict[str, Any] = {}


def _qt_classes() -> dict:
    """The page's widgets (built once, Qt imported lazily)."""
    if _QT:
        return _QT
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: PLC0415

    from treeaicoach import ui_widgets as W  # noqa: PLC0415

    Qt = QtCore.Qt

    class ChatInput(QtWidgets.QPlainTextEdit):
        """Multi-line input: Entrée sends (``submitted``), Maj+Entrée inserts a new line; grows to 6 lines."""

        submitted = QtCore.Signal()

        def __init__(self) -> None:
            super().__init__()
            self.setObjectName("ChatInput")
            self.setTabChangesFocus(True)
            self.setPlaceholderText(PLACEHOLDER)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.document().contentsChanged.connect(self._fit)
            self._fit()

        def keyPressEvent(self, e: Any) -> None:  # noqa: N802
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                if e.modifiers() & Qt.ShiftModifier:
                    self.insertPlainText("\n")
                else:
                    self.submitted.emit()
                e.accept()
                return
            super().keyPressEvent(e)

        def _fit(self) -> None:
            lines = max(INPUT_MIN_LINES, min(INPUT_MAX_LINES, int(self.document().size().height()) or 1))
            m = self.contentsMargins()
            h = lines * self.fontMetrics().lineSpacing() + 2 * int(self.document().documentMargin()) + m.top() \
                + m.bottom() + 14
            if self.height() != h:
                self.setFixedHeight(h)

    class FlowLayout(QtWidgets.QLayout):
        """Items left to right, wrapping to the next line (the suggestion chips at any window width)."""

        def __init__(self, hspace: int = W.SP_2, vspace: int = W.SP_2) -> None:
            super().__init__()
            self._items: list[Any] = []
            self._h, self._v = hspace, vspace
            self.setContentsMargins(0, 0, 0, 0)

        def addItem(self, item: Any) -> None:  # noqa: N802
            self._items.append(item)

        def count(self) -> int:
            return len(self._items)

        def itemAt(self, i: int) -> Any:  # noqa: N802
            return self._items[i] if 0 <= i < len(self._items) else None

        def takeAt(self, i: int) -> Any:  # noqa: N802
            return self._items.pop(i) if 0 <= i < len(self._items) else None

        def expandingDirections(self) -> Any:  # noqa: N802
            return Qt.Orientations(0)

        def hasHeightForWidth(self) -> bool:  # noqa: N802
            return True

        def heightForWidth(self, w: int) -> int:  # noqa: N802
            return self._place(QtCore.QRect(0, 0, w, 0), True)

        def setGeometry(self, rect: Any) -> None:  # noqa: N802
            super().setGeometry(rect)
            self._place(rect, False)

        def sizeHint(self) -> Any:  # noqa: N802
            return self.minimumSize()

        def minimumSize(self) -> Any:  # noqa: N802
            s = QtCore.QSize()
            for it in self._items:
                s = s.expandedTo(it.minimumSize())
            return s

        def _place(self, rect: Any, test: bool) -> int:
            x, y, line_h = rect.x(), rect.y(), 0
            for it in self._items:
                if it.widget() is not None and it.widget().isHidden():
                    continue
                hint = it.sizeHint()
                if x + hint.width() > rect.right() + 1 and line_h > 0:
                    x, y, line_h = rect.x(), y + line_h + self._v, 0
                if not test:
                    it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
                x += hint.width() + self._h
                line_h = max(line_h, hint.height())
            return y + line_h - rect.y()

    class Bubble(QtWidgets.QFrame):
        """One message. ``who``: "me" (right, accent tint), "coach" (left, card), "error" (left, danger tint),
        "typing" (left, animated dots)."""

        def __init__(self, who: str, text: str, fresh: str = "", on_copy: Any = None, on_retry: Any = None,
                     local: str = "") -> None:
            super().__init__()
            self.setObjectName("Bubble")
            self.setProperty("who", "coach" if who == "typing" else who)
            self.who = who
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(W.SP_4, W.SP_3, W.SP_4, W.SP_3)
            lay.setSpacing(W.SP_2)
            if who in ("coach", "error", "typing"):
                lay.addWidget(W.label("Coach TreeAI", "kicker"))
            self.text = W.label(text, None, wrap=True, tone="danger" if who == "error" else None,
                                selectable=who != "typing")
            self.text.setTextFormat(Qt.PlainText)
            if who == "typing":
                self.text.setProperty("role", "secondary")
            lay.addWidget(self.text)
            self.local = None
            if local:
                self.local = W.label("D'après tes données : " + local, "secondary", wrap=True, selectable=True)
                self.local.setTextFormat(Qt.PlainText)
                lay.addWidget(self.local)
            self.fresh = None
            self.copy_btn = None
            self.retry_btn = None
            if who in ("coach", "error") and (fresh or on_copy or on_retry):
                foot = QtWidgets.QHBoxLayout()
                foot.setContentsMargins(0, 0, 0, 0)
                foot.setSpacing(W.SP_3)
                self.fresh = W.label(fresh, "footnote", wrap=True)
                foot.addWidget(self.fresh, 1)
                if on_retry is not None:
                    self.retry_btn = W.button("Réessayer", on_retry, "link", tip="Repose la même question.")
                    foot.addWidget(self.retry_btn, 0, Qt.AlignRight | Qt.AlignTop)
                if on_copy is not None:
                    self.copy_btn = W.button("Copier", on_copy, "link", tip="Copie la réponse.")
                    foot.addWidget(self.copy_btn, 0, Qt.AlignRight | Qt.AlignTop)
                lay.addLayout(foot)
                if not fresh:
                    self.fresh.hide()

    class ChatList(QtWidgets.QWidget):
        """The column of bubbles (newest at the bottom); keeps every bubble under BUBBLE_MAX_RATIO of its width."""

        def __init__(self) -> None:
            super().__init__()
            self.setObjectName("ChatList")
            self.lay = QtWidgets.QVBoxLayout(self)
            self.lay.setContentsMargins(0, W.SP_2, W.SP_2, W.SP_2)
            self.lay.setSpacing(W.SP_3)
            self.lay.addStretch(1)
            self.rows: list[Any] = []

        def add(self, bubble: Any) -> Any:
            row = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(0)
            if bubble.who == "me":
                hl.addStretch(1)
                hl.addWidget(bubble, 0)
            else:
                hl.addWidget(bubble, 0)
                hl.addStretch(1)
            row.bubble = bubble
            self.lay.insertWidget(self.lay.count() - 1, row)
            self.rows.append(row)
            self._fit(bubble)
            return row

        def remove(self, row: Any) -> None:
            if row in self.rows:
                self.rows.remove(row)
            self.lay.removeWidget(row)
            row.hide()
            row.deleteLater()

        def clear(self) -> None:
            for row in list(self.rows):
                self.remove(row)

        def _fit(self, bubble: Any) -> None:
            """Max width = a share of the column; a short message stays on one line (QLabel's word-wrap size
            hint would fold it in two)."""
            w = max(220, int(self.width() * BUBBLE_MAX_RATIO))
            if bubble.maximumWidth() != w:
                bubble.setMaximumWidth(w)
            m = bubble.layout().contentsMargins()
            room = w - m.left() - m.right() - 2
            lbl = bubble.text
            natural = max((lbl.fontMetrics().horizontalAdvance(line) for line in (lbl.text() or " ").split("\n")),
                          default=0) + 4
            want = max(0, min(natural, room))
            if lbl.minimumWidth() != want:
                lbl.setMinimumWidth(want)

        def resizeEvent(self, e: Any) -> None:  # noqa: N802
            for row in self.rows:
                self._fit(row.bubble)
            super().resizeEvent(e)

    _QT.update(ChatInput=ChatInput, FlowLayout=FlowLayout, Bubble=Bubble, ChatList=ChatList, QtGui=QtGui)
    return _QT


class AIChatPage:
    def __init__(self, app: Any) -> None:
        from treeaicoach.wow import ai_chat  # noqa: PLC0415 - light (ai_advisor is imported by ask(), on a worker)

        self.app = app
        self.chat = ai_chat
        W = self.W = app.W
        QtCore, QtWidgets = app.QtCore, app.QtWidgets
        Qt = QtCore.Qt
        qt = _qt_classes()
        self.messages: list[Any] = []           # ai_chat.Message, oldest first (what is on screen)
        self._pending: Any = None               # (request id, question) while the provider answers
        self._req = 0
        self._last_failed: str | None = None
        self._typing_row: Any = None
        self._dots = 0
        self._history_loaded = False
        self.page = W.Page(TITLE, SUBTITLE)
        # the chat fills the page height (the input stays at the bottom): no trailing stretch in the column
        tail = self.page.lay.takeAt(self.page.lay.count() - 1)
        del tail
        self.clear_btn = W.button("Effacer", self.clear, tip="Efface la conversation (sur ce PC aussi).")
        self.page.set_action(self.clear_btn)

        self.fresh = W.label("Lecture des données du jeu…", "footnote", wrap=True)
        head_texts = self.page._head.itemAt(0).layout()      # under the subtitle: how old the game data is
        head_texts.addWidget(self.fresh)

        # -- no provider / no key: where to set it (instead of failing)
        self.notice = W.EmptyState("chat", "L'IA n'est pas configurée", "",
                                   "Ouvrir les réglages", self.open_settings)
        self.notice.hide()

        # -- the conversation
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setObjectName("ChatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(220)
        self.list = qt["ChatList"]()
        self.scroll.setWidget(self.list)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._stick_bottom)
        self.empty = W.EmptyState("chat", "Pose ta première question",
                                  "Le coach lit ton perso, tes quêtes, ton trajet et ton groupe dans les sauvegardes "
                                  "de l'addon (après un /reload en jeu).")
        self.list.lay.insertWidget(0, self.notice)          # in the conversation: no extra page height
        self.list.lay.insertWidget(1, self.empty)
        self.page.lay.addWidget(self.scroll, 1)

        # -- composer: suggestions, input card, footnote (tighter than the page's section gap)
        composer = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(composer)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(W.SP_3)
        chips = QtWidgets.QWidget()
        flow = qt["FlowLayout"]()
        chips.setLayout(flow)
        self.chips: list[Any] = []
        for text in ai_chat.SUGGESTIONS:
            b = W.button(text, lambda t=text: self.send(t), "chip", tip="Envoie cette question.")
            flow.addWidget(b)
            self.chips.append(b)
        self.chips_box = chips
        cl.addWidget(chips)

        # -- input card
        card = W.Card(pad=W.SP_4, spacing=W.SP_2)
        self.input = qt["ChatInput"]()
        self.input.submitted.connect(lambda: self.send())
        card.add(self.input)
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(W.SP_3)
        self.hint = W.label(INPUT_HINT, "footnote")
        bar.addWidget(self.hint, 1, Qt.AlignVCenter)
        self.send_btn = W.button("Envoyer", lambda: self.send(), "primary", tip="Envoie ta question (Entrée).")
        bar.addWidget(self.send_btn, 0, Qt.AlignRight)
        card.add(bar)
        self.input_card = card
        cl.addWidget(card)
        self.foot = W.label("La conversation reste sur ce PC (jamais ta clé). Chaque question compte dans les "
                            "requêtes IA du jour.", "footnote", wrap=True)
        self.foot.setContentsMargins(W.SP_1, 0, W.SP_1, 0)
        cl.addWidget(self.foot)
        self.page.lay.addWidget(composer)

        self._typing_timer = QtCore.QTimer(self.page)
        self._typing_timer.timeout.connect(self._tick_typing)
        self._sync_ready()
        app.run_job(ai_chat.load_history, self._on_history, None, name="TreeAI-ui-chat-history")

    # ------------------------------------------------------------------ lifecycle
    def on_show(self) -> None:
        self._sync_ready()
        self.refresh_freshness()
        if self.input.isEnabled():
            self.input.setFocus()

    def on_config(self, diff: set[str]) -> None:
        if diff & {"ai_provider", "ai_api_key", "ai_model"}:
            self._sync_ready()

    def on_close(self) -> None:
        self._typing_timer.stop()

    def refresh_freshness(self) -> None:
        engine = getattr(self.app, "engine", None)
        self.app.run_job(lambda: self.chat.data_freshness(engine), self._set_freshness, None,
                         name="TreeAI-ui-chat-fresh")

    def _set_freshness(self, text: str) -> None:
        self.fresh.setText(str(text or ""))

    # ------------------------------------------------------------------ state
    def ready(self) -> bool:
        return self.chat.ai_status(self.app.cfg)[0]

    def _sync_ready(self) -> None:
        note = self.chat.notice(self.app.cfg)
        ok = note is None
        if note is not None:
            self.notice.set_text(*note)
        self.notice.setVisible(not ok)
        self._sync_empty()
        busy = self._pending is not None
        self.input.setEnabled(ok)
        self.send_btn.setEnabled(ok and not busy)
        for b in self.chips:
            b.setEnabled(ok and not busy)
        self.input.setPlaceholderText(PLACEHOLDER if ok else "Configure d'abord l'IA dans Réglages.")

    def open_settings(self) -> None:
        self.app.show_page("settings", tab="IA")

    # ------------------------------------------------------------------ history
    def _on_history(self, msgs: Any) -> None:
        if self._history_loaded:
            return
        self._history_loaded = True
        if self.messages or not msgs:        # the player already started: keep the new conversation
            return
        for m in msgs:
            self.messages.append(m)
            self._add_bubble(m)
        self._sync_empty()

    def _save(self) -> None:
        msgs = list(self.messages)
        secret = self.chat.ai_settings(self.app.cfg)[1]
        self.app.run_job(lambda: self.chat.save_history(msgs, secret=secret), None, None,
                         name="TreeAI-ui-chat-save")

    # ------------------------------------------------------------------ send / answer
    def send(self, text: str | None = None) -> bool:
        """Send ``text`` (a chip) or the input's text. False when nothing was sent (empty, busy, no AI)."""
        from_input = text is None
        q = (self.input.toPlainText() if from_input else str(text)).strip()
        if not q or self._pending is not None:
            return False
        if not self.ready():
            self._sync_ready()
            return False
        q = q[:self.chat.QUESTION_MAX_CHARS]
        if from_input:
            self.input.clear()
        msg = self.chat.Message("me", q)
        self.messages.append(msg)
        self._add_bubble(msg)
        self._ask(q)
        return True

    def retry(self) -> None:
        """« Réessayer »: the failed question again (its error bubble goes away)."""
        if self._pending is not None or not self._last_failed:
            return
        q = self._last_failed
        rows = self.list.rows
        if rows and rows[-1].bubble.who == "error":
            self.list.remove(rows[-1])
        self._ask(q)

    def _ask(self, q: str) -> None:
        self._req += 1
        rid = self._req
        self._pending = (rid, q)
        self._last_failed = None
        history = [m for m in self.messages if m.role in ("me", "coach")]
        if history and history[-1].role == "me" and history[-1].text == q:
            history = history[:-1]           # the question itself goes in the prompt once, at the end
        cfg = self.app.cfg
        engine = getattr(self.app, "engine", None)
        self._show_typing()
        self._sync_ready()
        self._sync_empty()
        self.app.run_job(lambda: self.chat.ask(cfg, q, history, engine),
                         lambda r: self._on_reply(rid, q, r),
                         lambda e: self._on_failed(rid, q, e), name="TreeAI-ui-chat-ask")

    def _on_reply(self, rid: int, q: str, reply: Any) -> None:
        if self._pending is None or self._pending[0] != rid:
            return                           # cleared meanwhile: the answer is dropped
        self._pending = None
        self._hide_typing()
        if reply.fresh:
            self.fresh.setText(reply.fresh)
        if reply.ok:
            msg = self.chat.Message("coach", reply.text, fresh=reply.fresh)
            self.messages.append(msg)
            self._add_bubble(msg)
            self._save()
        else:
            self._last_failed = q if reply.retry else None
            self._add_error(reply.text, reply.fresh, reply.retry, reply.local)
            if reply.error in ("noai", "nokey"):
                self._sync_ready()
            self._save()                     # the question stays (its answer did not come)
        self._sync_ready()

    def _on_failed(self, rid: int, q: str, _exc: BaseException) -> None:
        from treeaicoach.wow.ai_chat import MSG_DOWN, Reply  # noqa: PLC0415

        self._on_reply(rid, q, Reply(False, MSG_DOWN, error="error"))

    def clear(self) -> None:
        """« Effacer »: the conversation goes (screen and file); an answer on its way is dropped."""
        self._pending = None
        self._last_failed = None
        self._hide_typing()
        self.list.clear()
        self.messages.clear()
        self._history_loaded = True
        self._save()
        self._sync_empty()
        self._sync_ready()

    def copy(self, text: str) -> None:
        self.app.copy_text(text)
        self.app.show_toast("Réponse copiée.")

    # ------------------------------------------------------------------ bubbles
    def _add_bubble(self, m: Any) -> Any:
        Bubble = _qt_classes()["Bubble"]
        if m.role == "me":
            b = Bubble("me", m.text)
        else:
            b = Bubble("coach", m.text, m.fresh, on_copy=lambda t=m.text: self.copy(t))
        row = self.list.add(b)
        self._sync_empty()
        self._scroll_bottom()
        return row

    def _add_error(self, text: str, fresh: str, retry: bool, local: str = "") -> Any:
        Bubble = _qt_classes()["Bubble"]
        b = Bubble("error", text, fresh, on_retry=self.retry if retry else None, local=local)
        row = self.list.add(b)
        if not retry and b.fresh is not None:
            b.fresh.hide()
        self._scroll_bottom()
        return row

    def _show_typing(self) -> None:
        self._hide_typing()
        Bubble = _qt_classes()["Bubble"]
        self._dots = 0
        self._typing_row = self.list.add(Bubble("typing", TYPING_TEXT + "…"))
        self._typing_timer.start(TYPING_MS)
        self._scroll_bottom()

    def _hide_typing(self) -> None:
        self._typing_timer.stop()
        if self._typing_row is not None:
            self.list.remove(self._typing_row)
            self._typing_row = None

    def _tick_typing(self) -> None:
        if self._typing_row is None:
            self._typing_timer.stop()
            return
        self._dots = (self._dots + 1) % 3
        self._typing_row.bubble.text.setText(TYPING_TEXT + "." * (self._dots + 1))

    def typing(self) -> bool:
        return self._typing_row is not None

    def _sync_empty(self) -> None:
        self.empty.setVisible(not self.list.rows and self.notice.isHidden())

    def _scroll_bottom(self) -> None:
        self._stick = time.monotonic() + 1.0
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _stick_bottom(self, _lo: int, hi: int) -> None:
        if time.monotonic() <= getattr(self, "_stick", 0.0):
            self.scroll.verticalScrollBar().setValue(hi)

    # ------------------------------------------------------------------ tests / diagnostics
    def bubbles(self) -> list[tuple[str, str]]:
        """[(who, text)] of the conversation on screen."""
        return [(r.bubble.who, r.bubble.text.text()) for r in self.list.rows]


def build(app: Any) -> AIChatPage:
    return AIChatPage(app)
