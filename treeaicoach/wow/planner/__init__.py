"""Quest route planner (app side): reads TreeAIDB.export, computes 3–4 routes, writes the TreeAI_Plan addon.

Contracts: ``SCHEMA.md`` next to this file. Entry point for the UI: :func:`compute_and_write` (blocking:
run it in a worker thread, it takes about ``seconds``).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def inputs_from_db(db: dict | None) -> tuple[dict | None, dict | None, dict | None]:
    """(export, learn, char_learn) from a parsed TreeAIDB. Never raises."""
    if not isinstance(db, dict):
        return None, None, None
    exp = db.get("export") if isinstance(db.get("export"), dict) else None
    learn = db.get("learn") if isinstance(db.get("learn"), dict) else None
    cl = None
    try:
        chars = db.get("chars") if isinstance(db.get("chars"), dict) else {}
        if exp is not None and isinstance(chars.get(exp.get("char")), dict):
            cl = chars[exp.get("char")].get("learn")
    except Exception:
        cl = None
    return exp, learn, cl if isinstance(cl, dict) else None


def compute_and_write(inst: Any, seconds: float = 16.0, db: dict | None = None) -> dict:
    """Compute the routes from the newest TreeAI SavedVariables of ``inst`` (a WowInstall) and write the plan
    addon. Returns {ok, message, plan}. Never raises."""
    try:
        from treeaicoach.wow import install, savedvars  # noqa: PLC0415
        from treeaicoach.wow.planner import routes, writer  # noqa: PLC0415

        if inst is None:
            return {"ok": False, "message": "WoW introuvable : choisis le dossier dans la page WoW.", "plan": None}
        if db is None:
            files = install.savedvars_files(inst)
            db = savedvars.read_db(files[0]) if files else {}
        exp, learn, cl = inputs_from_db(db)
        if exp is None:
            return {"ok": False, "plan": None,
                    "message": "Pas encore de données de quêtes : en jeu, tape /reload (l'addon TreeAI les "
                               "exporte), puis recalcule."}
        if str(exp.get("char") or "?") in ("", "?") or not isinstance(exp.get("player"), dict):
            # (QUESTQA) never a plan the addon could take for another character / a game state we do not know
            return {"ok": False, "plan": None,
                    "message": "Données de quêtes incomplètes : en jeu, tape /reload, puis recalcule."}
        plan = routes.compute_plan(exp, learn, cl, seconds=seconds)
        if not plan or not plan.get("routes"):
            return {"ok": False, "plan": plan, "message": "Aucun trajet possible avec ces quêtes."}
        ok, msg = writer.write_plan(inst.addons_dir, plan)
        return {"ok": ok, "message": msg, "plan": plan}
    except Exception as exc:
        log.debug("planner failed", exc_info=True)
        return {"ok": False, "message": f"Calcul impossible : {type(exc).__name__}.", "plan": None}
