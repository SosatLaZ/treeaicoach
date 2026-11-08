-- TreeAI — Modules/Binds.lua (agent BINDS) : « sur quoi je peux bind ».
-- Lit les barres d'action (GetActionInfo, cases 1–120 + barres de forme + barres 6–8 Mainline) et les
-- raccourcis (GetBindingKey ACTIONBUTTON / MULTIACTIONBAR1..7 ; Bartender4 / ElvUI / Dominos détectés et LUS
-- seulement : jamais modifiés). Liste : cases vides qui ont déjà une touche, touches confortables libres ou
-- peu utiles, conflits (doublons, sort important sans touche, sort sur une barre sans touche, sort absent),
-- et un plan de placement des macros TAI + sorts clés par priorité. (CLEAN 4.0) Aussi les sorts que tu viens
-- d'apprendre et qui ne sont sur aucune barre (repris de l'ancien module Confort) : « Mets X sur la touche 7 ».
-- Actions UNIQUEMENT sur clic / commande du joueur (TreeAI:UserAction), hors combat, une par clic :
--   poser une macro / un sort dans une case vide (PickupMacro / PickupSpell + PlaceAction) ;
--   lier une touche (SetBinding + SaveBindings) après une ligne de confirmation.
-- Si le jeu bloque (ADDON_ACTION_FORBIDDEN, vu via notre événement + TreeAIDB.diag.blocked du cœur), on le
-- retient et on donne les instructions à faire à la main. Lua 5.1, aucune globale, toutes les API testées.
local T = _G.TreeAI
if type(T) ~= "table" or type(T.RegisterModule) ~= "function" then return end
local G = _G
local pairs, ipairs, type, tostring, tonumber, pcall = pairs, ipairs, type, tostring, tonumber, pcall
local tinsert, tconcat, tsort = table.insert, table.concat, table.sort

local M = T:RegisterModule("Binds", {
  title = "Touches",
  defaults = { enabled = true, suggest = true, allowBind = true, bindBlocked = false, placeBlocked = false },
  charDefaults = { spells = false, newSpells = {} },
  options = {
    { key = "suggest", type = "toggle", label = "Signaler les sorts importants sans touche", section = "Touches" },
    { key = "allowBind", type = "toggle", label = "Proposer de lier une touche (sur clic, avec confirmation)" },
  },
})
if not M then return end
T.Binds = M

local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function API() return type(T.API) == "table" and T.API or {} end
local function opt(k) return M.db and M.db[k] end
local function inCombat()
  if G.InCombatLockdown then return try(G.InCombatLockdown) and true or false end
  return false
end
local function now() return try(G.GetTime) or 0 end
local function after(sec, fn)
  if T.After then T:After(sec, fn) elseif G.C_Timer and G.C_Timer.After then G.C_Timer.After(sec, fn) else fn() end
end
local function say(msg) if M.Print then M:Print(msg) elseif G.print then G.print("TreeAI Touches: " .. msg) end end

---------------------------------------------------------------------------------------------------
-- Barres : case -> commande de raccourci (interface Blizzard)
---------------------------------------------------------------------------------------------------
local BARS = {
  { first = 1,   cmd = "ACTIONBUTTON",          label = "barre 1" },
  { first = 13,  cmd = nil,                     label = "barre 1 page 2" },
  { first = 61,  cmd = "MULTIACTIONBAR1BUTTON", label = "barre bas gauche" },
  { first = 49,  cmd = "MULTIACTIONBAR2BUTTON", label = "barre bas droite" },
  { first = 25,  cmd = "MULTIACTIONBAR3BUTTON", label = "barre de droite" },
  { first = 37,  cmd = "MULTIACTIONBAR4BUTTON", label = "barre de droite 2" },
  { first = 73,  cmd = "ACTIONBUTTON",          label = "barre de forme 1", bonus = true },
  { first = 85,  cmd = "ACTIONBUTTON",          label = "barre de forme 2", bonus = true },
  { first = 97,  cmd = "ACTIONBUTTON",          label = "barre de forme 3", bonus = true },
  { first = 109, cmd = "ACTIONBUTTON",          label = "barre de forme 4", bonus = true },
  { first = 145, cmd = "MULTIACTIONBAR5BUTTON", label = "barre 6", modern = true },
  { first = 157, cmd = "MULTIACTIONBAR6BUTTON", label = "barre 7", modern = true },
  { first = 169, cmd = "MULTIACTIONBAR7BUTTON", label = "barre 8", modern = true },
}
M.BARS = BARS
-- classes dont les barres de forme (cases 73–120) sont réellement utilisées
local BONUS_CLASSES = { WARRIOR = 3, DRUID = 4, ROGUE = 1, PRIEST = 1 }

local function playerClass()
  if T.PlayerClass then local c = try(T.PlayerClass, T) if c then return c end end
  local _, c = try(G.UnitClass, "player")
  return c
end

local function barOf(slot)
  for _, b in ipairs(BARS) do
    if slot >= b.first and slot < b.first + 12 then return b, slot - b.first + 1 end
  end
end

---------------------------------------------------------------------------------------------------
-- Touches : affichage FR, candidates confortables, commandes peu utiles
---------------------------------------------------------------------------------------------------
local MOUSE = { BUTTON3 = "clic molette", BUTTON4 = "souris 4", BUTTON5 = "souris 5" }
function M.KeyText(key)
  if type(key) ~= "string" or key == "" then return "?" end
  local s = key:gsub("SHIFT%-", "Maj-"):gsub("CTRL%-", "Ctrl-"):gsub("ALT%-", "Alt-")
  s = s:gsub("BUTTON(%d)", function(n) return MOUSE["BUTTON" .. n] or ("souris " .. n) end)
  return s
end
-- « Maj+N » (Data/Macros.lua) -> « SHIFT-N »
function M.KeyFromHint(h)
  if type(h) ~= "string" or h == "" or h == "-" then return nil end
  local s = h:gsub("%s", ""):gsub("[Mm]aj%+", "SHIFT-"):gsub("[Ss]hift%+", "SHIFT-"):gsub("[Cc]trl%+", "CTRL-")
    :gsub("[Aa]lt%+", "ALT-")
  return s:upper()
end

local LETTERS1 = { "Q", "E", "R", "F", "A" }        -- A : AZERTY (Q/Z de déplacement sont exclus tout seuls)
local LETTERS2 = { "Z", "X", "C", "V", "W" }
local TIERS = {}
do
  local function add(list, mod)
    local t = {}
    for _, k in ipairs(list) do t[#t + 1] = (mod or "") .. k end
    TIERS[#TIERS + 1] = t
  end
  local nums = { "1", "2", "3", "4", "5", "6" }
  add(nums); add(LETTERS1); add(LETTERS2); add(nums, "SHIFT-")
  add({ "BUTTON4", "BUTTON5", "BUTTON3" })
  add(LETTERS1, "SHIFT-"); add(LETTERS2, "SHIFT-"); add({ "BUTTON4", "BUTTON5" }, "SHIFT-")
  add(nums, "CTRL-"); add(LETTERS1, "CTRL-"); add(LETTERS2, "CTRL-")
  add(nums, "ALT-"); add(LETTERS1, "ALT-"); add(LETTERS2, "ALT-")
end
M.TIERS = TIERS
local KEY_TIER = {}
for i, t in ipairs(TIERS) do for _, k in ipairs(t) do KEY_TIER[k] = KEY_TIER[k] or i end end

local USELESS = {
  TOGGLESHEATH = "rengainer", SITORSTAND = "s'asseoir", REPLY = "répondre", REPLY2 = "répondre (2)",
  ASSISTTARGET = "assister", TARGETNEARESTFRIEND = "ami le plus proche", TARGETPREVIOUSFRIEND = "ami précédent",
  NAMEPLATES = "barres de nom", FRIENDNAMEPLATES = "barres de nom amies", ALLNAMEPLATES = "toutes les barres de nom",
  TOGGLERUN = "marcher/courir", TOGGLEACTIONBARLOCK = "verrou des barres", NEXTACTIONPAGE = "page suivante",
  PREVIOUSACTIONPAGE = "page précédente", TOGGLEWORLDSTATESCORES = "scores", TOGGLEBATTLEFIELDMINIMAP = "carte de zone",
  TOGGLEFPS = "FPS", TOGGLESELFHIGHLIGHT = "surbrillance perso",
}
for i = 1, 6 do USELESS["ACTIONPAGE" .. i] = "page de barre " .. i end
M.USELESS = USELESS

local function bindingAction(key)
  local a = try(G.GetBindingAction, key, true)
  if a == nil then a = try(G.GetBindingAction, key) end
  return type(a) == "string" and a or ""
end

local function isMovement(cmd)
  return cmd:find("^MOVE") or cmd:find("^STRAFE") or cmd:find("^TURN") or cmd == "JUMP" or cmd == "TOGGLEAUTORUN"
    or cmd == "SITORSTAND_MOVE" or cmd:find("^PITCH")
end

---------------------------------------------------------------------------------------------------
-- Sorts clés par classe (IDs rang 1, nom FR via l'API ; priorité = ordre)
---------------------------------------------------------------------------------------------------
local KEY_SPELLS = {
  HUNTER  = { 75, 3044, 1978, 1130, 2973, 5116, 2643, 781, 5384, 1499, 136, 2974, 19503, 13165 },
  WARRIOR = { 78, 100, 772, 6343, 1715, 7384, 5308, 6673, 2687, 7386, 6572, 72, 6552, 355, 845, 1680, 12294, 23881 },
  MAGE    = { 133, 116, 2136, 122, 118, 2139, 1953, 1449, 120, 5143, 1463, 11426, 12051 },
  PRIEST  = { 585, 589, 17, 139, 2050, 2061, 8092, 8122, 586, 15407, 527, 2054 },
  ROGUE   = { 1752, 2098, 53, 1766, 1776, 6770, 1784, 2983, 5277, 5171, 408, 1856, 2094, 1833, 8676 },
  DRUID   = { 5176, 8921, 774, 5185, 8936, 339, 5487, 768, 6807, 1082, 1079, 779, 6795, 770, 783 },
  WARLOCK = { 686, 172, 348, 980, 5782, 1454, 689, 1120, 6789, 5676, 755, 5484 },
  PALADIN = { 21084, 20271, 635, 19750, 853, 498, 19740, 633, 21082, 879, 26573, 642, 1152, 4987 },
  SHAMAN  = { 403, 8042, 8050, 8056, 331, 8004, 324, 8017, 17364, 3599, 8071, 8075, 370, 421 },
}
M.KEY_SPELLS = KEY_SPELLS

local function spellName(id)
  local A = API()
  local n = A.SpellName and A.SpellName(id) or try(G.GetSpellInfo, id)
  if not n and T.Data and T.Data.Macros and T.Data.Macros.spells then n = T.Data.Macros.spells[id] end
  return n
end
local function spellKnown(id)
  local A = API()
  if A.IsSpellKnown then return A.IsSpellKnown(id) and true or false end
  return (try(G.IsSpellKnown, id) or try(G.IsPlayerSpell, id)) and true or false
end
-- ID du rang le plus haut connu (le nom sans rang pointe sur le meilleur rang), repli : l'ID donné
local function bestSpellId(name, id)
  if G.C_Spell and G.C_Spell.GetSpellInfo and name then
    local info = try(G.C_Spell.GetSpellInfo, name)
    if type(info) == "table" and info.spellID then return info.spellID end
  end
  if name then
    local sid = select(7, try(G.GetSpellInfo, name))
    if type(sid) == "number" then return sid end
  end
  return id
end
local function baseName(s)
  if type(s) ~= "string" then return nil end
  s = s:gsub("%(.-%)", ""):gsub("^%s+", ""):gsub("%s+$", ""):gsub("^!", "")
  return s ~= "" and s or nil
end

---------------------------------------------------------------------------------------------------
-- Macros TAI (module Macros, lecture gardée)
---------------------------------------------------------------------------------------------------
local function macroIndex(name)
  local i = try(G.GetMacroIndexByName, name)
  if type(i) == "number" and i > 0 then return i end
end
local function macroBody(name)
  local i = macroIndex(name)
  if not i then return nil end
  local _, _, body = try(G.GetMacroInfo, i)
  return body or try(G.GetMacroBody, i)
end

-- spells named by /cast /use lines of a macro body
local function bodySpells(body, out)
  out = out or {}
  if type(body) ~= "string" then return out end
  for line in body:gmatch("[^\n]+") do
    local rest = line:match("^/cast%s+(.*)") or line:match("^/castsequence%s+(.*)") or line:match("^/use%s+(.*)")
    if rest then
      rest = rest:gsub("%b[]", ""):gsub("reset=%S+", "")
      for part in rest:gmatch("[^;,]+") do
        local n = baseName(part)
        if n and not n:match("^%d+$") then out[n] = true end
      end
    end
  end
  return out
end

-- { {n=, key=, r={ids}, def=, prio=} } from the Macros module (any of its listing APIs)
function M.MacroList()
  local mod = T.modules and T.modules.Macros or T.Macros
  if type(mod) ~= "table" then return {} end
  local raw
  for _, fname in ipairs({ "ListNames", "MacroList", "Defs" }) do
    local f = mod[fname]
    if type(f) == "function" then
      raw = try(f, mod)
      if type(raw) == "table" and #raw > 0 then break end
    end
  end
  local out = {}
  if type(raw) ~= "table" then return out end
  for i, e in ipairs(raw) do
    local m
    if type(e) == "string" then m = { n = e }
    elseif type(e) == "table" then
      local r = {}
      for _, v in ipairs(e.v or {}) do for _, id in ipairs(v.r or {}) do r[#r + 1] = id end end
      m = { n = e.n or e.name, key = M.KeyFromHint(e.k or e.key), r = r, def = e.def, prio = tonumber(e.p or e.prio) }
    end
    if m and type(m.n) == "string" then
      m.prio = m.prio or (m.def and i or 100 + i)
      out[#out + 1] = m
    end
  end
  tsort(out, function(a, b) if a.prio ~= b.prio then return a.prio < b.prio end return a.n < b.n end)
  return out
end

---------------------------------------------------------------------------------------------------
-- Barres d'addons (Bartender4 / ElvUI / Dominos) : lecture seule des boutons et de leurs touches
---------------------------------------------------------------------------------------------------
local function addonLoaded(name)
  if G.C_AddOns and G.C_AddOns.IsAddOnLoaded then return try(G.C_AddOns.IsAddOnLoaded, name) and true or false end
  return try(G.IsAddOnLoaded, name) and true or false
end
local ADDON_BARS = {
  { addon = "Bartender4", global = "Bartender4", names = function(add) for i = 1, 180 do add("BT4Button" .. i) end end },
  { addon = "ElvUI", global = "ElvUI", names = function(add)
      for b = 1, 15 do for i = 1, 12 do add("ElvUI_Bar" .. b .. "Button" .. i) end end end },
  { addon = "Dominos", global = "Dominos", names = function(add) for i = 1, 180 do add("DominosActionButton" .. i) end end },
}
local function buttonSlot(btn)
  if type(btn) ~= "table" then return nil end
  local s = tonumber(rawget(btn, "_state_action")) or tonumber(rawget(btn, "action"))
  if not s and type(btn.GetAttribute) == "function" then
    s = tonumber((try(btn.GetAttribute, btn, "action")))
  end
  if s and s >= 1 and s <= 180 then return s end
end
local function firstKey(cmd)
  if type(cmd) ~= "string" then return nil end
  local k = try(G.GetBindingKey, cmd)
  if type(k) == "string" and k ~= "" then return k end
end
-- returns { [slot] = {key=, cmd=, src=} }, { detected addon names }
function M.ReadAddonBars()
  local keys, found = {}, {}
  for _, ab in ipairs(ADDON_BARS) do
    if rawget(G, ab.global) ~= nil or addonLoaded(ab.addon) then
      found[#found + 1] = ab.addon
      ab.names(function(name)
        local btn = rawget(G, name)
        local slot = btn and buttonSlot(btn)
        if not slot then return end
        local cfg = type(btn.config) == "table" and btn.config or nil
        local target = rawget(btn, "keyBoundTarget") or (cfg and cfg.keyBoundTarget)
        local cmd, key = nil, nil
        local cmds = { "CLICK " .. name .. ":LeftButton", "CLICK " .. name .. ":Keybind" }
        if type(target) == "string" then tinsert(cmds, 1, target) end
        for _, c in ipairs(cmds) do
          key = firstKey(c)
          if key then cmd = c break end
        end
        if key and not keys[slot] then keys[slot] = { key = key, cmd = cmd, src = ab.addon } end
      end)
    end
  end
  return keys, found
end

---------------------------------------------------------------------------------------------------
-- Scan
---------------------------------------------------------------------------------------------------
local function actionAt(slot)
  if not G.GetActionInfo then return nil end
  local kind, id, sub = try(G.GetActionInfo, slot)
  if not kind then
    if try(G.HasAction, slot) then return { kind = "?" } end
    return nil
  end
  local a = { kind = kind, id = id, sub = sub }
  if kind == "spell" then a.name = spellName(id)
  elseif kind == "macro" then
    a.name = try(G.GetActionText, slot)
    if not a.name and type(id) == "number" then a.name = try(G.GetMacroInfo, id) end
    a.covers = bodySpells(a.name and macroBody(a.name))
    if sub == "spell" and type(id) == "number" then local n = spellName(id) if n then a.covers[n] = true end end
  elseif kind == "item" then
    local A = API()
    a.name = (A.ItemName and A.ItemName(id)) or try(G.GetItemInfo, id)
  else
    a.name = try(G.GetActionText, slot)
  end
  return a
end

function M:Scan()
  local st = { slots = {}, emptyKeyed = {}, keyUse = {}, onBars = {}, addons = {}, at = now() }
  if not (G.GetActionInfo or G.HasAction) then st.noApi = true end
  local cls = playerClass()
  local bonusBars = BONUS_CLASSES[cls or ""] or 0
  local addonKeys, found = M.ReadAddonBars()
  st.addons = found
  for _, b in ipairs(BARS) do
    local skip = (b.bonus and (b.first - 73) / 12 >= bonusBars) or (b.modern and not G.C_Spell)
    if not skip then
      for i = 1, 12 do
        local slot = b.first + i - 1
        local a = actionAt(slot)
        local cmd = b.cmd and (b.cmd .. i)
        local key, src = nil, nil
        local ak = addonKeys[slot]
        if ak then key, src, cmd = ak.key, ak.src, ak.cmd
        elseif cmd then key = firstKey(cmd) end
        local e = { slot = slot, bar = b.label, idx = i, cmd = cmd, key = key, src = src, action = a, bonus = b.bonus }
        st.slots[slot] = e
        if key then
          local u = st.keyUse[key]
          if not u then u = {} st.keyUse[key] = u end
          u[#u + 1] = e
        end
        if not a and key and not b.bonus then st.emptyKeyed[#st.emptyKeyed + 1] = e end
        if a and a.name then
          local list = st.onBars[a.name] or {}
          list[#list + 1] = e
          st.onBars[a.name] = list
          for n in pairs(a.covers or {}) do
            local l2 = st.onBars[n] or {}
            l2[#l2 + 1] = e
            st.onBars[n] = l2
          end
        end
      end
    end
  end
  -- empty keyed slots by comfort
  tsort(st.emptyKeyed, function(x, y)
    local tx, ty = KEY_TIER[x.key] or 99, KEY_TIER[y.key] or 99
    if tx ~= ty then return tx < ty end
    return x.slot < y.slot
  end)
  -- comfortable keys: free / useless / taken
  st.free, st.useless = {}, {}
  for _, tier in ipairs(TIERS) do
    for _, key in ipairs(tier) do
      local cmd = bindingAction(key)
      if cmd == "" then st.free[#st.free + 1] = key
      elseif USELESS[cmd] and not isMovement(cmd) then st.useless[#st.useless + 1] = { key = key, cmd = cmd, label = USELESS[cmd] }
      end
    end
  end
  self.state = st
  return st
end

---------------------------------------------------------------------------------------------------
-- Analyse : sorts clés, macros, conflits, plan
---------------------------------------------------------------------------------------------------
local function keyedEntry(list)
  for _, e in ipairs(list or {}) do if e.key then return e end end
end
local function keysOf(list)
  local ks, seen = {}, {}
  for _, e in ipairs(list or {}) do
    if e.key and not seen[e.key] then seen[e.key] = true ks[#ks + 1] = e.key end
  end
  return ks
end
local function where(e) return ("%s case %d"):format(e.bar, e.idx) end
-- touche d'un sort / d'une macro : case avec touche, sinon raccourci direct (« MACRO nom » / « SPELL nom »)
local function keyFor(st, name, kind)
  local e = keyedEntry(st.onBars[name])
  if e then return e.key, e end
  local k = firstKey((kind == "spell" and "SPELL " or "MACRO ") .. name)
  if not k and kind == nil then k = firstKey("SPELL " .. name) end
  return k
end

---------------------------------------------------------------------------------------------------
-- Nouveaux sorts appris (grimoire relu à SPELLS_CHANGED) : gardés jusqu'à ce qu'ils soient sur une barre (3 jours max)
---------------------------------------------------------------------------------------------------
--- noms des sorts actifs du grimoire (rangs confondus) : C_SpellBook (Forever / moderne) ou GetSpellBookItemName
function M.SpellbookNames()
  local names, n = {}, 0
  local SB = G.C_SpellBook
  if SB and SB.GetNumSpellBookSkillLines and SB.GetSpellBookSkillLineInfo and SB.GetSpellBookItemInfo then
    local bank = (G.Enum and G.Enum.SpellBookSpellBank and G.Enum.SpellBookSpellBank.Player) or 0
    for i = 1, (tonumber((try(SB.GetNumSpellBookSkillLines))) or 0) do
      local info = try(SB.GetSpellBookSkillLineInfo, i)
      if type(info) == "table" then
        local off, num = info.itemIndexOffset or 0, info.numSpellBookItems or 0
        for j = off + 1, off + num do
          local it = try(SB.GetSpellBookItemInfo, j, bank)
          if type(it) == "table" and it.name and not it.isPassive and not it.isOffSpec and not names[it.name] then
            names[it.name] = it.spellID or true
            n = n + 1
          end
        end
      end
    end
    if n > 0 then return names, n end
  end
  if not G.GetNumSpellTabs or not G.GetSpellBookItemName then return names, n end
  local bt = G.BOOKTYPE_SPELL or "spell"
  local last = 0
  for tab = 1, (tonumber((try(G.GetNumSpellTabs))) or 0) do
    local _, _, off, num = try(G.GetSpellTabInfo, tab)
    if off and num then last = math.max(last, off + num) end
  end
  for i = 1, math.min(last, 400) do
    local name = try(G.GetSpellBookItemName, i, bt)
    if name and not try(G.IsPassiveSpell, i, bt) and not names[name] then names[name] = true; n = n + 1 end
  end
  return names, n
end
function M:CheckNewSpells()
  if not self.char then return end
  local names, n = M.SpellbookNames()
  if n == 0 then return end
  local known = self.char.spells
  self.char.newSpells = type(self.char.newSpells) == "table" and self.char.newSpells or {}
  local fresh = self.char.newSpells
  if type(known) == "table" then
    for name, id in pairs(names) do
      if not known[name] then fresh[name] = { t = try(G.time) or 0, id = type(id) == "number" and id or nil } end
    end
  end
  local saved, cnt = {}, 0
  for k, v in pairs(names) do cnt = cnt + 1 if cnt <= 500 then saved[k] = v end end
  self.char.spells = saved   -- première fois : on mémorise sans rien annoncer
end

function M:Analyze()
  local st = self.state or self:Scan()
  local res = { ok = {}, conflicts = {}, plan = {}, items = {}, missingMacros = 0 }
  -- items to place, by priority: our macros first, then key spells not covered
  local covered = {}
  for _, m in ipairs(M.MacroList()) do
    if macroIndex(m.n) then
      res.items[#res.items + 1] = { kind = "macro", name = m.n, key = m.key, prio = m.prio }
      for _, id in ipairs(m.r or {}) do local n = spellName(id) if n then covered[n] = m.n end end
      for n in pairs(bodySpells(macroBody(m.n))) do covered[n] = covered[n] or m.n end
    elseif m.def then
      res.missingMacros = res.missingMacros + 1
    end
  end
  local cls = playerClass()
  for i, id in ipairs(KEY_SPELLS[cls or ""] or {}) do
    if spellKnown(id) then
      local n = spellName(id)
      if n then
        local coverMacro = covered[n]
        local list = st.onBars[n]
        if coverMacro then
          -- couvert par une macro TAI (sous une touche ou dans le plan)
        elseif not keyFor(st, n, "spell") then
          if list and list[1] then
            res.conflicts[#res.conflicts + 1] = { prio = 2, kind = "nokey", name = n,
              text = ("« %s » est sur une barre sans touche (%s)"):format(n, where(list[1])) }
          else
            res.conflicts[#res.conflicts + 1] = { prio = 2, kind = "missing", name = n,
              text = ("« %s » n'est sur aucune barre"):format(n) }
          end
          res.items[#res.items + 1] = { kind = "spell", name = n, id = id, prio = 200 + i, onBar = list and list[1] }
        end
      end
    end
  end
  -- nouveaux sorts appris pas encore sur une barre (ni couverts par une macro TAI) ; posés = oubliés
  local fresh = self.char and self.char.newSpells or {}
  local tnow = try(G.time) or 0
  for name, info in pairs(fresh) do
    local old = type(info) == "table" and tnow - (tonumber(info.t) or tnow) > 3 * 86400
    if st.onBars[name] or covered[name] or old then
      fresh[name] = nil
    else
      res.conflicts[#res.conflicts + 1] = { prio = 1, kind = "new", name = name,
        text = ("« %s » : nouveau sort, sur aucune barre"):format(name) }
      res.items[#res.items + 1] = { kind = "spell", name = name, id = type(info) == "table" and info.id or nil, prio = 400, new = true }
    end
  end
  -- doublons : même sort / macro sur plusieurs touches ; même touche sur plusieurs cases
  local dupSeen = {}
  for name, list in pairs(st.onBars) do
    local ks = {}
    for _, e in ipairs(list) do
      if e.action and e.action.name == name and not e.bonus then ks[#ks + 1] = e end
    end
    local keys = keysOf(ks)
    if #keys >= 2 and not dupSeen[name] then
      dupSeen[name] = true
      local shown = {}
      for _, k in ipairs(keys) do shown[#shown + 1] = M.KeyText(k) end
      res.conflicts[#res.conflicts + 1] = { prio = 1, kind = "dup", name = name,
        text = ("« %s » est sur %d touches (%s) : une suffit, libère l'autre"):format(name, #keys, tconcat(shown, ", ")) }
    end
  end
  for key, list in pairs(st.keyUse) do
    local real = {}
    for _, e in ipairs(list) do if not e.bonus then real[#real + 1] = e end end
    if #real >= 2 then
      res.conflicts[#res.conflicts + 1] = { prio = 2, kind = "samekey", key = key,
        text = ("Touche %s utilisée par 2 cases (%s et %s)"):format(M.KeyText(key), where(real[1]), where(real[2])) }
    end
  end
  tsort(res.conflicts, function(a, b) if a.prio ~= b.prio then return a.prio > b.prio end return a.text < b.text end)
  tsort(res.items, function(a, b) return a.prio < b.prio end)

  -- plan
  local reservedKey, reservedSlot = {}, {}
  local freeSet, uselessSet = {}, {}
  for _, k in ipairs(st.free) do freeSet[k] = true end
  for _, u in ipairs(st.useless) do uselessSet[u.key] = u end
  local emptyByKey = {}
  for _, e in ipairs(st.emptyKeyed) do emptyByKey[e.key] = emptyByKey[e.key] or e end
  local canBind = opt("allowBind") ~= false
  local function target(key)
    if not key or reservedKey[key] then return nil end
    local e = emptyByKey[key]
    if e and not reservedSlot[e.slot] then return { how = "place", key = key, slot = e.slot, entry = e } end
    if canBind and freeSet[key] then return { how = "bind", key = key } end
    if canBind and uselessSet[key] then return { how = "bind", key = key, replaces = uselessSet[key].label } end
  end
  local function best()
    local cands = {}
    for _, e in ipairs(st.emptyKeyed) do
      if not reservedSlot[e.slot] and not reservedKey[e.key] then
        cands[#cands + 1] = { how = "place", key = e.key, slot = e.slot, entry = e, score = (KEY_TIER[e.key] or 20) * 2 }
      end
    end
    if canBind then
      for _, k in ipairs(st.free) do
        if not reservedKey[k] then cands[#cands + 1] = { how = "bind", key = k, score = (KEY_TIER[k] or 20) * 2 + 1 } end
      end
      for _, u in ipairs(st.useless) do
        if not reservedKey[u.key] then
          cands[#cands + 1] = { how = "bind", key = u.key, replaces = u.label, score = (KEY_TIER[u.key] or 20) * 2 + 1.5 }
        end
      end
    end
    tsort(cands, function(a, b) if a.score ~= b.score then return a.score < b.score end return a.key < b.key end)
    return cands[1]
  end
  for _, it in ipairs(res.items) do
    local k, ke = keyFor(st, it.name, it.kind)
    if k then
      it.keyed = k
      res.ok[#res.ok + 1] = { name = it.name, key = k, entry = ke }
      reservedKey[k] = true
    end
  end
  for _, it in ipairs(res.items) do
    if not it.keyed and #res.plan < 12 then
      local tg = target(it.key) or best()
      if tg then
        reservedKey[tg.key] = true
        if tg.slot then reservedSlot[tg.slot] = true end
        tg.item = it
        tg.id = it.kind .. ":" .. it.name .. ":" .. tg.how .. ":" .. tg.key
        res.plan[#res.plan + 1] = tg
      end
    end
  end
  self.result = res
  return res
end

local function label(it)
  if it.kind == "command" then return "« " .. it.name .. " »" end
  return it.kind == "macro" and ("« " .. it.name .. " »") or ("« " .. it.name .. " » (sort)")
end
function M.StepText(tg)
  local k = M.KeyText(tg.key)
  if tg.how == "place" then
    return ("Mets %s sur la touche %s (%s, libre)"):format(label(tg.item), k, where(tg.entry))
  end
  if tg.replaces then
    return ("Lie %s à %s (remplace « %s », peu utile)"):format(label(tg.item), k, tg.replaces)
  end
  return ("Lie %s à %s (touche libre)"):format(label(tg.item), k)
end

local function manualText(tg)
  if tg.item.kind == "command" then
    return ("À la main : Échap > Raccourcis > Mouvements > %s : clique la case, puis appuie sur %s."):format(
      tg.item.name, M.KeyText(tg.key))
  end
  local src = tg.item.kind == "macro" and "la fenêtre /macro" or "le grimoire (P)"
  if tg.how == "place" then
    return ("À la main : ouvre %s et glisse %s sur %s (touche %s)."):format(src, label(tg.item), where(tg.entry),
      M.KeyText(tg.key))
  end
  return ("À la main : glisse %s depuis %s sur une case vide, puis Échap > Raccourcis > Barres d'action : donne %s à cette case.")
    :format(label(tg.item), src, M.KeyText(tg.key))
end
M.ManualText = manualText

---------------------------------------------------------------------------------------------------
-- Actions (clic / commande du joueur, hors combat, une par clic)
---------------------------------------------------------------------------------------------------
local function blockedCount()
  local db = T.db
  local l = type(db) == "table" and type(db.diag) == "table" and db.diag.blocked
  return type(l) == "table" and #l or 0
end

local function guarded(fn)
  M._blockedFn = nil
  local before = blockedCount()
  M._acting = true
  local ok, r = pcall(fn)
  M._acting = false
  local blocked = M._blockedFn or (blockedCount() > before and "?") or nil
  return ok and r, blocked
end

function M:OnForbidden(_, addon, func)
  if M._acting and (addon == "TreeAI" or addon == nil) then M._blockedFn = tostring(func or "?") end
end

local function needsUserAction(fn)
  if T.InUserAction and T.UserAction and not T:InUserAction() then
    T:UserAction(fn)
    return true
  end
end

function M:Place(tg)
  if needsUserAction(function() M:Place(tg) end) then return end
  if inCombat() then say("Hors combat seulement.") return false end
  local e = tg.entry
  if try(G.HasAction, tg.slot) then say("Cette case n'est plus vide : je recalcule.") self:Refresh() return false end
  local it = tg.item
  local ok, blocked = guarded(function()
    if it.kind == "macro" then
      local idx = macroIndex(it.name)
      if not idx then return false end
      G.PickupMacro(idx)
    else
      G.PickupSpell(bestSpellId(it.name, it.id))
    end
    G.PlaceAction(tg.slot)
    return true
  end)
  if try(G.GetCursorInfo) then try(G.ClearCursor) end
  local placed = try(G.HasAction, tg.slot) and true or false
  if blocked or not placed then
    if blocked then M.db.placeBlocked = true end
    say((blocked and ("Le jeu a bloqué la pose (%s). "):format(blocked) or "La pose n'a pas marché. ") .. manualText(tg))
    self:Refresh()
    return false
  end
  say(("%s posé sur %s : touche %s."):format(label(it), where(e), M.KeyText(tg.key)))
  self:Refresh()
  return ok and true
end

function M:Bind(tg, confirmed)
  if needsUserAction(function() M:Bind(tg, confirmed) end) then return end
  if inCombat() then say("Hors combat seulement.") return false end
  if M.db.bindBlocked then say(manualText(tg)) return false end
  if not (G.SetBinding and G.SaveBindings) then say(manualText(tg)) return false end
  if not confirmed then
    self.confirm = { id = tg.id, step = tg, t = now() }
    say(("Confirmer : lier %s à %s%s ? Reclique la ligne (ou /tai touches oui)."):format(label(tg.item),
      M.KeyText(tg.key), tg.replaces and (" (remplace « " .. tg.replaces .. " »)") or ""))
    self:Render()
    return false
  end
  self.confirm = nil
  local cmd = tg.item.cmd or ((tg.item.kind == "macro" and "MACRO " or "SPELL ") .. tg.item.name)
  local res, blocked = guarded(function()
    local r = G.SetBinding(tg.key, cmd)
    if r == false then return false end
    local set = try(G.GetCurrentBindingSet) or G.CHARACTER_BINDINGS or 1
    G.SaveBindings(set)
    return true
  end)
  local got = bindingAction(tg.key)
  if blocked or not res or (G.GetBindingAction and got ~= cmd) then
    if blocked then M.db.bindBlocked = true end
    say((blocked and ("Le jeu a bloqué le raccourci (%s). "):format(blocked) or "Le raccourci n'a pas pris. ")
      .. manualText(tg))
    self:Refresh()
    return false
  end
  say(("%s lié à %s (enregistré)."):format(label(tg.item), M.KeyText(tg.key)))
  self:Refresh()
  return true
end

---------------------------------------------------------------------------------------------------
-- Course automatique : un addon ne peut PAS courir tout seul (ToggleAutoRun est protégée, jamais appelée).
-- On propose de lier la commande du jeu « Course automatique » (TOGGLEAUTORUN) à une touche confortable
-- libre : un clic sur la ligne + confirmation -> SetBinding (même chemin que les autres raccourcis).
---------------------------------------------------------------------------------------------------
local AUTORUN = "TOGGLEAUTORUN"
local AUTORUN_KEYS = { "BUTTON4", "BUTTON5", "BUTTON3", "ALT-R", "CTRL-R", "ALT-Z", "SHIFT-BUTTON4", "SHIFT-BUTTON5" }
M.AUTORUN_KEYS = AUTORUN_KEYS
function M.AutoRunKeys()
  local out = {}
  local function add(k) if type(k) == "string" and k ~= "" then out[#out + 1] = k end end
  local a, b = try(G.GetBindingKey, AUTORUN)
  add(a); add(b)
  return out
end
-- nom affiché d'une touche de course auto : « ` » = « ² » sur un clavier AZERTY (client frFR)
function M.AutoRunKeyText(k)
  if k == "NUMLOCK" then return "Verr Num" end
  local loc = try(G.GetLocale)
  if (k == "`" or k == "GRAVE") and (loc == "frFR" or loc == nil) then return "²" end
  return M.KeyText(k)
end
-- on ne propose une touche QUE si la course auto n'en a aucune (celle du joueur est respectée)
function M.AutoRunStep()
  if #M.AutoRunKeys() > 0 then return nil end
  for _, k in ipairs(AUTORUN_KEYS) do
    if bindingAction(k) == "" then
      return { id = "autorun", how = "bind", key = k,
        item = { kind = "command", name = "Course automatique", cmd = AUTORUN } }
    end
  end
  return nil
end
--- Ligne d'aide d'une ligne : « Course auto : Verr Num ou Bouton 4 » (Todo, boussole, Chemin sûr).
function M.AutoRunHint()
  local keys = M.AutoRunKeys()
  if #keys == 0 then return "Course auto : pas de touche (Échap > Raccourcis > Mouvements)" end
  local names = {}
  for _, k in ipairs(keys) do names[#names + 1] = M.AutoRunKeyText(k) end
  return "Course auto : " .. tconcat(names, " ou ")
end
T.AutoRunHint = function() return M.AutoRunHint() end

function M:DoStep(tg)
  if not tg then return false end
  if tg.how == "place" then
    if M.db.placeBlocked then say(manualText(tg)) return false end
    return self:Place(tg)
  end
  local c = self.confirm
  local confirmed = c and c.id == tg.id and (now() - (c.t or 0)) < 30
  return self:Bind(tg, confirmed)
end

function M:NextStep()
  local res = self.result or self:Analyze()
  return res.plan[1]
end

---------------------------------------------------------------------------------------------------
-- Affichage : onglet « Touches » (menu Plus), suggestion, résumé
---------------------------------------------------------------------------------------------------
local function joinKeys(list, n, f)
  local out = {}
  for i, v in ipairs(list) do
    if i > n then out[#out + 1] = "…" break end
    out[#out + 1] = f and f(v) or M.KeyText(v)
  end
  return tconcat(out, ", ")
end

-- Règle d'affichage (README « Où va quelle info ») : onglet « Touches » = 1 bouton « Lier le suivant » +
-- au plus 3 étapes cliquables + 1 ligne par genre de conflit ; les listes (touches libres, cases vides,
-- sorts absents) vont dans les infobulles. Le résumé de « Maintenant » commence par un verbe, clic = étape suivante.
local TAB, TAB_TITLE = "binds", "Touches"
M.TAB = TAB
local PLAN_SHOWN = 3

local function nameList(list, n)
  local out = {}
  for i, cf in ipairs(list) do
    if i > n then out[#out + 1] = "…" break end
    out[#out + 1] = cf.name or cf.key or "?"
  end
  return tconcat(out, ", ")
end

function M:Lines()
  local st, res = self.state, self.result
  local lines = {}
  if not st or not res then return lines end
  local function add(text, prio, onClick, tooltip) lines[#lines + 1] = { text = text, prio = prio or 1, onClick = onClick, tooltip = tooltip } end
  -- bouton de l'onglet : l'étape suivante, une par clic ; l'infobulle porte le bilan et les listes
  local tip = { ("%d bien placé(s), %d à placer."):format(#res.ok, #res.plan) }
  if #st.free > 0 then tip[#tip + 1] = "Touches confortables libres : " .. joinKeys(st.free, 10) end
  if #st.emptyKeyed > 0 then
    tip[#tip + 1] = "Cases vides avec touche : " .. joinKeys(st.emptyKeyed, 8, function(e) return M.KeyText(e.key) end)
  end
  if #st.useless > 0 then
    tip[#tip + 1] = "Touches peu utiles à récupérer : " .. joinKeys(st.useless, 6, function(u)
      return M.KeyText(u.key) .. " (" .. u.label .. ")" end)
  end
  if #st.addons > 0 then tip[#tip + 1] = "Barres lues : " .. tconcat(st.addons, ", ") .. " (lecture seule)" end
  tip[#tip + 1] = "Rien n'est changé sans ton clic (/tai touches)."
  if res.plan[1] then
    lines[#lines + 1] = { text = "Lier le suivant", label = "Lier le suivant", control = true, prio = 1, tooltip = tip,
      onClick = function() M:DoStep(M:NextStep()) end }
  end
  if st.noApi then add("Barres illisibles sur ce client : suis les conseils.", 1) end
  local c = self.confirm
  if c and (now() - (c.t or 0)) < 30 then
    local tg = c.step
    add(("Confirmer : lier %s à %s ? (clic = oui)"):format(label(tg.item), M.KeyText(tg.key)), 3,
      function() M:Bind(tg, true) end)
  end
  -- (pas de ligne « course automatique » : le joueur a sa touche ; Binds.AutoRunHint reste pour l'infobulle de Todo)
  -- macros pas encore créées : le module Macros a déjà sa ligne « Nouvelles macros prêtes » (pas de doublon)
  local macrosLine = T._suggestions and T._suggestions["Macros:ready"]
  if res.missingMacros > 0 and not macrosLine then
    add(("Installe %d macro(s) TAI (clic)"):format(res.missingMacros), 2,
      function() local mod = T.modules and T.modules.Macros if mod and mod.Install then mod:Install(false) end end)
  end
  for i, tg in ipairs(res.plan) do
    if i > PLAN_SHOWN then break end
    local blocked = (tg.how == "place" and M.db.placeBlocked) or (tg.how == "bind" and M.db.bindBlocked)
    local t = blocked and manualText(tg) or (tg.how == "place" and "Clic : poser (hors combat, une par clic)"
      or "Clic : lier la touche (confirmation demandée)")
    if i == PLAN_SHOWN and #res.plan > PLAN_SHOWN then
      t = { t, ("Encore %d étape(s) ensuite : bouton « Lier le suivant »."):format(#res.plan - PLAN_SHOWN) }
    end
    add(M.StepText(tg), i == 1 and 2 or 1, function() M:DoStep(tg) end, t)
  end
  -- conflits : une ligne par genre (le détail dans l'infobulle)
  local byKind, order = {}, {}
  for _, cf in ipairs(res.conflicts) do
    if not byKind[cf.kind] then byKind[cf.kind] = {}; order[#order + 1] = cf.kind end
    local l = byKind[cf.kind]
    l[#l + 1] = cf
  end
  for _, kind in ipairs(order) do
    local l = byKind[kind]
    if #l == 1 then
      add(l[1].text, l[1].prio)
    else
      local tt = {}
      for i, cf in ipairs(l) do if i <= 10 then tt[#tt + 1] = cf.text end end
      local head
      if kind == "missing" then head = ("Pose %d sorts sur tes barres : %s"):format(#l, nameList(l, 3))
      elseif kind == "nokey" then head = ("Donne une touche à %d sorts : %s"):format(#l, nameList(l, 3))
      elseif kind == "dup" then head = ("Libère %d touches en double : %s"):format(#l, nameList(l, 3))
      elseif kind == "new" then head = ("Pose tes %d nouveaux sorts : %s"):format(#l, nameList(l, 3))
      else head = ("Règle %d touches utilisées 2 fois"):format(#l) end
      add(head, l[1].prio, nil, tt)
    end
  end
  if M.db.bindBlocked or M.db.placeBlocked then
    add("Le jeu bloque certaines actions d'addon : suis les instructions (infobulles).", 1)
  end
  return lines
end

-- bouton « Touches » (assistant / options) : ouvre l'onglet
function M:Toggle()
  self:Refresh()
  if T.SelectTab then try(T.SelectTab, T, TAB) end
  if T.ShowPanel then try(T.ShowPanel, T) end
end

local function ensureTab()
  if M.tabRegistered or not T.RegisterTab then return M.tabRegistered end
  M.tabRegistered = try(T.RegisterTab, T, TAB, TAB_TITLE, 46) and true or false
  return M.tabRegistered
end

function M:Render()
  local lines = self:Lines()
  if T.SetTabLines then
    ensureTab()
    try(T.SetTabLines, T, TAB, lines, "Binds")
  end
  local res = self.result
  if res and opt("suggest") ~= false and self.Suggest then
    local n = 0
    for _, cf in ipairs(res.conflicts) do if cf.kind == "nokey" or cf.kind == "missing" then n = n + 1 end end
    if n > 0 then
      self:Suggest("todo", ("Lie tes %d sorts importants : clic = le suivant"):format(n), 1,
        { ttl = 600, tab = TAB, onClick = function() M:DoStep(M:NextStep()) end,
          tooltip = "Détail : onglet « Touches » (menu Plus)." })
    elseif self.Clear then self:Clear("todo") end
  end
  if T.Summary then
    local s = try(T.Summary, T)
    if type(s) == "table" and res and self.state then
      local plan = {}
      for i, tg in ipairs(res.plan) do if i <= 6 then plan[#plan + 1] = M.StepText(tg) end end
      local conf = {}
      for i, cf in ipairs(res.conflicts) do if i <= 6 then conf[#conf + 1] = cf.text end end
      s.binds = { ok = #res.ok, plan = plan, conflicts = conf, free = #self.state.free,
        emptyKeyed = #self.state.emptyKeyed, addons = self.state.addons,
        bindBlocked = M.db.bindBlocked or nil, placeBlocked = M.db.placeBlocked or nil }
    end
  end
  return lines
end

function M:Refresh()
  if inCombat() then self._dirty = true return end
  self._dirty = false
  pcall(self.CheckNewSpells, self)
  self:Scan()
  self:Analyze()
  self:Render()
end

local pending = false
local function schedule()
  if pending then return end
  pending = true
  after(1, function() pending = false M:Refresh() end)
end
M.Schedule = schedule

function M:OnEnable()
  for _, ev in ipairs({ "ACTIONBAR_SLOT_CHANGED", "UPDATE_BINDINGS", "SPELLS_CHANGED", "UPDATE_MACROS",
    "PLAYER_ENTERING_WORLD", "UPDATE_BONUS_ACTIONBAR", "LEARNED_SPELL_IN_TAB", "LEARNED_SPELL_IN_SKILL_LINE" }) do
    self:RegisterEvent(ev, schedule)
  end
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() if M._dirty then schedule() end end)
  self:RegisterEvent("ADDON_ACTION_FORBIDDEN", M.OnForbidden)
  self:RegisterEvent("ADDON_ACTION_BLOCKED", M.OnForbidden)
  after(4, function() M:Refresh() end)
end

function M:OnDisable()
  if T.SetTabLines then try(T.SetTabLines, T, TAB, {}, "Binds") end
end

---------------------------------------------------------------------------------------------------
-- /tai touches [poser|oui|reset]
---------------------------------------------------------------------------------------------------
function M:Report()
  self:Refresh()
  for i, l in ipairs(self:Lines()) do
    if i > 14 then break end
    say(l.text)
  end
end

local function slash(rest)
  local cmd = ((rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", ""))
  if cmd == "poser" or cmd == "suivant" or cmd == "placer" then
    M:Refresh()
    local tg = M:NextStep()
    if tg then M:DoStep(tg) else say("Rien à placer : tout est sous une touche.") end
  elseif cmd == "oui" then
    local c = M.confirm
    if c and (now() - (c.t or 0)) < 30 then M:Bind(c.step, true) else say("Rien à confirmer.") end
  elseif cmd == "reset" then
    M.db.bindBlocked, M.db.placeBlocked = false, false
    say("Tests de blocage remis à zéro.")
    M:Refresh()
  else
    M:Report()
  end
end
M.Slash = slash
if T.RegisterSlash then
  T:RegisterSlash("touches", slash, "touches [poser|oui|reset] : sur quoi binder (cases et touches libres, plan, conflits)")
end
