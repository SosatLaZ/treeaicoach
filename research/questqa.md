# QUESTQA — every facet of quest tracking, audited and replayed (TreeAI 3.9.9 → branch wow-questqa)

User (level-28 Horde hunter, WoW: Forever frFR, group of 4): « par pitié trouve un moyen de vraiment te concentrer
sur absolument toutes les facettes du suivi de quête, là c'est n'importe quoi ». Evidence (screenshot 17): in
Orneval, doing « La flamme sacrée », the panel said « Trajet 307 min · 27,3 km », « Arrêt 1/75 : Tue « Atrexis le
Chevalier de la tombe » » (a dungeon boss on another continent), « Changez de zone : Changez de zone : ailleurs :
+575 % d'XP/h après 5 min de trajet > Fossoyeuse » and « Étape : aller rendre 1 quête en Fossoyeuse ».
Screenshot 10 shows the log behind it: 20/20 quests, 5 dungeon quests, 2 elite, 1 PvP, 9 « à rendre » (one of them
in Fossoyeuse = Undercity, Eastern Kingdoms).

## Root causes of screenshot 17 (all fixed)

1. **The app plan was stale and nobody checked**: made in Mille pointes, loaded in Orneval. `Planner:FilePlan`
   only checked `v` and the character. → `Planner.Engine.PlanFresh` (zone vs work area, level, quest-log
   fingerprint, age) + the app stamps every plan with `log` / `level` / `exp`; a stale plan is dropped, said once
   (« Plan de TreeAI Coach ignoré : fait pour Mille pointes (trajet refait en jeu) ») and `summary.plan.stale`
   asks the app to recompute.
2. **Dungeon / elite / PvP quests were never flagged in the export** (`dg` documented, never written) → the app
   planned a dungeon boss solo; the addon's mask applied only « ignored » + « out of zone » to app plans. → Export
   writes `tag`, `dg` (game tag, Questie ZoneDB instance zone, instance map type, boss objective), `raid`, `pvp`,
   `failed`; ONE rule set (`planner/rules.py` ≡ `ZonePlan.Kind`/`ZonePlan:Accept`) used by the app, the in-game
   route and the mask of app plans.
3. **No length limit**: 75 stops / 307 min displayed. → app plans clipped to the horizon (60–120 min,
   `MAX_NODES`), the route view shows ≤ 1 h 30 / ≤ 30 stops and announces the rest in the tooltip.
4. **« Changez de zone » computed on a 1-quest sample**: Undercity looked +575 % because a finished elite quest is
   turned in there (turn-in XP for 5 min of « work »), and the export called the capitals where log quests are
   turned in « neighbour zones ». → rate comparison only when both routes are ≥ 10 min and the other zone has ≥ 3
   quests to take; > +100 % said « plus du double »; never another continent without the player's choice; the
   export's extra zones are the work area (or same-continent zones only).
5. **The label was repeated** (« Changez de zone : Changez de zone : … ») because the app put the verdict label in
   the reason and both UIs prefix it again. → reasons never carry the label (app), `E.CleanReason` strips it from
   old plans (addon).
6. **Two « steps » on screen**: the log's « Étape : aller rendre 1 quête en Fossoyeuse » (header-based zone test:
   « La flamme sacrée » sits under « Thunder Bluff » in the log) beside the route's « Arrêt 1/75 ». → one owner:
   the route's current stop; the log's « Étape » only without a route, judged on the quest's map, never a dungeon /
   PvP / elite-solo quest.

## Checklist (facet → behaviour before → what was wrong → fix / status)

| # | Facet | Before (code read) | Problem | Fix (✅ done, ⏳ in-game check, ➖ owner elsewhere) |
|---|---|---|---|---|
| 1 | Pick-up discovery | `Quests.QuestieScan` « here » = Doable quests of the zone, any level above grey; Tracker « Facile à côté », « Prends en chemin », « Tu pars sans prendre » from it | dungeon / elite quests offered as « facile, à côté »; no upper level bound | ✅ `Quests.SoloOK` (game tag: no dungeon / raid / PvP, no elite under 3 players) + level ≤ +3 |
| 2 | Objective progress (kill / collect / use / explore) | Planner/Route labels from the log text; app labels « Tuer 10 Loups » / raw objective text (« Fiole d'eau forte remplie ») | inconsistent verbs, objectives of not-yet-taken quests shown as « Objectif de « X » » | ✅ same verbs both sides (« Tue / Ramasse / Utilise / Explore »); our log label wins over the plan's; export carries objective names from Questie (`o.t`) |
| 3 | Escort | Pro « Escorte : prépare-toi » | shown for a FAILED escort | ✅ failed escorts excluded (« a échoué : abandonne-la puis reprends-la » instead) |
| 4 | Timed quests | only Edge2 (bêta) `questTimer` | nothing in safe mode; the route ignored the timer | ✅ Quests: « Chrono : « X » à finir avant 21:42 (3 min) » (end time, stable text), urgent < 5 min; route puts the timed quest first |
| 5 | Failed quests | `q.failed` skipped silently by some modules, planned by others | planned / advised although it cannot progress | ✅ never planned (rules « échouée »), one line « a échoué : abandonne-la puis reprends-la » |
| 6 | Item drops + rates | DATA fixed matching (Detect/Highlight/Tracker) | — | ➖ unchanged (DATA); Pro « Vrai taux de drop » kept (measured) |
| 7 | Objective locations | Questie clusters; game waypoint fallback; unpositioned steps kept | a step without position is judged nowhere | ✅ mask judges it on the quest's zone; ⏳ `GetNextWaypoint` on Forever |
| 8 | Turn-in readiness | COHERE: « Rendre » only for complete quests or after a visible pick/objective | masking ran AFTER that check → a masked objective could leave a « Rendre » of an unfinished quest | ✅ mask first, then the check |
| 9 | Completed, turn-in far away | whole quest masked if ANY stop was out of zone | « La flamme sacrée » (vial in Orneval, turn-in Thunder Bluff) vanished from the route | ✅ zone test stop by stop (addon + app): objectives here stay, the far turn-in is a detour proposal; the app credits half the quest XP to those objectives |
| 10 | Turn-ins at hand | 2-opt optimises travel only | 10 eggs before 5 turn-ins at the same NPC (2 s of path « saved ») | ✅ `N.NearFirst`: turn-ins / pick-ups within 60 yd first (precedences + log capacity kept) |
| 11 | Chains | Quests « Suite : « X » chez Y (dès le niv N) »; Pro « Chaîne : … » | Pro listed next quests of the other faction / done / dungeon chains | ✅ Pro filters with `Quests.Doable` + `SoloOK` + done |
| 12 | Level / elite / group / dungeon / PvP / raid / class / profession | ZonePlan.Kind: elite + dungeon (tag, instance map); Python: `dg` only (never exported) | boss objectives, PvP, raid unknown; app ≠ addon | ✅ shared rules (rules.py ≡ ZonePlan): ready turn-in kept; PvP/raid never; dungeon only opted-in AND ≥ 3; elite opted-in or ≥ 3; group quests; ≤ gap levels; class via Doable; profession sorts have no zone |
| 13 | Faction / race | Doable race/class masks (DATA) | — | ➖ unchanged |
| 14 | Prerequisites | export `pre`/`preAny`, solver closure | — | ➖ unchanged |
| 15 | Abandon advice (bêta) | verdict from an estimated XP/min (« ≈ 13 min pour 2000 XP ») | irreversible advice on unverified numbers; « Retire » an elite the group will do | ✅ « Retire » only on facts (grey, useless breadcrumb, full log and not a group quest); estimates gone from the tooltip; headers say what they are |
| 16 | Zone selection | ZonePlan work area; export « neighbours » = most referenced maps | capitals (turn-ins) exported as neighbours → app sent the player overseas | ✅ export = work area, else same-continent zones; app default = player's continent |
| 17 | Route order + transport + hearth | Route legs (walk / fly / boat / zeppelin / hearth) | Speed's « Pierre de foyer maintenant » duplicated the route's hearth stop, with another speed | ✅ Speed silent when the route's 1st leg is the hearth; same NavTime speed |
| 18 | ETA accuracy | « Arrêt 1 : … (≈ 1 min) » = time ON the spot; Planner fallback = Stats speed × Export detour (double detour); Tracker 7 yd/s | « ≈ 1 min » for a stop 2 km away; 3 estimators | ✅ « (≈ N min) » = done in N min from now (travel + work), tooltip splits both; NavTime everywhere (Planner fallback, Speed hearth, Tracker); ⏳ precision after an evening (NavTime ±%) |
| 19 | Numbers sanity | XP/h on a 1-min plan (« 102 414 XP/h »), « +575 % » | absurd | ✅ XP/h only on ≥ 10 min and ≤ 6 × grind rate (addon + app), % > 100 → words |
| 20 | Re-plan triggers + stability | events + hysteresis 10 % / 60 s | 2-opt could break the log capacity, then hysteresis kept it | ✅ capacity checked after 2-opt / Or-opt / patch; replay checks « same order after a replan / a few steps » |
| 21 | Log full | greedy refused pick-ups but 2-opt and app plans did not | « Prendre » ×5 with 20/20 | ✅ capacity everywhere (greedy, 2-opt, near-first, app-plan display); one line « Journal plein (20/20) : 5 quêtes à prendre ici attendent une place » |
| 22 | Party member quests | Planner WaitFor, Squad / Team | — | ➖ GROUPMERGE; group size feeds the rules (elite ≥ 3) |
| 23 | Quest items / usable items | Quests secure item button, Highlight glow | — | ➖ unchanged (NAV/DATA) |
| 24 | Quests from items | « Ce butin lance une quête : X » | — | ➖ unchanged |
| 25 | Repeatables | export skips `specialFlags & 1` | — | ➖ unchanged |
| 26 | Duplicates with Questie / SPF | one guide (COHERE); Quests « Rends … : terminée » off with a plan | « À rendre ici (Mille pointes) : « L'œuf étrange », « ON RECHERCHE » (cut mid-title) and « Tu es près de la remise » beside the route's « Rendre » stops; Tracker « Étape n/N » and Todo « Ensuite : … » = 2nd and 3rd step lists in the same tab | ✅ silent with a route; short « 3 quêtes à rendre ici : « X » et 2 autres »; Tracker/Todo step lists dropped when the route is in the tab |
| 27 | Stale data from the app plan | char + `v` only; « plan ancien » after 24 h (old view only) | screenshot 17 | ✅ `PlanFresh` (+ fingerprint), « spent » plan (nothing visible left) → in-game route at once |
| 28 | Wording | — | repeated labels, « niv 28 → 28 », arrows the font lacks in reasons, pluralisation | ✅ reasons without labels / arrows, « 11 quêtes, ~67 min, jusqu'au niveau 29 », plurals |
| 29 | Pro estimates (bêta) | XP/min ranking, per-objective ETA, dungeon list, checklist, rest, hearth | unverified / duplicates (review) | ✅ off by default (mechanics kept, opt-in) |
| 30 | Region (bêta) | « Région déconseillée : … — on y va quand même parce que … » | odd sentence, 3rd ordering of the same list | ➖ left for the Todo merge (review: FUSIONNER) |

## Invariants enforced (replay `tests/wow/questqa_scenario.lua`, every tick, 3 API flavours)

1. No repeated label / phrase (« X : X »). 2. No percentage > 100, no XP/h > 150 000. 3. « Trajet N min » ≤ 95,
« Arrêt k/N » with N ≤ 30, first-stop ETA ≤ 90 min. 4. « Étape : rendre « X » » only for a complete quest of the
log. 5. Route stops: objectives only for quests in the log (or picked earlier in the route), turn-ins only when
complete (or after its objectives), never a failed quest, never PvP, never a dungeon / elite quest solo unless
opted in and the party fits. 5b. A full log never gets a « Prendre » before a turn-in. 6. The current stop is in
the work area (or a detour the player added) and the compass aims at it. 7. One step on screen: no log « Étape »
and no « Ensuite » list beside the route; the step card = the route's current stop. 8. « Changez de zone » never
while a quest of the log can be done here. Plus: stability (no reorder on replan / a few steps), the stale plan
of screenshot 17 is dropped, a fresh but too-long app plan is masked and clipped, timers / failed / group / combat.

App side (`tests/wow/test_questqa.py`): the same rules on an export of the user's log (no dungeon / boss / PvP /
elite-solo / failed quest in solo routes, never another continent, ≤ horizon, reasons without labels / arrows /
% > 100, fingerprint present, XP/h only on ≥ 10 min), dungeons only opted in with 4 players.

## Needs an in-game check (Forever)

* `C_QuestLog.GetQuestTagInfo(questID)` for quests NOT in the log (client cache) — else dungeon quests to take are
  caught by Questie's ZoneDB / boss rank only.
* `C_QuestLog.GetTimeAllowed` / `IsFailed` on Forever (timed / failed lines).
* `GetQuestUiMapID(questID)` = the map of the objectives (« ici » for « La flamme sacrée » in Orneval).
* A real app plan: « Plan de TreeAI Coach ignoré : … » appears when changing zone, disappears after « Calculer ».
