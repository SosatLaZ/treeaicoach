# FIDELITY — the test mock made faithful to the real WoW: Forever client (round 8)

Branch `wow-fidelity` (worktree /home/user/fidelity), base `e07485b` = release 3.9.9. Agent FIDELITY.

**Root cause attacked.** The player keeps finding real-game bugs the suite misses because `tests/wow/wowmock.lua` did
not behave like WoW: Forever (Interface 16001, build 1.60.1 « 70862 », game type `camelot`, Mainline API family,
frFR). It defined 16 APIs the client does not have (each one hid a bug below), lacked 82 real APIs the addon calls
(so those code paths never ran), made only 12 functions secret, accepted unknown events, answered the auction house
synchronously and had a quest log without headers.

**Reference.** Blizzard's own interface code for Forever: `Gethe/wow-ui-source` branch `forever` commit `9dc84f0`
« 1.60.1 (70862) » — the generated API documentation (5 144 functions with arguments / returns / secret and
protected flags, 1 816 events, structures, enums) and the FrameXML that loads for camelot (TOC lines resolved:
`[AllowLoadGameType mainline]` includes camelot, `[ExcludeLoadGameType camelot]`, `[Family]` = Mainline, `[Game]` =
Camelot: 1 638 Lua + 904 XML files). warcraft.wiki.gg « Secret Values » for the semantics; two real error texts
from addon bug reports (« attempt to compare local 'gender' (a secret number value, while execution tainted by
'Interaction') », « attempt to compare a secret number value (execution tainted by 'Rarity') »).

## 1. Tools

* `tests/wow/forever_api_check.py` — parses the documentation (pure Python, never runs Blizzard code), resolves
  the camelot FrameXML, inventories every API the addon calls (globals, `_G.X`, `rawget(_G, "X")`, `g("X")`
  helpers, `C_X.Y` and local aliases, with file:line), probes the mock with lua5.1, writes the table of § 7.
  Committed snapshot: `tests/wow/fixtures/forever_api.json.gz`; `--ui-source DIR` rebuilds it; `--check` fails if
  the mock defines an API the client lacks (wired into `tools/wow_check.sh`); `--mock FILE` probes another mock.
* `tests/wow/forever_ref.lua` — generated subset the mock loads: which functions exist (documented + FrameXML +
  C functions the camelot UI calls; deprecated shims treated as absent), their flags (secret arguments refused,
  protected, hardware, secret-return predicate), events with payload flags, every documented enum.
* `tests/wow/forever_mock_spec.lua` — 63 expectations on the mock itself (secret semantics, unknown events,
  hardware events, quest log, AH, positions).
* `tests/wow/forever_known.py` + `tests/wow/conftest.py` — the known real bugs (§ 3) as output signatures: a run on
  the forever mock failing ONLY on them is reported **xfailed with the finding ids** (pytest hook, and
  `tools/wow_check.sh` prints « XFAIL F1 (research/fidelity.md) »); any other problem stays red.
* `tests/wow/test_forever_fidelity.py` — replays EVERY TreeAI harness run of the suite (103: each scenario with the
  classes / options its own test uses) on « forever », plus a 60-min session_sim (hunter, every module on).
* Harness plumbing: `TREEAI_WOW_FORCE_FLAVOR=forever` runs the whole suite on the faithful mock,
  `TREEAI_WOW_RUNLOG=FILE` logs each run's arguments, `TREEAI_WOW_SECRET_RETURNS=always` reads SecretReturns
  literally (§ 6), `TREEAI_WOW_SECRETBOOL=1` prints secret booleans handed to addon code.

## 2. What the « forever » mock now does (was → is)

| area | before | now (source) |
|---|---|---|
| API existence | hand list of 63 removed globals; 16 Era/shim APIs still defined (`GetMacroIndexByName`, `GetZonePVPInfo`, `GetNumTalentGroups`, `CombatLogGetCurrentEventInfo`, `SendChatMessage`, `UnitBuff`, `IsSpellInRange`…) | every global function / C_ member absent from forever_ref.lua is removed (73 globals); 6 world-data APIs left absent on purpose (§ 7) |
| real APIs the addon calls | 82 missing (`C_Spell.GetSpellCooldown`, `UnitQuestTrivialLevelRange`, `C_QuestLog.ReadyForTurnIn`, `GetProfessions`, `C_Map.HasUserWaypoint`, `UnitAttackPower`…) | doc-shaped stubs; `C_SpellBook` built from the known spells (lines, items, names, ranks); `GetProfessions` = prof1, prof2, first aid, fishing, cooking (Camelot ProfessionsBook) |
| secret values | opaque table, 12 functions, unit rules by hand; `type(secret)` = "table" | userdata carrying the real value; **every doc-flagged function** (UnitHealth/R, GetUnitSpeed & stats/combat incl. the player, UnitName/GUID/Class of NPCs, casts of others, auras, threat, UnitIsUnit compound tokens in instances, cooldowns, UnitInRange, GetRaidTargetIndex, UnitIsAFK in chat lockdown…); combat, instance map, encounter restrictions; `type()` = real type; concat / `string.format` / `strjoin` / `tostring` / `tonumber` give secrets; `#`, index, call, arithmetic, compare, C-function argument = the client's error text naming the variable + VIOLATION [secret], **even when the addon swallows it with pcall** |
| secret arguments | not modelled | « AllowedWhenUntainted » APIs refuse secrets from addon code; « AllowedWhenTainted » ones answer with secrets |
| events | any name accepted | unknown event → « Frame:RegisterEvent(): Attempt to register unknown event "X" »; CLEU & restricted events forbidden; payload secrets (UNIT_SPELLCAST_* of others, PARTY_KILL/UNIT_DIED identities, CHAT_MSG_* in chat lockdown) |
| hardware events | macros only | + SetBinding*/SaveBindings (ADDON_ACTION_FORBIDDEN), SAY/YELL/EMOTE/CHANNEL chat; a scenario's own `TreeAI:UserAction(fn)` = one click |
| positions | `UnitPosition` always nil | player / group world yards, nil for NPCs, enemy players and everyone in an instance; nameplates forbidden in instances |
| auction house | instant answers, 0.05 s throttle, works with the AH closed | answers ~0.15 s later, system busy ~0.3 s, `…_MESSAGE_DROPPED` when sent too early, nothing with the AH closed |
| quest log | no header, 4 QuestInfo fields, numQuests = entries | zone header first (stable quest IDs), all 27 documented QuestInfo fields, (entries, quests) counts, cap **40** (`QuestLogConstsMainlineCamelot`) |
| client info | build 1.15.8 / 11508 | `GetBuildInfo` 1.60.1 / 70862 / 16001, `WOW_PROJECT_CAMELOT`, documented enums, `C_Secrets`, `C_RestrictedActions` |

Mismatches mock ↔ real for the APIs the addon references (forever_api_check.py): **98 before → 6 now** (the 6 are
the world-data APIs deliberately absent). Session_sim on « forever » does not count the forever layer of the mock
(functions defined after `WowMock.FOREVER_LAYER_LINE`: secret values, documented flags, wrappers = C code in the game)
as addon CPU, nor the profiler hook's own allocations; the mock's API bodies are counted as in the other flavours.

## 3. Bugs found — real game (run on the faithful mock; module:line → what the player sees)

| id | where | in game | evidence | confidence |
|---|---|---|---|---|
| **F1** | `Missions.lua:1351` `TargetInfo` → `clean()` `Missions.lua:105` | in **every fight with a target**: `UnitName("target")` is secret, `tostring(v):gsub` raises « attempt to index a secret string value » → the Missions / Groupe tab stops refreshing during fights, `/tai erreurs` fills (229 per hour in the hunter session_sim) | 96 of 103 scenario runs, every storm, session_sim (229 violations / h) | certain (NPC name = SecretWhenUnitNameIdentityRestricted; wiki: « UnitName returns secrets for a non-player unit in combat ») |
| **F9** | `Binds.lua:186-189` `macroIndex()` | « Touches » never sees the TAI macros: only `GetMacroIndexByName`, absent on Forever (Macros.lua learned the GetMacroInfo scan, Binds did not) → plans raw spells, reports spells already covered by a TAI macro as « manquant », `DoStep` cannot place a macro (`Binds.lua:595`) | binds scenario on forever (11 checks) | certain (API absent) |
| **F2** | `Edge.lua:1041` | the « Achète la double spé » reminder (level 40) never appears: `GetNumTalentGroups` does not exist (Forever: `GetNumSpecGroups()` + `C_SpecializationInfo.GetActiveSpecGroup()`, both mocked now) | edge scenario × 3 classes | certain |
| **F5** | `PvPMark.lua:95` | enemies are marked even in a sanctuary: `GetZonePVPInfo` is `C_PvP.GetZonePVPInfo()` on Forever | pvpmark scenario | certain |
| **F6** | `PvPMark.lua:110` `call("UnitIsPVP", unit) == false` | in world PvP **in combat**: UnitIsPVP of an enemy player is secret, comparing it is a Lua error → the enemy line breaks exactly during the fight (mock blind spot: Lua 5.1 never calls `__eq` for `secret == false`; found through the secret-boolean report) | secret boolean `UnitIsPVP @ PvPMark.lua:37 < :110` | high |
| **F10** | `Missions.lua:1685`, `Squad.lua:936`, `Stats.lua:812` (`call(UnitIsAFK, …) and …`) | in **dungeon boss fights** (chat lockdown): `UnitIsAFK` is secret, a boolean test on it is a Lua error → roster / group sync / XP stats break during encounters | secret booleans in the dungeon scenario (encounter) | high (doc flag SecretInChatMessagingLockdown) |
| **F7** | `Fight.lua:519-531` `CheckPetAggro` | « Le familier a perdu l'aggro » never fires: threat values are secret in combat | x2 scenario | high (already « menace morte » in review.md: remove) |
| **F3** | `Edge2.lua:466/473` (`pct("player")` at the pull) | « Mange / bois maintenant » / « Enchaîne sans boire » never shown: player HP at the pull is secret in combat → no fight cost measured | edge2 × 3 classes | medium (§ 6) |
| **F4** | `Stats.lua:231/241` | Stats « dégâts subis par combat » stays empty for the same reason | stats × 3 classes | medium (§ 6) |
| **F8** | `Fight.lua:258-273` (death snapshot at PLAYER_DEAD) | the death recap cannot say « Tué par X »: plates / target names are secret while the player is still in combat | x2 scenario | medium (event order at death to check) |
| **F11** | `Compass.lua:263-273` `OtherGuide` | after « Aller là » (TreeAI sets a Blizzard waypoint), switching to « Guidage : Suivi Forever » leaves that waypoint → two arrows (Blizzard + SPF), the compass reports « blizzard » | nav scenario | medium (UX) |

Also seen, silent (no error, feature dead on Forever, no test asserts it): Fight `startHP` (`Fight.lua:69`, « low HP pull »
advice) for the same reason as F3; under the literal SecretReturns reading `GetRaidTargetIndex` is always secret and
`Missions.lua:1358/1537` (`info.mark == icon`, out of combat) would error — invisible to Lua 5.1 (`~=` on a secret).

Perf on Forever (session_sim hunter « tout », 60 min): `Macros.lua:694-705` `macroIndex()` — without
`GetMacroIndexByName` every lookup scans all macro slots with `pcall(GetMacroInfo)`, and `Refresh` looks up every
definition: Macros 35 → 54 kinstr/min, peak 139 → 237 (a name → index map per refresh brings it back to 37). Frame
p99 86-89 kinstr against the budget 86 (86 at base, modern 85): at the edge, not judged on forever (§ 5).

### Status after the 4.0 integration (INTEG2, branch m400): no known finding left, no xfail

| id | 4.0 status |
|---|---|
| F1 | gone: Missions.lua merged into `Modules/Group.lua` (GROUPMERGE) — names / states through `pub()` + `clean()`, no target read in combat |
| F2 | gone: `Modules/Edge.lua` removed (CLEAN) |
| F3 | gone: Edge2 « boire ou enchaîner » removed (CLEAN: duplicate of the Combat / Inventory owners) |
| F4 | fixed: `Stats.lua` follows the player's health OUT of combat (`UNIT_HEALTH` / `UNIT_MAXHEALTH`, `M.SnapHealth`); at the pull it starts from that reading, at the end (readable again) vie perdue = start − end; shown as « vie perdue N % / combat » |
| F5 | fixed: `PvPMark.lua` reads `C_PvP.GetZonePVPInfo()` (Era global as fallback) |
| F6 | fixed: `PvPMark.lua` passes `UnitIsPVP` through `_ns()` before comparing |
| F7 | gone: Combat (Fight) pet-aggro alert removed (CLEAN: threat is secret) |
| F8 | fixed by CLEAN: the killer is read on the target before / at the pull (`Fight.ReadFoe`), never at PLAYER_DEAD; « vie au départ » kept from the last out-of-combat reading (`clean_scenario` checks both on forever) |
| F9 | fixed: `Macros.MacroIndex` = one `GetMacroInfo` scan into a name → index map, kept until `UPDATE_MACROS` or our own create / edit / delete (+ a name check on each hit); `Binds.lua` and `PvP.lua` use it (PvP created a new macro each time on Forever) |
| F10 | fixed: `Stats.lua` guards `UnitIsAFK` (secret in chat lockdown); Group / Detect / QuickTravel already went through `pub()` |
| F11 | fixed: `Compass.Pin` remembers OUR Blizzard waypoint; when the player hands guidance to Suivi Forever and SPF guides, `Compass.DropOwnWaypoint` clears it (only ours: same map, same point) → one arrow (`nav_scenario` checks it) |
| perf | `Macros.lua` lookups no longer scan every slot per definition; session_sim on forever (hunter, 60 min): essentiel 1 867 kinstr/min, frame p99 40 (budget 48); tout 2 501, p99 50 (budget 86) — budgets now JUDGED on forever (`test_session_sim.py` HUNTER × forever × essentiel / tout, `test_forever_fidelity.py` WARRIOR × forever × tout) |

## 4. Scenarios that relied on mock-only behaviour (fixed, assertions unchanged)

* `dungeon`, `pvp`, `questpro`: replaced `issecretvalue` by a fake and then **removed it** (`rawset(_G, "issecretvalue",
  nil)`): every secret guard of the addon broke for the rest of the run → the fake now chains to the client's and
  the original is restored.
* `binds`, `pvp`: called `GetMacroIndexByName` (absent on Forever) → `WowMock.MacroIndex`.
* `abandon`: assumed the Classic 20-quest log (« 19/20 ») → uses the game's cap (`GetMaxNumQuestsCanAccept`, 40).
* `auction`, `auction_forever`, `auction_liquidity`: keyed on `M.flavor == "modern"` and clicked three times in the
  same instant → forever counts as the modern AH, the clicks wait for the asynchronous answers.
* `x2` « nouveau sort »: removed `C_SpellBook` to simulate the Era spellbook → on forever it now goes through the
  faithful `C_SpellBook` (the real path works).
* `questpro` predict: fed kills only through the combat log (forbidden on Forever) → also the XP message, as the
  client does (Predict's Forever path works).
* harness: API names the client lacks, set back by a scenario that simulates them, are not leaks.

## 5. How the suite reports it

`pytest tests/wow`: forever-flavoured runs of the other test files that fail only on § 3 signatures are **xfailed**
by `tests/wow/conftest.py` with the ids (output must be complete: as many problem lines as the RESULT counts);
`test_forever_fidelity.py` replays all 103 runs + the session on forever with the same rule. `tools/wow_check.sh`:
forever storms print « XFAIL F1 (research/fidelity.md) » instead of failing. Integrator: fix a finding → its
signature disappears → the runs pass; then delete its entry in `tests/wow/forever_known.py`.

Today that is **62 forever tests of 33 existing files xfailed** (all F1; + F2 in edge mage / warrior, F3 in edge2):
auction owned / rafale, cohere, content × 2 + every-line robustness × 5, data, detect, edge × 3, edge2 × 3, errlog
bugs, finder, lite storm, macro2 × 4, macrofix × 2, menu × 2, minipins, nav, next10 × 2, pathdraw, pvpmark, quick × 2,
quick2 × 3, quicktravel × 2, route, speed, stats × 2, taint scenario + 3 sessions, team, tidy × 2, ui2 × 2, uifix × 2,
selftest × 2, forever storm × 2, zoneplan — plus the replays of `test_forever_fidelity.py`. Modern / legacy runs
are untouched. An xfailed test stops at its first failing assertion: the assertions after it run again once the
finding is fixed. A run whose problem lines are cut by the test's own `stdout[-3000:]` stays red (unknown lines
could hide in the cut part). Session_sim instruction / GC budgets (`FAIL hitch:` / `gc:` / `cpu:`) are calibrated on
modern and enforced there by `test_session_sim.py`; no test judged them on forever before, and the forever verdicts
print them without judging.

## 6. Assumptions — to confirm in game

`/tai test`, line « Valeurs secrètes », run in combat with a target, answers the first one (« tes PV secrets /
lisibles »).

1. **SecretReturns** (UnitHealth, UnitInRange, GetRaidTargetIndex…): the mock makes them secret while a restriction is
   active (combat, instance, encounter) **for every unit, the player included**. The documentation says « always »
   (`TREEAI_WOW_SECRET_RETURNS=always`; the storms stay clean that way); if the player's own health turns out readable
   in combat, F3 / F4 / Fight startHP become non-issues.
2. Stats restriction (GetUnitSpeed, UnitRangedDamage…) applies to the player in combat (C_Secrets.ShouldUnitStatsBeSecret
   takes no unit; matches the speed-read errors the player reported in 3.9.7).
3. Instances are restricted maps out of combat for NPC identity (Interaction addon report: UnitSex("npc") in instances).
4. Chat lockdown = encounters (F10); event order at death (F8); AH round trip ~0.15 s / busy ~0.3 s.
5. Deprecated shims (`Blizzard_Deprecated*`, CVar `loadDeprecationFallbacks`) are treated as absent; secret-argument
   rules exempt the inspectors (`issecretvalue`, `canaccessvalue`…), documented « AllowedWhenUntainted » but used by
   addons in combat.
6. Lua 5.1 blind spots: `secret == x` with a non-secret x is silently false, `if secretBoolean` passes (reported as
   « secret boolean » notes), a secret table key is only found by the end-of-run scan.

## 7. API table

<!-- BEGIN API-TABLE (generated by tests/wow/forever_api_check.py) -->

Reference: `Gethe/wow-ui-source` branch `forever` commit `9dc84f0` « 1.60.1 (70862) » — 5144 documented functions, 1816 events; FrameXML for game type `camelot` ([Family] = Mainline): 1638 Lua + 904 XML files.

Counts (APIs the addon references): MISSING 93, c-global 89, documented 205, framexml 36, shim-only 5. Mismatches mock ↔ real: **5** (bold rows below).

Legend — real: `documented` (Blizzard API docs), `framexml` (defined by Blizzard's camelot UI code), `c-global` (undocumented C function Blizzard's camelot UI calls), `shim-only` (only in Blizzard_Deprecated*, CVar loadDeprecationFallbacks: do not rely on it), `MISSING` (nowhere in the Forever client). mock = what tests/wow/wowmock.lua does in the « forever » flavour.

| API | real (Forever 1.60.1) | flags | mock (forever) | mismatch | called from (file:line) |
|---|---|---|---|---|---|
| **`C_AreaPoiInfo.GetAreaPOIForMap`** | documented (uiMapID) → areaPoiIDs:table<number> |  | absent | **real API not mocked**: the addon path is never exercised | PvP.lua:190 |
| **`C_AreaPoiInfo.GetAreaPOIInfo`** | documented (uiMapID?, areaPoiID) → poiInfo:AreaPOIInfo |  | absent | **real API not mocked**: the addon path is never exercised | PvP.lua:193 |
| **`C_AreaPoiInfo.GetAreaPOISecondsLeft`** | documented (areaPoiID) → secondsLeft:number |  | absent | **real API not mocked**: the addon path is never exercised | PvP.lua:201 |
| **`C_TaxiMap.GetAllTaxiNodes`** | documented (uiMapID) → taxiNodes:table<TaxiNodeInfo> |  | absent | **real API not mocked**: the addon path is never exercised | Errands.lua:256 |
| **`C_TaxiMap.GetTaxiNodesForMap`** | documented (uiMapID) → mapTaxiNodes:table<MapTaxiNodeInfo> |  | absent | **real API not mocked**: the addon path is never exercised | Radar.lua:430,440 |
| `C_AddOns.GetAddOnCPUUsage` | MISSING |  | absent |  | SelfTest.lua:307 |
| `C_AddOns.GetAddOnMemoryUsage` | MISSING |  | absent |  | Core.lua:1464; SelfTest.lua:304 |
| `C_AddOns.UpdateAddOnMemoryUsage` | MISSING |  | absent |  | Core.lua:1463; SelfTest.lua:303 |
| `C_Spell.IsSpellKnown` | MISSING |  | absent |  | Core.lua:133,133 |
| `C_TradeSkillUI.GetAllRecipeIDs` | MISSING |  | absent |  | Prof.lua:278,285 |
| `C_TradeSkillUI.GetTradeSkillLine` | MISSING |  | absent |  | Prof.lua:282 |
| `CalculateAuctionDeposit` | MISSING |  | absent |  | Auction.lua:2086 |
| `CanSendAuctionQuery` | MISSING |  | absent |  | Auction.lua:1299,1384,2163,2164 |
| `Cancel` | MISSING |  | absent |  | Route.lua:568,575,911 |
| `CancelAuction` | MISSING |  | absent |  | Auction.lua:1562 |
| `CastingInfo` | MISSING |  | absent |  | Core.lua:448,449; Hunter.lua:836 |
| `ClickAuctionSellItemButton` | MISSING |  | absent |  | Auction.lua:1675 |
| `GetActiveTalentGroup` | MISSING |  | absent |  | Core.lua:2668,2669; Macros.lua:604; Talents.lua:163 |
| `GetAddOnInfo` | MISSING |  | absent |  | Quests.lua:97 |
| `GetAddOnMetadata` | MISSING |  | absent |  | Core.lua:296 |
| `GetAuctionItemInfo` | MISSING |  | absent |  | Auction.lua:601,776,1502,1557 |
| `GetAuctionSellItemInfo` | MISSING |  | absent |  | Auction.lua:1676,2079 |
| `GetBattlefieldFlagPosition` | MISSING |  | absent |  | PvP.lua:389 |
| `GetBattlefieldPortExpiration` | MISSING |  | absent |  | PvP.lua:709 |
| `GetContainerItemID` | MISSING |  | absent |  | Hunter.lua:266; Macros.lua:67 |
| `GetContainerItemInfo` | MISSING |  | absent |  | Core.lua:191; Inventory.lua:148; Macros.lua:68 |
| `GetContainerItemLink` | MISSING |  | absent |  | Hunter.lua:268 |
| `GetContainerItemQuestInfo` | MISSING |  | absent |  | Quests.lua:990 |
| `GetContainerNumFreeSlots` | MISSING |  | absent |  | Core.lua:176; Inventory.lua:124 |
| `GetContainerNumSlots` | MISSING |  | absent |  | Core.lua:171; Hunter.lua:261; Inventory.lua:116; Macros.lua:66 |
| `GetDetailedItemLevelInfo` | MISSING |  | absent |  | Group.lua:1883 |
| `GetItemCooldown` | MISSING |  | absent |  | Errands.lua:236; Export.lua:550; Quests.lua:867; Quick.lua:608; Route.lua:319; Speed.lua:146 |
| `GetItemCount` | MISSING |  | absent |  | Core.lua:161; Auction.lua:984; Classes.lua:208; Finder.lua:430; Hunter.lua:256; Prof.lua:311 (+3 files) |
| `GetItemIcon` | MISSING |  | absent |  | Macros.lua:1283 |
| `GetItemInfo` | MISSING |  | absent |  | Core.lua:148; Abandon.lua:477; Gear.lua:45; Hunter.lua:435; Inventory.lua:132; Macros.lua:49 (+3 files) |
| `GetItemInfoInstant` | MISSING |  | absent |  | Classes.lua:154; Hunter.lua:240 |
| `GetItemSpell` | MISSING |  | absent |  | Macros.lua:54 |
| `GetLootMethod` | MISSING |  | absent |  | QuickTravel.lua:962 |
| `GetLootSourceInfo` | MISSING |  | absent |  | Export.lua:267; Stats.lua:741 |
| `GetMacroIndexByName` | MISSING |  | absent |  | Macros.lua:708,709,1008; PvP.lua:611 |
| `GetMinimapShape` | MISSING |  | absent |  | Hub.lua:71; MinimapPins.lua:154; PathDraw.lua:239 |
| `GetNumAddOns` | MISSING |  | absent |  | Quests.lua:95 |
| `GetNumAuctionItems` | MISSING |  | absent |  | Auction.lua:1094,1431,1501 |
| `GetNumPartyMembers` | MISSING |  | absent |  | Core.lua:1310,2660,2661,2878; Macros.lua:634; Quests.lua:390; Quick.lua:416; Region.lua:48; Tracker.lua:310 |
| `GetNumQuestLogEntries` | MISSING |  | absent |  | Core.lua:431,432; Export.lua:501; Leveling.lua:166; Pro.lua:238; Quests.lua:267,268; Quick.lua:171 |
| `GetNumRaidMembers` | MISSING |  | absent |  | Core.lua:1311,2877 |
| `GetNumSkillLines` | MISSING |  | absent |  | Core.lua:364,365; Finder.lua:469; Gear.lua:484; Prof.lua:258 |
| `GetNumSpellTabs` | MISSING |  | absent |  | Core.lua:403,404 |
| `GetNumTalentGroups` | MISSING |  | absent |  | Talents.lua:184 |
| `GetNumTalentTabs` | MISSING |  | absent |  | Talents.lua:162 |
| `GetNumTalents` | MISSING |  | absent |  | Talents.lua:165 |
| `GetNumTradeSkills` | MISSING |  | absent |  | Prof.lua:295 |
| `GetPetFoodTypes` | MISSING |  | absent |  | Hunter.lua:108; Macros.lua:274 |
| `GetPetHappiness` | MISSING |  | absent |  | Hunter.lua:105 |
| `GetPetLoyalty` | MISSING |  | absent |  | Hunter.lua:106 |
| `GetPetTrainingPoints` | MISSING |  | absent |  | Finder.lua:611; Hunter.lua:107 |
| `GetQuestGreenRange` | MISSING |  | absent |  | Quests.lua:111 |
| `GetQuestIndexForTimer` | MISSING |  | absent |  | Quests.lua:713,716 |
| `GetQuestLogTitle` | MISSING |  | absent |  | Core.lua:431,433; Export.lua:503; Leveling.lua:168; Pro.lua:260; Quests.lua:267,271; Stats.lua:381 |
| `GetQuestTagInfo` | MISSING |  | absent |  | Export.lua:508,659; Quests.lua:176,177; ZonePlan.lua:516 |
| `GetQuestTimers` | MISSING |  | absent |  | Quests.lua:713,714 |
| `GetSkillLineInfo` | MISSING |  | absent |  | Core.lua:364,366; Finder.lua:475; Gear.lua:484; Prof.lua:260 |
| `GetSpecialization` | MISSING |  | absent |  | Core.lua:2662,2663 |
| `GetSpecializationInfo` | MISSING |  | absent |  | Core.lua:2664,2665 |
| `GetSpellBookItemName` | MISSING |  | absent |  | Core.lua:410; Finder.lua:186 |
| `GetSpellCooldown` | MISSING |  | absent |  | Core.lua:142 |
| `GetSpellInfo` | MISSING |  | absent |  | Core.lua:114,123; Classes.lua:82,100; Hunter.lua:133,166; Macros.lua:36,208,209; PvPMark.lua:135 |
| `GetSpellName` | MISSING |  | absent |  | Finder.lua:187 |
| `GetSpellTabInfo` | MISSING |  | absent |  | Core.lua:403,405 |
| `GetSpellTexture` | MISSING |  | absent |  | Macros.lua:1276 |
| `GetTalentInfo` | MISSING |  | absent |  | Talents.lua:145 |
| `GetTalentTabInfo` | MISSING |  | absent |  | Macros.lua:607; Talents.lua:155 |
| `GetTrackingTexture` | MISSING |  | absent |  | Edge2.lua:422 |
| `GetTradeSkillInfo` | MISSING |  | absent |  | Prof.lua:296 |
| `GetTradeSkillLine` | MISSING |  | absent |  | Prof.lua:290 |
| `GetTrainerServiceLevelReq` | MISSING |  | absent |  | Errands.lua:346 |
| `GetUnspentTalentPoints` | MISSING |  | absent |  | Leveling.lua:290; Pro.lua:526; Talents.lua:180 |
| `GetZonePVPInfo` | MISSING |  | absent |  | PvPMark.lua:98 |
| `HasPetSpells` | MISSING |  | absent |  | Core.lua:402; Finder.lua:182 |
| `InterfaceOptionsFrame_OpenToCategory` | MISSING |  | absent |  | Options.lua:790,792,793 |
| `InterfaceOptions_AddCategory` | MISSING |  | absent |  | Options.lua:748,748 |
| `IsAddOnLoaded` | MISSING |  | absent |  | MinimapPins.lua:83; Quests.lua:90 |
| `IsPassiveSpell` | MISSING |  | absent |  | Core.lua:411 |
| `IsQuestFlaggedCompleted` | MISSING |  | absent |  | Edge2.lua:140; Export.lua:522; Gear.lua:459; Planner.lua:486; Quests.lua:442,566 |
| `IsQuestWatched` | MISSING |  | absent |  | Quests.lua:200 |
| `IsSpellInRange` | MISSING |  | absent |  | PvPMark.lua:138 |
| `IsUnitOnQuest` | MISSING |  | absent |  | Quick.lua:434 |
| `PickupContainerItem` | MISSING |  | absent |  | Auction.lua:1673 |
| `PlaceAuctionBid` | MISSING |  | absent |  | Auction.lua:1106 |
| `PostAuction` | MISSING |  | absent |  | Auction.lua:1679 |
| `QueryAuctionItems` | MISSING |  | absent |  | Auction.lua:601,1306,1407 |
| `RegisterAddonMessagePrefix` | MISSING |  | absent |  | Core.lua:3185,3186 |
| `SendAddonMessage` | MISSING |  | absent |  | Core.lua:2775,2776 |
| `SetLootMethod` | MISSING |  | absent |  | QuickTravel.lua:966,974 |
| `UnitAura` | MISSING |  | absent |  | Core.lua:270,271; Classes.lua:114; Hunter.lua:180 |
| `UnitBuff` | MISSING |  | absent |  | PvP.lua:790 |
| `UnitCharacterPoints` | MISSING |  | absent |  | Leveling.lua:289; Pro.lua:525; Talents.lua:179 |
| `UseContainerItem` | MISSING |  | absent |  | Core.lua:250; Errands.lua:196; Inventory.lua:162; Quick.lua:332 |
| `GetBattlefieldScore` | shim-only |  | absent |  | PvP.lua:413 |
| `GetWeaponEnchantInfo` | shim-only |  | absent |  | Classes.lua:147 |
| `HasAction` | shim-only |  | absent |  | Core.lua:457 |
| `IsPlayerSpell` | shim-only |  | absent |  | Core.lua:131,131; Macros.lua:42 |
| `IsSpellKnown` | shim-only |  | absent |  | Core.lua:132,132; Macros.lua:42 |
| `C_ActionBar.HasAction` | documented (actionID) → hasAction:bool |  | mocked |  | Core.lua:456,456 |
| `C_AddOns.GetAddOnInfo` | documented (name) → name:cstring, title:cstring, notes:cstring, loadable:bool, reason:cstring, security:cstring |  | mocked |  | Quests.lua:97 |
| `C_AddOns.GetAddOnMetadata` | documented (name, variable) → value:cstring |  | mocked |  | Core.lua:296; Bugs.lua:115 |
| `C_AddOns.GetNumAddOns` | documented () → numAddOns:number |  | mocked |  | Quests.lua:95 |
| `C_AddOns.IsAddOnLoaded` | documented (name) → loadedOrLoading:bool, loaded:bool |  | mocked |  | Binds.lua:254,254; MinimapPins.lua:82,82; Quests.lua:86,87 |
| `C_AuctionHouse.GetReplicateItemInfo` | documented (index) → name:string, texture:fileID, count:number, qualityID:number, usable:bool, level:number, levelType:string, minBid:BigUInteger, minIncrement:BigUInteger, buyoutPrice:BigUInteger, bidAmount:BigUInteger, highBidder:string, bidderFullName:string, owner:string, ownerFullName:string, saleStatus:number, itemID:number, hasAllInfo:bool |  | mocked |  | Auction.lua:597 |
| `C_AuctionHouse.ReplicateItems` | documented () → - |  | mocked |  | Auction.lua:597 |
| `C_ChatInfo.RegisterAddonMessagePrefix` | documented (prefix) → result:RegisterAddonMessagePrefixResult |  | mocked |  | Core.lua:3183,3183,3184,3184 |
| `C_ChatInfo.SendAddonMessage` | documented (prefix, message, chatType?, target?) → result:SendAddonMessageResult |  | mocked |  | Core.lua:2773,2773,2774,2774 |
| `C_ClassTalents.HasUnspentTalentPoints` | documented () → hasUnspentPoints:bool, numClassPoints:number, numSpecPoints:number |  | mocked |  | Leveling.lua:285,286 |
| `C_CombatLog.IsCombatLogRestricted` | documented () → restricted:bool |  | mocked |  | Core.lua:697,698 |
| `C_Container.ContainerIDToInventoryID` | documented (containerID) → inventoryID:luaIndex |  | mocked |  | Hunter.lua:576 |
| `C_Container.GetContainerItemID` | documented (containerIndex, slotIndex) → containerID:number |  | mocked |  | Hunter.lua:265,265,279; Macros.lua:67 |
| `C_Container.GetContainerItemInfo` | documented (containerIndex, slotIndex) → containerInfo:ContainerItemInfo |  | mocked |  | Core.lua:182,183; Inventory.lua:143,144; Macros.lua:68 |
| `C_Container.GetContainerItemQuestInfo` | documented (containerIndex, slotIndex) → questInfo:ItemQuestInfo |  | mocked |  | Quests.lua:985,986 |
| `C_Container.GetContainerNumFreeSlots` | documented (bagIndex) → numFreeSlots:number, bagFamily:number |  | mocked |  | Core.lua:176; Inventory.lua:123,123 |
| `C_Container.GetContainerNumSlots` | documented (containerIndex) → numSlots:number |  | mocked |  | Core.lua:171; Hunter.lua:260,260; Inventory.lua:115,115; Macros.lua:66 |
| `C_Container.GetItemCooldown` | documented (itemID) → startTime:number, duration:number, enable:number |  | mocked |  | Errands.lua:234; Group.lua:1123,1123; Quests.lua:866,866 |
| `C_Container.PickupContainerItem` | documented (containerIndex, slotIndex) → - |  | mocked |  | Auction.lua:1673 |
| `C_Container.UseContainerItem` | documented (containerIndex, slotIndex, unitToken?, bankType?, reagentBankOpen) → - |  | mocked |  | Core.lua:250; Errands.lua:196; Inventory.lua:161,161 |
| `C_GossipInfo.GetActiveQuests` | documented () → info:table<GossipQuestUIInfo> |  | mocked |  | Quick.lua:242,242 |
| `C_GossipInfo.GetAvailableQuests` | documented () → info:table<GossipQuestUIInfo> |  | mocked |  | Quick.lua:242,242 |
| `C_GossipInfo.GetOptions` | documented () → info:table<GossipOptionUIInfo> |  | mocked |  | Quick.lua:266,266 |
| `C_GossipInfo.SelectActiveQuest` | documented (optionID) → - |  | mocked |  | Quick.lua:251,252 |
| `C_GossipInfo.SelectAvailableQuest` | documented (optionID) → - |  | mocked |  | Quick.lua:257,259 |
| `C_GossipInfo.SelectOption` | documented (optionID, text?, confirmed?) → - |  | mocked |  | Quick.lua:276,276; QuickTravel.lua:530,530 |
| `C_Item.GetDetailedItemLevelInfo` | documented (itemInfo) → actualItemLevel:number, previewLevel:bool, sparseItemLevel:number |  | mocked |  | Group.lua:1883; Stats.lua:126,126 |
| `C_Item.GetItemCooldown` | documented (itemInfo) → startTimeSeconds:number, durationSeconds:number, enableCooldownTimer:bool |  | mocked |  | Errands.lua:235 |
| `C_Item.GetItemCount` | documented (itemInfo, includeBank, includeUses, includeReagentBank, includeAccountBank) → count:number |  | mocked |  | Core.lua:161; Classes.lua:207,207; Hunter.lua:252,253 |
| `C_Item.GetItemIconByID` | documented (itemInfo) → icon:fileID |  | mocked |  | Macros.lua:1283,1283 |
| `C_Item.GetItemInfo` | documented (itemInfo) → itemName:cstring, itemLink:cstring, itemQuality:ItemQuality, itemLevel:number, itemMinLevel:number, itemType:cstring, itemSubType:cstring, itemStackCount:number, itemEquipLoc:cstring, itemTexture:fileID, sellPrice:number, classID:number, subclassID:number, bindType:number, expansionID:number, setID:number, isCraftingReagent:bool, itemDescription:cstring |  | mocked |  | Core.lua:148; Inventory.lua:131,131; Macros.lua:49 |
| `C_Item.GetItemInfoInstant` | documented (itemInfo) → itemID:number, itemType:cstring, itemSubType:cstring, itemEquipLoc:cstring, icon:fileID, classID:number, subClassID:number |  | mocked |  | Highlight.lua:113; Hunter.lua:235,236 |
| `C_Item.GetItemLinkByGUID` | documented (itemGUID) → itemLink:string |  | mocked |  | Gear.lua:1174,1175 |
| `C_Item.GetItemNameByID` | documented (itemInfo) → itemName:string |  | mocked |  | Core.lua:153,154; Hunter.lua:434,434 |
| `C_Item.GetItemSpell` | documented (itemInfo) → spellName:cstring, spellID:number |  | mocked |  | Macros.lua:54 |
| `C_Item.GetItemStats` | documented (itemLink) → statTable:LuaValueVariant |  | mocked |  | Gear.lua:170,170 |
| `C_Item.RequestLoadItemDataByID` | documented (itemInfo) → - |  | mocked |  | Auction.lua:633 |
| `C_Map.CanSetUserWaypointOnMap` | documented (uiMapID) → canSet:bool |  | mocked |  | Compass.lua:244,244 |
| `C_Map.ClearUserWaypoint` | documented () → - |  | mocked |  | Compass.lua:280,281 |
| `C_Map.GetAreaInfo` | documented (areaID) → name:cstring |  | mocked |  | Compass.lua:412,413; Dungeon.lua:502,502; Gear.lua:454,454; Pro.lua:189,189; Quests.lua:410,410 |
| `C_Map.GetBestMapForUnit` | documented (unitToken) → uiMapID:number |  | mocked |  | Abandon.lua:517,518; Compass.lua:167,168; Errands.lua:255; Group.lua:1046,1086,1202,1788…; Leveling.lua:152,152; PathDraw.lua:211,212,377 (+4 files) |
| `C_Map.GetMapInfo` | documented (uiMapID) → info:UiMapDetails |  | mocked |  | Compass.lua:177,177; Group.lua:1007; Quests.lua:79,80; Radar.lua:435,435 |
| `C_Map.GetMapWorldSize` | documented (uiMapID) → width:number, height:number |  | mocked |  | Compass.lua:91,92 |
| `C_Map.GetPlayerMapPosition` | documented (uiMapID, unitToken) → position:vector2 |  | mocked |  | Compass.lua:167,170; Group.lua:1789,1789; PathDraw.lua:211,214; Prof.lua:491,491; Radar.lua:185,186,315,316 |
| `C_Map.GetUserWaypoint` | documented () → point:UiMapPoint |  | mocked |  | Compass.lua:260,261 |
| `C_Map.GetWorldPosFromMapPos` | documented (uiMapID, mapPosition) → continentID:number, worldPosition:vector2 |  | mocked |  | Compass.lua:77,78; Radar.lua:99,100 |
| `C_Map.HasUserWaypoint` | documented () → hasUserWaypoint:bool |  | mocked |  | Compass.lua:260,260,309,309 |
| `C_Map.SetUserWaypoint` | documented (point) → wasSet:bool |  | mocked |  | Compass.lua:243,246 |
| `C_Minimap.GetNumTrackingTypes` | documented () → numTrackingTypes:number |  | mocked |  | Edge2.lua:411,413 |
| `C_Minimap.GetTrackingInfo` | documented (spellIndex) → trackingInfo:MinimapScriptTrackingInfo |  | mocked |  | Edge2.lua:411,414 |
| `C_Minimap.SetTracking` | documented (index, on) → - |  | mocked |  | Edge2.lua:441,442 |
| `C_MountJournal.GetMountIDs` | documented () → mountIDs:table<number> |  | mocked |  | Quick.lua:480,482 |
| `C_MountJournal.GetMountInfoByID` | documented (mountID) → name:cstring, spellID:number, icon:fileID, isActive:bool, isUsable:bool, sourceType:number, isFavorite:bool, isFactionSpecific:bool, faction:PvPFaction, shouldHideOnChar:bool, isCollected:bool, mountID:number, isSteadyFlight:bool |  | mocked |  | Quick.lua:480,483 |
| `C_NamePlate.GetNamePlateForUnit` | documented (unitToken, includeForbidden) → nameplate:NamePlateFrame |  | mocked |  | PvPMark.lua:332,332 |
| `C_NamePlate.GetNamePlates` | documented () → nameplates:table<NamePlateFrame> |  | mocked |  | PvPMark.lua:362,363; SelfTest.lua:237; Speed.lua:190,191 |
| `C_PetInfo.GetPetFoodTypes` | documented () → foodTypes:table<string> |  | mocked |  | Hunter.lua:108 |
| `C_PetInfo.GetPetHappiness` | documented () → happiness:number, damagePercentage:number, loyaltyRate:number |  | mocked |  | Hunter.lua:105 |
| `C_PetInfo.GetPetLoyalty` | documented () → loyaltyName:string |  | mocked |  | Hunter.lua:106 |
| `C_PetInfo.GetPetTrainingPoints` | documented () → totalPoints:number, usedPoints:number |  | mocked |  | Hunter.lua:107 |
| `C_PvP.GetBattlefieldFlagPosition` | documented (flagIndex, uiMapId) → uiPosx:number, uiPosy:number, flagTexture:number |  | mocked |  | PvP.lua:381,382 |
| `C_PvP.GetScoreInfo` | documented (offsetIndex) → info:PVPScoreInfo | secret in PvP match | mocked | secret: modelled by flags (forever_ref.lua) | PvP.lua:405,406 |
| `C_QuestLog.AddQuestWatch` | documented (questID) → wasWatched:bool |  | mocked |  | Region.lua:254 |
| `C_QuestLog.GetDistanceSqToQuest` | documented (questID) → distanceSq:number, onContinent:bool |  | mocked |  | Quests.lua:205,206; Region.lua:116 |
| `C_QuestLog.GetInfo` | documented (questLogIndex) → info:QuestInfo |  | mocked |  | Core.lua:420,422; Abandon.lua:466; Export.lua:481,484; Group.lua:1263,1265; Leveling.lua:181,184; Pro.lua:242,243 (+3 files) |
| `C_QuestLog.GetMaxNumQuestsCanAccept` | documented () → maxNumQuestsCanAccept:number |  | mocked |  | Abandon.lua:409,409; Group.lua:1154,1154; Quick.lua:172,172; WindowPages.lua:141 |
| `C_QuestLog.GetNextWaypoint` | documented (questID) → mapID:number, x:number, y:number |  | mocked |  | Compass.lua:529,530; Quests.lua:232,233 |
| `C_QuestLog.GetNumQuestLogEntries` | documented () → numShownEntries:number, numQuests:number |  | mocked |  | Core.lua:420,421; Export.lua:481,482; Gear.lua:514,515; Group.lua:1263,1264; Leveling.lua:181,182; Pro.lua:238,238 (+2 files) |
| `C_QuestLog.GetQuestIDForLogIndex` | documented (questLogIndex) → questID:number |  | mocked |  | Gear.lua:514,516 |
| `C_QuestLog.GetQuestObjectives` | documented (questID) → objectives:table<QuestObjectiveInfo> |  | mocked |  | Export.lua:489; Group.lua:1273; Leveling.lua:188,188; Pro.lua:249,249; Quests.lua:258 |
| `C_QuestLog.GetQuestTagInfo` | documented (questID) → info:QuestTagInfo |  | mocked |  | Export.lua:493,493; Pro.lua:254,254; Quests.lua:173,174 |
| `C_QuestLog.GetQuestWatchType` | documented (questID) → watchType:QuestWatchType |  | mocked |  | Quests.lua:199,199 |
| `C_QuestLog.GetTimeAllowed` | documented (questID) → totalTime:number, elapsedTime:number |  | mocked |  | Edge2.lua:346; Quests.lua:705,707 |
| `C_QuestLog.GetTitleForQuestID` | documented (questID) → title:cstring |  | mocked |  | Gear.lua:502,502; Session.lua:204 |
| `C_QuestLog.IsComplete` | documented (questID) → isComplete:bool |  | mocked |  | Core.lua:424,424; Export.lua:488; Group.lua:1271; Leveling.lua:187,187; Pro.lua:248,248; Quests.lua:256 (+1 files) |
| `C_QuestLog.IsFailed` | documented (questID) → isFailed:bool |  | mocked |  | Export.lua:495,495; Pro.lua:256,256; Quests.lua:257 |
| `C_QuestLog.IsPushableQuest` | documented (questID) → isPushable:bool |  | mocked |  | Group.lua:1325,1325; Quick.lua:424,424 |
| `C_QuestLog.IsQuestFlaggedCompleted` | documented (questID) → isCompleted:bool |  | mocked |  | Edge2.lua:140; Gear.lua:459; Quests.lua:442,566,1000,1000 |
| `C_QuestLog.IsUnitOnQuest` | documented (unit, questID) → isOnQuest:bool |  | mocked |  | Group.lua:1334; Quick.lua:430,431; Tracker.lua:342,347 |
| `C_QuestLog.ReadyForTurnIn` | documented (questID) → readyForTurnIn:bool |  | mocked |  | Quests.lua:256 |
| `C_SkillInfo.GetNumSkillLines` | documented () → numSkillLines:number |  | mocked |  | Core.lua:355,356 |
| `C_SkillInfo.GetSkillLineInfo` | documented (index) → skillLineAttributes:SkillLineAttributes |  | mocked |  | Core.lua:355,357 |
| `C_Spell.GetSpellCooldown` | documented (spellIdentifier) → spellCooldownInfo:SpellCooldownInfo | secret: cooldowns (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:138,139 |
| `C_Spell.GetSpellInfo` | documented (spellIdentifier) → spellInfo:SpellInfo |  | mocked |  | Core.lua:106,107,119,120; Binds.lua:168,169; Classes.lua:78,79; Hunter.lua:128,129; Macros.lua:32,33,202,203… |
| `C_Spell.GetSpellName` | documented (spellIdentifier) → name:cstring |  | mocked |  | Core.lua:109,110; PvPMark.lua:134,134 |
| `C_Spell.GetSpellTexture` | documented (spellIdentifier) → iconID:fileID, originalIconID:fileID, conditionalIconID:fileID |  | mocked |  | Macros.lua:1276,1276 |
| `C_Spell.IsSpellInRange` | documented (spellIdentifier, targetUnit?) → inRange:bool |  | mocked |  | PvPMark.lua:137,137 |
| `C_SpellBook.FindSpellBookSlotForSpell` | documented (spellIdentifier, includeHidden, includeFlyouts, includeFutureSpells, includeOffSpec) → spellBookItemSlotIndex:luaIndex, spellBookItemSpellBank:SpellBookSpellBank |  | mocked |  | Macros.lua:196,198,1009 |
| `C_SpellBook.GetNumSpellBookSkillLines` | documented () → numSpellBookSkillLines:number |  | mocked |  | Core.lua:382,383 |
| `C_SpellBook.GetSpellBookItemName` | documented (spellBookItemSlotIndex, spellBookItemSpellBank) → name:string, subName:string |  | mocked |  | Core.lua:378,392; Finder.lua:185,185 |
| `C_SpellBook.GetSpellBookSkillLineInfo` | documented (skillLineIndex) → skillLineInfo:SpellBookSkillLineInfo |  | mocked |  | Core.lua:382,384 |
| `C_SpellBook.HasPetSpells` | documented () → numPetSpells:number, petNameToken:string |  | mocked |  | Core.lua:381 |
| `C_SpellBook.IsSpellBookItemPassive` | documented (spellBookItemSlotIndex, spellBookItemSpellBank) → isPassive:bool |  | mocked |  | Core.lua:395,395 |
| `C_SpellBook.IsSpellKnown` | documented (spellID, spellBank) → isKnown:bool |  | mocked |  | Core.lua:130,130 |
| `C_SuperTrack.GetSuperTrackedQuestID` | documented () → questID:number |  | mocked |  | Compass.lua:288,289,604,604 |
| `C_SuperTrack.IsSuperTrackingUserWaypoint` | documented () → isSuperTracking:bool |  | mocked |  | Compass.lua:296,296 |
| `C_SuperTrack.SetSuperTrackedUserWaypoint` | documented (superTracked) → - |  | mocked |  | Compass.lua:247,247 |
| `C_Timer.After` | documented (seconds, callback) → - |  | mocked |  | Core.lua:520,520; Binds.lua:41,41; Macros.lua:93,93 |
| `C_Timer.NewTicker` | documented (seconds, callback, iterations?) → cbObject:TickerCallback |  | mocked |  | Core.lua:624,625 |
| `C_TradeSkillUI.GetBaseProfessionInfo` | documented () → info:ProfessionInfo |  | mocked |  | Prof.lua:279,279 |
| `C_TradeSkillUI.GetRecipeInfo` | documented (recipeSpellID, recipeLevel?) → recipeInfo:TradeSkillRecipeInfo |  | mocked |  | Prof.lua:278,286 |
| `C_UIWidgetManager.GetAllWidgetsBySetID` | documented (setID) → widgets:table<UIWidgetInfo> |  | mocked |  | PvP.lua:315,317 |
| `C_UIWidgetManager.GetIconAndTextWidgetVisualizationInfo` | documented (widgetID) → widgetInfo:IconAndTextWidgetVisualizationInfo |  | mocked |  | PvP.lua:301,303 |
| `C_UIWidgetManager.GetTopCenterWidgetSetID` | documented () → setID:number |  | mocked |  | PvP.lua:315,316 |
| `C_UnitAuras.GetAuraDataByIndex` | documented (unit, index, filter?) → aura:AuraData | secret: auras (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:268,269; Classes.lua:106,107; Group.lua:1935,1940,1941; Hunter.lua:174,175; PvP.lua:785,786 |
| `CheckInteractDistance` | documented (unitGUID, distIndex) → result:bool |  | mocked |  | Core.lua:321,323,324 |
| `ClearCursor` | documented () → - |  | mocked |  | Auction.lua:1677 |
| `GetAddOnMemoryUsage` | documented (name) → result:number |  | mocked |  | Core.lua:1464 |
| `GetBindLocation` | documented () → result:cstring |  | mocked |  | Errands.lua:291; Export.lua:559; Pro.lua:557,576; Quick.lua:673; QuickTravel.lua:491; Route.lua:325,888 (+2 files) |
| `GetBlockChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:160 |
| `GetBuildInfo` | documented () → buildVersion:cstring, buildNumber:cstring, buildDate:cstring, interfaceVersion:number, localizedVersion:cstring, buildInfo:string |  | mocked |  | Auction.lua:2155 |
| `GetCombatRating` | documented (ratingIndex) → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:153 |
| `GetCritChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:145 |
| `GetCursorPosition` | documented () → posX:number, posY:number |  | mocked |  | Hub.lua:436; Todo.lua:1309; ZonePlan.lua:1119 |
| `GetDodgeChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:160 |
| `GetFramerate` | documented () → framerate:number |  | mocked |  | Core.lua:859 |
| `GetHaste` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:152 |
| `GetHitModifier` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:150 |
| `GetInstanceInfo` | documented () → name:cstring, instanceType:cstring, difficultyID:number, difficultyName:cstring, maxPlayers:number, dynamicDifficulty:number, isDynamic:bool, instanceID:number, instanceGroupSize:number, lfgDungeonID:number, hasWorldTier:bool |  | mocked |  | Errands.lua:499 |
| `GetManaRegen` | documented () → baseManaRegen:number, castingManaRegen:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:176 |
| `GetMeleeHaste` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:152 |
| `GetMinimapZoneText` | documented () → text:cstring |  | mocked |  | Auction.lua:648 |
| `GetMoney` | documented () → result:number |  | mocked |  | Core.lua:306; Auction.lua:1064; Errands.lua:368,397; Gear.lua:365; Inventory.lua:515; Leveling.lua:355 (+3 files) |
| `GetPVPSessionStats` | documented () → honorableKills:number, dishonorableKills:number |  | mocked |  | PvP.lua:647 |
| `GetParryChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:160 |
| `GetPlayerFacing` | documented () → result:number |  | mocked |  | Compass.lua:766,845; Horizon.lua:161,193; MinimapPins.lua:152; PathDraw.lua:235,328; Radar.lua:998 |
| `GetPowerRegen` | documented () → basePowerRegen:number, castingPowerRegen:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:177 |
| `GetRangedCritChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:146 |
| `GetRealZoneText` | documented (mapID?) → text:cstring |  | mocked |  | Core.lua:2655; Abandon.lua:507; Edge2.lua:72; Leveling.lua:352; Pro.lua:61; Prof.lua:459,501,524 (+6 files) |
| `GetRealmName` | documented () → realmName:cstring |  | mocked |  | Core.lua:958,958; Auction.lua:671; Hunter.lua:965; Inventory.lua:57; Leveling.lua:51,342 |
| `GetScreenHeight` | documented () → height:number |  | mocked |  | UI.lua:1797,1797 |
| `GetScreenWidth` | documented () → width:number |  | mocked |  | UI.lua:1796,1796 |
| `GetServerTime` | documented () → time:number |  | mocked |  | Auction.lua:68; Stats.lua:50 |
| `GetSpellCritChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:148 |
| `GetSpellHitModifier` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:151 |
| `GetSubZoneText` | documented () → text:cstring |  | mocked |  | Core.lua:2656; Auction.lua:648; Edge2.lua:146; Leveling.lua:353; MinimapPins.lua:189; Pro.lua:502 |
| `GetTime` | documented () → time:number |  | mocked |  | Core.lua:33,33; Abandon.lua:394; Auction.lua:581,667; Classes.lua:69; Compass.lua:48; Edge2.lua:63 (+27 files) |
| `GetUnitSpeed` | documented (unit) → currentSpeed:number, runSpeed:number, flightSpeed:number, swimSpeed:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Compass.lua:185; Export.lua:545; Hunter.lua:809; NavTime.lua:78; PathDraw.lua:328; Planner.lua:455 (+2 files) |
| `GetXPExhaustion` | documented () → result:number |  | mocked |  | Core.lua:2654; Export.lua:555; Leveling.lua:265,350; Pro.lua:567; Quests.lua:365; Todo.lua:361 |
| `GetZoneText` | documented () → text:cstring |  | mocked |  | Core.lua:2655; Abandon.lua:507; Auction.lua:648; Edge2.lua:72; Pro.lua:61; Prof.lua:459 (+6 files) |
| `IsAltKeyDown` | documented () → down:bool |  | mocked |  | Tracker.lua:83,83 |
| `IsControlKeyDown` | documented () → down:bool |  | mocked |  | Tracker.lua:82,82 |
| `IsInInstance` | documented () → isInInstance:bool, instanceType:cstring |  | mocked |  | Core.lua:1313,1751,1925,2782; Compass.lua:164; Edge2.lua:68; Errands.lua:497; PvP.lua:86; PvPMark.lua:102 (+3 files) |
| `IsIndoors` | documented () → result:bool |  | mocked |  | MinimapPins.lua:150; PathDraw.lua:231 |
| `IsLoggedIn` | documented () → result:bool |  | mocked |  | Hunter.lua:1071 |
| `IsMounted` | documented () → result:bool |  | mocked |  | Core.lua:1929; Hunter.lua:453; NavTime.lua:63; Planner.lua:453; Stats.lua:55 |
| `IsResting` | documented () → result:bool |  | mocked |  | Core.lua:1930; Edge2.lua:69; Errands.lua:386; Leveling.lua:267,351,391; Pro.lua:501,569; Quick.lua:703 (+2 files) |
| `IsShiftKeyDown` | documented () → down:bool |  | mocked |  | Compass.lua:887,887; Finder.lua:752,759; Hunter.lua:794; Macros.lua:141,141,1467,1467; Prof.lua:731,731; Quests.lua:836,836 (+3 files) |
| `IsSwimming` | documented (unit?) → result:bool |  | mocked |  | NavTime.lua:64 |
| `ResetCursor` | documented () → - |  | mocked |  | UI.lua:372 |
| `SetCursor` | documented (name?) → result:bool |  | mocked |  | UI.lua:372 |
| `UnitAffectingCombat` | documented (unit) → result:bool |  | mocked |  | Classes.lua:167; Hunter.lua:322,649,660; Inventory.lua:52; Leveling.lua:46 |
| `UnitArmor` | documented (unit) → base:number, effective:number, real:number, bonus:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:158 |
| `UnitAttackPower` | documented (unit) → attackPower:number, posBuff:number, negBuff:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:143 |
| `UnitAttackSpeed` | documented (unit) → attackSpeed:number, offhandAttackSpeed:number, rangedAttackSpeed:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:172 |
| `UnitCanAttack` | documented (unit, target) → result:bool |  | mocked |  | Core.lua:1755; Edge2.lua:311; Export.lua:197,245; Hunter.lua:630; Pro.lua:390; PvP.lua:498 (+5 files) |
| `UnitCastingInfo` | documented (unit) → name:cstring, displayName:string, textureID:fileID, startTimeMs:number, endTimeMs:number, isTradeskill:bool, castID:WOWGUID, notInterruptible:bool, castingSpellID:number, castBarID:UnitCastBarID, delayTimeMs:number | secret: casts of others (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:444,445; Hunter.lua:834 |
| `UnitClass` | documented (unit) → className:cstring, classFilename:cstring, classID:number | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:965,966,2649; Classes.lua:247; Export.lua:635; Finder.lua:66; Hunter.lua:1044; Inventory.lua:352 (+10 files) |
| `UnitClassification` | documented (unit) → result:cstring |  | mocked |  | Hunter.lua:662; Pro.lua:415; Radar.lua:373; Stats.lua:259 |
| `UnitCreatureFamily` | documented (unit) → name:cstring, id:number | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Finder.lua:207,245; Hunter.lua:526,856,938 |
| `UnitCreatureType` | documented (unit) → name:cstring, id:number | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Prof.lua:585 |
| `UnitDamage` | documented (unit) → minDamage:number, maxDamage:number, offhandMinDamage:number, offhandMaxDamage:number, posBuff:number, negBuff:number, percent:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:171 |
| `UnitExists` | documented (unit?) → result:bool |  | mocked |  | Core.lua:1754; Edge2.lua:310; Errands.lua:439; Finder.lua:178; Hunter.lua:413,452,499,523…; Planner.lua:1019 (+7 files) |
| `UnitFactionGroup` | documented (unitName, checkDisplayRace) → factionGroupTag:cstring, localized:cstring |  | mocked |  | Auction.lua:671; Export.lua:556; Finder.lua:68,586; Leveling.lua:245; PvP.lua:76; Quests.lua:695 (+3 files) |
| `UnitGUID` | documented (unit) → result:WOWGUID | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Export.lua:224,243; Finder.lua:250; Hunter.lua:661; Pro.lua:388; PvP.lua:514; PvPMark.lua:125 (+1 files) |
| `UnitHealth` | documented (unit, usePredicted) → result:number | secret ALWAYS | mocked | secret: modelled by flags (forever_ref.lua) | Errands.lua:423; Pro.lua:608; PvP.lua:347; QuickTravel.lua:841; Stats.lua:213,234,241,251… |
| `UnitHealthMax` | documented (unit) → result:number | secret: NPC max health (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Errands.lua:423; Pro.lua:608; PvP.lua:347; QuickTravel.lua:841; Stats.lua:179,213,234,252 |
| `UnitInParty` | documented (unit?, partyIndex?) → result:bool |  | mocked |  | Core.lua:322 |
| `UnitInRaid` | documented (unit?, partyIndex?) → result:luaIndex | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:322 |
| `UnitIsAFK` | documented (unit) → result:bool | secret in chat lockdown | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:1933; QuickTravel.lua:839; Stats.lua:825 |
| `UnitIsConnected` | documented (unit) → isConnected:bool |  | mocked |  | QuickTravel.lua:837 |
| `UnitIsDead` | documented (unit) → result:bool |  | mocked |  | Edge2.lua:310; Export.lua:225,245; Hunter.lua:460,631,863,939; Pro.lua:394; Prof.lua:582; Radar.lua:378 (+2 files) |
| `UnitIsDeadOrGhost` | documented (unit) → result:bool |  | mocked |  | Core.lua:1924; Classes.lua:224; Compass.lua:1028; Hunter.lua:454; PvPMark.lua:118,405; QuickTravel.lua:838 (+3 files) |
| `UnitIsEnemy` | documented (unit, target) → result:bool |  | mocked |  | Pro.lua:390 |
| `UnitIsGhost` | documented (unit) → result:bool |  | mocked |  | Core.lua:1923; Compass.lua:1029; Radar.lua:1145 |
| `UnitIsGroupAssistant` | documented (unit) → isAssistant:bool | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | QuickTravel.lua:921 |
| `UnitIsGroupLeader` | documented (unit, partyCategory?) → isLeader:bool | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | QuickTravel.lua:912 |
| `UnitIsPVP` | documented (unit) → result:bool | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | PvPMark.lua:115,115 |
| `UnitIsPVPFreeForAll` | documented (unit) → result:bool |  | mocked |  | PvPMark.lua:115 |
| `UnitIsPlayer` | documented (unit?, partyIndex?) → result:bool |  | mocked |  | Core.lua:1754; Edge2.lua:310; Hunter.lua:631; Pro.lua:391; Prof.lua:582; PvP.lua:497 (+3 files) |
| `UnitIsTapDenied` | documented (unit) → result:bool |  | mocked |  | Edge2.lua:313; Export.lua:248; Pro.lua:409 |
| `UnitIsUnit` | documented (unit1, unit2) → result:bool | secret: compound-token comparison (instances) | mocked | secret: modelled by flags (forever_ref.lua) | PvPMark.lua:124; Radar.lua:311 |
| `UnitLevel` | documented (name) → result:number |  | mocked |  | Core.lua:2652; Abandon.lua:488; Auction.lua:974,1055; Classes.lua:226; Edge2.lua:66; Errands.lua:339,368,378,403… (+25 files) |
| `UnitName` | documented (unit) → unitName:cstring, unitServer:cstring | secret: name of NPCs (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:957,957,2931,2931; Auction.lua:101; Edge2.lua:312; Export.lua:823; Finder.lua:264; Hunter.lua:857,965 (+14 files) |
| `UnitOnTaxi` | documented (unit) → result:bool |  | mocked |  | Core.lua:1928; Classes.lua:225; Export.lua:301; Hunter.lua:454; NavTime.lua:207; Stats.lua:54 |
| `UnitPlayerControlled` | documented (unit?) → result:bool |  | mocked |  | Export.lua:246; Finder.lua:246 |
| `UnitPosition` | documented (unit) → positionX:number, positionY:number, positionZ:number, mapID:number |  | mocked |  | Core.lua:310,311,312; Compass.lua:849; NavTime.lua:50; Planner.lua:1075; Radar.lua:193,321; Stats.lua:441,481 |
| `UnitPower` | documented (unitToken, powerType?, unmodified) → power:number | secret: power of others (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Errands.lua:427; Pro.lua:612; QuickTravel.lua:844; Stats.lua:214 |
| `UnitPowerMax` | documented (unitToken, powerType?, unmodified) → maxPower:number | secret: max power of others (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Errands.lua:427; Pro.lua:611; QuickTravel.lua:844; Stats.lua:180,214 |
| `UnitPowerType` | documented (unit, index) → powerType:PowerType, powerTypeToken:string, rgbX:number, rgbY:number, rgbZ:number |  | mocked |  | Errands.lua:425; Inventory.lua:353; QuickTravel.lua:772,843 |
| `UnitQuestTrivialLevelRange` | documented (unit) → levelRange:number |  | mocked |  | Quests.lua:111 |
| `UnitRace` | documented (unit) → localizedRaceName:cstring, englishRaceName:cstring, raceID:number | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:2651; Export.lua:626; Leveling.lua:345; Quests.lua:448,499 |
| `UnitRangedAttackPower` | documented (unit) → attackPower:number, posBuff:number, negBuff:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:144 |
| `UnitRangedDamage` | documented (unit) → speed:number, minDamage:number, maxDamage:number, posBuff:number, negBuff:number, percent:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Hunter.lua:766; Stats.lua:174 |
| `UnitResistance` | documented (unit, damageClass) → baseResistance:number, realResistance:number, effectiveResistance:number, bonusResistance:number | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:163 |
| `UnitStat` | documented (unit, index) → currentStat:number, effectiveStat:number, statPositiveBuff:number, statNegativeBuff:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:142 |
| `UnitXP` | documented (unit) → result:number |  | mocked |  | Core.lua:2653; Abandon.lua:500; Edge2.lua:332; Export.lua:330; Leveling.lua:348; Quests.lua:356 (+3 files) |
| `UnitXPMax` | documented (unit) → result:number |  | mocked |  | Core.lua:2653; Abandon.lua:500; Edge2.lua:332; Export.lua:330; Leveling.lua:266,349; Pro.lua:566 (+4 files) |
| `UpdateAddOnMemoryUsage` | documented () → - |  | mocked |  | Core.lua:1463 |
| `debugprofilestop` | documented () → elapsedMilliseconds:number |  | mocked |  | Core.lua:465; Auction.lua:582; Export.lua:46; Planner.lua:46; Radar.lua:989,1069; ZonePlan.lua:994,1008 |
| `issecretvalue` | documented (value) → isSecret:bool |  | mocked |  | Core.lua:93,695,1914,1915; Abandon.lua:395; Compass.lua:12; Edge2.lua:60; Errands.lua:51; Export.lua:13,42 (+25 files) |
| `AcceptQuest` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:182 |
| `AutoLootMailItem` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:401,401 |
| `CanMerchantRepair` | c-global |  | mocked |  | Inventory.lua:468; MinimapPins.lua:576,586; Quick.lua:327 |
| `CompleteQuest` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:190 |
| `CreateFrame` | c-global |  | mocked |  | Core.lua:497,499,591,592…; Auction.lua:2266,2273,2275,2294…; Classes.lua:251,289; Compass.lua:874,875; Errands.lua:206; Export.lua:1003 (+21 files) |
| `CreateMacro` | c-global |  | mocked |  | Macros.lua:791,824,829,1008; PvP.lua:616 |
| `DeleteMacro` | c-global |  | mocked |  | Macros.lua:1199 |
| `EditMacro` | c-global |  | mocked |  | Macros.lua:791,813; PvP.lua:614 |
| `GetActionInfo` | c-global |  | mocked |  | Macros.lua:1294,1296 |
| `GetActiveTitle` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:282 |
| `GetAvailableQuestInfo` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:287 |
| `GetBattlefieldEstimatedWaitTime` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:714 |
| `GetBattlefieldStatus` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:706 |
| `GetBattlefieldTimeWaited` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:713 |
| `GetBattlefieldWinner` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:726 |
| `GetBindingAction` | c-global |  | mocked |  | Auction.lua:1893,1894 |
| `GetBindingKey` | c-global |  | mocked |  | Auction.lua:1885 |
| `GetGuildInfo` | c-global |  | mocked |  | Core.lua:2659; PvP.lua:511 |
| `GetInboxHeaderInfo` | c-global |  | mocked |  | Auction.lua:2015; Errands.lua:121; Quick.lua:374 |
| `GetInboxInvoiceInfo` | c-global |  | mocked |  | Auction.lua:2023 |
| `GetInboxItem` | c-global |  | mocked |  | Auction.lua:2024 |
| `GetInboxNumItems` | c-global |  | mocked |  | Auction.lua:2013; Errands.lua:117; Quick.lua:371 |
| `GetInventoryItemCount` | c-global |  | mocked |  | Errands.lua:435; Hunter.lua:563; QuickTravel.lua:767,849; Stats.lua:832 |
| `GetInventoryItemDurability` | c-global |  | mocked |  | Errands.lua:442; Inventory.lua:221; QuickTravel.lua:745 |
| `GetInventoryItemID` | c-global |  | mocked |  | Classes.lua:149; Finder.lua:429; Gear.lua:398,800; Hunter.lua:556,561,579; Leveling.lua:214; Macros.lua:262,444 |
| `GetInventoryItemLink` | c-global |  | mocked |  | Gear.lua:188,396; Pro.lua:144; Stats.lua:122 |
| `GetLootSlotInfo` | c-global |  | mocked |  | MinimapPins.lua:175; Pro.lua:457; Quests.lua:967; Stats.lua:746 |
| `GetLootSlotLink` | c-global |  | absent | not mocked (FrameXML/C global) | Export.lua:265; Stats.lua:747 |
| `GetMacroInfo` | c-global |  | mocked |  | Macros.lua:687,775,804,816… |
| `GetMaxBattlefieldID` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:702 |
| `GetMerchantItemID` | c-global |  | absent | not mocked (FrameXML/C global) | Inventory.lua:393; QuickTravel.lua:570 |
| `GetMerchantItemLink` | c-global |  | absent | not mocked (FrameXML/C global) | Inventory.lua:393; QuickTravel.lua:572 |
| `GetMerchantNumItems` | c-global |  | mocked |  | Inventory.lua:385; QuickTravel.lua:568 |
| `GetNumActiveQuests` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:281 |
| `GetNumAvailableQuests` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:286 |
| `GetNumBattlefieldScores` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:421 |
| `GetNumGroupMembers` | c-global |  | mocked |  | Core.lua:1310,2661; Abandon.lua:506; Edge2.lua:70; Export.lua:552; Finder.lua:208,610; Macros.lua:634 (+9 files) |
| `GetNumLootItems` | c-global |  | mocked |  | Export.lua:259; MinimapPins.lua:173; Pro.lua:456; Quests.lua:964; Stats.lua:742 |
| `GetNumMacros` | c-global |  | mocked |  | Core.lua:301; Macros.lua:682 |
| `GetNumQuestChoices` | c-global |  | mocked |  | Pro.lua:482; Quick.lua:195,222 |
| `GetNumQuestLeaderBoards` | c-global |  | mocked |  | Export.lua:510; Leveling.lua:171; Pro.lua:265; Quests.lua:279 |
| `GetNumQuestLogChoices` | c-global |  | absent | not mocked (FrameXML/C global) | Abandon.lua:466,467; Gear.lua:513; ZonePlan.lua:563 |
| `GetNumQuestLogRewards` | c-global |  | absent | not mocked (FrameXML/C global) | ZonePlan.lua:564 |
| `GetNumShapeshiftForms` | c-global |  | mocked |  | Macros.lua:493 |
| `GetNumSubgroupMembers` | c-global |  | mocked |  | Todo.lua:128; Tracker.lua:343 |
| `GetNumTrainerServices` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:337 |
| `GetObjectiveText` | c-global |  | absent | not mocked (FrameXML/C global) | Pro.lua:474 |
| `GetProfessionInfo` | c-global |  | mocked |  | Gear.lua:473; Prof.lua:242 |
| `GetProfessions` | c-global |  | mocked |  | Gear.lua:473; Prof.lua:242 |
| `GetQuestID` | c-global |  | absent | not mocked (FrameXML/C global) | Pro.lua:472,478 |
| `GetQuestItemInfo` | c-global |  | mocked |  | Quick.lua:201 |
| `GetQuestItemLink` | c-global |  | mocked |  | Pro.lua:485; Quick.lua:200 |
| `GetQuestLogItemLink` | c-global |  | absent | not mocked (FrameXML/C global) | Abandon.lua:466,469; Gear.lua:513 |
| `GetQuestLogLeaderBoard` | c-global |  | mocked |  | Export.lua:511; Leveling.lua:173; Pro.lua:266; Quests.lua:281 |
| `GetQuestLogQuestText` | c-global |  | absent | not mocked (FrameXML/C global) | Quests.lua:653,654 |
| `GetQuestLogRewardXP` | c-global |  | mocked |  | Pro.lua:295; Quests.lua:159,160,163 |
| `GetQuestLogSpecialItemInfo` | c-global |  | mocked |  | Quests.lua:189,190 |
| `GetQuestMoneyToGet` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:189 |
| `GetQuestReward` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:232 |
| `GetQuestText` | c-global |  | absent | not mocked (FrameXML/C global) | Pro.lua:474 |
| `GetQuestUiMapID` | c-global |  | mocked |  | Quests.lua:227 |
| `GetRepairAllCost` | c-global |  | mocked |  | Inventory.lua:469; Quick.lua:328; Stats.lua:505,509 |
| `GetShapeshiftFormInfo` | c-global |  | mocked |  | Macros.lua:497 |
| `GetTaxiMapID` | c-global |  | absent | not mocked (FrameXML/C global) | QuickTravel.lua:191 |
| `GetTitleText` | c-global |  | absent | not mocked (FrameXML/C global) | Session.lua:429 |
| `GetTrainerServiceCost` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:344,349 |
| `GetTrainerServiceInfo` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:341 |
| `HasNewMail` | c-global |  | absent | not mocked (FrameXML/C global) | QuickTravel.lua:777 |
| `InCombatLockdown` | c-global |  | mocked |  | Core.lua:321,880,1506,1507…; Classes.lua:166; Errands.lua:192,202,380; Hunter.lua:321,1118; Inventory.lua:52; Leveling.lua:46 (+4 files) |
| `IsFishingLoot` | c-global |  | absent | not mocked (FrameXML/C global) | Stats.lua:660 |
| `IsInGroup` | c-global |  | mocked |  | Core.lua:1309,2660,2878; Macros.lua:633; Planner.lua:511; Talents.lua:302 |
| `IsInRaid` | c-global |  | mocked |  | Core.lua:2877; Export.lua:560; Planner.lua:419,1002; Quick.lua:411; QuickTravel.lua:876,930,952,1125…; Radar.lua:306 |
| `IsQuestCompletable` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:188 |
| `IsTradeskillTrainer` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:336 |
| `NumTaxiNodes` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:245; QuickTravel.lua:204 |
| `PickupMacro` | c-global |  | mocked |  | Macros.lua:961,961,1325 |
| `QuestGetAutoAccept` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:181 |
| `QuestLogPushQuest` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:457 |
| `RepairAllItems` | c-global |  | mocked |  | Quick.lua:343 |
| `RequestBattlefieldScoreData` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:1002 |
| `SelectActiveQuest` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:283 |
| `SelectAvailableQuest` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:289 |
| `ShowMacroFrame` | c-global |  | absent | not mocked (FrameXML/C global) | Macros.lua:1040,1040 |
| `TakeInboxItem` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:401 |
| `TakeInboxMoney` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:399 |
| `TakeTaxiNode` | c-global |  | absent | not mocked (FrameXML/C global) | QuickTravel.lua:410 |
| `TaxiNodeCost` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:245; QuickTravel.lua:216 |
| `TaxiNodeGetType` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:245; QuickTravel.lua:206 |
| `TaxiNodeName` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:245; QuickTravel.lua:206 |
| `AuctionHouseFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Auction.lua:610,2160,2409 |
| `BackdropTemplateMixin` | framexml |  | absent | not mocked (FrameXML/C global) | Auction.lua:2275; Hub.lua:261; Macros.lua:1061,1092,1362,1391…; ZonePlan.lua:1044; Options.lua:802; UI.lua:704,906,1038,1609… |
| `CLASS_ICON_TCOORDS` | framexml |  | absent | not mocked (FrameXML/C global) | Todo.lua:930 |
| `CR_HASTE_MELEE` | framexml |  | absent | not mocked (FrameXML/C global) | Stats.lua:155 |
| `ChatFontNormal` | framexml |  | absent | not mocked (FrameXML/C global) | Macros.lua:1101 |
| `CreateVector2D` | framexml |  | mocked |  | Compass.lua:58; Export.lua:54; Radar.lua:88 |
| `DEFAULT_CHAT_FRAME` | framexml |  | absent | not mocked (FrameXML/C global) | Core.lua:53 |
| `FlightMapFrame` | framexml |  | absent | not mocked (FrameXML/C global) | QuickTravel.lua:366,377 |
| `GameFontHighlight` | framexml |  | absent | not mocked (FrameXML/C global) | Todo.lua:791 |
| `GameFontHighlightSmall` | framexml |  | absent | not mocked (FrameXML/C global) | Todo.lua:1220,1220; Tracker.lua:1005 |
| `GameTooltip` | framexml |  | absent | not mocked (FrameXML/C global) | Auction.lua:2361,2363,2368; Compass.lua:899,923,923,923; Errands.lua:214,222; Finder.lua:818,825; Hub.lua:110,120,324; Macros.lua:1241,1248,1248 (+10 files) |
| `GetCVar` | framexml |  | mocked |  | MinimapPins.lua:153; PathDraw.lua:236; Quick.lua:813 |
| `InspectFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Group.lua:1849 |
| `ItemLocation` | framexml |  | absent | not mocked (FrameXML/C global) | Auction.lua:1662 |
| `LOCALIZED_CLASS_NAMES_MALE` | framexml |  | absent | not mocked (FrameXML/C global) | PvP.lua:63; PvPMark.lua:254 |
| `MAX_QUESTLOG_QUESTS` | framexml |  | absent | not mocked (FrameXML/C global) | Abandon.lua:410; Export.lua:547; Planner.lua:607; Quick.lua:172 |
| `MailFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Errands.lua:203 |
| `MapCanvasDataProviderMixin` | framexml |  | absent | not mocked (FrameXML/C global) | PathDraw.lua:612 |
| `MerchantFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Quick.lua:859 |
| `Minimap` | framexml |  | absent | not mocked (FrameXML/C global) | Hub.lua:69; MinimapPins.lua:77; PathDraw.lua:79 |
| `NUM_BAG_SLOTS` | framexml |  | absent | not mocked (FrameXML/C global) | Core.lua:166; Inventory.lua:105; Macros.lua:69 |
| `OpenAllMail` | framexml |  | absent | not mocked (FrameXML/C global) | Errands.lua:143 |
| `PlaySound` | framexml |  | mocked |  | Core.lua:1699,1699; Tracker.lua:586 |
| `RAID_CLASS_COLORS` | framexml |  | absent | not mocked (FrameXML/C global) | Radar.lua:208; Todo.lua:936 |
| `SOUNDKIT` | framexml |  | absent | not mocked (FrameXML/C global) | Core.lua:1684; Tracker.lua:584 |
| `SelectGossipOption` | framexml |  | absent | not mocked (FrameXML/C global) | Quick.lua:276; QuickTravel.lua:531,531 |
| `SendMailFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Errands.lua:177 |
| `SetCVar` | framexml |  | mocked |  | Quick.lua:824 |
| `Settings` | framexml |  | absent | not mocked (FrameXML/C global) | Options.lua:737,785 |
| `TaxiFrame` | framexml |  | absent | not mocked (FrameXML/C global) | QuickTravel.lua:379 |
| `TooltipDataProcessor` | framexml |  | absent | not mocked (FrameXML/C global) | Prof.lua:608; Quests.lua:952; Tracker.lua:749 |
| `UIErrorsFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Core.lua:2204,2204,2205,2205 |
| `UISpecialFrames` | framexml |  | absent | not mocked (FrameXML/C global) | Finder.lua:885; Hub.lua:281; ZonePlan.lua:1062; Options.lua:834; UI.lua:726,2607,2607 |
| `UiMapPoint` | framexml |  | absent | not mocked (FrameXML/C global) | Compass.lua:242; Planner.lua:1150 |
| `WOW_PROJECT_ID` | framexml |  | absent | not mocked (FrameXML/C global) | Auction.lua:2157 |
| `WorldMapFrame` | framexml |  | absent | not mocked (FrameXML/C global) | PathDraw.lua:366 |

### Events the addon registers / names

| event | real | payload flags | called from |
|---|---|---|---|
| `ACTIONBAR_SLOT_CHANGED` | documented |  | Binds.lua:946 |
| `ACTIVE_TALENT_GROUP_CHANGED` | documented |  | Macros.lua:1702; Talents.lua:390 |
| `ADDON_ACTION_BLOCKED` | documented |  | Core.lua:3355,679; Binds.lua:952 |
| `ADDON_ACTION_FORBIDDEN` | documented |  | Core.lua:3354,679; Binds.lua:951; Macros.lua:1704 |
| `ADDON_LOADED` | documented |  | Core.lua:3356,738 |
| `AREA_POIS_UPDATED` | documented |  | PvP.lua:964 |
| `AUCTION_HOUSE_BROWSE_RESULTS_ADDED` | documented |  | Auction.lua:2609 |
| `AUCTION_HOUSE_BROWSE_RESULTS_UPDATED` | documented |  | Auction.lua:2609 |
| `AUCTION_HOUSE_CLOSED` | documented |  | Core.lua:1499; Auction.lua:2602 |
| `AUCTION_HOUSE_SHOW` | documented |  | Core.lua:1498; Auction.lua:2602; MinimapPins.lua:573; Tour.lua:1048 |
| `AUCTION_HOUSE_THROTTLED_SYSTEM_READY` | documented |  | Auction.lua:2603 |
| `AUCTION_OWNED_LIST_UPDATE` | **MISSING** (RegisterEvent errors: unknown event) |  | Auction.lua:2612 |
| `BAG_UPDATE` | documented |  | Core.lua:3371 |
| `BAG_UPDATE_COOLDOWN` | documented |  | Quests.lua:1280 |
| `BAG_UPDATE_DELAYED` | documented |  | Core.lua:3371; Classes.lua:253; DungeonPrep.lua:206; Gear.lua:1199; Group.lua:2653; Highlight.lua:1344 (+7 files) |
| `BANKFRAME_OPENED` | documented |  | Prof.lua:895 |
| `BOSS_KILL` | documented |  | Dungeon.lua:1791 |
| `CHARACTER_POINTS_CHANGED` | documented |  | Gear.lua:1201; Macros.lua:1703; Talents.lua:390 |
| `CHAT_MSG_ADDON` | documented |  | Core.lua:3362,739 |
| `CHAT_MSG_BG_SYSTEM_ALLIANCE` | documented |  | PvP.lua:961 |
| `CHAT_MSG_BG_SYSTEM_HORDE` | documented |  | PvP.lua:962 |
| `CHAT_MSG_BG_SYSTEM_NEUTRAL` | documented |  | PvP.lua:963 |
| `CHAT_MSG_COMBAT_HONOR_GAIN` | documented |  | PvP.lua:979 |
| `CHAT_MSG_COMBAT_XP_GAIN` | documented |  | Detect.lua:1095; Export.lua:1056 |
| `CHAT_MSG_LOOT` | documented |  | Dungeon.lua:1795; Session.lua:432 |
| `CHAT_MSG_MONSTER_EMOTE` | documented | secret in chat lockdown | Dungeon.lua:1799 |
| `CHAT_MSG_MONSTER_YELL` | documented | secret in chat lockdown | Dungeon.lua:1799; Edge2.lua:502 |
| `CHAT_MSG_RAID_BOSS_EMOTE` | documented | secret in chat lockdown | Dungeon.lua:1799 |
| `CHAT_MSG_SKILL` | documented | secret in chat lockdown | Prof.lua:893 |
| `CHAT_MSG_SYSTEM` | documented | secret in chat lockdown | Errands.lua:560 |
| `COMBAT_END` | **MISSING** (RegisterEvent errors: unknown event) |  | Abandon.lua:688; Detect.lua:1118; Dungeon.lua:1804; Fight.lua:218; Gear.lua:1202; Planner.lua:1421 (+4 files) |
| `COMBAT_START` | **MISSING** (RegisterEvent errors: unknown event) |  | Dungeon.lua:1803; Fight.lua:217; QuickTravel.lua:1209; Tour.lua:1040 |
| `COMMODITY_PRICE_UPDATED` | documented |  | Auction.lua:2603 |
| `COMMODITY_SEARCH_RESULTS_ADDED` | documented |  | Auction.lua:1194 |
| `COMMODITY_SEARCH_RESULTS_UPDATED` | documented |  | Auction.lua:1194,2606 |
| `CONFIRM_TALENT_WIPE` | documented |  | Talents.lua:394 |
| `CVAR_UPDATE` | documented |  | MinimapPins.lua:568; PathDraw.lua:656; Quick.lua:882 |
| `DAMAGE_METER_COMBAT_SESSION_UPDATED` | documented |  | Dungeon.lua:1802 |
| `DETECT` | **MISSING** (RegisterEvent errors: unknown event) |  | Edge2.lua:499; Pro.lua:852; Stats.lua:1243 |
| `DISPLAY_SIZE_CHANGED` | documented |  | UI.lua:2327 |
| `DUEL_FINISHED` | documented |  | PvP.lua:983 |
| `DUEL_REQUESTED` | documented |  | PvP.lua:982 |
| `ENCOUNTER_END` | documented |  | Dungeon.lua:1790 |
| `ENCOUNTER_START` | documented |  | Dungeon.lua:1789 |
| `EXPORT_UPDATED` | **MISSING** (RegisterEvent errors: unknown event) |  | Planner.lua:1424; ZonePlan.lua:1260 |
| `GET_ITEM_INFO_RECEIVED` | documented |  | Dungeon.lua:1798; Highlight.lua:1350 |
| `GLOBAL_MOUSE_DOWN` | documented |  | Hub.lua:274; ZonePlan.lua:1057; UI.lua:718 |
| `GOSSIP_CLOSED` | documented |  | QuickTravel.lua:1197 |
| `GOSSIP_SHOW` | documented |  | Quick.lua:853; QuickTravel.lua:1196 |
| `GROUP_ROSTER_UPDATE` | documented |  | Core.lua:1495,3377,3378; Abandon.lua:684; Detect.lua:1107; Dungeon.lua:1782; DungeonPrep.lua:206; Export.lua:1062 (+6 files) |
| `HEARTHSTONE_BOUND` | documented |  | Speed.lua:336 |
| `INSPECT_READY` | documented |  | Group.lua:2667 |
| `ITEM_SEARCH_RESULTS_UPDATED` | documented |  | Auction.lua:2606 |
| `LAYOUT_DONE` | **MISSING** (RegisterEvent errors: unknown event) |  | Window.lua:1622 |
| `LEARNED_SPELL_IN_SKILL_LINE` | documented |  | Binds.lua:947; Macros.lua:1701 |
| `LEARNED_SPELL_IN_TAB` | **MISSING** (RegisterEvent errors: unknown event) |  | Macros.lua:1700 |
| `LOOT_CLOSED` | documented |  | Core.lua:3371; Highlight.lua:1359 |
| `LOOT_OPENED` | documented |  | Edge2.lua:500; Export.lua:1057; Highlight.lua:1357; MinimapPins.lua:571; Pro.lua:845; Quests.lua:1281 (+1 files) |
| `LOOT_SLOT_CLEARED` | documented |  | Highlight.lua:1358 |
| `MAIL_CLOSED` | documented |  | Errands.lua:543; Quick.lua:869; QuickTravel.lua:1199 |
| `MAIL_INBOX_UPDATE` | documented |  | Auction.lua:2603; Errands.lua:542; Quick.lua:868 |
| `MAIL_SEND_INFO_UPDATE` | documented |  | Errands.lua:544 |
| `MAIL_SHOW` | documented |  | Errands.lua:541; MinimapPins.lua:572; Quick.lua:867 |
| `MERCHANT_CLOSED` | documented |  | Core.lua:3371; Quick.lua:866; QuickFight.lua:856; QuickTravel.lua:1199; Stats.lua:1257 |
| `MERCHANT_SHOW` | documented |  | Detect.lua:1104; MinimapPins.lua:574; Quick.lua:854; QuickFight.lua:855; QuickTravel.lua:1198; Stats.lua:1256 |
| `MINIMAP_UPDATE_ZOOM` | documented |  | MinimapPins.lua:567; PathDraw.lua:656 |
| `MIRROR_TIMER_START` | documented |  | Fight.lua:214 |
| `MIRROR_TIMER_STOP` | documented |  | Fight.lua:215 |
| `NAME_PLATE_UNIT_ADDED` | documented |  | Boost.lua:140; Detect.lua:1096; Highlight.lua:1352; Pro.lua:846; PvP.lua:972; PvPMark.lua:389 (+1 files) |
| `NAME_PLATE_UNIT_REMOVED` | documented |  | Detect.lua:1097; Highlight.lua:1353; PvPMark.lua:390 |
| `NEW_MOUNT_ADDED` | documented |  | Quick.lua:881 |
| `OWNED_AUCTIONS_UPDATED` | documented |  | Auction.lua:2613 |
| `PARTY_LEADER_CHANGED` | documented |  | QuickTravel.lua:1207 |
| `PARTY_MEMBERS_CHANGED` | **MISSING** (RegisterEvent errors: unknown event) |  | Core.lua:3378 |
| `PET_BAR_UPDATE` | documented |  | Hunter.lua:982,990 |
| `PLAYER_ALIVE` | documented |  | Core.lua:1969; Compass.lua:1123; Radar.lua:1124; Session.lua:435; Speed.lua:355; Tour.lua:1044 |
| `PLAYER_CONTROL_GAINED` | documented |  | Export.lua:1057 |
| `PLAYER_CONTROL_LOST` | documented |  | Export.lua:1057 |
| `PLAYER_DEAD` | documented |  | Core.lua:1969; Compass.lua:1123,1126; Dungeon.lua:1792; Fight.lua:213; Group.lua:2673; NavTime.lua:250 (+6 files) |
| `PLAYER_ENTERING_WORLD` | documented |  | Core.lua:1495,3365,3371; Binds.lua:947; Classes.lua:252; Compass.lua:1119; Detect.lua:1087; Dungeon.lua:1780 (+19 files) |
| `PLAYER_EQUIPMENT_CHANGED` | documented |  | Core.lua:3372; Dungeon.lua:1797; Gear.lua:1197; Macros.lua:1694; QuickFight.lua:847; Stats.lua:1230 |
| `PLAYER_FLAGS_CHANGED` | documented |  | Detect.lua:1107; Group.lua:2661; PvPMark.lua:398 |
| `PLAYER_GUILD_UPDATE` | documented |  | Core.lua:3379 |
| `PLAYER_INTERACTION_MANAGER_FRAME_SHOW` | documented |  | MinimapPins.lua:579 |
| `PLAYER_LEAVING_WORLD` | documented |  | Hunter.lua:1020,984 |
| `PLAYER_LEVEL_UP` | documented |  | Core.lua:3379,3384; Abandon.lua:683,684; DungeonPrep.lua:207; Errands.lua:545; Finder.lua:933; Gear.lua:1198 (+15 files) |
| `PLAYER_LOGIN` | documented |  | Core.lua:3357,738; Auction.lua:2614; Classes.lua:290; Hunter.lua:1113 |
| `PLAYER_LOGOUT` | documented |  | Core.lua:3359,739; Export.lua:1063; Group.lua:2675; Hunter.lua:1020,984; Session.lua:436; Stats.lua:1259 |
| `PLAYER_MONEY` | documented |  | Core.lua:3379; Dungeon.lua:1794; Errands.lua:549; Session.lua:431; Stats.lua:1245 |
| `PLAYER_PVP_KILLS_CHANGED` | documented |  | PvP.lua:980 |
| `PLAYER_REGEN_DISABLED` | documented |  | Core.lua:3360,740; Classes.lua:252; Compass.lua:1121; Export.lua:1056; Group.lua:2665; Hunter.lua:1022,981 (+6 files) |
| `PLAYER_REGEN_ENABLED` | documented |  | Core.lua:3361,740; Binds.lua:950; Classes.lua:252; Compass.lua:1120; Detect.lua:1099; Export.lua:1056 (+10 files) |
| `PLAYER_STARTED_MOVING` | documented |  | NavTime.lua:247; Stats.lua:1251 |
| `PLAYER_STOPPED_MOVING` | documented |  | NavTime.lua:248; Stats.lua:1252 |
| `PLAYER_TALENT_UPDATE` | documented |  | Talents.lua:390 |
| `PLAYER_TARGET_CHANGED` | documented |  | Boost.lua:141; Detect.lua:1098; Export.lua:1056; Fight.lua:212; Finder.lua:931; Group.lua:2664 (+7 files) |
| `PLAYER_UNGHOST` | documented |  | Core.lua:1969; Compass.lua:1123; Fight.lua:216; Radar.lua:1125; Session.lua:434; Speed.lua:356 (+1 files) |
| `PLAYER_UPDATE_RESTING` | documented |  | Core.lua:1969; Errands.lua:550; Pro.lua:847; Quick.lua:877; QuickTravel.lua:1199 |
| `PLAYER_XP_UPDATE` | documented |  | Dungeon.lua:1793; Export.lua:1058; Group.lua:2650; Quests.lua:1241; Session.lua:427; Stats.lua:1244 (+1 files) |
| `QUESTS_UPDATED` | **MISSING** (RegisterEvent errors: unknown event) |  | Compass.lua:1136; Dungeon.lua:1805; Quick.lua:847; Tracker.lua:1266 |
| `QUEST_ACCEPTED` | documented |  | Abandon.lua:683,684; Compass.lua:1115; Detect.lua:1090; Export.lua:1057; Gear.lua:1200; Highlight.lua:1346 (+5 files) |
| `QUEST_COMPLETE` | documented |  | Gear.lua:1195; Group.lua:2671; Pro.lua:845; Quick.lua:851; Session.lua:429 |
| `QUEST_DETAIL` | documented |  | Pro.lua:845; Quick.lua:849 |
| `QUEST_FINISHED` | documented |  | Gear.lua:1196 |
| `QUEST_GREETING` | documented |  | Quick.lua:852 |
| `QUEST_LOG_UPDATE` | documented |  | Abandon.lua:684; Compass.lua:1115; Detect.lua:1090; DungeonPrep.lua:206; Edge2.lua:501; Group.lua:2648 (+6 files) |
| `QUEST_PROGRESS` | documented |  | Quick.lua:850 |
| `QUEST_REMOVED` | documented |  | Abandon.lua:683,684; Compass.lua:1116; Detect.lua:1090; Highlight.lua:1348; Planner.lua:1413; Quests.lua:1246 (+2 files) |
| `QUEST_TURNED_IN` | documented |  | Compass.lua:1115; Detect.lua:1094; Dungeon.lua:1796; Export.lua:1057; Group.lua:2672; Highlight.lua:1347 (+7 files) |
| `QUEST_WATCH_LIST_CHANGED` | documented |  | Compass.lua:1115; Quests.lua:1240 |
| `QUEST_WATCH_UPDATE` | documented |  | Detect.lua:1090; Quests.lua:1241; Stats.lua:1248 |
| `RAID_BOSS_EMOTE` | documented |  | Dungeon.lua:1799 |
| `READY` | **MISSING** (RegisterEvent errors: unknown event) |  | UI.lua:2328,2712,2917 |
| `READY_CHECK` | documented | secret in chat lockdown | Core.lua:1646; Errands.lua:556; QuickTravel.lua:1208 |
| `READY_CHECK_FINISHED` | documented |  | Errands.lua:557 |
| `REPLICATE_ITEM_LIST_UPDATE` | documented |  | Auction.lua:2602 |
| `SKILL_LINES_CHANGED` | documented |  | Group.lua:2655; Macros.lua:1698; Prof.lua:893 |
| `SPELLS_CHANGED` | documented |  | Binds.lua:946; Group.lua:2656; Macros.lua:1699; QuickFight.lua:846 |
| `SPELL_UPDATE_COOLDOWN` | documented |  | QuickFight.lua:848 |
| `SQUAD_UPDATE` | **MISSING** (RegisterEvent errors: unknown event) |  | Planner.lua:1426; Todo.lua:1475 |
| `START_AUTOREPEAT_SPELL` | documented |  | Hunter.lua:1003,983 |
| `START_LOOT_ROLL` | documented |  | Group.lua:2674; Highlight.lua:1360 |
| `STOP_AUTOREPEAT_SPELL` | documented |  | Hunter.lua:1004,983 |
| `SUMMARY` | **MISSING** (RegisterEvent errors: unknown event) |  | Abandon.lua:689; Auction.lua:2616; Boost.lua:143; Compass.lua:1130; Detect.lua:1119; Dungeon.lua:1812 (+14 files) |
| `SUPER_TRACKING_CHANGED` | documented |  | Compass.lua:1116 |
| `TAXIMAP_CLOSED` | documented |  | Errands.lua:552; QuickTravel.lua:1195 |
| `TAXIMAP_OPENED` | documented |  | Errands.lua:551; Export.lua:1057; QuickTravel.lua:1194; Route.lua:896 |
| `TRADE_SKILL_CLOSE` | documented |  | Prof.lua:894 |
| `TRADE_SKILL_LIST_UPDATE` | documented |  | Prof.lua:893 |
| `TRADE_SKILL_SHOW` | documented |  | Prof.lua:893 |
| `TRAINER_CLOSED` | documented |  | Errands.lua:555; QuickTravel.lua:1200 |
| `TRAINER_SHOW` | documented |  | Errands.lua:553; Pro.lua:845 |
| `TRAINER_UPDATE` | documented |  | Errands.lua:554 |
| `UI_SCALE_CHANGED` | documented |  | UI.lua:2327 |
| `UNIT_AURA` | documented | secret: auras (combat) | Core.lua:676; Classes.lua:253,257; Export.lua:1059; Hunter.lua:984,996; PvP.lua:978; QuickFight.lua:839 (+1 files) |
| `UNIT_COMBAT` | documented |  | Detect.lua:1113 |
| `UNIT_CONNECTION` | documented |  | Detect.lua:1107; Group.lua:2661 |
| `UNIT_FACTION` | documented |  | PvPMark.lua:397 |
| `UNIT_FLAGS` | documented |  | Detect.lua:1110; Group.lua:2661 |
| `UNIT_HAPPINESS` | documented |  | Hunter.lua:982; QuickFight.lua:841 |
| `UNIT_HEALTH` | documented |  | Detect.lua:1114; Group.lua:2658; PvP.lua:975; PvPMark.lua:404; QuickFight.lua:836; Stats.lua:1239 |
| `UNIT_INVENTORY_CHANGED` | documented |  | Core.lua:3375; Classes.lua:253,257; Group.lua:2668; Hunter.lua:1002,1029,982…; Macros.lua:1693 |
| `UNIT_LEVEL` | documented |  | Group.lua:2661 |
| `UNIT_MAXHEALTH` | documented |  | QuickFight.lua:837; Stats.lua:1240 |
| `UNIT_PET` | documented |  | DungeonPrep.lua:207; Group.lua:2661; Hunter.lua:982,990; Macros.lua:1695; QuickFight.lua:840 |
| `UNIT_POWER_UPDATE` | documented |  | Group.lua:2659; QuickFight.lua:838 |
| `UNIT_QUEST_LOG_CHANGED` | documented |  | Detect.lua:1093; DungeonPrep.lua:207; Group.lua:2649; Highlight.lua:1349; Planner.lua:1415; Quests.lua:1244 |
| `UNIT_SPELLCAST_SENT` | documented | secret: casts of others (combat) | Prof.lua:895 |
| `UNIT_SPELLCAST_START` | documented | secret: casts of others (combat) | Hunter.lua:1015,983 |
| `UNIT_SPELLCAST_SUCCEEDED` | documented | secret: casts of others (combat) | Hunter.lua:1005,983; Route.lua:881; Speed.lua:337 |
| `UNIT_STATS` | documented |  | Stats.lua:1231 |
| `UNIT_TARGET` | documented |  | PvPMark.lua:400 |
| `UNIT_THREAT_SITUATION_UPDATE` | documented |  | Detect.lua:1115 |
| `UPDATE_BATTLEFIELD_SCORE` | documented |  | PvP.lua:968 |
| `UPDATE_BATTLEFIELD_STATUS` | documented |  | PvP.lua:969 |
| `UPDATE_BINDINGS` | documented |  | Binds.lua:946; Theme.lua:915 |
| `UPDATE_BONUS_ACTIONBAR` | documented |  | Binds.lua:947 |
| `UPDATE_EXHAUSTION` | documented |  | Group.lua:2651; Todo.lua:1476 |
| `UPDATE_INVENTORY_DURABILITY` | documented |  | DungeonPrep.lua:206; Group.lua:2654; QuickTravel.lua:1199; Stats.lua:1258; Todo.lua:1476 |
| `UPDATE_MACROS` | documented |  | Binds.lua:946; Macros.lua:1705 |
| `UPDATE_MOUSEOVER_UNIT` | documented |  | Boost.lua:142; Finder.lua:932; Pro.lua:846; PvP.lua:973; Radar.lua:1115 |
| `UPDATE_PENDING_MAIL` | documented |  | QuickTravel.lua:1200 |
| `UPDATE_UI_WIDGET` | documented |  | PvP.lua:965 |
| `VIGNETTES_UPDATED` | documented |  | Detect.lua:1084 |
| `VIGNETTE_MINIMAP_UPDATED` | documented |  | Detect.lua:1085 |
| `ZONE_CHANGED` | documented |  | Radar.lua:1106; Speed.lua:348; Stats.lua:1253 |
| `ZONE_CHANGED_INDOORS` | documented |  | MinimapPins.lua:567; Radar.lua:1106 |
| `ZONE_CHANGED_NEW_AREA` | documented |  | Core.lua:1495,3365,3379; Abandon.lua:684; Compass.lua:1116; Detect.lua:1086; Dungeon.lua:1781; Errands.lua:559 (+20 files) |

<!-- END API-TABLE -->
