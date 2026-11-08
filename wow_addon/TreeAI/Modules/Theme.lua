-- Modules/Theme.lua — one look for every TreeAI frame (loaded right after UI.lua).
-- TreeAI.Theme: colour tokens, fonts S/M/L, backdrop/skin, widget factories, tooltip helper.
-- Themes: 1 « Sombre » (flat dark + gold), 2 « Clair » (flat slate, brighter), 3 « Blizzard » (tooltip look).
-- Only ever skins TreeAI's OWN frames: unnamed or « TreeAI… » named, never protected / forbidden ones,
-- never a Blizzard frame (never re-parented, no field written on foreign frames: state lives in weak tables).
local T = TreeAI
if not T then return end
local G = _G

local Th = {}
T.Theme = Th

local THEMES = { "Sombre", "Clair", "Blizzard" }
Th.NAMES = THEMES
local WHITE = "Interface\\Buttons\\WHITE8X8"
local BAR_BLIZ = "Interface\\TargetingFrame\\UI-StatusBar"

-- colour tokens ------------------------------------------------------------------------------------
local COMMON = {
  gold = { 1, 0.82, 0.2, 1 }, prio1 = { 0.6, 0.8, 1, 1 }, prio2 = { 1, 0.82, 0.2, 1 }, prio3 = { 1, 0.3, 0.25, 1 },
  good = { 0.3, 0.9, 0.4, 1 }, bad = { 1, 0.3, 0.25, 1 }, warn = { 1, 0.6, 0.15, 1 },
}
local PALETTES = {
  { -- Sombre
    bg = { 0.05, 0.06, 0.08, 0.92 }, panel = { 0.09, 0.10, 0.13, 0.95 }, hud = { 0, 0, 0, 0.45 },
    barBg = { 0, 0, 0, 0.55 }, border = { 0.78, 0.62, 0.28, 0.9 }, borderSoft = { 0.78, 0.62, 0.28, 0.35 },
    text = { 0.92, 0.92, 0.92, 1 }, muted = { 0.6, 0.6, 0.62, 1 }, hover = { 1, 1, 1, 0.08 },
    button = { 0.14, 0.15, 0.19, 0.95 }, selected = { 0.78, 0.62, 0.28, 0.30 }, flat = true,
  },
  { -- Clair
    bg = { 0.20, 0.22, 0.26, 0.88 }, panel = { 0.26, 0.28, 0.32, 0.92 }, hud = { 0.15, 0.16, 0.19, 0.55 },
    barBg = { 0.1, 0.1, 0.12, 0.6 }, border = { 0.95, 0.85, 0.55, 0.95 }, borderSoft = { 0.95, 0.85, 0.55, 0.4 },
    text = { 1, 1, 1, 1 }, muted = { 0.78, 0.78, 0.8, 1 }, hover = { 1, 1, 1, 0.12 },
    button = { 0.32, 0.34, 0.39, 0.95 }, selected = { 0.95, 0.85, 0.55, 0.30 }, flat = true,
  },
  { -- Blizzard
    bg = { 0, 0, 0, 0.85 }, panel = { 0.05, 0.05, 0.05, 0.9 }, hud = { 0, 0, 0, 0.4 },
    barBg = { 0, 0, 0, 0.5 }, border = { 0.8, 0.8, 0.8, 1 }, borderSoft = { 0.6, 0.6, 0.6, 0.6 },
    text = { 1, 1, 1, 1 }, muted = { 0.65, 0.65, 0.65, 1 }, hover = { 1, 0.82, 0.2, 0.15 },
    button = { 0.12, 0.08, 0.04, 0.9 }, selected = { 1, 0.82, 0.2, 0.25 }, flat = false,
  },
}
for _, p in ipairs(PALETTES) do for k, v in pairs(COMMON) do if p[k] == nil then p[k] = v end end end

local CLASS = {  -- fallback when RAID_CLASS_COLORS is absent
  HUNTER = { 0.67, 0.83, 0.45 }, WARRIOR = { 0.78, 0.61, 0.43 }, MAGE = { 0.41, 0.8, 0.94 },
  ROGUE = { 1, 0.96, 0.41 }, PRIEST = { 1, 1, 1 }, WARLOCK = { 0.58, 0.51, 0.79 }, PALADIN = { 0.96, 0.55, 0.73 },
  DRUID = { 1, 0.49, 0.04 }, SHAMAN = { 0, 0.44, 0.87 },
}

-- skin kinds: which background token, border or not -------------------------------------------------
local KINDS = {
  window = { bg = "bg", border = "border", glass = true },   -- verre sombre arrondi (UI2)
  panel = { bg = "panel", border = "border" },
  hud = { bg = "hud", border = "borderSoft" },
  bar = { bg = "barBg", border = "borderSoft", bar = true },
  badge = { bg = "bg", border = "border" },
  button = { bg = "button", border = "borderSoft" },
  row = { bg = false, border = false, hover = true },
  alert = { bg = "bg", border = "prio3" },
  tooltip = { bg = "bg", border = "border" },
}
Th.KINDS = KINDS

local skins = setmetatable({}, { __mode = "k" })   -- frame -> { kind, opts, tex }
local fonts = setmetatable({}, { __mode = "k" })   -- fontstring -> size letter
local SIZES = { S = { "GameFontHighlightSmall", 10 }, M = { "GameFontHighlight", 12 }, L = { "GameFontNormalLarge", 16 } }

local function try(fn, ...) if type(fn) == "function" then return pcall(fn, ...) end return false end
local function meth(o, name, ...)
  local f = o and o[name]
  if type(f) ~= "function" then return nil end
  local ok, a, b, c, d = pcall(f, o, ...)
  if ok then return a, b, c, d end
end

-- settings (module db once bound, defaults before) ---------------------------------------------------
local M = T:RegisterModule("Theme", {
  title = "Thème",
  description = "Une seule apparence propre pour tous les cadres TreeAI (jamais ceux de Blizzard).",
  defaults = { theme = 1, scale = 1, skin = true },
  options = {
    { key = "theme", type = "choice", label = "Thème", values = THEMES, section = "Apparence" },
    { key = "scale", type = "range", label = "Échelle du texte et des widgets", min = 0.8, max = 1.4, step = 0.05 },
    { key = "skin", type = "toggle", label = "Appliquer le thème aux cadres TreeAI (sinon : look d'origine)" },
  },
})
Th.module = M

function Th:Index()
  local i = tonumber(M and M.db and M.db.theme) or 1
  if i < 1 or i > #PALETTES then i = 1 end
  return math.floor(i)
end
function Th:Name() return THEMES[self:Index()] end
function Th:Scale()
  local s = tonumber(M and M.db and M.db.scale) or 1
  if s < 0.5 then s = 0.5 elseif s > 2 then s = 2 end
  return s
end
function Th:Enabled() return not (M and M.db and (M.db.skin == false or M.db.enabled == false)) end
function Th:Palette() return PALETTES[self:Index()] end
function Th:IsBlizzard() return self:Index() == 3 end

-- colours ---------------------------------------------------------------------------------------------
function Th:Color(token)
  local c = self:Palette()[token] or COMMON[token] or { 1, 1, 1, 1 }
  return c[1], c[2], c[3], c[4] or 1
end
function Th:Hex(token)
  local r, g, b = self:Color(token)
  return ("ff%02x%02x%02x"):format(math.floor(r * 255 + 0.5), math.floor(g * 255 + 0.5), math.floor(b * 255 + 0.5))
end
function Th:Wrap(token, text) return "|c" .. self:Hex(token) .. tostring(text or "") .. "|r" end
function Th:PrioColor(prio)
  prio = tonumber(prio) or 1
  return self:Color(prio >= 3 and "prio3" or prio == 2 and "prio2" or "prio1")
end
function Th:ClassColor(class)
  local rc = G.RAID_CLASS_COLORS
  local c = type(rc) == "table" and class and rc[class]
  if type(c) == "table" and c.r then return c.r, c.g, c.b, 1 end
  c = CLASS[class or ""]
  if c then return c[1], c[2], c[3], 1 end
  return self:Color("text")
end

-- MENU : un genre de ligne = une icône + une couleur, partout (panneau, menu, alertes) ---------------------
-- Couleurs : rouge = urgent SEULEMENT ; or = argent ; bleu clair = trajet ; orange = mise en garde ;
-- les quêtes gardent la couleur de difficulté du jeu (gris / vert / jaune / orange / rouge, dans leur texte).
local ICON = "Interface\\Icons\\"
Th.LINE = {
  danger = { icon = "Interface\\DialogFrame\\UI-Dialog-Icon-AlertNew", color = { 1, 0.3, 0.25 }, label = "Danger" },
  warn = { icon = "Interface\\DialogFrame\\UI-Dialog-Icon-AlertOther", color = { 1, 0.6, 0.15 }, label = "Mise en garde" },
  turnin = { icon = "Interface\\GossipFrame\\ActiveQuestIcon", label = "Quête à rendre" },
  quest = { icon = "Interface\\GossipFrame\\AvailableQuestIcon", label = "Quête" },
  kill = { icon = ICON .. "Ability_DualWield", label = "Objectif : tuer" },
  collect = { icon = ICON .. "INV_Misc_Bag_09", label = "Objectif : ramasser" },
  use = { icon = ICON .. "INV_Misc_Rune_01", label = "Objectif : utiliser" },
  travel = { icon = ICON .. "Ability_Rogue_Sprint", color = { 0.55, 0.8, 1 }, label = "Trajet" },
  flight = { icon = "Interface\\Minimap\\Tracking\\FlightMaster", color = { 0.55, 0.8, 1 }, label = "Vol" },
  boat = { icon = ICON .. "INV_Misc_Spyglass_03", color = { 0.55, 0.8, 1 }, label = "Bateau / zeppelin" },
  gear = { icon = ICON .. "INV_Chest_Chain", label = "Équipement" },
  pet = { icon = ICON .. "Ability_Hunter_BeastCall", label = "Familier" },
  group = { icon = ICON .. "Spell_Holy_PrayerOfHealing", label = "Équipe" },
  money = { icon = ICON .. "INV_Misc_Coin_01", color = { 1, 0.82, 0.2 }, label = "Argent / HV" },
  consumable = { icon = ICON .. "INV_Misc_Food_15", label = "Consommables" },
  info = { icon = ICON .. "INV_Misc_Note_01", label = "Info" },
}
--- Texte normal selon la priorité (l'or est réservé à l'argent, le rouge à l'urgent).
Th.LINE_PRIO = { { 0.80, 0.80, 0.80 }, { 1, 1, 1 }, { 1, 0.3, 0.25 } }
function Th:LineStyle(kind, prio)
  local k = self.LINE[kind or "info"] or self.LINE.info
  local c = k.color or self.LINE_PRIO[math.min(3, math.max(1, tonumber(prio) or 1))]
  if (tonumber(prio) or 1) >= 3 then c = self.LINE.danger.color end
  return k.icon, c[1], c[2], c[3]
end
--- Couleur de difficulté d'une quête, comme le jeu (rouge ≥ +5, orange ≥ +3, jaune ±2, vert, gris).
function Th:QuestDiffColor(qlevel, plevel)
  qlevel, plevel = tonumber(qlevel), tonumber(plevel)
  if not qlevel or qlevel <= 0 or not plevel then return 1, 0.82, 0, "jaune" end
  local d = qlevel - plevel
  if d >= 5 then return 1, 0.1, 0.1, "rouge" end
  if d >= 3 then return 1, 0.5, 0.25, "orange" end
  if d >= -2 then return 1, 1, 0, "jaune" end
  local grey = plevel <= 5 and 0 or plevel <= 39 and plevel - math.floor(plevel / 10) - 5 or plevel - math.floor(plevel / 5) - 1
  if qlevel > grey then return 0.25, 0.75, 0.25, "vert" end
  return 0.5, 0.5, 0.5, "gris"
end

-- fonts -------------------------------------------------------------------------------------------------
function Th:FontObject(size) return (SIZES[size] or SIZES.M)[1] end
local function applyFont(fs, size)
  local spec = SIZES[size] or SIZES.M
  local obj = G[spec[1]]
  if obj then meth(fs, "SetFontObject", obj) end
  local file, _, flags
  if type(obj) == "table" then file, _, flags = meth(obj, "GetFont") end
  if not file then file, _, flags = meth(fs, "GetFont") end
  if file then meth(fs, "SetFont", file, math.floor(spec[2] * Th:Scale() + 0.5), flags or "") end
end
function Th:Font(fs, size, token)
  if type(fs) ~= "table" then return fs end
  size = SIZES[size] and size or "M"
  fonts[fs] = size
  applyFont(fs, size)
  if token then meth(fs, "SetTextColor", self:Color(token)) end
  return fs
end
function Th:Text(parent, size, token, layer)
  local fs = meth(parent, "CreateFontString", nil, layer or "OVERLAY", self:FontObject(size))
  if fs then self:Font(fs, size, token) end
  return fs
end

-- what may be skinned ------------------------------------------------------------------------------------
function Th:CanSkin(frame)
  if type(frame) ~= "table" or type(frame.CreateTexture) ~= "function" then return false end
  if meth(frame, "IsForbidden") then return false end
  if meth(frame, "IsProtected") then return false end
  local name = meth(frame, "GetName")
  if type(name) == "string" and name ~= "" and not name:find("^TreeAI") then return false end
  return true
end

local BD_FLAT = { bgFile = WHITE, edgeFile = WHITE, edgeSize = 1, insets = { left = 1, right = 1, top = 1, bottom = 1 } }
local BD_BLIZ = { bgFile = "Interface\\Tooltips\\UI-Tooltip-Background", edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
  tile = true, tileSize = 16, edgeSize = 12, insets = { left = 3, right = 3, top = 3, bottom = 3 } }
Th.BACKDROP_FLAT, Th.BACKDROP_BLIZZARD = BD_FLAT, BD_BLIZ

local function setColorTex(tex, r, g, b, a)
  if not tex then return end
  if type(tex.SetColorTexture) == "function" then meth(tex, "SetColorTexture", r, g, b, a)
  else meth(tex, "SetTexture", WHITE); meth(tex, "SetVertexColor", r, g, b, a) end
end

local function textures(frame, s, spec)
  local tex = s.tex
  if not tex then
    tex = {}
    s.tex = tex
    tex.bg = s.opts.bg or meth(frame, "CreateTexture", nil, "BACKGROUND", nil, -8)
    if tex.bg and not s.opts.bg then meth(tex.bg, "SetAllPoints", frame) end
    tex.edge = {}
    for i = 1, 4 do tex.edge[i] = meth(frame, "CreateTexture", nil, "BORDER") end
    local e = tex.edge
    if e[1] then meth(e[1], "SetPoint", "TOPLEFT", frame, "TOPLEFT"); meth(e[1], "SetPoint", "TOPRIGHT", frame, "TOPRIGHT"); meth(e[1], "SetHeight", 1) end
    if e[2] then meth(e[2], "SetPoint", "BOTTOMLEFT", frame, "BOTTOMLEFT"); meth(e[2], "SetPoint", "BOTTOMRIGHT", frame, "BOTTOMRIGHT"); meth(e[2], "SetHeight", 1) end
    if e[3] then meth(e[3], "SetPoint", "TOPLEFT", frame, "TOPLEFT"); meth(e[3], "SetPoint", "BOTTOMLEFT", frame, "BOTTOMLEFT"); meth(e[3], "SetWidth", 1) end
    if e[4] then meth(e[4], "SetPoint", "TOPRIGHT", frame, "TOPRIGHT"); meth(e[4], "SetPoint", "BOTTOMRIGHT", frame, "BOTTOMRIGHT"); meth(e[4], "SetWidth", 1) end
  end
  if tex.bg then
    if spec.bg then setColorTex(tex.bg, Th:Color(spec.bg)); meth(tex.bg, "Show") elseif not s.opts.bg then meth(tex.bg, "Hide") end
  end
  for i = 1, 4 do
    local t = tex.edge[i]
    if t then
      if spec.border then setColorTex(t, Th:Color(spec.border)); meth(t, "Show") else meth(t, "Hide") end
    end
  end
end

local function hover(frame, s)
  if s.hl or type(frame.CreateTexture) ~= "function" then return end
  local hl = meth(frame, "CreateTexture", nil, "HIGHLIGHT")
  if not hl then return end
  meth(hl, "SetAllPoints", frame)
  s.hl = hl
end

function Th:_Apply(frame)
  local s = skins[frame]
  if not s then return end
  local spec = KINDS[s.kind] or KINDS.panel
  if s.opts.border == false then spec = { bg = spec.bg, bar = spec.bar, hover = spec.hover } end
  if s.opts.noBg then spec = { border = spec.border, bar = spec.bar, hover = spec.hover } end
  if type(frame.SetBackdrop) == "function" and not s.opts.textures then
    local bd = self:IsBlizzard() and BD_BLIZ or (spec.glass and Th.BACKDROP_GLASS) or BD_FLAT
    if spec.bg or spec.border then
      meth(frame, "SetBackdrop", bd)
      if spec.bg then meth(frame, "SetBackdropColor", self:Color(spec.bg)) else meth(frame, "SetBackdropColor", 0, 0, 0, 0) end
      if spec.border then meth(frame, "SetBackdropBorderColor", self:Color(spec.border))
      else meth(frame, "SetBackdropBorderColor", 0, 0, 0, 0) end
    end
  else
    textures(frame, s, spec)
  end
  if spec.bar and type(frame.SetStatusBarTexture) == "function" then
    local r, g, b, a = meth(frame, "GetStatusBarColor")
    meth(frame, "SetStatusBarTexture", self:IsBlizzard() and BAR_BLIZ or (s.opts.barTexture or BAR_BLIZ))
    if r then meth(frame, "SetStatusBarColor", r, g, b, a) end
  end
  if spec.hover then
    hover(frame, s)
    if s.hl then setColorTex(s.hl, self:Color("hover")) end
  end
  s.theme = self:Index()
end

-- Skin(frame, kind[, opts]) -> true when applied. kinds: window, panel, hud, bar, badge, button, row, alert, tooltip.
-- opts: bg = existing background texture to recolour, noBg, border=false, textures (never SetBackdrop).
function Th:Skin(frame, kind, opts)
  if not self:Enabled() or not self:CanSkin(frame) then return false end
  local s = skins[frame]
  if not s then s = { opts = {} }; skins[frame] = s end
  s.kind = KINDS[kind] and kind or "panel"
  if type(opts) == "table" then s.opts = opts end
  local ok, err = pcall(self._Apply, self, frame)
  if not ok then if T.Debug then T:Debug("Theme:Skin", tostring(err)) end return false end
  return true
end
function Th:SkinOf(frame) local s = skins[frame] return s and s.kind, s and s.theme end
function Th:IsSkinned(frame) return skins[frame] ~= nil end
function Th:Count()
  local n = 0
  for _ in pairs(skins) do n = n + 1 end
  return n
end

-- re-apply after a theme / scale change
function Th:Refresh()
  if self:Enabled() then
    for frame in pairs(skins) do pcall(self._Apply, self, frame) end
  end
  for fs, size in pairs(fonts) do pcall(applyFont, fs, size) end
  for b in pairs(self._buttons) do pcall(self._PaintButton, self, b) end
  if T.Fire then pcall(T.Fire, T, "THEME_CHANGED", self:Name()) end
end

-- tooltip helper (GameTooltip is only owned and filled, never skinned) ------------------------------------
function Th:ShowTooltip(owner, title, lines, anchor)
  local tt = G.GameTooltip
  if not (tt and owner) then return end
  pcall(tt.SetOwner, tt, owner, anchor or "ANCHOR_RIGHT")
  if title then pcall(tt.AddLine, tt, tostring(title), self:Color("gold")) end
  if type(lines) == "function" then local ok, v = pcall(lines) lines = ok and v or nil end
  if type(lines) == "string" then
    local t = {}
    for l in lines:gmatch("[^\n]+") do t[#t + 1] = l end
    lines = t
  end
  if type(lines) == "table" then
    for _, l in ipairs(lines) do
      if type(l) == "table" then pcall(tt.AddLine, tt, tostring(l[1] or ""), self:Color(l[2] or "text"))
      else pcall(tt.AddLine, tt, tostring(l), 1, 1, 1, true) end
    end
  end
  pcall(tt.Show, tt)
end
function Th:HideTooltip() local tt = G.GameTooltip if tt then pcall(tt.Hide, tt) end end
-- Tooltip(frame, title, lines|fn): attaches OnEnter/OnLeave without replacing existing handlers.
function Th:Tooltip(frame, title, lines, anchor)
  if type(frame) ~= "table" or type(frame.SetScript) ~= "function" or not self:CanSkin(frame) then return frame end
  local enter = function(f) Th:ShowTooltip(f, title, lines, anchor) end
  local leave = function() Th:HideTooltip() end
  if meth(frame, "GetScript", "OnEnter") and type(frame.HookScript) == "function" then
    meth(frame, "HookScript", "OnEnter", enter); meth(frame, "HookScript", "OnLeave", leave)
  else
    meth(frame, "SetScript", "OnEnter", enter); meth(frame, "SetScript", "OnLeave", leave)
  end
  return frame
end

-- widget factories -----------------------------------------------------------------------------------------
Th._buttons = setmetatable({}, { __mode = "k" })
local function newFrame(kind, parent, template)
  if type(G.CreateFrame) ~= "function" then return nil end
  local ok, f = pcall(G.CreateFrame, kind, nil, parent or G.UIParent, template)
  if ok then return f end
end
local function backdropTemplate() return G.BackdropTemplateMixin and "BackdropTemplate" or nil end

function Th:Frame(parent, kind, w, h)
  local f = newFrame("Frame", parent, backdropTemplate())
  if not f then return nil end
  if w then meth(f, "SetSize", w, h or w) end
  self:Skin(f, kind or "panel")
  return f
end

function Th:_PaintButton(b)
  local st = self._buttons[b]
  if not st then return end
  local spec = st.selected and "selected" or "button"
  self:Skin(b, "button", { textures = not b.SetBackdrop and true or nil })
  local s = skins[b]
  if st.selected and s then
    if s.tex and s.tex.bg then setColorTex(s.tex.bg, self:Color(spec))
    elseif b.SetBackdropColor then meth(b, "SetBackdropColor", self:Color(spec)) end
  end
  if b.label then meth(b.label, "SetTextColor", self:Color(st.selected and "gold" or "text")) end
end

function Th:Button(parent, text, w, h, onClick, tip)
  local b = newFrame("Button", parent, backdropTemplate())
  if not b then return nil end
  meth(b, "SetSize", w or 100, h or 20)
  b.label = self:Text(b, "S", "text")
  if b.label then meth(b.label, "SetPoint", "CENTER", b, "CENTER", 0, 0); meth(b.label, "SetText", text or "") end
  local hl = meth(b, "CreateTexture", nil, "HIGHLIGHT")
  if hl then meth(hl, "SetAllPoints", b); setColorTex(hl, self:Color("hover")) end
  function b.SetLabel(self2, t) if self2.label then meth(self2.label, "SetText", t) end end
  if onClick then meth(b, "SetScript", "OnClick", onClick) end
  self._buttons[b] = { selected = false }
  self:_PaintButton(b)
  if tip then self:Tooltip(b, text, tip) end
  return b
end

function Th:IconButton(parent, icon, size, onClick, title, tip)
  local b = newFrame("Button", parent, backdropTemplate())
  if not b then return nil end
  size = size or 20
  meth(b, "SetSize", size, size)
  b.icon = meth(b, "CreateTexture", nil, "ARTWORK")
  if b.icon then
    meth(b.icon, "SetPoint", "TOPLEFT", b, "TOPLEFT", 1, -1); meth(b.icon, "SetPoint", "BOTTOMRIGHT", b, "BOTTOMRIGHT", -1, 1)
    meth(b.icon, "SetTexture", icon); meth(b.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
  end
  local hl = meth(b, "CreateTexture", nil, "HIGHLIGHT")
  if hl then meth(hl, "SetAllPoints", b); setColorTex(hl, self:Color("hover")) end
  if onClick then meth(b, "SetScript", "OnClick", onClick) end
  self:Skin(b, "button", { noBg = true })
  if title or tip then self:Tooltip(b, title, tip) end
  return b
end

-- ProgressBar: bar:SetProgress(value, max[, prio|{r,g,b}][, text])
function Th:ProgressBar(parent, w, h)
  local bar = newFrame("StatusBar", parent)
  if not bar then return nil end
  meth(bar, "SetSize", w or 160, h or 12)
  meth(bar, "SetStatusBarTexture", BAR_BLIZ)
  meth(bar, "SetMinMaxValues", 0, 1)
  meth(bar, "SetValue", 0)
  bar.text = self:Text(bar, "S", "text")
  if bar.text then meth(bar.text, "SetPoint", "CENTER", bar, "CENTER", 0, 0) end
  function bar.SetProgress(b, v, max, color, text)
    max = tonumber(max) or 1
    if max <= 0 then max = 1 end
    meth(b, "SetMinMaxValues", 0, max)
    meth(b, "SetValue", math.max(0, math.min(max, tonumber(v) or 0)))
    if type(color) == "table" then meth(b, "SetStatusBarColor", color[1], color[2], color[3])
    elseif color then meth(b, "SetStatusBarColor", Th:PrioColor(color)) end
    if b.text then meth(b.text, "SetText", text or "") end
  end
  self:Skin(bar, "bar")
  return bar
end

function Th:Header(parent, text, size)
  local fs = self:Text(parent, size or "M", "gold")
  if fs then meth(fs, "SetText", text or "") end
  return fs
end

function Th:Separator(parent, w)
  local t = meth(parent, "CreateTexture", nil, "ARTWORK")
  if not t then return nil end
  meth(t, "SetHeight", 1)
  if w then meth(t, "SetWidth", w) end
  setColorTex(t, self:Color("borderSoft"))
  return t
end

function Th:Checkbox(parent, label, get, set)
  local c = newFrame("CheckButton", parent, "UICheckButtonTemplate") or newFrame("CheckButton", parent)
  if not c then return nil end
  meth(c, "SetSize", 22, 22)
  c.label = self:Text(c, "S", "text")
  if c.label then meth(c.label, "SetPoint", "LEFT", c, "RIGHT", 2, 0); meth(c.label, "SetText", label or "") end
  function c.Refresh(self2) if get then local ok, v = pcall(get) if ok then meth(self2, "SetChecked", v and true or false) end end end
  meth(c, "SetScript", "OnClick", function(self2)
    local v = meth(self2, "GetChecked") and true or false
    if set then pcall(set, v) end
  end)
  c:Refresh()
  return c
end

function Th:Slider(parent, label, min, max, step, get, set, w)
  local s = newFrame("Slider", parent, backdropTemplate())
  if not s then return nil end
  meth(s, "SetOrientation", "HORIZONTAL")
  meth(s, "SetSize", w or 160, 12)
  meth(s, "SetMinMaxValues", min or 0, max or 1)
  meth(s, "SetValueStep", step or 0.1)
  meth(s, "SetObeyStepOnDrag", true)
  local thumb = meth(s, "CreateTexture", nil, "OVERLAY")
  if thumb then meth(thumb, "SetSize", 8, 14); setColorTex(thumb, self:Color("gold")); meth(s, "SetThumbTexture", thumb) end
  s.label = self:Text(s, "S", "muted")
  if s.label then meth(s.label, "SetPoint", "BOTTOMLEFT", s, "TOPLEFT", 0, 2) end
  local function paint(v)
    if s.label then meth(s.label, "SetText", ("%s : %s"):format(label or "", tostring(math.floor((v or 0) * 100 + 0.5) / 100))) end
  end
  if get then local ok, v = pcall(get) if ok and v then meth(s, "SetValue", v); paint(v) end end
  meth(s, "SetScript", "OnValueChanged", function(_, v) paint(v) if set then pcall(set, v) end end)
  self:Skin(s, "bar")
  return s
end

function Th:Tab(parent, text, w, onClick)
  local b = self:Button(parent, text, w or 80, 20, onClick)
  if not b then return nil end
  function b.SetSelected(self2, v)
    local st = Th._buttons[self2]
    if st then st.selected = v and true or false; Th:_PaintButton(self2) end
  end
  return b
end

-- Badge(parent, text, prio): small boxed count / label; badge:SetBadge(text, prio)
function Th:Badge(parent, text, prio)
  local f = newFrame("Frame", parent, backdropTemplate())
  if not f then return nil end
  meth(f, "SetSize", 18, 14)
  if f.EnableMouse then meth(f, "EnableMouse", false) end
  f.text = self:Text(f, "S", "text")
  if f.text then meth(f.text, "SetPoint", "CENTER", f, "CENTER", 0, 0) end
  function f.SetBadge(self2, t, p)
    if self2.text then
      meth(self2.text, "SetText", tostring(t or ""))
      meth(self2.text, "SetTextColor", Th:PrioColor(p))
      local wd = meth(self2.text, "GetStringWidth")
      meth(self2, "SetWidth", math.max(16, (tonumber(wd) or 10) + 8))
    end
  end
  self:Skin(f, "badge")
  f:SetBadge(text, prio)
  return f
end

-- fenêtres (UI2) : en-tête commun (icône + titre + réduire + fermer), verre sombre arrondi, survol,
-- fondu à l'ouverture (un seul OnUpdate partagé, éteint au repos), redimensionnable avec bornes,
-- état mémorisé (settings.windows[id] = { w, h, collapsed }), Échap ferme (UISpecialFrames, nos cadres seulement).
local BD_GLASS = { bgFile = WHITE, edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", tile = false, edgeSize = 12,
  insets = { left = 3, right = 3, top = 3, bottom = 3 } }
Th.BACKDROP_GLASS = BD_GLASS
KINDS.glass = { bg = "bg", border = "borderSoft", glass = true }   -- variante au liseré doux
Th.HEADER_H = 22
Th._windows = setmetatable({}, { __mode = "k" })   -- frame -> { opts, header, grip, collapsed }
Th._escNames = {}
local ICON_CLOSE = "Interface\\Buttons\\UI-StopButton"
local ICON_MIN, ICON_MAX = "Interface\\Buttons\\UI-MinusButton-Up", "Interface\\Buttons\\UI-PlusButton-Up"
local GRIP = "Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up"

local function winState(id)
  local s = T.settings
  if not (s and id) then return nil end
  if type(s.windows) ~= "table" then s.windows = {} end
  local w = s.windows[id]
  if type(w) ~= "table" then w = {}; s.windows[id] = w end
  return w
end
function Th:WindowState(id) return winState(id) end

-- animations : alpha avec ease-out, un seul OnUpdate partagé (coupé dès que plus rien ne bouge)
local anims, driver = {}, nil
local function stepAnims(_, elapsed)
  local any = false
  for f, a in pairs(anims) do
    a.t = a.t + (elapsed or 0.016)
    local k = math.min(1, a.t / a.dur)
    local e = 1 - (1 - k) * (1 - k)
    meth(f, "SetAlpha", a.from + (a.to - a.from) * e)
    if k >= 1 then
      anims[f] = nil
      if a.done then pcall(a.done, f) end
    else any = true end
  end
  if not any and driver then meth(driver, "SetScript", "OnUpdate", nil) end
end
Th._StepAnims = stepAnims
function Th:Animate(frame, from, to, dur, done)
  if type(frame) ~= "table" then return end
  if not driver and type(G.CreateFrame) == "function" then
    local ok, d = pcall(G.CreateFrame, "Frame", nil, G.UIParent)
    if ok then driver = d end
  end
  meth(frame, "SetAlpha", from)
  anims[frame] = { from = from, to = to, dur = math.max(0.01, dur or 0.15), t = 0, done = done }
  if driver then meth(driver, "SetScript", "OnUpdate", stepAnims) else stepAnims(nil, 1) end
end
function Th:IsAnimating(frame) return anims[frame] ~= nil end
function Th:FadeIn(frame, dur, alpha) self:Animate(frame, 0, alpha or 1, dur or 0.15) end
--- Ferme en fondu (puis Hide). En combat : Hide direct.
function Th:FadeOut(frame, dur)
  if type(frame) ~= "table" then return end
  if not meth(frame, "IsShown") or (T.InCombat and T:InCombat()) then meth(frame, "Hide"); return end
  local a = meth(frame, "GetAlpha") or 1
  self:Animate(frame, a, 0, dur or 0.12, function(f) meth(f, "Hide"); meth(f, "SetAlpha", a) end)
end

-- Échap : nom TreeAI… dans UISpecialFrames ; cadre sans nom = petit relais nommé caché avec lui
local function registerEsc(frame, id)
  local list = rawget(G, "UISpecialFrames")
  if type(list) ~= "table" then return nil end
  local name = meth(frame, "GetName")
  if not (type(name) == "string" and name:find("^TreeAI")) then
    name = "TreeAIEsc_" .. (tostring(id or "w"):gsub("[^%w]", ""))
    if not rawget(G, name) then
      if type(G.CreateFrame) ~= "function" then return nil end
      local ok, proxy = pcall(G.CreateFrame, "Frame", name, G.UIParent)
      if not ok or not proxy then return nil end
      meth(proxy, "SetSize", 1, 1)
      meth(proxy, "Hide")
      meth(proxy, "SetScript", "OnHide", function() if meth(frame, "IsShown") then meth(frame, "Hide") end end)
      if type(frame.HookScript) == "function" then
        meth(frame, "HookScript", "OnShow", function() meth(proxy, "Show") end)
        meth(frame, "HookScript", "OnHide", function() meth(proxy, "Hide") end)
      end
      if meth(frame, "IsShown") then meth(proxy, "Show") end
    end
  end
  if not Th._escNames[name] then
    Th._escNames[name] = true
    local seen = false
    for _, n in ipairs(list) do if n == name then seen = true end end
    if not seen then list[#list + 1] = name end
  end
  return name
end
Th._RegisterEsc = registerEsc

local function children(frame)
  if type(frame.GetChildren) ~= "function" then return {} end
  local ok, r = pcall(function() return { frame:GetChildren() } end)
  return ok and r or {}
end

--- Réduit à l'en-tête / déplie. Mémorisé si opts.id.
function Th:SetCollapsed(frame, v)
  local w = self._windows[frame]
  if not (w and w.header) then return end
  v = v and true or false
  if v == w.collapsed then return end
  if v then
    w.fullH = meth(frame, "GetHeight") or w.fullH
    w.hidden = {}
    for _, c in ipairs(children(frame)) do
      if c ~= w.header and meth(c, "IsShown") then w.hidden[#w.hidden + 1] = c; meth(c, "Hide") end
    end
    meth(frame, "SetHeight", self.HEADER_H + 6)
  else
    for _, c in ipairs(w.hidden or {}) do meth(c, "Show") end
    w.hidden = nil
    if w.fullH then meth(frame, "SetHeight", w.fullH) end
  end
  w.collapsed = v
  local mb = w.header.min
  if mb and mb.icon then meth(mb.icon, "SetTexture", v and ICON_MAX or ICON_MIN) end
  local st = winState(w.opts.id)
  if st then st.collapsed = v end
  if w.opts.onCollapse then pcall(w.opts.onCollapse, v) end
end
function Th:IsCollapsed(frame) local w = self._windows[frame] return w and w.collapsed or false end

local function buildHeader(frame, w)
  local o = w.opts
  local hd = newFrame("Button", frame)
  if not hd then return nil end
  meth(hd, "SetHeight", Th.HEADER_H)
  meth(hd, "SetPoint", "TOPLEFT", frame, "TOPLEFT", 3, -3)
  meth(hd, "SetPoint", "TOPRIGHT", frame, "TOPRIGHT", -3, -3)
  hd.bg = meth(hd, "CreateTexture", nil, "BACKGROUND")
  if hd.bg then meth(hd.bg, "SetAllPoints", hd); setColorTex(hd.bg, Th:Color("button")) end
  local hl = meth(hd, "CreateTexture", nil, "HIGHLIGHT")
  if hl then meth(hl, "SetAllPoints", hd); setColorTex(hl, Th:Color("hover")) end
  local x = 6
  if o.icon then
    hd.icon = meth(hd, "CreateTexture", nil, "ARTWORK")
    if hd.icon then
      meth(hd.icon, "SetSize", 16, 16); meth(hd.icon, "SetPoint", "LEFT", hd, "LEFT", 4, 0)
      meth(hd.icon, "SetTexture", o.icon); meth(hd.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
      x = 24
    end
  end
  hd.title = Th:Text(hd, "M", "gold")
  if hd.title then
    meth(hd.title, "SetPoint", "LEFT", hd, "LEFT", x, 0)
    meth(hd.title, "SetJustifyH", "LEFT")
    meth(hd.title, "SetWordWrap", false)          -- jamais de débordement : « … » automatique
    meth(hd.title, "SetText", o.title or "")
  end
  local right, rpt = hd, "RIGHT"
  if o.close ~= false then
    hd.close = Th:IconButton(hd, ICON_CLOSE, 16, function()
      if o.onClose then pcall(o.onClose, frame) else Th:FadeOut(frame) end
    end, "Fermer", (o.esc ~= false) and "Échap ferme aussi" or nil)
    if hd.close then meth(hd.close, "SetPoint", "RIGHT", hd, "RIGHT", -3, 0); right, rpt = hd.close, "LEFT" end
  end
  if o.min ~= false then
    hd.min = Th:IconButton(hd, ICON_MIN, 16, function() Th:SetCollapsed(frame, not w.collapsed) end,
      "Réduire / déplier", "Double-clic sur l'en-tête aussi")
    if hd.min then
      if hd.min.icon then meth(hd.min.icon, "SetTexCoord", 0, 1, 0, 1) end
      meth(hd.min, "SetPoint", "RIGHT", right, rpt, -3, 0); right, rpt = hd.min, "LEFT"
    end
  end
  if hd.title then meth(hd.title, "SetPoint", "RIGHT", right, rpt, -4, 0) end
  meth(hd, "RegisterForDrag", "LeftButton")
  meth(hd, "SetScript", "OnDragStart", function()
    if o.locked and o.locked() then return end
    if meth(frame, "IsMovable") then meth(frame, "StartMoving") end
  end)
  meth(hd, "SetScript", "OnDragStop", function()
    meth(frame, "StopMovingOrSizing")
    if o.onMoved then pcall(o.onMoved, frame) end
  end)
  meth(hd, "SetScript", "OnDoubleClick", function() if o.min ~= false then Th:SetCollapsed(frame, not w.collapsed) end end)
  return hd
end

--- Bornes de taille (et jamais plus de 90 % de l'écran). Applique et renvoie la taille.
function Th:ClampSize(frame, fw, fh)
  local w = self._windows[frame]
  local r = (w and w.opts.resize) or {}
  fw = tonumber(fw) or meth(frame, "GetWidth") or 300
  fh = tonumber(fh) or meth(frame, "GetHeight") or 200
  local P = G.UIParent
  local W, H = (P and meth(P, "GetWidth")) or 1920, (P and meth(P, "GetHeight")) or 1080
  local maxW, maxH = math.min(r.maxW or 900, W * 0.9), math.min(r.maxH or 1000, H * 0.9)
  fw = math.floor(math.max(r.minW or 200, math.min(maxW, fw)) + 0.5)
  fh = math.floor(math.max(r.minH or 80, math.min(maxH, fh)) + 0.5)
  if r.width == "only" or (w and w.collapsed) then meth(frame, "SetWidth", fw) else meth(frame, "SetSize", fw, fh) end
  return fw, fh
end

local function buildGrip(frame, w)
  local r = w.opts.resize
  meth(frame, "SetResizable", true)
  if type(frame.SetResizeBounds) == "function" then
    meth(frame, "SetResizeBounds", r.minW or 200, r.minH or 80, r.maxW or 900, r.maxH or 1000)
  else
    meth(frame, "SetMinResize", r.minW or 200, r.minH or 80)
    meth(frame, "SetMaxResize", r.maxW or 900, r.maxH or 1000)
  end
  local g = newFrame("Button", frame)
  if not g then return nil end
  meth(g, "SetSize", 14, 14)
  meth(g, "SetPoint", "BOTTOMRIGHT", frame, "BOTTOMRIGHT", -2, 2)
  meth(g, "SetNormalTexture", GRIP)
  meth(g, "SetHighlightTexture", "Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Highlight")
  meth(g, "SetScript", "OnMouseDown", function()
    if T.InCombat and T:InCombat() then return end
    meth(frame, "StartSizing", "BOTTOMRIGHT")
  end)
  meth(g, "SetScript", "OnMouseUp", function()
    meth(frame, "StopMovingOrSizing")
    local fw, fh = Th:ClampSize(frame)
    local st = winState(w.opts.id)
    if st then st.w, st.h = fw, fh end
    if r.onResize then pcall(r.onResize, fw, fh) end
  end)
  return g
end

--- Décore une fenêtre TreeAI. opts = { id, title, icon, header (défaut true), close, min, esc (défaut true),
--- onClose(frame), onMoved(frame), locked = fn, fade (défaut true), alpha = fn, skin (défaut "glass", false = rien),
--- resize = { minW, minH, maxW, maxH, onResize(w, h), width = "only" } }. Renvoie l'en-tête (ou nil).
function Th:Window(frame, opts)
  if not self:CanSkin(frame) then return nil end
  local w = self._windows[frame]
  if w then return w.header end
  opts = opts or {}
  w = { opts = opts, collapsed = false }
  self._windows[frame] = w
  if opts.skin ~= false then self:Skin(frame, opts.skin or "window") end
  if opts.header ~= false then w.header = buildHeader(frame, w) end
  if opts.resize then
    w.grip = buildGrip(frame, w)
    local st = winState(opts.id)
    if st and tonumber(st.w) and opts.remember ~= false then self:ClampSize(frame, st.w, st.h) end
  end
  if opts.esc ~= false then
    w.esc = registerEsc(frame, opts.id)
    if not w.esc and type(frame.HookScript) == "function" then   -- liste pas encore là : réessayé à l'ouverture
      meth(frame, "HookScript", "OnShow", function(f) if not w.esc then w.esc = registerEsc(f, opts.id) end end)
    end
  end
  if opts.fade ~= false and type(frame.HookScript) == "function" then
    meth(frame, "HookScript", "OnShow", function(f)
      if anims[f] then return end
      local a = 1
      if opts.alpha then local ok, v = pcall(opts.alpha) if ok and tonumber(v) then a = v end end
      Th:Animate(f, 0, a, 0.15)
    end)
  end
  local st = winState(opts.id)
  if st and st.collapsed and w.header then self:SetCollapsed(frame, true) end
  return w.header
end
function Th:IsWindow(frame) return self._windows[frame] ~= nil end
function Th:GetWindow(frame) return self._windows[frame] end

-- module: settings → refresh; skin the core panel + alert box (TreeAI's own frames) -----------------------
local function skinCore()
  local p = T.GetPanel and T:GetPanel()
  if p then
    Th:Skin(p, "window")
    -- panneau : verre sombre, poignée de largeur (300-560), fondu à l'ouverture ; pas d'Échap (c'est un HUD)
    Th:Window(p, { id = "panel", header = false, esc = false,
      alpha = function() local s = T.settings and T.settings.panel return s and s.alpha or 0.9 end,
      resize = { minW = T.PANEL_MIN_WIDTH or 300, maxW = 560, minH = 24, maxH = 2000, width = "only", remember = false,
        onResize = function(w)
          local s = T.settings and T.settings.panel
          if s then s.width = w end
          if T.ApplyPanelSettings then T:ApplyPanelSettings() end
        end } })
  end
  local a = T.GetAlert and T:GetAlert()
  if a then Th:Skin(a, "alert") end
end

if type(T.CreatePanel) == "function" and not Th._wrapped then
  Th._wrapped = true
  local orig = T.CreatePanel
  T.CreatePanel = function(self, ...)
    local r = orig(self, ...)
    pcall(skinCore)
    return r
  end
end
if type(T.ShowAlert) == "function" and not Th._wrappedAlert then
  Th._wrappedAlert = true
  local orig = T.ShowAlert
  T.ShowAlert = function(self, ...)
    local r = orig(self, ...)
    local a = T.GetAlert and T:GetAlert()
    if a and not skins[a] then pcall(Th.Skin, Th, a, "alert") end
    return r
  end
end

function M:OnEnable()
  pcall(skinCore)
  T:After(1, function() pcall(skinCore) end)  -- alert box is built lazily
end
function M:OnOptionChanged() Th:Refresh() end

T:RegisterSlash("theme", function(arg)
  local n = tonumber(arg)
  if not n and type(arg) == "string" then
    for i, name in ipairs(THEMES) do if name:lower() == arg:lower() then n = i end end
  end
  if n and M.db and THEMES[n] then M.db.theme = n; Th:Refresh() end
  T:Print("Thème : " .. Th:Name() .. " (/tai theme sombre|clair|blizzard)")
end, "thème sombre / clair / blizzard")
