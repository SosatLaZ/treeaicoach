"""Game end: record + post-game report job, spoken summary, session break reminder, LCU truth.

Mixin of :class:`treeaicoach.engine.CoachEngine` (split out of ``engine.py`` without any
behaviour change): the methods use the engine's state (``self._lock``, ``self._cfg`` ...),
created in ``CoachEngine.__init__``. Not meant to be used on its own.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from treeaicoach.engine_base import (
    BREAK_LOSS_STREAK,
    BREAK_TEXT,
    MSG_WAITING,
    EngineState,
)

log = logging.getLogger("treeaicoach.engine")   # same logger as before the split


class PostgameMixin:
    """Game end: record + post-game report job, spoken summary, session break reminder, LCU truth."""

    def _end_game(self, result: str | None, t: float) -> None:
        """Game over: finish the record + report in the background, session statistics."""
        log.info("Game over (result: %s)", result or "inconnu")
        self._ended = True
        self._in_game = False
        self._game_evt.clear()
        self._death_due = None
        self._state, self._message = EngineState.WAITING_GAME, MSG_WAITING
        rec, self._recorder = self._recorder, None
        if result in ("Win", "Lose") and not self._demo:
            s = self._session
            s["games"] += 1
            if result == "Win":
                s["wins"] += 1
                s["loss_streak"] = 0
            else:
                s["losses"] += 1
                s["loss_streak"] += 1
                if s["loss_streak"] >= BREAK_LOSS_STREAK and self._cfg.break_reminder:
                    self._banner = BREAK_TEXT
                    self._say(BREAK_TEXT, 0)
        plays_summary = self.plays_summary()
        health = None
        sc = getattr(self, "_selfcheck", None)
        if sc is not None and sc.enabled:      # "Santé TreeAI" of this game (what went wrong / was fixed)
            health = sc.game_report()
        mm_block = None
        mm = getattr(self, "_mastermind", None)
        if mm is not None:                     # the game model (mastermind.py): plan, windows, calls
            try:
                mm_block = mm.summary()
            except Exception:
                log.debug("mastermind summary failed", exc_info=True)
        if rec is not None:
            th = threading.Thread(target=self._finish_job, args=(rec, plays_summary, health, mm_block),
                                  name="TreeAICoach-report", daemon=True)
            self._bg_threads = [b for b in self._bg_threads if b.is_alive()] + [th]
            th.start()

    def _say_game_summary(self, record_path: Path) -> None:
        """Speak the short end-of-game summary (analysis.spoken_summary). Never raises."""
        try:
            if not getattr(self._cfg, "post_game_summary", True):
                return
            import json

            from treeaicoach.analysis import analyze_game, spoken_summary

            data = json.loads(Path(record_path).read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("meta"), dict):
                text = spoken_summary(analyze_game(data))
                if text:
                    self._say(text, 0)
        except Exception:
            log.debug("End-of-game summary unavailable", exc_info=True)

    def _finish_job(self, rec: Any, plays_summary: dict | None = None, health: dict | None = None,
                    mm_block: dict | None = None) -> None:
        try:
            path = rec.finish()
            if path is None:
                return
            if plays_summary:                   # play ratings + "précision" (plays.py) in the record
                from treeaicoach.plays import attach_to_record

                attach_to_record(path, plays_summary)
            if health:                          # self-check of the game (selfcheck.py) -> report section
                from treeaicoach.selfcheck import attach_to_record as attach_health

                attach_health(path, health)
            if mm_block:                        # game understanding (mastermind.py) -> report section
                from treeaicoach.mastermind import attach_to_record as attach_mm

                attach_mm(path, mm_block)
            self.last_record_path = Path(path)
            cfg = self._cfg
            self._say_game_summary(Path(path))
            lcu = self._postgame_lcu()          # League Client found: its timeline comes after the report
            if not cfg.post_game_report:
                if lcu is not None:
                    self._lcu_truth_job(lcu, Path(path), None)
                return
            writer = self._report_writer
            if writer is None:
                from treeaicoach.report import write_report

                def writer(p: Path, _pending: bool = lcu is not None) -> Path | None:
                    return write_report(p, lcu_pending=_pending)
            html = writer(Path(path))
            if html is None:
                return
            self.last_report_path = Path(html)
            log.info("Post-game report: %s", html)
            prev_review = getattr(self, "last_ai_review", None)
            self._ai_postgame_review(Path(path), Path(html))
            if cfg.open_report_automatically:
                self._report_opener(Path(html))
            if lcu is not None:
                review = getattr(self, "last_ai_review", None)
                self._lcu_truth_job(lcu, Path(path), Path(html), review if review is not prev_review else None)
        except Exception:
            log.exception("Post-game record / report failed")

    # ------------------------------------------------------------------ League Client (lcu.py)
    def _postgame_lcu(self) -> Any:
        """The League Client API client when enabled and the client is running, else None. Never raises."""
        try:
            if self._demo or not getattr(self._cfg, "lcu_enabled", True):
                return None
            client = getattr(self, "_lcu", None)
            if client is None:
                from treeaicoach.lcu import get_default_client

                client = self._lcu = get_default_client()
            return client if client.available() else None
        except Exception:
            log.debug("League Client unavailable", exc_info=True)
            return None

    def _lcu_truth_job(self, client: Any, record_path: Path, html: Path | None, review: str | None = None) -> None:
        """Wait for the client's match timeline (~2 min max), save the ground truth next to the
        record, then rewrite the report with it (the pending page reloads itself). Never raises."""
        truth = None
        try:
            import json

            from treeaicoach import ground_truth
            from treeaicoach.lcu import fetch_postgame_truth

            record = json.loads(Path(record_path).read_text(encoding="utf-8"))
            truth = fetch_postgame_truth(record, client, cancel=self._stop_evt,
                                         timeout_s=getattr(self, "_lcu_timeout_s", 120.0),
                                         poll_s=getattr(self, "_lcu_poll_s", 8.0))
            if truth is not None:
                truth["record"] = Path(record_path).name
                try:
                    from treeaicoach.analysis import analyze_game

                    t = analyze_game(record, truth=truth).get("truth") or {}
                    truth["score"] = ground_truth.compact_score(t.get("reliability"), record)
                except Exception:
                    log.exception("Cannot score the alerts against the client timeline")
                self.last_truth_path = ground_truth.save_truth(record_path, truth)
        except Exception:
            log.exception("League Client post-game data failed")
        if html is None:
            return
        try:   # final page (with the truth, or without the "pending" banner)
            writer = self._report_writer
            if writer is None:
                from treeaicoach.report import write_report as writer
            out = writer(Path(record_path))
            if out is not None and review:
                from treeaicoach.ai_advisor import append_review_html

                append_review_html(out, review, str(self._cfg.ai_provider))
            if out is not None and truth is not None:
                log.info("Post-game report updated with the League Client data: %s", out)
        except Exception:
            log.exception("Report update with the League Client data failed")
