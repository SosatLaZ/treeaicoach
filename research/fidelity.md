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

Counts (APIs the addon references): MISSING 98, c-global 92, documented 214, framexml 37, shim-only 8. Mismatches mock ↔ real: **6** (bold rows below).

Legend — real: `documented` (Blizzard API docs), `framexml` (defined by Blizzard's camelot UI code), `c-global` (undocumented C function Blizzard's camelot UI calls), `shim-only` (only in Blizzard_Deprecated*, CVar loadDeprecationFallbacks: do not rely on it), `MISSING` (nowhere in the Forever client). mock = what tests/wow/wowmock.lua does in the « forever » flavour.

| API | real (Forever 1.60.1) | flags | mock (forever) | mismatch | called from (file:line) |
|---|---|---|---|---|---|
| **`C_AreaPoiInfo.GetAreaPOIForMap`** | documented (uiMapID) → areaPoiIDs:table<number> |  | absent | **real API not mocked**: the addon path is never exercised | PvP.lua:190 |
| **`C_AreaPoiInfo.GetAreaPOIInfo`** | documented (uiMapID?, areaPoiID) → poiInfo:AreaPOIInfo |  | absent | **real API not mocked**: the addon path is never exercised | PvP.lua:193 |
| **`C_AreaPoiInfo.GetAreaPOISecondsLeft`** | documented (areaPoiID) → secondsLeft:number |  | absent | **real API not mocked**: the addon path is never exercised | PvP.lua:201 |
| **`C_QuestLog.GetQuestsOnMap`** | documented (uiMapID) → quests:table<QuestPOIMapInfo> |  | absent | **real API not mocked**: the addon path is never exercised | Missions.lua:755,756 |
| **`C_TaxiMap.GetAllTaxiNodes`** | documented (uiMapID) → taxiNodes:table<TaxiNodeInfo> |  | absent | **real API not mocked**: the addon path is never exercised | Errands.lua:196 |
| **`C_TaxiMap.GetTaxiNodesForMap`** | documented (uiMapID) → mapTaxiNodes:table<MapTaxiNodeInfo> |  | absent | **real API not mocked**: the addon path is never exercised | Radar.lua:451,461; SafePath.lua:535 |
| `C_AddOns.GetAddOnCPUUsage` | MISSING |  | absent |  | Comfort.lua:276; SelfTest.lua:309 |
| `C_AddOns.GetAddOnMemoryUsage` | MISSING |  | absent |  | Core.lua:1445; Comfort.lua:283; SelfTest.lua:306 |
| `C_AddOns.UpdateAddOnMemoryUsage` | MISSING |  | absent |  | Core.lua:1444; Comfort.lua:282; SelfTest.lua:305 |
| `C_QuestLog.GetQuestInfo` | MISSING |  | absent |  | Group.lua:354,355 |
| `C_Spell.IsSpellKnown` | MISSING |  | absent |  | Core.lua:133,133 |
| `C_TradeSkillUI.GetAllRecipeIDs` | MISSING |  | absent |  | Economy.lua:496,497; Prof.lua:271,278 |
| `C_TradeSkillUI.GetTradeSkillLine` | MISSING |  | absent |  | Prof.lua:275 |
| `C_Trainer.GetNumTrainerServices` | MISSING |  | absent |  | Edge2.lua:738 |
| `CalculateAuctionDeposit` | MISSING |  | absent |  | Auction.lua:2086 |
| `CanSendAuctionQuery` | MISSING |  | absent |  | Auction.lua:1299,1384,2163,2164 |
| `Cancel` | MISSING |  | absent |  | Route.lua:568,575,876 |
| `CancelAuction` | MISSING |  | absent |  | Auction.lua:1562 |
| `CastingInfo` | MISSING |  | absent |  | Core.lua:445,446; Hunter.lua:763 |
| `ClickAuctionSellItemButton` | MISSING |  | absent |  | Auction.lua:1675 |
| `GetActiveTalentGroup` | MISSING |  | absent |  | Core.lua:2649,2650; Macros.lua:604; Talents.lua:163 |
| `GetAddOnInfo` | MISSING |  | absent |  | Quests.lua:96 |
| `GetAddOnMetadata` | MISSING |  | absent |  | Core.lua:296 |
| `GetAuctionItemInfo` | MISSING |  | absent |  | Auction.lua:601,776,1502,1557 |
| `GetAuctionSellItemInfo` | MISSING |  | absent |  | Auction.lua:1676,2079 |
| `GetBattlefieldFlagPosition` | MISSING |  | absent |  | PvP.lua:389 |
| `GetBattlefieldPortExpiration` | MISSING |  | absent |  | PvP.lua:707 |
| `GetContainerItemID` | MISSING |  | absent |  | Hunter.lua:236; Macros.lua:67 |
| `GetContainerItemInfo` | MISSING |  | absent |  | Core.lua:191; Inventory.lua:143; Macros.lua:68 |
| `GetContainerItemLink` | MISSING |  | absent |  | Hunter.lua:238 |
| `GetContainerItemQuestInfo` | MISSING |  | absent |  | Quests.lua:922 |
| `GetContainerNumFreeSlots` | MISSING |  | absent |  | Core.lua:176; Inventory.lua:119 |
| `GetContainerNumSlots` | MISSING |  | absent |  | Core.lua:171; Hunter.lua:231; Inventory.lua:111; Macros.lua:66 |
| `GetDetailedItemLevelInfo` | MISSING |  | absent |  | Team.lua:336 |
| `GetGuildRosterInfo` | MISSING |  | absent |  | Edge.lua:448 |
| `GetItemCooldown` | MISSING |  | absent |  | Economy.lua:182; Errands.lua:176; Export.lua:517; Predict.lua:173; Quests.lua:799; Quick.lua:604 (+2 files) |
| `GetItemCount` | MISSING |  | absent |  | Core.lua:161; Auction.lua:984; Classes.lua:208; Finder.lua:429; Hunter.lua:226; Prof.lua:304 (+3 files) |
| `GetItemIcon` | MISSING |  | absent |  | Macros.lua:1269 |
| `GetItemInfo` | MISSING |  | absent |  | Core.lua:148; Abandon.lua:472; Economy.lua:59; Gear.lua:44; Hunter.lua:392; Inventory.lua:127 (+5 files) |
| `GetItemInfoInstant` | MISSING |  | absent |  | Classes.lua:154; Hunter.lua:210 |
| `GetItemSpell` | MISSING |  | absent |  | Macros.lua:54 |
| `GetLootMethod` | MISSING |  | absent |  | QuickTravel.lua:965 |
| `GetLootSourceInfo` | MISSING |  | absent |  | Export.lua:267; Predict.lua:272; Stats.lua:729 |
| `GetMacroIndexByName` | MISSING |  | absent |  | Hunter.lua:417; Macros.lua:696,697,994; PvP.lua:609 |
| `GetMerchantItemInfo` | MISSING |  | absent |  | Economy.lua:334; Edge.lua:809 |
| `GetMinimapShape` | MISSING |  | absent |  | Hub.lua:71; MinimapPins.lua:155; PathDraw.lua:239 |
| `GetNumAddOns` | MISSING |  | absent |  | Quests.lua:94 |
| `GetNumAuctionItems` | MISSING |  | absent |  | Auction.lua:1094,1431,1501 |
| `GetNumPartyMembers` | MISSING |  | absent |  | Core.lua:1295,2641,2642,2859; Group.lua:108; Hunter.lua:603; Macros.lua:634; Quests.lua:380; Quick.lua:416 (+2 files) |
| `GetNumQuestLogEntries` | MISSING |  | absent |  | Core.lua:428,429; Export.lua:471; Leveling.lua:162; Pro.lua:236; Quests.lua:265,266; Quick.lua:171 (+1 files) |
| `GetNumRaidMembers` | MISSING |  | absent |  | Core.lua:1296,2858 |
| `GetNumSkillLines` | MISSING |  | absent |  | Core.lua:361,362; Finder.lua:468; Gear.lua:311; Prof.lua:251 |
| `GetNumSpellTabs` | MISSING |  | absent |  | Core.lua:400,401 |
| `GetNumTalentGroups` | MISSING |  | absent |  | Edge.lua:1041; Talents.lua:184 |
| `GetNumTalentTabs` | MISSING |  | absent |  | Talents.lua:162 |
| `GetNumTalents` | MISSING |  | absent |  | Talents.lua:165 |
| `GetNumTradeSkills` | MISSING |  | absent |  | Economy.lua:474; Prof.lua:288 |
| `GetPetFoodTypes` | MISSING |  | absent |  | Hunter.lua:97; Macros.lua:274 |
| `GetPetHappiness` | MISSING |  | absent |  | Hunter.lua:94 |
| `GetPetLoyalty` | MISSING |  | absent |  | Hunter.lua:95 |
| `GetPetTrainingPoints` | MISSING |  | absent |  | Finder.lua:631; Hunter.lua:96 |
| `GetQuestGreenRange` | MISSING |  | absent |  | Quests.lua:110 |
| `GetQuestLogTitle` | MISSING |  | absent |  | Core.lua:428,430; Export.lua:473; Leveling.lua:164; Pro.lua:257; Quests.lua:265,269; Stats.lua:366 (+1 files) |
| `GetQuestTagInfo` | MISSING |  | absent |  | Quests.lua:175,176 |
| `GetSkillLineInfo` | MISSING |  | absent |  | Core.lua:361,363; Finder.lua:474; Gear.lua:311; Prof.lua:253 |
| `GetSpecialization` | MISSING |  | absent |  | Core.lua:2643,2644 |
| `GetSpecializationInfo` | MISSING |  | absent |  | Core.lua:2645,2646 |
| `GetSpellBookItemName` | MISSING |  | absent |  | Core.lua:407; Finder.lua:185 |
| `GetSpellCooldown` | MISSING |  | absent |  | Core.lua:142; Hunter.lua:659 |
| `GetSpellInfo` | MISSING |  | absent |  | Core.lua:114,123; Classes.lua:82,100; Hunter.lua:122,145; Macros.lua:36,208,209; PvPMark.lua:130 |
| `GetSpellName` | MISSING |  | absent |  | Finder.lua:186 |
| `GetSpellTabInfo` | MISSING |  | absent |  | Core.lua:400,402 |
| `GetSpellTexture` | MISSING |  | absent |  | Macros.lua:1262 |
| `GetTalentInfo` | MISSING |  | absent |  | Talents.lua:145 |
| `GetTalentTabInfo` | MISSING |  | absent |  | Macros.lua:607; Talents.lua:155 |
| `GetTrackingTexture` | MISSING |  | absent |  | Edge.lua:971 |
| `GetTradeSkillInfo` | MISSING |  | absent |  | Economy.lua:477; Prof.lua:289 |
| `GetTradeSkillLine` | MISSING |  | absent |  | Economy.lua:536; Prof.lua:283 |
| `GetTradeSkillNumReagents` | MISSING |  | absent |  | Economy.lua:481 |
| `GetTradeSkillReagentInfo` | MISSING |  | absent |  | Economy.lua:482 |
| `GetTradeSkillReagentItemLink` | MISSING |  | absent |  | Economy.lua:483 |
| `GetTrainerServiceLevelReq` | MISSING |  | absent |  | Errands.lua:286 |
| `GetUnspentTalentPoints` | MISSING |  | absent |  | Leveling.lua:249; Pro.lua:526; Talents.lua:180 |
| `GetZonePVPInfo` | MISSING |  | absent |  | PvPMark.lua:95 |
| `HasPetSpells` | MISSING |  | absent |  | Core.lua:399; Finder.lua:181 |
| `InterfaceOptionsFrame_OpenToCategory` | MISSING |  | absent |  | Options.lua:765,767,768 |
| `InterfaceOptions_AddCategory` | MISSING |  | absent |  | Options.lua:739,739 |
| `IsAddOnLoaded` | MISSING |  | absent |  | MinimapPins.lua:84; Quests.lua:89 |
| `IsPassiveSpell` | MISSING |  | absent |  | Core.lua:408 |
| `IsQuestFlaggedCompleted` | MISSING |  | absent |  | Edge.lua:410; Export.lua:489; Gear.lua:286; Planner.lua:417; Quests.lua:432,545 |
| `IsQuestWatched` | MISSING |  | absent |  | Quests.lua:197 |
| `IsSpellInRange` | MISSING |  | absent |  | Hunter.lua:620; PvPMark.lua:133 |
| `IsUnitOnQuest` | MISSING |  | absent |  | Quick.lua:434; Team.lua:264,265 |
| `PickupContainerItem` | MISSING |  | absent |  | Auction.lua:1673 |
| `PlaceAuctionBid` | MISSING |  | absent |  | Auction.lua:1106 |
| `PostAuction` | MISSING |  | absent |  | Auction.lua:1679 |
| `QueryAuctionItems` | MISSING |  | absent |  | Auction.lua:601,1306,1407 |
| `RegisterAddonMessagePrefix` | MISSING |  | absent |  | Core.lua:3166,3167 |
| `SendAddonMessage` | MISSING |  | absent |  | Core.lua:2756,2757 |
| `SetLootMethod` | MISSING |  | absent |  | QuickTravel.lua:969,977 |
| `UnitAura` | MISSING |  | absent |  | Core.lua:270,271; Classes.lua:114; Hunter.lua:159; Team.lua:199,200 |
| `UnitBuff` | MISSING |  | absent |  | PvP.lua:788 |
| `UnitCharacterPoints` | MISSING |  | absent |  | Leveling.lua:248; Pro.lua:525; Talents.lua:179 |
| `UseContainerItem` | MISSING |  | absent |  | Core.lua:250; Economy.lua:409; Inventory.lua:157; Quick.lua:332 |
| `ChatFrame_OpenChat` | shim-only |  | absent |  | Edge.lua:466 |
| `CombatLogGetCurrentEventInfo` | shim-only |  | absent |  | Hunter.lua:667; Predict.lua:248 |
| `GetBattlefieldScore` | shim-only |  | absent |  | PvP.lua:413 |
| `GetWeaponEnchantInfo` | shim-only |  | absent |  | Classes.lua:147 |
| `HasAction` | shim-only |  | absent |  | Core.lua:454 |
| `IsPlayerSpell` | shim-only |  | absent |  | Core.lua:131,131; Macros.lua:42 |
| `IsSpellKnown` | shim-only |  | absent |  | Core.lua:132,132; Macros.lua:42 |
| `SendChatMessage` | shim-only |  | absent |  | Missions.lua:1054 |
| `Ambiguate` | documented (fullName, context) → result:string |  | mocked |  | Group.lua:124 |
| `C_ActionBar.HasAction` | documented (actionID) → hasAction:bool |  | mocked |  | Core.lua:453,453 |
| `C_AddOns.GetAddOnInfo` | documented (name) → name:cstring, title:cstring, notes:cstring, loadable:bool, reason:cstring, security:cstring |  | mocked |  | Quests.lua:96 |
| `C_AddOns.GetAddOnMetadata` | documented (name, variable) → value:cstring |  | mocked |  | Core.lua:296; Bugs.lua:115 |
| `C_AddOns.GetNumAddOns` | documented () → numAddOns:number |  | mocked |  | Quests.lua:94 |
| `C_AddOns.IsAddOnLoaded` | documented (name) → loadedOrLoading:bool, loaded:bool |  | mocked |  | Binds.lua:249,249; MinimapPins.lua:83,83; Quests.lua:85,86 |
| `C_AuctionHouse.GetReplicateItemInfo` | documented (index) → name:string, texture:fileID, count:number, qualityID:number, usable:bool, level:number, levelType:string, minBid:BigUInteger, minIncrement:BigUInteger, buyoutPrice:BigUInteger, bidAmount:BigUInteger, highBidder:string, bidderFullName:string, owner:string, ownerFullName:string, saleStatus:number, itemID:number, hasAllInfo:bool |  | mocked |  | Auction.lua:597 |
| `C_AuctionHouse.ReplicateItems` | documented () → - |  | mocked |  | Auction.lua:597 |
| `C_ChatInfo.RegisterAddonMessagePrefix` | documented (prefix) → result:RegisterAddonMessagePrefixResult |  | mocked |  | Core.lua:3164,3164,3165,3165; Group.lua:508,509 |
| `C_ChatInfo.SendAddonMessage` | documented (prefix, message, chatType?, target?) → result:SendAddonMessageResult |  | mocked |  | Core.lua:2754,2754,2755,2755; Group.lua:156,157 |
| `C_ChatInfo.SendChatMessage` | documented (message, chatType?, languageID?, target?) → - | hardware/restricted | mocked | protected/hardware: gated by forever_ref.lua | Missions.lua:1054,1054 |
| `C_ClassTalents.HasUnspentTalentPoints` | documented () → hasUnspentPoints:bool, numClassPoints:number, numSpecPoints:number |  | mocked |  | Leveling.lua:244,245 |
| `C_CombatLog.IsCombatLogRestricted` | documented () → restricted:bool |  | mocked |  | Core.lua:694,695 |
| `C_Container.ContainerIDToInventoryID` | documented (containerID) → inventoryID:luaIndex |  | mocked |  | Hunter.lua:533 |
| `C_Container.GetContainerItemID` | documented (containerIndex, slotIndex) → containerID:number |  | mocked |  | Hunter.lua:235,235,249; Macros.lua:67 |
| `C_Container.GetContainerItemInfo` | documented (containerIndex, slotIndex) → containerInfo:ContainerItemInfo |  | mocked |  | Core.lua:182,183; Inventory.lua:138,139; Macros.lua:68 |
| `C_Container.GetContainerItemQuestInfo` | documented (containerIndex, slotIndex) → questInfo:ItemQuestInfo |  | mocked |  | Quests.lua:917,918 |
| `C_Container.GetContainerNumFreeSlots` | documented (bagIndex) → numFreeSlots:number, bagFamily:number |  | mocked |  | Core.lua:176; Inventory.lua:118,118 |
| `C_Container.GetContainerNumSlots` | documented (containerIndex) → numSlots:number |  | mocked |  | Core.lua:171; Hunter.lua:230,230; Inventory.lua:110,110; Macros.lua:66 |
| `C_Container.GetItemCooldown` | documented (itemID) → startTime:number, duration:number, enable:number |  | mocked |  | Boost.lua:876,876; Economy.lua:181,181; Errands.lua:174; Predict.lua:172,172; Quests.lua:798,798; Squad.lua:809,809 |
| `C_Container.PickupContainerItem` | documented (containerIndex, slotIndex) → - |  | mocked |  | Auction.lua:1673 |
| `C_Container.UseContainerItem` | documented (containerIndex, slotIndex, unitToken?, bankType?, reagentBankOpen) → - |  | mocked |  | Core.lua:250; Economy.lua:408,408; Inventory.lua:156,156 |
| `C_GossipInfo.GetActiveQuests` | documented () → info:table<GossipQuestUIInfo> |  | mocked |  | Quick.lua:242,242 |
| `C_GossipInfo.GetAvailableQuests` | documented () → info:table<GossipQuestUIInfo> |  | mocked |  | Quick.lua:242,242 |
| `C_GossipInfo.GetOptions` | documented () → info:table<GossipOptionUIInfo> |  | mocked |  | Quick.lua:266,266 |
| `C_GossipInfo.SelectActiveQuest` | documented (optionID) → - |  | mocked |  | Quick.lua:251,252 |
| `C_GossipInfo.SelectAvailableQuest` | documented (optionID) → - |  | mocked |  | Quick.lua:257,259 |
| `C_GossipInfo.SelectOption` | documented (optionID, text?, confirmed?) → - |  | mocked |  | Quick.lua:276,276; QuickTravel.lua:533,533 |
| `C_GuildInfo.GuildRoster` | documented () → - |  | mocked |  | Edge.lua:441,441 |
| `C_Item.GetDetailedItemLevelInfo` | documented (itemInfo) → actualItemLevel:number, previewLevel:bool, sparseItemLevel:number |  | mocked |  | Stats.lua:126,126; Team.lua:336 |
| `C_Item.GetItemCooldown` | documented (itemInfo) → startTimeSeconds:number, durationSeconds:number, enableCooldownTimer:bool |  | mocked |  | Errands.lua:175 |
| `C_Item.GetItemCount` | documented (itemInfo, includeBank, includeUses, includeReagentBank, includeAccountBank) → count:number |  | mocked |  | Core.lua:161; Classes.lua:207,207; Hunter.lua:222,223 |
| `C_Item.GetItemIconByID` | documented (itemInfo) → icon:fileID |  | mocked |  | Macros.lua:1269,1269 |
| `C_Item.GetItemInfo` | documented (itemInfo) → itemName:cstring, itemLink:cstring, itemQuality:ItemQuality, itemLevel:number, itemMinLevel:number, itemType:cstring, itemSubType:cstring, itemStackCount:number, itemEquipLoc:cstring, itemTexture:fileID, sellPrice:number, classID:number, subclassID:number, bindType:number, expansionID:number, setID:number, isCraftingReagent:bool, itemDescription:cstring |  | mocked |  | Core.lua:148; Dungeon.lua:377,377; Inventory.lua:126,126; Macros.lua:49 |
| `C_Item.GetItemInfoInstant` | documented (itemInfo) → itemID:number, itemType:cstring, itemSubType:cstring, itemEquipLoc:cstring, icon:fileID, classID:number, subClassID:number |  | mocked |  | Highlight.lua:113; Hunter.lua:205,206 |
| `C_Item.GetItemLinkByGUID` | documented (itemGUID) → itemLink:string |  | mocked |  | Boost.lua:552,553 |
| `C_Item.GetItemNameByID` | documented (itemInfo) → itemName:string |  | mocked |  | Core.lua:153,154; Hunter.lua:391,391 |
| `C_Item.GetItemSpell` | documented (itemInfo) → spellName:cstring, spellID:number |  | mocked |  | Macros.lua:54 |
| `C_Item.GetItemStats` | documented (itemLink) → statTable:LuaValueVariant |  | mocked |  | Boost.lua:273,273 |
| `C_Item.RequestLoadItemDataByID` | documented (itemInfo) → - |  | mocked |  | Auction.lua:633 |
| `C_Map.CanSetUserWaypointOnMap` | documented (uiMapID) → canSet:bool |  | mocked |  | Compass.lua:244,244 |
| `C_Map.GetAreaInfo` | documented (areaID) → name:cstring |  | mocked |  | Compass.lua:388,389; Gear.lua:281,281; Pro.lua:187,187; Quests.lua:400,400 |
| `C_Map.GetBestMapForUnit` | documented (unitToken) → uiMapID:number |  | mocked |  | Abandon.lua:512,513; Compass.lua:167,168; Economy.lua:551,552; Errands.lua:195; Group.lua:255,255; Leveling.lua:148,148 (+6 files) |
| `C_Map.GetMapInfo` | documented (uiMapID) → info:UiMapDetails |  | mocked |  | Compass.lua:177,177; Missions.lua:1673,1674; Quests.lua:78,79; Radar.lua:456,456; Team.lua:179 |
| `C_Map.GetMapWorldSize` | documented (uiMapID) → width:number, height:number |  | mocked |  | Compass.lua:91,92 |
| `C_Map.GetPlayerMapPosition` | documented (uiMapID, unitToken) → position:vector2 |  | mocked |  | Compass.lua:167,170; Economy.lua:553,553; PathDraw.lua:211,214; Radar.lua:186,187,336,337 |
| `C_Map.GetUserWaypoint` | documented () → point:UiMapPoint |  | mocked |  | Compass.lua:263,264 |
| `C_Map.GetWorldPosFromMapPos` | documented (uiMapID, mapPosition) → continentID:number, worldPosition:vector2 |  | mocked |  | Compass.lua:77,78; Radar.lua:100,101 |
| `C_Map.HasUserWaypoint` | documented () → hasUserWaypoint:bool |  | mocked |  | Compass.lua:263,263,285,285 |
| `C_Map.SetUserWaypoint` | documented (point) → wasSet:bool |  | mocked |  | Compass.lua:243,246 |
| `C_MerchantFrame.GetItemInfo` | documented (index) → info:MerchantItemInfo |  | mocked |  | Economy.lua:335 |
| `C_Minimap.GetNumTrackingTypes` | documented () → numTrackingTypes:number |  | mocked |  | Edge.lua:960,962 |
| `C_Minimap.GetTrackingInfo` | documented (spellIndex) → trackingInfo:MinimapScriptTrackingInfo |  | mocked |  | Edge.lua:960,963 |
| `C_Minimap.SetTracking` | documented (index, on) → - |  | mocked |  | Edge.lua:990,991 |
| `C_MountJournal.GetMountIDs` | documented () → mountIDs:table<number> |  | mocked |  | Quick.lua:480,482 |
| `C_MountJournal.GetMountInfoByID` | documented (mountID) → name:cstring, spellID:number, icon:fileID, isActive:bool, isUsable:bool, sourceType:number, isFavorite:bool, isFactionSpecific:bool, faction:PvPFaction, shouldHideOnChar:bool, isCollected:bool, mountID:number, isSteadyFlight:bool |  | mocked |  | Quick.lua:480,483 |
| `C_NamePlate.GetNamePlateForUnit` | documented (unitToken, includeForbidden) → nameplate:NamePlateFrame |  | mocked |  | PvPMark.lua:327,327 |
| `C_NamePlate.GetNamePlates` | documented () → nameplates:table<NamePlateFrame> |  | mocked |  | PvPMark.lua:357,358; SafePath.lua:479; SelfTest.lua:237; Speed.lua:190,191 |
| `C_PetInfo.GetPetFoodTypes` | documented () → foodTypes:table<string> |  | mocked |  | Hunter.lua:97 |
| `C_PetInfo.GetPetHappiness` | documented () → happiness:number, damagePercentage:number, loyaltyRate:number |  | mocked |  | Hunter.lua:94 |
| `C_PetInfo.GetPetLoyalty` | documented () → loyaltyName:string |  | mocked |  | Hunter.lua:95 |
| `C_PetInfo.GetPetTrainingPoints` | documented () → totalPoints:number, usedPoints:number |  | mocked |  | Hunter.lua:96 |
| `C_PvP.GetBattlefieldFlagPosition` | documented (flagIndex, uiMapId) → uiPosx:number, uiPosy:number, flagTexture:number |  | mocked |  | PvP.lua:381,382 |
| `C_PvP.GetScoreInfo` | documented (offsetIndex) → info:PVPScoreInfo | secret in PvP match | mocked | secret: modelled by flags (forever_ref.lua) | PvP.lua:405,406 |
| `C_QuestLog.AddQuestWatch` | documented (questID) → wasWatched:bool |  | mocked |  | Region.lua:254 |
| `C_QuestLog.GetDistanceSqToQuest` | documented (questID) → distanceSq:number, onContinent:bool |  | mocked |  | Missions.lua:776,777; Quests.lua:202,203; Region.lua:116 |
| `C_QuestLog.GetInfo` | documented (questLogIndex) → info:QuestInfo |  | mocked |  | Core.lua:417,419; Abandon.lua:461; Export.lua:452,455; Leveling.lua:177,180; Pro.lua:240,241; Quests.lua:243,247,632 (+4 files) |
| `C_QuestLog.GetMaxNumQuestsCanAccept` | documented () → maxNumQuestsCanAccept:number |  | mocked |  | Abandon.lua:404,404; Quick.lua:172,172; Squad.lua:817,818 |
| `C_QuestLog.GetNextWaypoint` | documented (questID) → mapID:number, x:number, y:number |  | mocked |  | Compass.lua:505,506; Quests.lua:230,231 |
| `C_QuestLog.GetNumQuestLogEntries` | documented () → numShownEntries:number, numQuests:number |  | mocked |  | Core.lua:417,418; Export.lua:452,453; Gear.lua:341,342; Leveling.lua:177,178; Pro.lua:236,236; Quests.lua:243,244 (+3 files) |
| `C_QuestLog.GetQuestIDForLogIndex` | documented (questLogIndex) → questID:number |  | mocked |  | Gear.lua:341,343 |
| `C_QuestLog.GetQuestObjectives` | documented (questID) → objectives:table<QuestObjectiveInfo> |  | mocked |  | Export.lua:460; Leveling.lua:184,184; Missions.lua:764,765; Pro.lua:247,247; Quests.lua:256; Squad.lua:846,846 |
| `C_QuestLog.GetQuestTagInfo` | documented (questID) → info:QuestTagInfo |  | mocked |  | Export.lua:464,464; Pro.lua:252,252; Quests.lua:172,173; Squad.lua:843,843 |
| `C_QuestLog.GetQuestWatchType` | documented (questID) → watchType:QuestWatchType |  | mocked |  | Quests.lua:196,196 |
| `C_QuestLog.GetTimeAllowed` | documented (questID) → totalTime:number, elapsedTime:number |  | mocked |  | Edge2.lua:779 |
| `C_QuestLog.GetTitleForQuestID` | documented (questID) → title:cstring |  | mocked |  | Gear.lua:329,329; Session.lua:200 |
| `C_QuestLog.IsComplete` | documented (questID) → isComplete:bool |  | mocked |  | Core.lua:421,421; Export.lua:459; Leveling.lua:183,183; Pro.lua:246,246; Quests.lua:254; Squad.lua:841 (+2 files) |
| `C_QuestLog.IsFailed` | documented (questID) → isFailed:bool |  | mocked |  | Quests.lua:255 |
| `C_QuestLog.IsPushableQuest` | documented (questID) → isPushable:bool |  | mocked |  | Quick.lua:424,424; Team.lua:287,288 |
| `C_QuestLog.IsQuestFlaggedCompleted` | documented (questID) → isCompleted:bool |  | mocked |  | Edge.lua:410; Gear.lua:286; Quests.lua:432,545,932,932 |
| `C_QuestLog.IsUnitOnQuest` | documented (unit, questID) → isOnQuest:bool |  | mocked |  | Edge.lua:894,894,894; Quick.lua:430,431; Team.lua:255; Tracker.lua:342,347 |
| `C_QuestLog.ReadyForTurnIn` | documented (questID) → readyForTurnIn:bool |  | mocked |  | Quests.lua:254 |
| `C_SkillInfo.GetNumSkillLines` | documented () → numSkillLines:number |  | mocked |  | Core.lua:352,353 |
| `C_SkillInfo.GetSkillLineInfo` | documented (index) → skillLineAttributes:SkillLineAttributes |  | mocked |  | Core.lua:352,354 |
| `C_Spell.GetSpellCooldown` | documented (spellIdentifier) → spellCooldownInfo:SpellCooldownInfo | secret: cooldowns (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:138,139; Hunter.lua:650,651 |
| `C_Spell.GetSpellInfo` | documented (spellIdentifier) → spellInfo:SpellInfo |  | mocked |  | Core.lua:106,107,119,120; Binds.lua:167,168; Classes.lua:78,79; Hunter.lua:117,118; Macros.lua:32,33,202,203… |
| `C_Spell.GetSpellName` | documented (spellIdentifier) → name:cstring |  | mocked |  | Core.lua:109,110; PvPMark.lua:129,129 |
| `C_Spell.GetSpellTexture` | documented (spellIdentifier) → iconID:fileID, originalIconID:fileID, conditionalIconID:fileID |  | mocked |  | Macros.lua:1262,1262 |
| `C_Spell.IsSpellInRange` | documented (spellIdentifier, targetUnit?) → inRange:bool |  | mocked |  | Hunter.lua:615,616; PvPMark.lua:132,132 |
| `C_SpellBook.FindSpellBookSlotForSpell` | documented (spellIdentifier, includeHidden, includeFlyouts, includeFutureSpells, includeOffSpec) → spellBookItemSlotIndex:luaIndex, spellBookItemSpellBank:SpellBookSpellBank |  | mocked |  | Macros.lua:196,198,995 |
| `C_SpellBook.GetNumSpellBookSkillLines` | documented () → numSpellBookSkillLines:number |  | mocked |  | Core.lua:379,380 |
| `C_SpellBook.GetSpellBookItemName` | documented (spellBookItemSlotIndex, spellBookItemSpellBank) → name:string, subName:string |  | mocked |  | Core.lua:375,389; Finder.lua:184,184 |
| `C_SpellBook.GetSpellBookSkillLineInfo` | documented (skillLineIndex) → skillLineInfo:SpellBookSkillLineInfo |  | mocked |  | Core.lua:379,381 |
| `C_SpellBook.HasPetSpells` | documented () → numPetSpells:number, petNameToken:string |  | mocked |  | Core.lua:378 |
| `C_SpellBook.IsSpellBookItemPassive` | documented (spellBookItemSlotIndex, spellBookItemSpellBank) → isPassive:bool |  | mocked |  | Core.lua:392,392 |
| `C_SpellBook.IsSpellKnown` | documented (spellID, spellBank) → isKnown:bool |  | mocked |  | Core.lua:130,130 |
| `C_SuperTrack.GetSuperTrackedQuestID` | documented () → questID:number |  | mocked |  | Compass.lua:259,260,580,580 |
| `C_SuperTrack.IsSuperTrackingUserWaypoint` | documented () → isSuperTracking:bool |  | mocked |  | Compass.lua:272,272 |
| `C_SuperTrack.SetSuperTrackedUserWaypoint` | documented (superTracked) → - |  | mocked |  | Compass.lua:247,247 |
| `C_Timer.After` | documented (seconds, callback) → - |  | mocked |  | Core.lua:517,517; Binds.lua:40,40; Group.lua:100,100; Inventory.lua:84,84; Leveling.lua:78,78; Macros.lua:93,93 |
| `C_Timer.NewTicker` | documented (seconds, callback, iterations?) → cbObject:TickerCallback |  | mocked |  | Core.lua:621,622; Group.lua:501,502 |
| `C_TradeSkillUI.GetBaseProfessionInfo` | documented () → info:ProfessionInfo |  | mocked |  | Prof.lua:272,272 |
| `C_TradeSkillUI.GetRecipeInfo` | documented (recipeSpellID, recipeLevel?) → recipeInfo:TradeSkillRecipeInfo |  | mocked |  | Economy.lua:496,501; Prof.lua:271,279 |
| `C_TradeSkillUI.GetRecipeSchematic` | documented (recipeSpellID, isRecraft, recipeLevel?) → schematic:CraftingRecipeSchematic |  | mocked |  | Economy.lua:505,505 |
| `C_UIWidgetManager.GetAllWidgetsBySetID` | documented (setID) → widgets:table<UIWidgetInfo> |  | mocked |  | PvP.lua:315,317 |
| `C_UIWidgetManager.GetIconAndTextWidgetVisualizationInfo` | documented (widgetID) → widgetInfo:IconAndTextWidgetVisualizationInfo |  | mocked |  | PvP.lua:301,303 |
| `C_UIWidgetManager.GetTopCenterWidgetSetID` | documented () → setID:number |  | mocked |  | PvP.lua:315,316 |
| `C_UnitAuras.GetAuraDataByIndex` | documented (unit, index, filter?) → aura:AuraData | secret: auras (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:268,269; Classes.lua:106,107; Hunter.lua:153,154; PvP.lua:783,784; Team.lua:195,196 |
| `CanInspect` | documented (targetGUID) → result:bool |  | mocked |  | Team.lua:312,312 |
| `CheckInteractDistance` | documented (unitGUID, distIndex) → result:bool |  | mocked |  | Core.lua:319,320,321; Edge.lua:1059; Team.lua:171,171,313,313 |
| `ClearCursor` | documented () → - |  | mocked |  | Auction.lua:1677 |
| `GetAddOnMemoryUsage` | documented (name) → result:number |  | mocked |  | Core.lua:1445 |
| `GetBindLocation` | documented () → result:cstring |  | mocked |  | Errands.lua:231; Export.lua:526; Pro.lua:559,578; Quick.lua:660; QuickTravel.lua:494; Route.lua:325,853 (+2 files) |
| `GetBlockChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:160 |
| `GetBuildInfo` | documented () → buildVersion:cstring, buildNumber:cstring, buildDate:cstring, interfaceVersion:number, localizedVersion:cstring, buildInfo:string |  | mocked |  | Auction.lua:2155 |
| `GetCombatRating` | documented (ratingIndex) → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:153 |
| `GetCritChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:145 |
| `GetCursorPosition` | documented () → posX:number, posY:number |  | mocked |  | Hub.lua:413; Todo.lua:1299; ZonePlan.lua:948 |
| `GetDodgeChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:160 |
| `GetHaste` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:152 |
| `GetHitModifier` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:150 |
| `GetInstanceInfo` | documented () → name:cstring, instanceType:cstring, difficultyID:number, difficultyName:cstring, maxPlayers:number, dynamicDifficulty:number, isDynamic:bool, instanceID:number, instanceGroupSize:number, lfgDungeonID:number, hasWorldTier:bool |  | mocked |  | Errands.lua:395 |
| `GetManaRegen` | documented () → baseManaRegen:number, castingManaRegen:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:176 |
| `GetMeleeHaste` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:152 |
| `GetMinimapZoneText` | documented () → text:cstring |  | mocked |  | Auction.lua:648 |
| `GetMoney` | documented () → result:number |  | mocked |  | Core.lua:306; Auction.lua:1064; Economy.lua:92,371,638; Edge.lua:80; Edge2.lua:73; Errands.lua:298 (+7 files) |
| `GetPVPSessionStats` | documented () → honorableKills:number, dishonorableKills:number |  | mocked |  | PvP.lua:645 |
| `GetParryChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:160 |
| `GetPlayerFacing` | documented () → result:number |  | mocked |  | Compass.lua:742,821; Horizon.lua:161,193; MinimapPins.lua:153; PathDraw.lua:235,328; Radar.lua:1021; SafePath.lua:515 |
| `GetPowerRegen` | documented () → basePowerRegen:number, castingPowerRegen:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:177 |
| `GetRangedCritChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:146 |
| `GetRealZoneText` | documented (mapID?) → text:cstring |  | mocked |  | Core.lua:2636; Abandon.lua:502; Economy.lua:54; Edge.lua:76; Group.lua:266; Leveling.lua:347 (+8 files) |
| `GetRealmName` | documented () → realmName:cstring |  | mocked |  | Core.lua:936,936; Auction.lua:671; Group.lua:68; Hunter.lua:811; Inventory.lua:53; Leveling.lua:47,337 |
| `GetScreenHeight` | documented () → height:number |  | mocked |  | UI.lua:1798,1798 |
| `GetScreenWidth` | documented () → width:number |  | mocked |  | UI.lua:1797,1797 |
| `GetServerTime` | documented () → time:number |  | mocked |  | Auction.lua:68; Edge.lua:490; Stats.lua:50 |
| `GetSpellCritChance` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:148 |
| `GetSpellHitModifier` | documented () → result:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:151 |
| `GetSubZoneText` | documented () → text:cstring |  | mocked |  | Core.lua:2637; Auction.lua:648; Leveling.lua:348; MinimapPins.lua:190; Pro.lua:502; QuickTravel.lua:1004 |
| `GetTime` | documented () → time:number |  | mocked |  | Core.lua:33,33; Abandon.lua:389; Auction.lua:581,667; Classes.lua:69; Compass.lua:48; Economy.lua:47 (+33 files) |
| `GetUnitSpeed` | documented (unit) → currentSpeed:number, runSpeed:number, flightSpeed:number, swimSpeed:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Compass.lua:185; Export.lua:512; Hunter.lua:736; NavTime.lua:78; PathDraw.lua:328; Planner.lua:391 (+2 files) |
| `GetXPExhaustion` | documented () → result:number |  | mocked |  | Core.lua:2635; Edge.lua:696; Edge2.lua:710; Export.lua:522; Leveling.lua:228,345; Pro.lua:569 (+2 files) |
| `GetZoneText` | documented () → text:cstring |  | mocked |  | Core.lua:2636; Abandon.lua:502; Auction.lua:648; Economy.lua:54; Edge.lua:76; Pro.lua:59 (+8 files) |
| `IsAltKeyDown` | documented () → down:bool |  | mocked |  | Tracker.lua:83,83 |
| `IsControlKeyDown` | documented () → down:bool |  | mocked |  | Tracker.lua:82,82 |
| `IsInGuild` | documented () → result:bool |  | mocked |  | Edge.lua:454 |
| `IsInInstance` | documented () → isInInstance:bool, instanceType:cstring |  | mocked |  | Core.lua:1298,1732,1909,2763; Compass.lua:164; Economy.lua:278; Edge.lua:78; Edge2.lua:71; Errands.lua:393 (+6 files) |
| `IsIndoors` | documented () → result:bool |  | mocked |  | MinimapPins.lua:151; PathDraw.lua:231 |
| `IsLoggedIn` | documented () → result:bool |  | mocked |  | Hunter.lua:917 |
| `IsMounted` | documented () → result:bool |  | mocked |  | Core.lua:1913; Hunter.lua:438; NavTime.lua:63; Planner.lua:389; Stats.lua:55 |
| `IsResting` | documented () → result:bool |  | mocked |  | Core.lua:1914; Edge.lua:79; Edge2.lua:72; Leveling.lua:230,275,346,376…; Pro.lua:501,571; Quick.lua:689 (+2 files) |
| `IsShiftKeyDown` | documented () → down:bool |  | mocked |  | Compass.lua:863,863; Finder.lua:760,767; Hunter.lua:721; Macros.lua:141,141,1453,1453; Prof.lua:579,579; Quests.lua:768,768 (+3 files) |
| `IsSwimming` | documented (unit?) → result:bool |  | mocked |  | NavTime.lua:64 |
| `NotifyInspect` | documented (targetGUID) → - |  | mocked |  | Team.lua:307,316 |
| `ResetCursor` | documented () → - |  | mocked |  | UI.lua:375 |
| `SetCursor` | documented (name?) → result:bool |  | mocked |  | UI.lua:375 |
| `UnitAffectingCombat` | documented (unit) → result:bool |  | mocked |  | Classes.lua:167; Group.lua:63; Hunter.lua:292; Inventory.lua:48; Leveling.lua:42 |
| `UnitArmor` | documented (unit) → base:number, effective:number, real:number, bonus:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:158 |
| `UnitAttackPower` | documented (unit) → attackPower:number, posBuff:number, negBuff:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:143 |
| `UnitAttackSpeed` | documented (unit) → attackSpeed:number, offhandAttackSpeed:number, rangedAttackSpeed:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:172 |
| `UnitCanAttack` | documented (unit, target) → result:bool |  | mocked |  | Core.lua:1736; Edge.lua:480,574,583; Edge2.lua:600,623,647,671; Export.lua:197,245; Hunter.lua:598,628; Pro.lua:387 (+8 files) |
| `UnitCastingInfo` | documented (unit) → name:cstring, displayName:string, textureID:fileID, startTimeMs:number, endTimeMs:number, isTradeskill:bool, castID:WOWGUID, notInterruptible:bool, castingSpellID:number, castBarID:UnitCastBarID, delayTimeMs:number | secret: casts of others (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:441,442; Hunter.lua:761 |
| `UnitClass` | documented (unit) → className:cstring, classFilename:cstring, classID:number | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:943,944,2630; Classes.lua:247; Export.lua:602; Finder.lua:65; Group.lua:265; Hunter.lua:891 (+12 files) |
| `UnitClassification` | documented (unit) → result:cstring |  | mocked |  | Edge.lua:586; Edge2.lua:621,698; Hunter.lua:601; Pro.lua:412; Radar.lua:394; SafePath.lua:499 (+1 files) |
| `UnitCreatureFamily` | documented (unit) → name:cstring, id:number | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Finder.lua:206,244; Hunter.lua:782 |
| `UnitDamage` | documented (unit) → minDamage:number, maxDamage:number, offhandMinDamage:number, offhandMaxDamage:number, posBuff:number, negBuff:number, percent:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:171 |
| `UnitExists` | documented (unit?) → result:bool |  | mocked |  | Core.lua:1735; Edge.lua:479,573,582,1057; Edge2.lua:599,646,670; Errands.lua:335; Finder.lua:177; Group.lua:137 (+10 files) |
| `UnitFactionGroup` | documented (unitName, checkDisplayRace) → factionGroupTag:cstring, localized:cstring |  | mocked |  | Auction.lua:671; Export.lua:523; Finder.lua:67,606; Leveling.lua:216; PvP.lua:76; Quests.lua:674 (+4 files) |
| `UnitGUID` | documented (unit) → result:WOWGUID | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Edge.lua:575,584,1202; Edge2.lua:624,648; Export.lua:224,243; Finder.lua:249; Hunter.lua:599,670; Predict.lua:251,251 (+5 files) |
| `UnitGroupRolesAssigned` | documented (unit?) → result:cstring | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Team.lua:143 |
| `UnitHealth` | documented (unit, usePredicted) → result:number | secret ALWAYS | mocked | secret: modelled by flags (forever_ref.lua) | Edge.lua:1058,1085; Errands.lua:319; Hunter.lua:449; Pro.lua:610; PvP.lua:347; QuickTravel.lua:844 (+2 files) |
| `UnitHealthMax` | documented (unit) → result:number | secret: NPC max health (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Edge.lua:1058,1085; Errands.lua:319; Hunter.lua:449; Pro.lua:610; PvP.lua:347; QuickTravel.lua:844 (+2 files) |
| `UnitInParty` | documented (unit?, partyIndex?) → result:bool |  | mocked |  | QuickTravel.lua:1017 |
| `UnitInRaid` | documented (unit?, partyIndex?) → result:luaIndex | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | QuickTravel.lua:1017 |
| `UnitInRange` | documented (unit) → inRange:bool, checkedRange:bool | secret ALWAYS | mocked | secret: modelled by flags (forever_ref.lua) | Edge.lua:1060; Team.lua:168 |
| `UnitIsAFK` | documented (unit) → result:bool | secret in chat lockdown | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:1917; QuickTravel.lua:842; Stats.lua:812; Team.lua:163 |
| `UnitIsConnected` | documented (unit) → isConnected:bool |  | mocked |  | Edge.lua:1057; QuickTravel.lua:840; Team.lua:160 |
| `UnitIsDead` | documented (unit) → result:bool |  | mocked |  | Edge.lua:479,573,582; Edge2.lua:599,646,670; Export.lua:225,245; Hunter.lua:444,598,628,783; Predict.lua:275; Pro.lua:391 (+5 files) |
| `UnitIsDeadOrGhost` | documented (unit) → result:bool |  | mocked |  | Core.lua:1908; Classes.lua:224; Compass.lua:997; Edge.lua:1057; Hunter.lua:439; PvPMark.lua:113,400 (+5 files) |
| `UnitIsEnemy` | documented (unit, target) → result:bool |  | mocked |  | Pro.lua:387 |
| `UnitIsGhost` | documented (unit) → result:bool |  | mocked |  | Core.lua:1907; Compass.lua:998; Radar.lua:1168; Team.lua:161 |
| `UnitIsGroupAssistant` | documented (unit) → isAssistant:bool | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | QuickTravel.lua:924 |
| `UnitIsGroupLeader` | documented (unit, partyCategory?) → isLeader:bool | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | QuickTravel.lua:915 |
| `UnitIsPVP` | documented (unit) → result:bool | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | PvPMark.lua:110,110 |
| `UnitIsPVPFreeForAll` | documented (unit) → result:bool |  | mocked |  | PvPMark.lua:110 |
| `UnitIsPlayer` | documented (unit?, partyIndex?) → result:bool |  | mocked |  | Core.lua:1735; Edge.lua:479,573,582; Edge2.lua:599,623,646,670; Pro.lua:388; PvP.lua:497; PvPMark.lua:107 (+3 files) |
| `UnitIsTapDenied` | documented (unit) → result:bool |  | mocked |  | Edge.lua:480; Edge2.lua:602; Export.lua:248; Pro.lua:406 |
| `UnitIsUnit` | documented (unit1, unit2) → result:bool | secret: compound-token comparison (instances) | mocked | secret: modelled by flags (forever_ref.lua) | PvPMark.lua:119; Radar.lua:332 |
| `UnitIsVisible` | documented (unit?) → result:bool |  | mocked |  | Team.lua:173,311 |
| `UnitLevel` | documented (name) → result:number |  | mocked |  | Core.lua:2633; Abandon.lua:483; Auction.lua:974,1055; Classes.lua:226; Economy.lua:52; Edge.lua:75 (+29 files) |
| `UnitName` | documented (unit) → unitName:cstring, unitServer:cstring | secret: name of NPCs (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:935,935,2912,2912; Auction.lua:101; Economy.lua:327,387; Edge.lua:456,575,584,898…; Edge2.lua:601,624; Export.lua:765 (+19 files) |
| `UnitOnTaxi` | documented (unit) → result:bool |  | mocked |  | Core.lua:1912; Classes.lua:225; Export.lua:301; Hunter.lua:439; NavTime.lua:207; Stats.lua:54 |
| `UnitPlayerControlled` | documented (unit?) → result:bool |  | mocked |  | Export.lua:246; Finder.lua:245 |
| `UnitPosition` | documented (unit) → positionX:number, positionY:number, positionZ:number, mapID:number |  | mocked |  | Core.lua:310,311,312; Compass.lua:825; Group.lua:249; NavTime.lua:50; Planner.lua:954; Radar.lua:194,342 (+1 files) |
| `UnitPower` | documented (unitToken, powerType?, unmodified) → power:number | secret: power of others (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Errands.lua:323; Pro.lua:614; QuickTravel.lua:847; Stats.lua:214; Team.lua:167 |
| `UnitPowerMax` | documented (unitToken, powerType?, unmodified) → maxPower:number | secret: max power of others (combat) | mocked | secret: modelled by flags (forever_ref.lua) | Errands.lua:323; Pro.lua:613; QuickTravel.lua:847; Stats.lua:180,214; Team.lua:167 |
| `UnitPowerType` | documented (unit, index) → powerType:PowerType, powerTypeToken:string, rgbX:number, rgbY:number, rgbZ:number |  | mocked |  | Edge2.lua:469; Errands.lua:321; QuickTravel.lua:775,846; Team.lua:165 |
| `UnitQuestTrivialLevelRange` | documented (unit) → levelRange:number |  | mocked |  | Quests.lua:110 |
| `UnitRace` | documented (unit) → localizedRaceName:cstring, englishRaceName:cstring, raceID:number | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Core.lua:2632; Export.lua:593; Leveling.lua:340; Quests.lua:438,479 |
| `UnitRangedAttackPower` | documented (unit) → attackPower:number, posBuff:number, negBuff:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:144 |
| `UnitRangedDamage` | documented (unit) → speed:number, minDamage:number, maxDamage:number, posBuff:number, negBuff:number, percent:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Hunter.lua:693; Stats.lua:174 |
| `UnitResistance` | documented (unit, damageClass) → baseResistance:number, realResistance:number, effectiveResistance:number, bonusResistance:number | secret: identity of non-group units (combat / instance) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:163 |
| `UnitStat` | documented (unit, index) → currentStat:number, effectiveStat:number, statPositiveBuff:number, statNegativeBuff:number | secret in combat (ALL units, player too) | mocked | secret: modelled by flags (forever_ref.lua) | Stats.lua:142 |
| `UnitXP` | documented (unit) → result:number |  | mocked |  | Core.lua:2634; Abandon.lua:495; Edge.lua:695; Edge2.lua:724; Export.lua:330; Leveling.lua:343 (+5 files) |
| `UnitXPMax` | documented (unit) → result:number |  | mocked |  | Core.lua:2634; Abandon.lua:495; Edge.lua:695; Edge2.lua:709,724; Export.lua:330; Leveling.lua:229,344 (+6 files) |
| `UpdateAddOnMemoryUsage` | documented () → - |  | mocked |  | Core.lua:1444 |
| `debugprofilestop` | documented () → elapsedMilliseconds:number |  | mocked |  | Core.lua:462; Auction.lua:582; Export.lua:46; Planner.lua:46; Radar.lua:1012,1092; SafePath.lua:64 (+1 files) |
| `issecretvalue` | documented (value) → isSecret:bool |  | mocked |  | Core.lua:93,692,1898,1899; Abandon.lua:390; Boost.lua:15; Compass.lua:12; Economy.lua:49; Edge.lua:69 (+27 files) |
| `AcceptQuest` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:182 |
| `AutoLootMailItem` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:401,401 |
| `CanMerchantRepair` | c-global |  | mocked |  | Inventory.lua:338; MinimapPins.lua:559,569; Quick.lua:327 |
| `ClearInspectPlayer` | c-global |  | mocked |  | Team.lua:358,358 |
| `CompleteQuest` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:190 |
| `CreateFrame` | c-global |  | mocked |  | Core.lua:494,496,588,589…; Auction.lua:2266,2273,2275,2294…; Classes.lua:251,289; Compass.lua:850,851; Economy.lua:440; Export.lua:936 (+24 files) |
| `CreateMacro` | c-global |  | mocked |  | Macros.lua:779,811,815,994; PvP.lua:614 |
| `DeleteMacro` | c-global |  | mocked |  | Macros.lua:1185 |
| `EditMacro` | c-global |  | mocked |  | Macros.lua:779,801; PvP.lua:612 |
| `GetActionInfo` | c-global |  | mocked |  | Macros.lua:1280,1282 |
| `GetActiveTitle` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:282 |
| `GetAvailableQuestInfo` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:287 |
| `GetBattlefieldEstimatedWaitTime` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:712 |
| `GetBattlefieldStatus` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:704 |
| `GetBattlefieldTimeWaited` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:711 |
| `GetBattlefieldWinner` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:724 |
| `GetBindingAction` | c-global |  | mocked |  | Auction.lua:1893,1894 |
| `GetBindingKey` | c-global |  | mocked |  | Auction.lua:1885 |
| `GetGuildInfo` | c-global |  | mocked |  | Core.lua:2640; PvP.lua:511 |
| `GetInboxHeaderInfo` | c-global |  | mocked |  | Auction.lua:2015; Errands.lua:116; Quick.lua:374 |
| `GetInboxInvoiceInfo` | c-global |  | mocked |  | Auction.lua:2023 |
| `GetInboxItem` | c-global |  | mocked |  | Auction.lua:2024 |
| `GetInboxNumItems` | c-global |  | mocked |  | Auction.lua:2013; Errands.lua:112; Quick.lua:371 |
| `GetInventoryItemCount` | c-global |  | mocked |  | Errands.lua:331; Hunter.lua:520; Predict.lua:130; QuickTravel.lua:770,852; Stats.lua:817 |
| `GetInventoryItemDurability` | c-global |  | mocked |  | Economy.lua:237; Errands.lua:338; Inventory.lua:216; Predict.lua:153,156; QuickTravel.lua:748 |
| `GetInventoryItemID` | c-global |  | mocked |  | Classes.lua:149; Finder.lua:428; Gear.lua:225,628; Hunter.lua:513,518,536; Macros.lua:262,444 |
| `GetInventoryItemLink` | c-global |  | mocked |  | Gear.lua:223; Pro.lua:142; Stats.lua:122; Team.lua:332 |
| `GetLootSlotInfo` | c-global |  | mocked |  | MinimapPins.lua:176; Predict.lua:344; Pro.lua:457; Quests.lua:899; Stats.lua:734 |
| `GetLootSlotLink` | c-global |  | absent | not mocked (FrameXML/C global) | Export.lua:265; Stats.lua:735 |
| `GetMacroInfo` | c-global |  | mocked |  | Macros.lua:687,763,792,803… |
| `GetMaxBattlefieldID` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:700 |
| `GetMerchantItemID` | c-global |  | absent | not mocked (FrameXML/C global) | Edge.lua:810; QuickTravel.lua:573 |
| `GetMerchantItemLink` | c-global |  | absent | not mocked (FrameXML/C global) | Economy.lua:330; Edge.lua:810; QuickTravel.lua:575 |
| `GetMerchantNumItems` | c-global |  | mocked |  | Economy.lua:324; Edge.lua:807; QuickTravel.lua:571 |
| `GetNumActiveQuests` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:281 |
| `GetNumAvailableQuests` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:286 |
| `GetNumBattlefieldScores` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:421 |
| `GetNumGroupMembers` | c-global |  | mocked |  | Core.lua:1295,2642; Abandon.lua:501; Economy.lua:298; Edge.lua:81; Edge2.lua:74; Export.lua:519 (+11 files) |
| `GetNumGuildMembers` | c-global |  | absent | not mocked (FrameXML/C global) | Edge.lua:446 |
| `GetNumLootItems` | c-global |  | mocked |  | Export.lua:259; MinimapPins.lua:174; Predict.lua:341; Pro.lua:456; Quests.lua:896; Stats.lua:730 |
| `GetNumMacros` | c-global |  | mocked |  | Core.lua:301; Hunter.lua:423; Macros.lua:682 |
| `GetNumQuestChoices` | c-global |  | mocked |  | Pro.lua:482; Quick.lua:195,222 |
| `GetNumQuestLeaderBoards` | c-global |  | mocked |  | Export.lua:477; Leveling.lua:167; Pro.lua:262; Quests.lua:277 |
| `GetNumQuestLogChoices` | c-global |  | absent | not mocked (FrameXML/C global) | Abandon.lua:461,462; Gear.lua:340; ZonePlan.lua:500 |
| `GetNumQuestLogRewards` | c-global |  | absent | not mocked (FrameXML/C global) | ZonePlan.lua:501 |
| `GetNumShapeshiftForms` | c-global |  | mocked |  | Macros.lua:493 |
| `GetNumSubgroupMembers` | c-global |  | mocked |  | Todo.lua:128; Tracker.lua:343 |
| `GetNumTrainerServices` | c-global |  | absent | not mocked (FrameXML/C global) | Edge2.lua:738; Errands.lua:277 |
| `GetObjectiveText` | c-global |  | absent | not mocked (FrameXML/C global) | Pro.lua:474 |
| `GetProfessionInfo` | c-global |  | mocked |  | Gear.lua:300; Prof.lua:240 |
| `GetProfessions` | c-global |  | mocked |  | Gear.lua:300; Prof.lua:240 |
| `GetQuestID` | c-global |  | absent | not mocked (FrameXML/C global) | Pro.lua:472,478 |
| `GetQuestItemInfo` | c-global |  | mocked |  | Quick.lua:201 |
| `GetQuestItemLink` | c-global |  | mocked |  | Pro.lua:485; Quick.lua:200 |
| `GetQuestLogItemLink` | c-global |  | absent | not mocked (FrameXML/C global) | Abandon.lua:461,464; Gear.lua:340 |
| `GetQuestLogLeaderBoard` | c-global |  | mocked |  | Export.lua:478; Leveling.lua:169; Pro.lua:263; Quests.lua:279 |
| `GetQuestLogQuestText` | c-global |  | absent | not mocked (FrameXML/C global) | Quests.lua:632,633 |
| `GetQuestLogRewardXP` | c-global |  | mocked |  | Pro.lua:292; Quests.lua:158,159,162 |
| `GetQuestLogSpecialItemInfo` | c-global |  | mocked |  | Quests.lua:186,187 |
| `GetQuestMoneyToGet` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:189 |
| `GetQuestReward` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:232 |
| `GetQuestText` | c-global |  | absent | not mocked (FrameXML/C global) | Pro.lua:474 |
| `GetQuestUiMapID` | c-global |  | absent | not mocked (FrameXML/C global) | Quests.lua:225 |
| `GetRepairAllCost` | c-global |  | mocked |  | Economy.lua:311; Inventory.lua:339; Quick.lua:328; Stats.lua:490,494 |
| `GetShapeshiftFormInfo` | c-global |  | mocked |  | Macros.lua:497 |
| `GetTaxiMapID` | c-global |  | absent | not mocked (FrameXML/C global) | QuickTravel.lua:194 |
| `GetTitleText` | c-global |  | absent | not mocked (FrameXML/C global) | Session.lua:404 |
| `GetTrainerServiceCost` | c-global |  | absent | not mocked (FrameXML/C global) | Edge2.lua:744; Errands.lua:284 |
| `GetTrainerServiceInfo` | c-global |  | absent | not mocked (FrameXML/C global) | Edge2.lua:742; Errands.lua:281 |
| `HasNewMail` | c-global |  | absent | not mocked (FrameXML/C global) | QuickTravel.lua:780 |
| `HasPetUI` | c-global |  | mocked |  | Edge.lua:924 |
| `InCombatLockdown` | c-global |  | mocked |  | Core.lua:859,1487,1488,3273…; Classes.lua:166; Group.lua:63; Hunter.lua:291,959; Inventory.lua:48; Leveling.lua:42 (+5 files) |
| `IsFishingLoot` | c-global |  | absent | not mocked (FrameXML/C global) | Stats.lua:648 |
| `IsInGroup` | c-global |  | mocked |  | Core.lua:1294,2641,2859; Group.lua:108; Hunter.lua:603; Macros.lua:633; Planner.lua:430; Talents.lua:301 |
| `IsInRaid` | c-global |  | mocked |  | Core.lua:2858; Edge.lua:1053; Export.lua:527; Group.lua:117,133; Planner.lua:355,881; Quick.lua:411 (+3 files) |
| `IsQuestCompletable` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:188 |
| `IsTradeskillTrainer` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:276 |
| `NumTaxiNodes` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:185; QuickTravel.lua:207 |
| `PickupMacro` | c-global |  | mocked |  | Macros.lua:947,947,1311 |
| `QuestGetAutoAccept` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:181 |
| `QuestLogPushQuest` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:457 |
| `RepairAllItems` | c-global |  | mocked |  | Quick.lua:343 |
| `RequestBattlefieldScoreData` | c-global |  | absent | not mocked (FrameXML/C global) | PvP.lua:1000 |
| `SelectActiveQuest` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:283 |
| `SelectAvailableQuest` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:289 |
| `ShowMacroFrame` | c-global |  | absent | not mocked (FrameXML/C global) | Macros.lua:1026,1026 |
| `TakeInboxItem` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:401 |
| `TakeInboxMoney` | c-global |  | absent | not mocked (FrameXML/C global) | Quick.lua:399 |
| `TakeTaxiNode` | c-global |  | absent | not mocked (FrameXML/C global) | QuickTravel.lua:413 |
| `TaxiNodeCost` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:185; QuickTravel.lua:219 |
| `TaxiNodeGetType` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:185; QuickTravel.lua:209 |
| `TaxiNodeName` | c-global |  | absent | not mocked (FrameXML/C global) | Errands.lua:185; QuickTravel.lua:209 |
| `AuctionHouseFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Auction.lua:610,2160,2409 |
| `BackdropTemplateMixin` | framexml |  | absent | not mocked (FrameXML/C global) | Auction.lua:2275; Hub.lua:238; Macros.lua:1047,1078,1348,1377…; ZonePlan.lua:873; Options.lua:777; UI.lua:707,909,1041,1610… |
| `CLASS_ICON_TCOORDS` | framexml |  | absent | not mocked (FrameXML/C global) | Todo.lua:920 |
| `CR_HASTE_MELEE` | framexml |  | absent | not mocked (FrameXML/C global) | Stats.lua:155 |
| `ChatFontNormal` | framexml |  | absent | not mocked (FrameXML/C global) | Macros.lua:1087 |
| `CreateVector2D` | framexml |  | mocked |  | Compass.lua:58; Export.lua:54; Radar.lua:89 |
| `DEFAULT_CHAT_FRAME` | framexml |  | absent | not mocked (FrameXML/C global) | Core.lua:53 |
| `FlightMapFrame` | framexml |  | absent | not mocked (FrameXML/C global) | QuickTravel.lua:369,380 |
| `GameFontHighlight` | framexml |  | absent | not mocked (FrameXML/C global) | Todo.lua:781 |
| `GameFontHighlightSmall` | framexml |  | absent | not mocked (FrameXML/C global) | Todo.lua:1210,1210; Tracker.lua:992 |
| `GameTooltip` | framexml |  | absent | not mocked (FrameXML/C global) | Auction.lua:2361,2363,2368; Compass.lua:875,892,892,892; Finder.lua:823,830; Hub.lua:110,118,301; Macros.lua:1227,1234,1234; MinimapPins.lua:343,359 (+8 files) |
| `GetCVar` | framexml |  | mocked |  | MinimapPins.lua:154; PathDraw.lua:236; Quick.lua:798 |
| `GetUnitName` | framexml |  | absent | not mocked (FrameXML/C global) | Group.lua:138 |
| `InspectFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Team.lua:296 |
| `ItemLocation` | framexml |  | absent | not mocked (FrameXML/C global) | Auction.lua:1662 |
| `LOCALIZED_CLASS_NAMES_MALE` | framexml |  | absent | not mocked (FrameXML/C global) | PvP.lua:63; PvPMark.lua:249 |
| `MAX_QUESTLOG_QUESTS` | framexml |  | absent | not mocked (FrameXML/C global) | Abandon.lua:405; Export.lua:514; Quick.lua:172 |
| `MailFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Economy.lua:437,444 |
| `MapCanvasDataProviderMixin` | framexml |  | absent | not mocked (FrameXML/C global) | PathDraw.lua:612 |
| `MerchantFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Quick.lua:844 |
| `Minimap` | framexml |  | absent | not mocked (FrameXML/C global) | Hub.lua:69; MinimapPins.lua:78; PathDraw.lua:79 |
| `NUM_BAG_SLOTS` | framexml |  | absent | not mocked (FrameXML/C global) | Core.lua:166; Inventory.lua:100; Macros.lua:69 |
| `OpenAllMail` | framexml |  | absent | not mocked (FrameXML/C global) | Errands.lua:138 |
| `PlaySound` | framexml |  | mocked |  | Core.lua:1680,1680; Tracker.lua:577 |
| `RAID_CLASS_COLORS` | framexml |  | absent | not mocked (FrameXML/C global) | Radar.lua:209; Todo.lua:926 |
| `SOUNDKIT` | framexml |  | absent | not mocked (FrameXML/C global) | Core.lua:1665; Tracker.lua:575 |
| `SelectGossipOption` | framexml |  | absent | not mocked (FrameXML/C global) | Quick.lua:276; QuickTravel.lua:534,534 |
| `SendMailFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Economy.lua:403,437,444 |
| `SetCVar` | framexml |  | mocked |  | Quick.lua:809 |
| `Settings` | framexml |  | absent | not mocked (FrameXML/C global) | Options.lua:728,760 |
| `TaxiFrame` | framexml |  | absent | not mocked (FrameXML/C global) | QuickTravel.lua:382 |
| `TooltipDataProcessor` | framexml |  | absent | not mocked (FrameXML/C global) | Quests.lua:884; Tracker.lua:740 |
| `UIErrorsFrame` | framexml |  | absent | not mocked (FrameXML/C global) | Core.lua:2188,2188,2189,2189 |
| `UISpecialFrames` | framexml |  | absent | not mocked (FrameXML/C global) | Finder.lua:890; Hub.lua:258; ZonePlan.lua:891; Options.lua:809; UI.lua:729,2598,2598 |
| `UiMapPoint` | framexml |  | absent | not mocked (FrameXML/C global) | Compass.lua:242; Planner.lua:1029 |
| `WOW_PROJECT_ID` | framexml |  | absent | not mocked (FrameXML/C global) | Auction.lua:2157 |
| `WorldMapFrame` | framexml |  | absent | not mocked (FrameXML/C global) | PathDraw.lua:366 |

### Events the addon registers / names

| event | real | payload flags | called from |
|---|---|---|---|
| `ACTIONBAR_SLOT_CHANGED` | documented |  | Binds.lua:872 |
| `ACTIVE_TALENT_GROUP_CHANGED` | documented |  | Macros.lua:1688; Talents.lua:389 |
| `ADDON_ACTION_BLOCKED` | documented |  | Core.lua:3336,676; Binds.lua:878 |
| `ADDON_ACTION_FORBIDDEN` | documented |  | Core.lua:3335,676; Binds.lua:877; Macros.lua:1690 |
| `ADDON_LOADED` | documented |  | Core.lua:3337,735 |
| `AREA_POIS_UPDATED` | documented |  | PvP.lua:962 |
| `AUCTION_HOUSE_BROWSE_RESULTS_ADDED` | documented |  | Auction.lua:2609 |
| `AUCTION_HOUSE_BROWSE_RESULTS_UPDATED` | documented |  | Auction.lua:2609 |
| `AUCTION_HOUSE_CLOSED` | documented |  | Core.lua:1480; Auction.lua:2602; Edge.lua:1193 |
| `AUCTION_HOUSE_SHOW` | documented |  | Core.lua:1479; Auction.lua:2602; Edge.lua:1192; MinimapPins.lua:556 |
| `AUCTION_HOUSE_THROTTLED_SYSTEM_READY` | documented |  | Auction.lua:2603 |
| `AUCTION_OWNED_LIST_UPDATE` | **MISSING** (RegisterEvent errors: unknown event) |  | Auction.lua:2612 |
| `BAG_UPDATE` | documented |  | Core.lua:3352 |
| `BAG_UPDATE_COOLDOWN` | documented |  | Quests.lua:1196 |
| `BAG_UPDATE_DELAYED` | documented |  | Core.lua:3352; Classes.lua:253; Economy.lua:687; Edge2.lua:921; Gear.lua:880; Highlight.lua:1339 (+9 files) |
| `BANKFRAME_OPENED` | documented |  | Prof.lua:739 |
| `BOSS_KILL` | documented |  | Dungeon.lua:984 |
| `CHARACTER_POINTS_CHANGED` | documented |  | Gear.lua:882; Macros.lua:1689; Talents.lua:389 |
| `CHAT_MSG_ADDON` | documented |  | Core.lua:3343,736 |
| `CHAT_MSG_BG_SYSTEM_ALLIANCE` | documented |  | PvP.lua:959 |
| `CHAT_MSG_BG_SYSTEM_HORDE` | documented |  | PvP.lua:960 |
| `CHAT_MSG_BG_SYSTEM_NEUTRAL` | documented |  | PvP.lua:961 |
| `CHAT_MSG_COMBAT_HONOR_GAIN` | documented |  | PvP.lua:977 |
| `CHAT_MSG_COMBAT_XP_GAIN` | documented |  | Boost.lua:966; Detect.lua:1095; Edge.lua:1195; Edge2.lua:905; Export.lua:989; HunterPro.lua:483 (+3 files) |
| `CHAT_MSG_LOOT` | documented |  | Economy.lua:685; Edge.lua:1194; Missions.lua:1900; Session.lua:407 |
| `CHAT_MSG_MONSTER_EMOTE` | documented | secret in chat lockdown | Dungeon.lua:988; Fight.lua:680 |
| `CHAT_MSG_MONSTER_YELL` | documented | secret in chat lockdown | Dungeon.lua:988; Edge.lua:1205 |
| `CHAT_MSG_RAID_BOSS_EMOTE` | documented | secret in chat lockdown | Dungeon.lua:988 |
| `CHAT_MSG_SKILL` | documented | secret in chat lockdown | Prof.lua:738 |
| `CHAT_MSG_SYSTEM` | documented | secret in chat lockdown | Errands.lua:444 |
| `COMBAT_END` | **MISSING** (RegisterEvent errors: unknown event) |  | Abandon.lua:683; Boost.lua:978; Detect.lua:1118; Dungeon.lua:993; Fight.lua:699; Gear.lua:883 (+5 files) |
| `COMBAT_LOG_EVENT_UNFILTERED` | documented | RESTRICTED (CLEU: ADDON_ACTION_FORBIDDEN) | Fight.lua:694; Hunter.lua:830,837; HunterPro.lua:488; Predict.lua:627 |
| `COMBAT_START` | **MISSING** (RegisterEvent errors: unknown event) |  | Boost.lua:973; Dungeon.lua:992; Economy.lua:691; Fight.lua:695; HunterPro.lua:489; QuickTravel.lua:1242 |
| `COMMODITY_PRICE_UPDATED` | documented |  | Auction.lua:2603 |
| `COMMODITY_SEARCH_RESULTS_ADDED` | documented |  | Auction.lua:1194 |
| `COMMODITY_SEARCH_RESULTS_UPDATED` | documented |  | Auction.lua:1194,2606 |
| `CONFIRM_TALENT_WIPE` | documented |  | Talents.lua:393 |
| `CVAR_UPDATE` | documented |  | MinimapPins.lua:551; PathDraw.lua:656; Quick.lua:867 |
| `DAMAGE_METER_COMBAT_SESSION_UPDATED` | documented |  | Dungeon.lua:991 |
| `DISPLAY_SIZE_CHANGED` | documented |  | UI.lua:2327 |
| `DUEL_FINISHED` | documented |  | PvP.lua:981 |
| `DUEL_REQUESTED` | documented |  | PvP.lua:980 |
| `ENCOUNTER_END` | documented |  | Dungeon.lua:983 |
| `ENCOUNTER_START` | documented |  | Dungeon.lua:982 |
| `EXPORT_UPDATED` | **MISSING** (RegisterEvent errors: unknown event) |  | Planner.lua:1294; ZonePlan.lua:1076 |
| `GET_ITEM_INFO_RECEIVED` | documented |  | Highlight.lua:1345 |
| `GLOBAL_MOUSE_DOWN` | documented |  | Hub.lua:251; ZonePlan.lua:886; UI.lua:721 |
| `GOSSIP_CLOSED` | documented |  | QuickTravel.lua:1230 |
| `GOSSIP_SHOW` | documented |  | Quick.lua:838; QuickTravel.lua:1229 |
| `GROUP_ROSTER_UPDATE` | documented |  | Core.lua:1476,3358,3359; Abandon.lua:679; Detect.lua:1107; Dungeon.lua:981; Export.lua:995; Missions.lua:1889 (+7 files) |
| `HEARTHSTONE_BOUND` | documented |  | Speed.lua:323 |
| `INSPECT_READY` | documented |  | Team.lua:607 |
| `ITEM_SEARCH_RESULTS_UPDATED` | documented |  | Auction.lua:2606 |
| `LEARNED_SPELL_IN_SKILL_LINE` | documented |  | Comfort.lua:386; Macros.lua:1687 |
| `LEARNED_SPELL_IN_TAB` | **MISSING** (RegisterEvent errors: unknown event) |  | Comfort.lua:385; Macros.lua:1686 |
| `LOOT_CLOSED` | documented |  | Core.lua:3352; Highlight.lua:1354; Predict.lua:630 |
| `LOOT_OPENED` | documented |  | Economy.lua:685; Edge2.lua:908; Export.lua:990; Highlight.lua:1352; MinimapPins.lua:554; Predict.lua:628 (+3 files) |
| `LOOT_SLOT_CLEARED` | documented |  | Highlight.lua:1353 |
| `MAIL_CLOSED` | documented |  | Economy.lua:685; Errands.lua:434; Quick.lua:854; QuickTravel.lua:1232 |
| `MAIL_INBOX_UPDATE` | documented |  | Auction.lua:2603; Errands.lua:433; Quick.lua:853 |
| `MAIL_SHOW` | documented |  | Economy.lua:685; Errands.lua:432; MinimapPins.lua:555; Quick.lua:852 |
| `MERCHANT_CLOSED` | documented |  | Core.lua:3352; Economy.lua:686; Edge.lua:1191; Quick.lua:851; QuickFight.lua:834; QuickTravel.lua:1232 (+1 files) |
| `MERCHANT_SHOW` | documented |  | Detect.lua:1104; Economy.lua:686; Edge.lua:1190; MinimapPins.lua:557; Quick.lua:839; QuickFight.lua:833 (+2 files) |
| `MINIMAP_UPDATE_ZOOM` | documented |  | MinimapPins.lua:550; PathDraw.lua:656 |
| `NAME_PLATE_UNIT_ADDED` | documented |  | Boost.lua:961; Detect.lua:1096; Edge.lua:1200; Edge2.lua:913; Fight.lua:674; Highlight.lua:1347 (+5 files) |
| `NAME_PLATE_UNIT_REMOVED` | documented |  | Detect.lua:1097; Edge.lua:1201; Edge2.lua:914; Highlight.lua:1348; PvPMark.lua:385 |
| `NEW_MOUNT_ADDED` | documented |  | Quick.lua:866 |
| `OWNED_AUCTIONS_UPDATED` | documented |  | Auction.lua:2613 |
| `PARTY_KILL` | documented | secret: identity of non-group units (combat / instance) | Predict.lua:250 |
| `PARTY_LEADER_CHANGED` | documented |  | QuickTravel.lua:1240 |
| `PARTY_MEMBERS_CHANGED` | **MISSING** (RegisterEvent errors: unknown event) |  | Core.lua:3359 |
| `PET_BAR_UPDATE` | documented |  | Boost.lua:972; Hunter.lua:828 |
| `PLAYER_ALIVE` | documented |  | Core.lua:1953; Compass.lua:1091; Radar.lua:1147; Session.lua:410; Speed.lua:342 |
| `PLAYER_CONTROL_GAINED` | documented |  | Export.lua:990 |
| `PLAYER_CONTROL_LOST` | documented |  | Export.lua:990 |
| `PLAYER_DEAD` | documented |  | Core.lua:1953; Compass.lua:1091,1094; Dungeon.lua:985; Fight.lua:679; NavTime.lua:250; PvP.lua:979 (+5 files) |
| `PLAYER_ENTERING_WORLD` | documented |  | Core.lua:1476,3346,3352; Binds.lua:873; Classes.lua:252; Compass.lua:1087; Detect.lua:1087; Dungeon.lua:979 (+19 files) |
| `PLAYER_EQUIPMENT_CHANGED` | documented |  | Core.lua:3353; Gear.lua:878; Macros.lua:1680; QuickFight.lua:825; Stats.lua:1183 |
| `PLAYER_FLAGS_CHANGED` | documented |  | Detect.lua:1107; PvPMark.lua:393; Team.lua:599 |
| `PLAYER_GUILD_UPDATE` | documented |  | Core.lua:3360 |
| `PLAYER_INTERACTION_MANAGER_FRAME_SHOW` | documented |  | MinimapPins.lua:562 |
| `PLAYER_LEAVING_WORLD` | documented |  | Hunter.lua:830,860 |
| `PLAYER_LEVEL_UP` | documented |  | Core.lua:3360,3365; Abandon.lua:678,679; Boost.lua:969; Edge2.lua:919; Finder.lua:937; Gear.lua:879 (+16 files) |
| `PLAYER_LOGIN` | documented |  | Core.lua:3338,735; Auction.lua:2614; Classes.lua:290; Hunter.lua:954 |
| `PLAYER_LOGOUT` | documented |  | Core.lua:3340,736; Comfort.lua:387; Export.lua:996; Hunter.lua:830,860; Missions.lua:1902; Session.lua:411 (+2 files) |
| `PLAYER_MONEY` | documented |  | Core.lua:3360; Dungeon.lua:987; Economy.lua:685; Predict.lua:632; Session.lua:406; Stats.lua:1194 |
| `PLAYER_PVP_KILLS_CHANGED` | documented |  | PvP.lua:978 |
| `PLAYER_REGEN_DISABLED` | documented |  | Core.lua:3341,737; Classes.lua:252; Compass.lua:1089; Edge2.lua:906; Export.lua:989; Hunter.lua:827,862 (+6 files) |
| `PLAYER_REGEN_ENABLED` | documented |  | Core.lua:3342,737; Binds.lua:876; Classes.lua:252; Compass.lua:1088; Detect.lua:1099; Edge2.lua:907 (+13 files) |
| `PLAYER_STARTED_MOVING` | documented |  | NavTime.lua:247; Stats.lua:1200 |
| `PLAYER_STOPPED_MOVING` | documented |  | NavTime.lua:248; Stats.lua:1201 |
| `PLAYER_TALENT_UPDATE` | documented |  | Talents.lua:389 |
| `PLAYER_TARGET_CHANGED` | documented |  | Boost.lua:962; Detect.lua:1098; Edge.lua:1199; Edge2.lua:909; Export.lua:989; Fight.lua:671 (+11 files) |
| `PLAYER_UNGHOST` | documented |  | Core.lua:1953; Compass.lua:1091; Radar.lua:1148; Session.lua:409; Speed.lua:343 |
| `PLAYER_UPDATE_RESTING` | documented |  | Core.lua:1953; Edge2.lua:920; Pro.lua:844; Quick.lua:862; QuickTravel.lua:1232 |
| `PLAYER_XP_UPDATE` | documented |  | Boost.lua:968; Dungeon.lua:986; Export.lua:991; Predict.lua:631; Quests.lua:1157; Session.lua:402 (+3 files) |
| `QUESTS_UPDATED` | **MISSING** (RegisterEvent errors: unknown event) |  | Compass.lua:1104; Dungeon.lua:994; Quick.lua:832; Tracker.lua:1253 |
| `QUEST_ACCEPTED` | documented |  | Abandon.lua:678,679; Compass.lua:1083; Detect.lua:1090; Export.lua:990; Gear.lua:881; Highlight.lua:1341 (+5 files) |
| `QUEST_COMPLETE` | documented |  | Boost.lua:964; Missions.lua:1899; Pro.lua:842; Quick.lua:836; Session.lua:404 |
| `QUEST_DETAIL` | documented |  | Pro.lua:842; Quick.lua:834 |
| `QUEST_FINISHED` | documented |  | Boost.lua:965 |
| `QUEST_GREETING` | documented |  | Quick.lua:837 |
| `QUEST_LOG_UPDATE` | documented |  | Abandon.lua:679; Compass.lua:1083; Detect.lua:1090; Edge2.lua:915; Highlight.lua:1340; Missions.lua:1898 (+6 files) |
| `QUEST_PROGRESS` | documented |  | Quick.lua:835 |
| `QUEST_REMOVED` | documented |  | Abandon.lua:678,679; Compass.lua:1084; Detect.lua:1090; Highlight.lua:1343; Planner.lua:1283; Quests.lua:1162 (+2 files) |
| `QUEST_TURNED_IN` | documented |  | Boost.lua:967; Compass.lua:1083; Detect.lua:1094; Economy.lua:685; Export.lua:990; Highlight.lua:1342 (+7 files) |
| `QUEST_WATCH_LIST_CHANGED` | documented |  | Compass.lua:1083; Quests.lua:1156 |
| `QUEST_WATCH_UPDATE` | documented |  | Detect.lua:1090; Quests.lua:1157; Stats.lua:1197 |
| `RAID_BOSS_EMOTE` | documented |  | Dungeon.lua:988 |
| `READY` | **MISSING** (RegisterEvent errors: unknown event) |  | UI.lua:2328,2703,2908 |
| `READY_CHECK` | documented | secret in chat lockdown | Core.lua:1627; Errands.lua:440; QuickTravel.lua:1241 |
| `READY_CHECK_FINISHED` | documented |  | Errands.lua:441 |
| `REPLICATE_ITEM_LIST_UPDATE` | documented |  | Auction.lua:2602 |
| `SKILL_LINES_CHANGED` | documented |  | Macros.lua:1684; Prof.lua:738; Squad.lua:1445 |
| `SPELLS_CHANGED` | documented |  | Binds.lua:872; Comfort.lua:384; Macros.lua:1685; QuickFight.lua:824 |
| `SPELL_UPDATE_COOLDOWN` | documented |  | QuickFight.lua:826 |
| `SQUAD_UPDATE` | **MISSING** (RegisterEvent errors: unknown event) |  | Planner.lua:1296; Todo.lua:1465 |
| `START_AUTOREPEAT_SPELL` | documented |  | Fight.lua:690; Hunter.lua:829,844 |
| `START_LOOT_ROLL` | documented |  | Highlight.lua:1355; Squad.lua:1448 |
| `STOP_AUTOREPEAT_SPELL` | documented |  | Hunter.lua:829,845 |
| `SUMMARY` | **MISSING** (RegisterEvent errors: unknown event) |  | Abandon.lua:684; Auction.lua:2616; Boost.lua:982; Comfort.lua:388; Compass.lua:1098; Detect.lua:1119 (+20 files) |
| `SUPER_TRACKING_CHANGED` | documented |  | Compass.lua:1084 |
| `TAXIMAP_CLOSED` | documented |  | Errands.lua:436; QuickTravel.lua:1228 |
| `TAXIMAP_OPENED` | documented |  | Errands.lua:435; Export.lua:990; QuickTravel.lua:1227; Route.lua:861 |
| `TRADE_SKILL_CLOSE` | documented |  | Economy.lua:686 |
| `TRADE_SKILL_LIST_UPDATE` | documented |  | Prof.lua:738 |
| `TRADE_SKILL_SHOW` | documented |  | Economy.lua:686; Prof.lua:738 |
| `TRAINER_CLOSED` | documented |  | Edge2.lua:918; Errands.lua:439; QuickTravel.lua:1233 |
| `TRAINER_SHOW` | documented |  | Edge.lua:1209; Edge2.lua:916; Errands.lua:437; Pro.lua:842 |
| `TRAINER_UPDATE` | documented |  | Edge2.lua:917; Errands.lua:438 |
| `UI_SCALE_CHANGED` | documented |  | UI.lua:2327 |
| `UNIT_AURA` | documented | secret: auras (combat) | Core.lua:673; Classes.lua:253,257; Export.lua:992; Fight.lua:675; HunterPro.lua:479; Predict.lua:634 (+4 files) |
| `UNIT_COMBAT` | documented |  | Detect.lua:1113; Fight.lua:678; HunterPro.lua:482 |
| `UNIT_CONNECTION` | documented |  | Detect.lua:1107; Team.lua:599 |
| `UNIT_FACTION` | documented |  | PvPMark.lua:392 |
| `UNIT_FLAGS` | documented |  | Detect.lua:1110; Team.lua:599 |
| `UNIT_HAPPINESS` | documented |  | Fight.lua:689; Hunter.lua:828; QuickFight.lua:819 |
| `UNIT_HEALTH` | documented |  | Detect.lua:1114; Edge.lua:1206; PvP.lua:973; PvPMark.lua:399; QuickFight.lua:814; Stats.lua:250,261,345 (+1 files) |
| `UNIT_INVENTORY_CHANGED` | documented |  | Core.lua:3356; Classes.lua:253,257; Hunter.lua:828,843,866; Macros.lua:1679; Predict.lua:635; Team.lua:608 |
| `UNIT_LEVEL` | documented |  | Team.lua:599 |
| `UNIT_MAXHEALTH` | documented |  | QuickFight.lua:815 |
| `UNIT_PET` | documented |  | Boost.lua:971; Hunter.lua:828; Macros.lua:1681; QuickFight.lua:818 |
| `UNIT_POWER_UPDATE` | documented |  | QuickFight.lua:816; Team.lua:598 |
| `UNIT_QUEST_LOG_CHANGED` | documented |  | Detect.lua:1093; Highlight.lua:1344; Planner.lua:1285; Quests.lua:1160; Team.lua:603 |
| `UNIT_SPELLCAST_CHANNEL_START` | documented | secret: casts of others (combat) | Fight.lua:681 |
| `UNIT_SPELLCAST_CHANNEL_STOP` | documented | secret: casts of others (combat) | Fight.lua:685 |
| `UNIT_SPELLCAST_FAILED` | documented | secret: casts of others (combat) | Fight.lua:684 |
| `UNIT_SPELLCAST_INTERRUPTED` | documented | secret: casts of others (combat) | Fight.lua:684 |
| `UNIT_SPELLCAST_SENT` | documented | secret: casts of others (combat) | Economy.lua:687 |
| `UNIT_SPELLCAST_START` | documented | secret: casts of others (combat) | Fight.lua:681; Hunter.lua:829,855 |
| `UNIT_SPELLCAST_STOP` | documented | secret: casts of others (combat) | Fight.lua:684 |
| `UNIT_SPELLCAST_SUCCEEDED` | documented | secret: casts of others (combat) | Edge2.lua:922; Fight.lua:685; Hunter.lua:829,846; HunterPro.lua:472; Route.lua:846; Speed.lua:324 |
| `UNIT_STATS` | documented |  | Stats.lua:1184 |
| `UNIT_TARGET` | documented |  | PvPMark.lua:395 |
| `UNIT_THREAT_LIST_UPDATE` | documented |  | Boost.lua:970 |
| `UNIT_THREAT_SITUATION_UPDATE` | documented |  | Detect.lua:1115; Fight.lua:688 |
| `UPDATE_BATTLEFIELD_SCORE` | documented |  | PvP.lua:966 |
| `UPDATE_BATTLEFIELD_STATUS` | documented |  | PvP.lua:967 |
| `UPDATE_BINDINGS` | documented |  | Binds.lua:872 |
| `UPDATE_BONUS_ACTIONBAR` | documented |  | Binds.lua:873 |
| `UPDATE_EXHAUSTION` | documented |  | Todo.lua:1466 |
| `UPDATE_INVENTORY_DURABILITY` | documented |  | Economy.lua:687; Predict.lua:633; QuickTravel.lua:1232; Squad.lua:1445; Stats.lua:1207; Todo.lua:1466 |
| `UPDATE_MACROS` | documented |  | Binds.lua:872 |
| `UPDATE_MOUSEOVER_UNIT` | documented |  | Boost.lua:963; Finder.lua:936; Pro.lua:843; PvP.lua:971; Radar.lua:1138 |
| `UPDATE_PENDING_MAIL` | documented |  | QuickTravel.lua:1233 |
| `UPDATE_UI_WIDGET` | documented |  | PvP.lua:963 |
| `VIGNETTES_UPDATED` | documented |  | Detect.lua:1084 |
| `VIGNETTE_MINIMAP_UPDATED` | documented |  | Detect.lua:1085 |
| `ZONE_CHANGED` | documented |  | Radar.lua:1129; Speed.lua:335; Stats.lua:1202 |
| `ZONE_CHANGED_INDOORS` | documented |  | MinimapPins.lua:550; Radar.lua:1129 |
| `ZONE_CHANGED_NEW_AREA` | documented |  | Core.lua:1476,3346,3360; Abandon.lua:679; Compass.lua:1084; Detect.lua:1086; Dungeon.lua:980; Errands.lua:443 (+20 files) |

<!-- END API-TABLE -->
