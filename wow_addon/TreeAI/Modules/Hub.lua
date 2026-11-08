-- TreeAI — module Hub (agent MENU) : la pastille ronde autour de la minimap (comme les vrais addons) + son menu.
--  * Pastille : bouton rond enfant de la minimap (comme LibDBIcon : bordure de suivi, glisser autour du bord,
--    angle sauvé ; minimaps carrées / arrondies selon GetMinimapShape), aussi dans le compartiment d'addons
--    du jeu s'il existe (AddonCompartmentFrame:RegisterAddon, API publique).
--  * Clic gauche : menu « hub » (sections Jeu / Outils / Réglages, icônes) : chaque onglet du panneau, Options,
--    Avantages, Recherche, Meilleur familier, HV, Macros, Touches, Erreurs, Blocages, Profil… tout à la souris.
--    Clic droit : Options. Infobulle : résumé en direct (prochaine étape, XP/h, niveau dans…).
-- Rien d'automatique ; seul le glisser a un OnUpdate (pendant le glisser seulement). Pas de cadre de Blizzard
-- modifié : notre bouton est notre enfant de Minimap (non protégée), jamais de SetScript / SetSize sur elle.
-- Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local H = T:RegisterModule("Hub", {
  title = "Pastille de la minimap",
  defaults = { enabled = true, button = true, angle = 205, compartment = true },
  options = {
    { key = "button", type = "toggle", label = "Pastille ronde autour de la minimap (clic : menu de TreeAI)" },
    { key = "compartment", type = "toggle", label = "Aussi dans le compartiment d'addons du jeu (s'il existe)" },
  },
})
T.Hub = H
if type(T.MODULE_INFO) == "table" and not T.MODULE_INFO.Hub then
  T.MODULE_INFO.Hub = { lite = true, desc = "Pastille de la minimap : tout TreeAI en un clic" }
end

local floor, max, min, sqrt = math.floor, math.max, math.min, math.sqrt
local fmt = string.format
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function G(name) return rawget(_G, name) end
local function cfg(k) return not (H.db and H.db[k] == false) end
local function secret(v) local f = G("issecretvalue") return type(f) == "function" and f(v) and true or false end
local L = T.L or setmetatable({}, { __index = function(_, k) return k end })
local ICON = "Interface\\Icons\\"
H.ICON = ICON .. "Spell_Nature_ResistNature"

-- ------------------------------------------------------------------ position (pur : testé)
-- quadrants arrondis (true) ou carrés (false) selon la forme de la minimap (même géométrie que les addons connus)
H.SHAPES = {
  ROUND = { true, true, true, true }, SQUARE = { false, false, false, false },
  ["CORNER-TOPLEFT"] = { false, false, false, true }, ["CORNER-TOPRIGHT"] = { false, false, true, false },
  ["CORNER-BOTTOMLEFT"] = { false, true, false, false }, ["CORNER-BOTTOMRIGHT"] = { true, false, false, false },
  ["SIDE-LEFT"] = { false, true, false, true }, ["SIDE-RIGHT"] = { true, false, true, false },
  ["SIDE-TOP"] = { false, false, true, true }, ["SIDE-BOTTOM"] = { true, true, false, false },
  ["TRICORNER-TOPLEFT"] = { false, true, true, true }, ["TRICORNER-TOPRIGHT"] = { true, false, true, true },
  ["TRICORNER-BOTTOMLEFT"] = { true, true, false, true }, ["TRICORNER-BOTTOMRIGHT"] = { true, true, true, false },
}
--- Décalage (x, y) du centre de la pastille depuis le centre de la minimap pour un angle (degrés).
function H.Offset(angle, w, h, shape)
  local rad = math.rad(tonumber(angle) or 205)
  local x, y, q = math.cos(rad), math.sin(rad), 1
  if x < 0 then q = q + 1 end
  if y > 0 then q = q + 2 end
  local quad = H.SHAPES[shape or "ROUND"] or H.SHAPES.ROUND
  local rw, rh = (w or 140) / 2 + 5, (h or 140) / 2 + 5
  if quad[q] then return x * rw, y * rh end
  local dw, dh = sqrt(2 * rw * rw) - 10, sqrt(2 * rh * rh) - 10
  return max(-rw, min(x * dw, rw)), max(-rh, min(y * dh, rh))
end
--- Angle (degrés, 0–360) d'un point écran par rapport au centre de la minimap.
function H.AngleOf(cx, cy, px, py)
  local a = math.deg(math.atan2(py - cy, px - cx))
  if a < 0 then a = a + 360 end
  return a
end

local button
local function minimap() return G("Minimap") end
local function shape()
  local f = G("GetMinimapShape")
  local s = type(f) == "function" and try(f) or nil
  return type(s) == "string" and s or "ROUND"
end
function H.Place()
  local mm = minimap()
  if not (button and mm) then return end
  local x, y = H.Offset(H.db and H.db.angle, mm:GetWidth(), mm:GetHeight(), shape())
  button:ClearAllPoints()
  button:SetPoint("CENTER", mm, "CENTER", x, y)
end

-- ------------------------------------------------------------------ résumé en direct (infobulle)
local function plain(s) return T.PlainText and T.PlainText(s) or tostring(s or "") end
function H.Summary()
  local out = {}
  local now = T.GetTabLines and T:GetTabLines("now") or {}
  local first
  for _, e in ipairs(now) do if e.pin then first = e break end end
  first = first or now[1]
  if first and type(first.text) == "string" and not secret(first.text) then
    out[#out + 1] = { "Prochaine étape : " .. plain(first.text), 1, 1, 1 }
  else
    out[#out + 1] = { "Rien d'urgent.", 0.7, 0.7, 0.7 }
  end
  local B = T.modules and T.modules.Boost
  local r = B and B.XPStats and select(2, pcall(B.XPStats, B))
  if type(r) == "table" and tonumber(r.perHour) and r.perHour > 0 then
    local Fm = T.Fmt
    local xp = Fm and Fm.Num and Fm.Num(r.perHour) or tostring(floor(r.perHour))
    local eta = tonumber(r.eta) and Fm and Fm.Time and Fm.Time(r.eta) or nil
    out[#out + 1] = { "XP/h : " .. xp .. (eta and (" · niveau suivant dans ~" .. eta) or ""), 0.55, 0.8, 1 }
  end
  local n = 0
  for _, e in ipairs(now) do if (e.prio or 1) >= 3 then n = n + 1 end end
  if n > 0 then out[#out + 1] = { n .. " alerte" .. (n > 1 and "s" or "") .. " urgente" .. (n > 1 and "s" or ""), 1, 0.35, 0.3 } end
  return out
end
local function showTip(owner)
  local tt = G("GameTooltip")
  if not tt then return end
  tt:SetOwner(owner, "ANCHOR_LEFT")
  tt:AddLine("TreeAI Coach", 0.2, 0.8, 0.4)
  for _, l in ipairs(H.Summary()) do tt:AddLine(l[1], l[2], l[3], l[4], true) end
  tt:AddLine("Clic : menu · Clic droit : options · Glisser : déplacer", 0.5, 0.5, 0.5, true)
  tt:Show()
end
local function hideTip() local tt = G("GameTooltip") if tt then tt:Hide() end end

-- ------------------------------------------------------------------ entrées du menu (tout à la souris)
local function act(fn, ...) local a = { ... } return function() T:UserAction(function() fn(unpack(a)) end) end end
local function slashCmd(cmd) return act(function(c) T:HandleSlash(c) end, cmd) end
local function hasSlash(c) return T.HasSlash and T:HasSlash(c:match("^(%S+)")) or false end
local function tab(id) return act(function() T:ShowPanel() T:SelectTab(id) end) end
H.GROUPS = {
  { id = "jeu", title = "Jeu", icon = ICON .. "INV_Misc_Map_01" },
  { id = "outils", title = "Outils", icon = ICON .. "INV_Misc_Wrench_01" },
  { id = "reglages", title = "Réglages", icon = ICON .. "INV_Misc_Gear_01" },
}
local TAB_ICON = {
  now = ICON .. "Spell_Nature_TimeStop", quests = ICON .. "INV_Misc_Note_02", group = ICON .. "Ability_Hunter_Pet_Wolf",
  predict = ICON .. "INV_Misc_PocketWatch_01", hv = ICON .. "INV_Misc_Coin_01", prof = ICON .. "Trade_BlackSmithing",
  talents = ICON .. "Ability_Marksmanship", gear = ICON .. "INV_Chest_Chain", dungeon = ICON .. "INV_Misc_Key_03",
  pvp = ICON .. "Ability_DualWield", info = ICON .. "INV_Misc_QuestionMark", binds = ICON .. "INV_Misc_Key_14",
  adv = ICON .. "Ability_Hunter_SniperShot",
}
function H.Entries()
  local out = {}
  for _, t in ipairs(T.GetTabs and T:GetTabs() or {}) do
    out[#out + 1] = { group = "jeu", id = "tab:" .. t.id, title = L[t.title or t.id], icon = TAB_ICON[t.id] or ICON .. "INV_Misc_Note_01",
      tip = "Clic : ouvrir l'onglet « " .. L[t.title or t.id] .. " » du panneau", fn = tab(t.id) }
  end
  local function add(g, id, title, icon, tip, fn, ok)
    if ok == false then return end
    out[#out + 1] = { group = g, id = id, title = title, icon = icon, tip = tip, fn = fn }
  end
  local F, Mc, Td = T.Finder, T.modules and T.modules.Macros, T.Todo
  add("outils", "cherche", "Recherche", ICON .. "INV_Misc_Spyglass_02", "Clic : chercher un truc qui change tout près de toi",
    act(function() if F and F.Show then F.Show(true) else T:HandleSlash("cherche") end end), (F and F.Show or hasSlash("cherche")) and true or false)
  add("outils", "pet", "Meilleur familier", ICON .. "Ability_Hunter_BeastTaming", "Clic : la meilleure bête à apprivoiser maintenant",
    slashCmd("cherche familier"), (T.PlayerClass and T:PlayerClass() == "HUNTER" and hasSlash("cherche")) and true or false)
  add("outils", "macros", "Macros", ICON .. "INV_Misc_Note_01", "Clic : ouvrir les macros (hors combat)",
    act(function() if Mc and Mc.TogglePanel then Mc:TogglePanel() end end), (Mc and Mc.TogglePanel) and true or false)
  add("outils", "todo", "Fenêtre « À faire »", ICON .. "INV_Misc_Note_02", "Clic : la liste complète des quêtes (épingler, trier)",
    act(function() Td.Toggle() end), (Td and Td.Toggle) and true or false)
  add("outils", "boussole", "Boussole", ICON .. "INV_Misc_Spyglass_02", "Clic : afficher / cacher la flèche", slashCmd("boussole"), hasSlash("boussole"))
  add("outils", "radar", "Radar", ICON .. "INV_Misc_Map_01", "Clic : afficher / cacher le radar", slashCmd("radar"), hasSlash("radar"))
  for _, e in ipairs(type(T.HUB_EXTRA) == "table" and T.HUB_EXTRA or {}) do   -- entrées des modules (Dungeon…)
    if type(e) == "table" and type(e.fn) == "function" and e.title then
      add(e.group or "outils", e.id or e.title, e.title, e.icon or ICON .. "INV_Misc_Note_01", e.tip, act(e.fn))
    end
  end
  add("reglages", "options", "Options", ICON .. "INV_Misc_Gear_01", "Clic : toutes les options (taille, transparence, modules)",
    act(function() if T.OpenOptions then T:OpenOptions() end end))
  add("reglages", "profil", "Profil des modules", ICON .. "INV_Misc_Book_09", "Clic : essentiel / tout", slashCmd("profil"), hasSlash("profil"))
  add("reglages", "erreurs", "Erreurs", "Interface\\DialogFrame\\UI-Dialog-Icon-AlertOther", "Clic : dernières erreurs de TreeAI", slashCmd("erreurs"), hasSlash("erreurs"))
  add("reglages", "bloque", "Blocages", "Interface\\DialogFrame\\UI-Dialog-Icon-AlertNew", "Clic : actions bloquées par le jeu (doit rester à 0)", slashCmd("bloque"), hasSlash("bloque"))
  add("reglages", "panel", "Afficher / cacher le panneau", ICON .. "INV_Misc_Note_06", "Clic : le panneau de TreeAI", act(function() T:TogglePanel() end))
  add("reglages", "edit", "Déplacer les cadres", ICON .. "INV_Misc_Wrench_01", "Clic : mode édition (glisser, molette = taille)",
    act(function() if T.SetEditMode then T:SetEditMode(not T:IsEditMode()) end end), T.SetEditMode and true or false)
  add("reglages", "reset", "Réinitialiser l'interface", "Interface\\Buttons\\UI-RefreshButton", "Clic : tout remettre à sa place automatique",
    act(function() if T.ResetInterface then T:ResetInterface() end end), T.ResetInterface and true or false)
  return out
end

-- ------------------------------------------------------------------ menu (cadre à nous)
local menu
H.layout = {}
local ROW, HEAD, W = 20, 17, 220
local function closeMenu() if menu and menu:IsShown() then menu:Hide() end end
H.Close = closeMenu
local function buildMenu()
  if menu then return menu end
  local tpl = G("BackdropTemplateMixin") and "BackdropTemplate" or nil
  menu = G("CreateFrame")("Frame", "TreeAIHubMenu", G("UIParent"), tpl)
  menu:SetFrameStrata("DIALOG")
  menu:SetClampedToScreen(true)
  menu:EnableMouse(true)
  if menu.SetBackdrop then
    menu:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8", edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      edgeSize = 10, insets = { left = 2, right = 2, top = 2, bottom = 2 } })
    menu:SetBackdropColor(0.05, 0.06, 0.08, 0.96)
    menu:SetBackdropBorderColor(0.78, 0.62, 0.28, 0.7)
  end
  menu.rows, menu.heads, menu.seps = {}, {}, {}
  if menu.RegisterEvent and pcall(menu.RegisterEvent, menu, "GLOBAL_MOUSE_DOWN") then
    menu:SetScript("OnEvent", function(self)
      if not self:IsShown() then return end
      local over = (self.IsMouseOver and self:IsMouseOver()) or (button and button.IsMouseOver and button:IsMouseOver())
      if not over then closeMenu() end
    end)
  end
  local list = G("UISpecialFrames")
  if type(list) == "table" then list[#list + 1] = "TreeAIHubMenu" end
  menu:Hide()
  return menu
end
local function row(i)
  local b = menu.rows[i]
  if b then return b end
  b = G("CreateFrame")("Button", nil, menu)
  b:SetHeight(ROW)
  b:RegisterForClicks("LeftButtonUp")
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetSize(14, 14)
  b.icon:SetPoint("LEFT", b, "LEFT", 8, 0)
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.text:SetPoint("LEFT", b.icon, "RIGHT", 6, 0)
  b.text:SetPoint("RIGHT", b, "RIGHT", -6, 0)
  b.text:SetJustifyH("LEFT")
  if b.text.SetWordWrap then b.text:SetWordWrap(false) end
  b.hl = b:CreateTexture(nil, "HIGHLIGHT")
  b.hl:SetAllPoints(b)
  b.hl:SetTexture("Interface\\Buttons\\WHITE8X8")
  if b.hl.SetVertexColor then b.hl:SetVertexColor(1, 1, 1, 0.10) end
  b:SetScript("OnClick", function(self)
    local e = self.e
    closeMenu()
    if e and e.fn then e.fn() end
  end)
  b:SetScript("OnEnter", function(self)
    local tt, e = G("GameTooltip"), self.e
    if not (tt and e) then return end
    tt:SetOwner(self, "ANCHOR_RIGHT")
    tt:AddLine(e.tip or e.title, 0.4, 0.8, 1, true)
    tt:Show()
  end)
  b:SetScript("OnLeave", hideTip)
  menu.rows[i] = b
  return b
end
function H.Fill()
  buildMenu()
  local lay = H.layout
  for i = #lay, 1, -1 do lay[i] = nil end
  for _, r in ipairs(menu.rows) do r:Hide(); r.e = nil end
  for _, h in ipairs(menu.heads) do h:Hide(); h.icon:Hide() end
  for _, s in ipairs(menu.seps) do s:Hide() end
  local y, n, nh, ns, last = -5, 0, 0, 0, nil
  for _, e in ipairs(H.Entries()) do
    if e.group ~= last then
      if last then
        ns = ns + 1
        local s = menu.seps[ns]
        if not s then
          s = menu:CreateTexture(nil, "ARTWORK")
          s:SetTexture("Interface\\Buttons\\WHITE8X8")
          if s.SetVertexColor then s:SetVertexColor(0.78, 0.62, 0.28, 0.35) end
          s:SetHeight(1)
          menu.seps[ns] = s
        end
        s:ClearAllPoints()
        s:SetPoint("TOPLEFT", menu, "TOPLEFT", 8, y - 3)
        s:SetPoint("TOPRIGHT", menu, "TOPRIGHT", -8, y - 3)
        s:Show()
        lay[#lay + 1] = { kind = "sep", y = y - 3 }
        y = y - 7
      end
      last = e.group
      for _, g in ipairs(H.GROUPS) do
        if g.id == e.group then
          nh = nh + 1
          local h = menu.heads[nh]
          if not h then
            h = menu:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
            h:SetJustifyH("LEFT")
            h.icon = menu:CreateTexture(nil, "ARTWORK")
            h.icon:SetSize(12, 12)
            menu.heads[nh] = h
          end
          h.icon:ClearAllPoints()
          h.icon:SetPoint("TOPLEFT", menu, "TOPLEFT", 8, y - 2)
          h.icon:SetTexture(g.icon)
          h.icon:Show()
          h:ClearAllPoints()
          h:SetPoint("TOPLEFT", menu, "TOPLEFT", 24, y - 2)
          h:SetText(L[g.title])
          h:SetTextColor(0.78, 0.62, 0.28)
          h:Show()
          lay[#lay + 1] = { kind = "header", group = g.id, y = y }
          y = y - HEAD
        end
      end
    end
    n = n + 1
    local b = row(n)
    b.e = e
    -- (même convention que « Plus → Modules » : l'orage de clics du harnais laisse ces bascules tranquilles)
    b.entry = { id = "mod:hub:" .. e.id }
    b:ClearAllPoints()
    b:SetPoint("TOPLEFT", menu, "TOPLEFT", 3, y)
    b:SetPoint("TOPRIGHT", menu, "TOPRIGHT", -3, y)
    b:SetWidth(W - 6)
    b.icon:SetTexture(e.icon)
    b.text:SetText(e.title)
    b.text:SetTextColor(e.group == "jeu" and 1 or 0.6, e.group == "jeu" and 0.82 or 0.85, e.group == "jeu" and 0 or 1)
    b:Show()
    lay[#lay + 1] = { kind = "item", id = e.id, group = e.group, y = y, text = e.title,
      textW = b.text:GetStringWidth() or 0, textMax = W - 6 - 8 - 14 - 6 - 6, button = b }
    y = y - ROW
  end
  menu:SetSize(W, -y + 5)
  menu:ClearAllPoints()
  if button then menu:SetPoint("TOPRIGHT", button, "BOTTOMLEFT", 8, 4)
  else menu:SetPoint("TOPRIGHT", G("UIParent"), "TOPRIGHT", -200, -200) end
  return lay
end
function H.Toggle(anchor)
  buildMenu()
  if menu:IsShown() then closeMenu() return false end
  H.Fill()
  if anchor and anchor ~= button then menu:ClearAllPoints() menu:SetPoint("TOPRIGHT", anchor, "BOTTOMLEFT", 8, 4) end
  menu:Show()
  return true
end
function H.IsOpen() return menu and menu:IsShown() or false end

-- ------------------------------------------------------------------ pastille
local function onDragUpdate()
  local mm = minimap()
  local cur = G("GetCursorPosition")
  if not (mm and cur) then return end
  local px, py = cur()
  local sc = mm:GetEffectiveScale() or 1
  local l, b, w, h = mm:GetLeft(), mm:GetBottom(), mm:GetWidth(), mm:GetHeight()
  if not (l and b) then return end
  H.db.angle = floor(H.AngleOf(l + w / 2, b + h / 2, px / sc, py / sc) + 0.5)
  H.Place()
end
function H.CreateButton()
  if button then return button end
  local mm = minimap()
  if not (mm and G("CreateFrame")) then return nil end
  button = G("CreateFrame")("Button", "TreeAIMinimapButton", mm)
  button:SetSize(31, 31)
  button:SetFrameStrata("MEDIUM")
  if button.SetFrameLevel and mm.GetFrameLevel then button:SetFrameLevel((mm:GetFrameLevel() or 1) + 8) end
  button:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  button:RegisterForDrag("LeftButton")
  local bg = button:CreateTexture(nil, "BACKGROUND")
  bg:SetSize(20, 20)
  bg:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
  bg:SetPoint("CENTER", button, "CENTER", 0, 0)
  local icon = button:CreateTexture(nil, "ARTWORK")
  icon:SetSize(18, 18)
  icon:SetTexture(H.ICON)
  icon:SetPoint("CENTER", button, "CENTER", 0, 1)
  if icon.SetTexCoord then icon:SetTexCoord(0.07, 0.93, 0.07, 0.93) end
  button.icon = icon
  local border = button:CreateTexture(nil, "OVERLAY")
  border:SetSize(53, 53)
  border:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
  border:SetPoint("TOPLEFT", button, "TOPLEFT", 0, 0)
  if button.SetHighlightTexture then pcall(button.SetHighlightTexture, button, "Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight") end
  button:SetScript("OnClick", function(self, btn)
    hideTip()
    if btn == "RightButton" then
      closeMenu()
      T:UserAction(function() if T.OpenOptions then T:OpenOptions() end end)
    else
      H.Toggle()
    end
  end)
  button:SetScript("OnEnter", function(self) if not H.IsOpen() then showTip(self) end end)
  button:SetScript("OnLeave", hideTip)
  button:SetScript("OnDragStart", function(self)
    closeMenu()
    self:SetScript("OnUpdate", onDragUpdate)
  end)
  button:SetScript("OnDragStop", function(self)
    self:SetScript("OnUpdate", nil)
    H.Place()
  end)
  H.Place()
  return button
end
function H.Button() return button end

local compartment = false
local function registerCompartment()
  if compartment or not cfg("compartment") then return end
  local acf = G("AddonCompartmentFrame")
  if type(acf) == "table" and type(acf.RegisterAddon) == "function" then
    compartment = pcall(acf.RegisterAddon, acf, {
      text = "TreeAI", icon = H.ICON, notCheckable = true, registerForAnyClick = true,
      func = function(_, input, owner)
        local btn = type(input) == "table" and input.buttonName or input
        if btn == "RightButton" then T:UserAction(function() if T.OpenOptions then T:OpenOptions() end end)
        else H.Toggle(type(owner) == "table" and owner.GetObjectType and owner or nil) end
      end,
      funcOnEnter = function(f) if type(f) == "table" and f.GetObjectType then showTip(f) end end,
      funcOnLeave = hideTip,
    }) and true or false
  end
end

function H:OnEnable()
  if cfg("button") then
    H.CreateButton()
    if button then button:Show() end
  elseif button then button:Hide() end
  registerCompartment()
end
function H:OnDisable()
  if button then button:Hide() end
  closeMenu()
end
function H:OnOptionChanged()
  if cfg("button") then
    H.CreateButton()
    if button then button:Show(); H.Place() end
  elseif button then button:Hide() end
  registerCompartment()
end
T:RegisterSlash("menu", function() H.Toggle() end, "menu de TreeAI (comme la pastille de la minimap)")
