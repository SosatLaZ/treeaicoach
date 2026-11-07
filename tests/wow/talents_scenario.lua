-- Scenario for Modules/Talents.lua on the real addon (both API flavours). Fake hunter tree built from
-- Data/Talents.lua; checks the next point + why, « Maintenant », clicks (mode / full list), combat guard,
-- dual spec hint at 40, respec price, summary, and that LearnTalent is NEVER called.
return function(M, ctx)
  M.RunSlash("/tai module Planner off")
  local T = TreeAI
  local TL = T and T.modules and T.modules.Talents
  ctx.check(TL and TL.db and TL.db.enabled and TL.Core, "Talents registered + enabled")
  ctx.check(T.Data and T.Data.Talents and T.Data.Talents.trees and T.Data.Talents.trees.HUNTER, "Data/Talents.lua loaded")
  if not (TL and T.Data and T.Data.Talents) then return end
  M.LeaveCombat()
  local learned = {}
  rawset(_G, "LearnTalent", function(...) learned[#learned + 1] = { ... } end)
  rawset(_G, "LearnTalents", function(...) learned[#learned + 1] = { ... } end)

  local tree = T.Data.Talents.trees.HUNTER
  local ranks = {}   -- "tab:tier:col" -> rank
  local unspent = 0
  local function set(tab, tier, col, r) ranks[tab .. ":" .. tier .. ":" .. col] = r end
  rawset(_G, "GetNumTalentTabs", function() return 3 end)
  rawset(_G, "GetNumTalents", function(tab) return #tree[tab].t end)
  rawset(_G, "GetTalentTabInfo", function(tab) return tree[tab].name, nil, 0, "x" end)
  rawset(_G, "GetTalentInfo", function(tab, i)
    local t = tree[tab] and tree[tab].t[i]
    if not t then return nil end
    return t[4], 132100, t[1], t[2], ranks[tab .. ":" .. t[1] .. ":" .. t[2]] or 0, t[3], false, true
  end)
  rawset(_G, "UnitCharacterPoints", function() return unspent end)

  local function find(lines, pat)
    for _, l in ipairs(lines or {}) do if tostring(l.text):find(pat, 1, true) then return l end end
  end
  local function dump(lines)
    local t = {}
    for _, l in ipairs(lines or {}) do t[#t + 1] = l.text end
    return table.concat(t, " || ")
  end
  local function sugg()
    for _, e in ipairs(T:GetSuggestions() or {}) do if e.id == "Talents:next" then return e end end
  end
  local function tab() return T:GetTabLines("info") end

  -- level 26: BM started (Aspects mortels 5, Entraînement 5, Fureur libérée 3) + 4 free points
  M.player.level = 26
  set(1, 1, 2, 5); set(1, 1, 3, 5); set(1, 3, 3, 3)
  unspent = 4
  M.FireEvent("CHARACTER_POINTS_CHANGED", -1)
  M.Advance(3)
  local lines = tab()
  ctx.check(find(lines, "Maîtrise des bêtes — quêtes en solo"), "solo BM build picked: " .. dump(lines))
  local nxt = find(lines, "4 points libres → prends « Fureur libérée » (4/5)")
  ctx.check(nxt and nxt.prio == 2 and nxt.tooltip, "next point + why + how-to tooltip: " .. dump(lines))
  ctx.check(find(lines, "Tes points : 13/0/0"), "points per tree read")
  local s = sugg()
  ctx.check(s and s.text:find("Prochain point : Fureur libérée", 1, true), "« Prochain point » in Maintenant")

  -- clicks: full list, then mode cycle auto -> solo -> group
  local full = find(lines, "[Clic] Voir le parcours complet")
  ctx.check(full and full.onClick, "full list line clickable")
  if full and full.onClick then full.onClick() end
  ctx.check(find(tab(), "✓ Aspects mortels → 5"), "full list shows done steps")
  local head = find(tab(), "Parcours :")
  if head and head.onClick then head.onClick(); head = find(tab(), "Parcours :"); head.onClick() end
  ctx.check(find(tab(), "Précision — donjons / groupe"), "click cycles to group build: " .. dump(tab()))
  head = find(tab(), "Parcours :"); if head then head.onClick() end   -- back to auto
  ctx.check(TL.char.mode == "auto", "mode back to auto")

  -- combat guard: nothing recomputed in combat
  M.EnterCombat()
  set(1, 3, 3, 5); unspent = 2
  M.FireEvent("CHARACTER_POINTS_CHANGED", -1)
  M.Advance(3)
  ctx.check(sugg() and sugg().text:find("Fureur libérée", 1, true), "no recompute in combat")
  M.LeaveCombat()
  M.FireEvent("PLAYER_REGEN_ENABLED")
  TL.Update()
  ctx.check(sugg() and sugg().text:find("Rapidité bestiale", 1, true), "recomputed after combat: " .. tostring(sugg() and sugg().text))

  unspent = 0
  TL.Update()
  ctx.check(not sugg(), "no free point -> Maintenant line cleared")

  -- level 40, no dual spec yet -> dual hint ; wipe cost seen
  M.player.level = 40
  M.talentGroup, M.numTalentGroups = 1, 1
  M.FireEvent("CONFIRM_TALENT_WIPE", 100000)
  M.Advance(2)
  TL.Update()
  ctx.check(find(tab(), "Double spé disponible (50 po"), "dual spec hint at 40: " .. dump(tab()))
  ctx.check(TL.RespecText():find("10 po", 1, true), "respec cost from CONFIRM_TALENT_WIPE")

  -- summary
  unspent = 3
  TL.Update()
  if T.UpdateSummary then T:UpdateSummary() end
  local sum = T:Summary()
  ctx.check(sum and sum.talents and sum.talents.next and sum.talents.next.name == "Rapidité bestiale"
    and sum.talents.build == "HUNTER_BM_SOLO" and sum.talents.unspent == 3, "summary.talents written")

  -- reset detected
  local before = TL.char.resets or 0
  for k in pairs(ranks) do ranks[k] = 0 end
  TL.Update()
  ctx.check((TL.char.resets or 0) == before + 1, "talent reset counted")

  M.RunSlash("/tai talents")
  ctx.check(#learned == 0, "LearnTalent never called")
  rawset(_G, "LearnTalent", nil); rawset(_G, "LearnTalents", nil)
end
