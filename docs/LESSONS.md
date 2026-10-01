# Lessons learned (read before changing anything)

Hard rules distilled from real user feedback. Each one cost a bad release.

## Process
1. **Never build mid-edit.** Build from a clean snapshot (git worktree of a commit), run
   `--selftest` and `--ui-smoke` on the built exe before publishing.
2. **Synthetic tests are not reality.** Every detection or overlay change is checked on the real
   screenshots in `tests/fixtures/` (real minimap art 2026: plate-number towers, hourglass camps,
   reddish wall outline late game). If no real data covers a case, say so in the release notes.
3. **Measure before/after** (accuracy, latency, CPU) and report numbers honestly, including
   regressions.
4. **Game knowledge expires.** Objectives/items/timers come from data (Data Dragon refresh,
   `objectives.json`), checked against the current patch. Atakhan was removed in 26.1 and we kept
   advising on it.

## Overlay and advice
5. **One thing at a time.** Priority: danger > one action line > nothing. Clutter (chips, roster
   rows, "non vu", AI counters, duplicate labels) is a bug.
6. **The state must match reality.** Never "SÛR"/"NORMAL" while dead, outnumbered nearby,
   low HP in a fight, or while the base is under siege / after an ace. Never lane advice while dead
   or in fountain at 0:26. Never two contradictory lines within 10 s.
7. **Enemies on screen still kill beginners.** Personal danger (HP, 2v1, level/item spike) is
   alerted even when the enemy is visible.
8. **Alerts must come early** (≥ 5 s before contact) or they are useless; measure lead time.
9. **Never draw over other apps.** Hide when the game is not foreground or the minimap is occluded;
   never analyse pixels of another window.
10. **Labels never lie.** No ghost label on a live champion, one label per champion, no ghosts for
    dead enemies, no text on stale ghosts except the enemy jungler.
11. **Visual > voice.** Voice only for danger that can't wait.
12. **No "AI look".** Follow `docs/DESIGN.md` and its ban list; legibility first (the app font was
    once far too small).

## Detection
13. Team comes from the ring colour; identity only among that team's alive champions; one-to-one
    assignment; respect Live Client facts (dead set, 5 alive max, fountain respawn).
14. Tracking must follow walking champions with near-zero lag and survive camera-rectangle lines,
    camera moves, stacks and brief misses.
15. After every game, compare with LCU ground truth, log errors, and auto-tune bounded parameters
    (see `analysis.py` / `ground_truth.py`). Be severe in the "Erreurs de TreeAI" report section.

## Added after more feedback
16. **One router decides where every message goes** (banner / panel line / badge / voice / drop),
    by urgency and context. The small panel shows ONE line; low-value tips are dropped, not shown small.
17. **Danger is beep-first.** The beep plays instantly; voice is optional and never delays it.
18. **Never over the game's own UI.** Everything we draw is placed by ONE solver (`layout.py`)
    against League's zones measured on real captures (kill announcer, ally portraits, votes, item
    bar, minimap buttons, kill feed, chat, death recap...): one consistent slot per element, sized
    for its largest content so nothing jumps, never two of our elements on top of each other. Check
    any placement change with `tools/layout_audit.py` on the real screenshots (before / after).
