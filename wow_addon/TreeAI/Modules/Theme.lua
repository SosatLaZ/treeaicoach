-- Modules/Theme.lua — one look for every TreeAI frame (loaded right after UI.lua).
-- TreeAI.Theme: colour tokens, fonts S/M/L, backdrop/skin, widget factories, tooltip helper.
-- Themes: 1 « Sombre » (flat dark + gold), 2 « Clair » (flat slate, brighter), 3 « Blizzard » (tooltip look).
-- Only ever skins TreeAI's OWN frames: unnamed or « TreeAI… » named, never protected / forbidden ones,
-- never a Blizzard frame (never re-parented, no field written on foreign frames: state lives in weak tables).
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
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
-- (5.1 LOOK) langage visuel V6 : plat, fin, sombre et translucide, petit texte net — comme les panneaux du joueur
-- (compteurs, colonne « Suivi Forever », suivi du jeu). UNE couleur d'accent (bleu clair) pour ce qui est choisi /
-- allumé / compté ; l'or ne sert qu'aux titres de section ; le rouge seulement à l'urgent. Liserés : 1 px, blancs à
-- 6-10 %, jamais dorés ; aucun dégradé, aucune tuile colorée, rien qui pulse.
local ACCENT = { 0.45, 0.74, 1, 1 }
local COMMON = {
  gold = { 1, 0.82, 0.2, 1 }, accent = ACCENT,
  prio1 = { 0.70, 0.72, 0.76, 1 }, prio2 = { 0.95, 0.95, 0.95, 1 }, prio3 = { 1, 0.42, 0.36, 1 },
  good = { 0.45, 0.82, 0.52, 1 }, bad = { 1, 0.42, 0.36, 1 }, warn = { 1, 0.66, 0.28, 1 },
  -- états et rôles communs à tous les thèmes : allumé / coupé, touche, nombre, bouton d'action
  on = ACCENT, off = { 0.55, 0.55, 0.58, 1 }, key = { 0.62, 0.66, 0.72, 1 },
  count = ACCENT, disabled = { 0.45, 0.45, 0.48, 1 }, pressed = { 0, 0, 0, 0.25 },
  tile = { 1, 1, 1, 0.045 }, tileOn = { 0.45, 0.74, 1, 0.16 }, tileOff = { 1, 1, 1, 0.02 },
  pillOn = { 0.45, 0.74, 1, 0.14 }, pillOff = { 1, 1, 1, 0.04 }, caption = { 0.62, 0.62, 0.64, 1 },
  -- sacs (légende des surbrillances) : mêmes couleurs dans les sacs, les cartes et la page Sacs
  keep = { 0.30, 0.85, 0.40, 1 }, sell = { 1, 0.82, 0.2, 1 }, ah = { 0.35, 0.65, 1, 1 }, bank = { 0.70, 0.55, 1, 1 },
  use = { 1, 1, 1, 1 }, destroy = { 1, 0.3, 0.25, 1 },
}
local PALETTES = {
  { -- Sombre (V6 : verre noir, liseré blanc discret)
    bg = { 0.025, 0.025, 0.03, 0.88 }, panel = { 0.05, 0.05, 0.06, 0.90 }, hud = { 0, 0, 0, 0.40 },
    barBg = { 0, 0, 0, 0.50 }, border = { 1, 1, 1, 0.10 }, borderSoft = { 1, 1, 1, 0.06 },
    text = { 0.90, 0.90, 0.90, 1 }, muted = { 0.60, 0.60, 0.63, 1 }, hover = { 1, 1, 1, 0.07 },
    button = { 1, 1, 1, 0.06 }, selected = { 0.45, 0.74, 1, 0.18 }, flat = true,
    small = { 0, 0, 0, 0.45 }, smallBorder = { 1, 1, 1, 0.05 },
  },
  { -- Clair
    bg = { 0.16, 0.17, 0.20, 0.92 }, panel = { 0.20, 0.21, 0.25, 0.92 }, hud = { 0.15, 0.16, 0.19, 0.50 },
    barBg = { 0.1, 0.1, 0.12, 0.6 }, border = { 1, 1, 1, 0.16 }, borderSoft = { 1, 1, 1, 0.09 },
    text = { 1, 1, 1, 1 }, muted = { 0.78, 0.78, 0.8, 1 }, hover = { 1, 1, 1, 0.10 },
    button = { 1, 1, 1, 0.09 }, selected = { 0.45, 0.74, 1, 0.24 }, flat = true,
    small = { 0.15, 0.16, 0.19, 0.50 }, smallBorder = { 1, 1, 1, 0.09 },
  },
  { -- Blizzard
    bg = { 0, 0, 0, 0.96 }, panel = { 0.05, 0.05, 0.05, 0.96 }, hud = { 0, 0, 0, 0.4 },
    barBg = { 0, 0, 0, 0.5 }, border = { 0.8, 0.8, 0.8, 1 }, borderSoft = { 0.6, 0.6, 0.6, 0.6 },
    text = { 1, 1, 1, 1 }, muted = { 0.65, 0.65, 0.65, 1 }, hover = { 1, 0.82, 0.2, 0.15 },
    button = { 0.12, 0.08, 0.04, 0.9 }, selected = { 1, 0.82, 0.2, 0.25 }, flat = false,
    small = { 0, 0, 0, 0.50 }, smallBorder = { 0.6, 0.6, 0.6, 0.6 },
    tile = { 0.09, 0.10, 0.13, 0.88 }, tileOn = { 0.36, 0.27, 0.06, 0.94 }, tileOff = { 0.09, 0.10, 0.13, 0.55 },
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
  window = { bg = "bg", border = "border" },                 -- (5.1) verre noir plat, liseré 1 px (plus de bord arrondi doré)
  panel = { bg = "panel", border = "border" },
  hud = { bg = "hud", border = "borderSoft" },
  bar = { bg = "barBg", border = "borderSoft", bar = true },
  badge = { bg = "bg", border = "border" },
  button = { bg = "button", border = "borderSoft" },
  row = { bg = false, border = false, hover = true },
  alert = { bg = "small", border = "smallBorder" },   -- (5.1) alerte : un petit cadre comme les autres, sans liseré rouge
  tooltip = { bg = "bg", border = "border" },
  card = { bg = "bg", border = "borderSoft" },                -- (UIREDO) fenêtre / carte : fond plat, liseré 1 px
  -- (4.2 FIX42) petits cadres posés sur l'écran (bande, alertes, cartes du marchand / de l'HV / de la banque) : fond
  -- transparent ~0,4, liseré 1 px discret, petite police (Th.SMALL) — jamais la fenêtre ni les menus
  small = { bg = "small", border = "smallBorder" },
  -- (5.1 LOOK) petite carte accrochée SOUS une fenêtre du jeu (marchand, banque, HV) : elle tombe souvent sur une
  -- autre colonne (Suivi Forever) — fond presque opaque pour que les deux textes ne se mélangent pas
  docked = { bg = "bg", border = "smallBorder" },
}
--- (4.2 FIX42) jetons des petits cadres : alpha du fond (0,35-0,5), police, hauteur des boutons
Th.SMALL = { alpha = 0.45, font = "S", fontPx = 10, btnH = 20, pad = 6 }
Th.KINDS = KINDS

local skins = setmetatable({}, { __mode = "k" })   -- frame -> { kind, opts, tex }
local fonts = setmetatable({}, { __mode = "k" })   -- fontstring -> size letter
-- (5.0 LOOK) N = 11 px (mots des boutons posés sur l'écran : lisibles à l'échelle d'interface 0,64), H = titre de fenêtre
local SIZES = { S = { "GameFontHighlightSmall", 10 }, N = { "GameFontHighlightSmall", 11 }, M = { "GameFontHighlight", 12 },
  L = { "GameFontNormalLarge", 14 }, H = { "GameFontNormalHuge", 16 } }   -- (5.1) titres plus petits : 14 / 16
-- (REVIEW) un seul rythme d'espacement et de hauteurs pour tous les cadres TreeAI (menus, fenêtres, lignes)
Th.SIZES = SIZES
Th.SPACE = { xs = 2, s = 4, m = 8, l = 12, xl = 16 }
Th.ROW = { h = 20, h2 = 32, head = 18, icon = 14, iconL = 18 }
-- (UIREDO) jetons de la fenêtre TreeAI et de la carte « Prochaine étape » : tailles de texte par rôle, hauteurs des
-- blocs (carte d'en-tête, étape numérotée, quête, ligne, en-tête de section), icônes des genres d'étape
Th.TYPE = { title = "L", body = "M", small = "S" }
Th.BLOCK = { hero = 66, step = 40, quest = 38, row = 24, header = 24, badge = 26, gap = 8 }
Th.GRID = 4                                                   -- tout est posé sur une grille de 4 px
Th.SHORTCUT = { size = 30, gap = 4, pad = 4, handle = 10 }     -- barre « Raccourcis » : boutons, écarts, poignée
Th.STEP_ICON = {
  pick = "Interface\\GossipFrame\\AvailableQuestIcon", turn = "Interface\\GossipFrame\\ActiveQuestIcon",
  fly = "Interface\\Minimap\\Tracking\\FlightMaster", boat = "Interface\\Icons\\INV_Misc_Spyglass_03",
  hs = "Interface\\Icons\\INV_Misc_Rune_01", obj = "Interface\\Icons\\INV_Misc_Note_02",
}
--- étiquette « bêta » (même couleur partout : Plus → Modules, pastille, options)
function Th:BetaTag() return "|cffb48cffbêta|r" end

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
  if self.RefreshActions then pcall(self.RefreshActions, self) end
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
  if b.label then meth(b.label, "SetTextColor", self:Color(st.selected and "accent" or "text")) end
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
KINDS.glass = { bg = "bg", border = "borderSoft" }   -- variante au liseré doux (5.1 : plate elle aussi)
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
  -- (4.1 BUGHUNT) copie : un « done » (Hide → OnHide → un autre cadre qui s'ouvre en fondu) ajoute une animation
  -- pendant le parcours (« invalid key to 'next' ») et le pilote se coupait avec elle dedans (cadre montré à alpha 0,
  -- qui prend les clics sans se voir)
  local list = {}
  for f, a in pairs(anims) do list[#list + 1] = f list[#list + 1] = a end
  for i = 1, #list, 2 do
    local f, a = list[i], list[i + 1]
    if anims[f] == a then
      a.t = a.t + (elapsed or 0.016)
      local k = math.min(1, a.t / a.dur)
      local e = 1 - (1 - k) * (1 - k)
      meth(f, "SetAlpha", a.from + (a.to - a.from) * e)
      if k >= 1 then
        anims[f] = nil
        if a.done then pcall(a.done, f) end
      end
    end
  end
  if not next(anims) and driver then meth(driver, "SetScript", "OnUpdate", nil) end
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
--- (5.0) coupe l'animation en cours d'un cadre (rouvert pendant son fondu de fermeture : il reste ouvert, bien visible)
function Th:StopAnim(frame, alpha)
  if anims[frame] == nil then return false end
  anims[frame] = nil
  if alpha then meth(frame, "SetAlpha", alpha) end
  return true
end
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
    if a and Th._OneToast then pcall(Th._OneToast, (...)) end   -- (5.0 LOOK) une alerte à la fois, 8 à 15 s
    return r
  end
end

-- (UIREDO) raccourcis : la touche liée s'affiche sur le bouton (coin haut gauche, comme les barres d'action) et
-- l'infobulle dit « Touche : X » ou comment en choisir une. Lecture seule (GetBindingKey) : jamais SetBinding.
Th._hotkeys = setmetatable({}, { __mode = "k" })   -- bouton -> nom de liaison (« CLICK TreeAIQuickItem:LeftButton »)
function Th:BindingOf(b)
  local name = b and meth(b, "GetName")
  return type(name) == "string" and ("CLICK " .. name .. ":LeftButton") or nil
end
function Th:KeyText(b)
  local bind = self:BindingOf(b)
  local key = bind and type(G.GetBindingKey) == "function" and select(2, pcall(G.GetBindingKey, bind)) or nil
  if type(key) ~= "string" or key == "" then return nil end
  local short = type(G.GetBindingText) == "function" and select(2, pcall(G.GetBindingText, key, true)) or nil
  return (type(short) == "string" and short ~= "") and short or key
end
--- (UIREDO) ombre douce sous un cadre (deux couronnes sombres, hors du cadre, sous son fond)
function Th:Shadow(frame, size)
  if type(frame) ~= "table" or frame.__shadow or not self:CanSkin(frame) then return end
  size = size or 6
  local out = {}
  for i, a in ipairs({ 0.30, 0.14 }) do
    local t = meth(frame, "CreateTexture", nil, "BACKGROUND", nil, -8)
    if t then
      local d = math.floor(size * i / 2 + 0.5)
      meth(t, "SetPoint", "TOPLEFT", frame, "TOPLEFT", -d, d - 2)
      meth(t, "SetPoint", "BOTTOMRIGHT", frame, "BOTTOMRIGHT", d, -d - 2)
      setColorTex(t, 0, 0, 0, a)
      out[#out + 1] = t
    end
  end
  frame.__shadow = out
  return out
end
--- (UIREDO) un bouton de raccourci, le même partout : fond sombre, liseré 1 px, icône rognée, enfoncé, touche liée.
--- Appelé à la création (hors combat) ; ne touche ni aux attributs ni aux scripts du bouton.
function Th:ShortcutStyle(b)
  if type(b) ~= "table" or b.__scStyled then return b end
  b.__scStyled = true
  local bg = meth(b, "CreateTexture", nil, "BACKGROUND", nil, -7)
  if bg then meth(bg, "SetAllPoints", b); setColorTex(bg, 0, 0, 0, 0.55) end
  b.edges = b.edges or {}
  local pts = { { "TOPLEFT", "TOPRIGHT", true }, { "BOTTOMLEFT", "BOTTOMRIGHT", true }, { "TOPLEFT", "BOTTOMLEFT" },
    { "TOPRIGHT", "BOTTOMRIGHT" } }
  for i, p in ipairs(pts) do
    local e = meth(b, "CreateTexture", nil, "OVERLAY", nil, 6)
    if e then
      meth(e, "SetPoint", p[1], b, p[1]); meth(e, "SetPoint", p[2], b, p[2])
      if p[3] then meth(e, "SetHeight", 1) else meth(e, "SetWidth", 1) end
      setColorTex(e, self:Color("borderSoft"))
      b.edges[i] = e
    end
  end
  if b.icon then
    meth(b.icon, "ClearAllPoints")
    meth(b.icon, "SetPoint", "TOPLEFT", b, "TOPLEFT", 1, -1)
    meth(b.icon, "SetPoint", "BOTTOMRIGHT", b, "BOTTOMRIGHT", -1, 1)
    meth(b.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
  end
  meth(b, "SetPushedTexture", "Interface\\Buttons\\UI-Quickslot-Depress")
  self:HotKey(b)
  return b
end
--- Pose / met à jour l'étiquette de touche d'un bouton de raccourci.
function Th:HotKey(b)
  if type(b) ~= "table" then return nil end
  if not b.hotkey then
    b.hotkey = meth(b, "CreateFontString", nil, "OVERLAY", "NumberFontNormalSmallGray")
    if b.hotkey then meth(b.hotkey, "SetPoint", "TOPLEFT", b, "TOPLEFT", 2, -2); meth(b.hotkey, "SetJustifyH", "LEFT") end
  end
  self._hotkeys[b] = true
  local k = self:KeyText(b)
  if b.hotkey then meth(b.hotkey, "SetText", k or "") end
  return k
end
--- Ligne d'infobulle : la touche, ou où la choisir.
function Th:KeyHint(b)
  local k = self:KeyText(b)
  if k then return "Touche : " .. k end
  return "Touche : aucune (Échap > Options > Raccourcis > TreeAI)"
end
function Th:RefreshHotKeys() for b in pairs(self._hotkeys) do self:HotKey(b) end end

-- =====================================================================================================
-- (5.0 LOOK) SYSTÈME DE DESIGN V5 : un seul jeu de jetons, un bouton, une ligne, une carte, une pastille, un jeu
-- d'icônes (celles du jeu), des états (survol / enfoncé / coupé / choisi). Tous les cadres TreeAI passent par ici ;
-- les pages des autres modules en héritent par la mise en page de la fenêtre (Ly.actions, Ly.toggle, Ly.card…).
-- Rien ici ne tourne tout seul : aucun OnUpdate (le survol et l'enfoncé sont des scripts de souris).
-- =====================================================================================================
Th.V = 6   -- (5.1 LOOK) V6 : plat, fin, translucide ; un accent ; bande d'une rangée (forme « slim »)
Th.TOKENS = {
  grid = 4,                                                              -- tout est posé sur une grille de 4 px
  space = Th.SPACE,                                                      -- xs 2 · s 4 · m 8 · l 12 · xl 16
  font = { S = 10, N = 11, M = 12, L = 14, H = 16 },                     -- px (× l'échelle du thème)
  border = 1,                                                            -- liseré : 1 px, partout
  -- fonds : menus et fenêtres opaques ; petits cadres posés sur l'écran à moitié transparents, leurs boutons pleins
  -- (5.1) tout est translucide ; idle / combat = alpha de la bande au repos / en combat (pleine au survol)
  alpha = { menu = 0.88, card = 0.88, small = 0.45, tile = 0.045, hover = 0.07, pressed = 0.25, disabled = 0.45,
    idle = 0.60, combat = 0.30 },
  button = { h = 24, big = 44, pad = 8, icon = 16, iconBig = 24 },
  row = { h = 24, h2 = 36, icon = 16, gap = 2 },
  card = { pad = 8, gap = 8, head = 24 },
  badge = { h = 13, w = 13 },
  tile = { w = 46, h = 42, icon = 22, gap = 2, compact = 22 },           -- tuile (fenêtre) ; compact = icône seule de la bande
  slim = { h = 22, icon = 14, gap = 1, pad = 6 },                        -- bouton de la bande : icône + mot court, une rangée
  strip = { cap = 16, pad = 2, grip = 8, sep = 7 },
  toast = { min = 8, max = 15, w = 320, quietMin = 5, quietMax = 8, quietW = 280 },   -- alertes : une à la fois ; « discret » 5 à 8 s
  window = { nav = 176, top = 56, foot = 40, pad = 16 },
}
-- un seul jeu d'icônes : celles du jeu (jamais d'image à nous), par rôle
local IC = "Interface\\Icons\\"
Th.ICONS = {
  guide = IC .. "Ability_Tracking", turnin = "Interface\\GossipFrame\\ActiveQuestIcon",
  pickup = "Interface\\GossipFrame\\AvailableQuestIcon", next = IC .. "Ability_Rogue_Sprint", zone = IC .. "INV_Misc_Map_01",
  fast = IC .. "Spell_Nature_Swiftness", bags = IC .. "INV_Misc_Bag_08", gear = IC .. "INV_Chest_Chain",
  prof = IC .. "Trade_BlackSmithing", group = IC .. "Spell_Holy_PrayerOfHealing", window = IC .. "INV_Misc_Book_09",
  quests = IC .. "INV_Misc_Book_09", route = IC .. "Ability_Rogue_Sprint", dungeon = IC .. "INV_Misc_Key_03",
  hv = IC .. "INV_Misc_Coin_01", hunter = IC .. "Ability_Hunter_BeastCall", coach = IC .. "INV_Misc_Note_01",
  binds = IC .. "INV_Misc_Key_14", settings = IC .. "INV_Misc_Gear_01", search = IC .. "INV_Misc_Spyglass_02",
  on = "Interface\\RaidFrame\\ReadyCheck-Ready", off = "Interface\\RaidFrame\\ReadyCheck-NotReady",
  warn = "Interface\\DialogFrame\\UI-Dialog-Icon-AlertOther", danger = "Interface\\DialogFrame\\UI-Dialog-Icon-AlertNew",
  move = IC .. "INV_Misc_Wrench_01", reset = "Interface\\Buttons\\UI-RefreshButton", keys = IC .. "INV_Misc_Key_03",
  display = IC .. "INV_Misc_Spyglass_02", profile = IC .. "INV_Misc_EngGizmos_20", tour = IC .. "INV_Misc_Book_08",
}
function Th:Icon(role) return self.ICONS[role or ""] or (IC .. "INV_Misc_QuestionMark") end
--- rogne le bord d'une icône d'objet / de sort (pas les icônes de dialogue ou de raid : elles n'ont pas de cadre)
function Th:SetIcon(tex, path)
  if not tex then return end
  meth(tex, "SetTexture", path)
  if type(path) == "string" and path:find("Icons\\", 1, true) then meth(tex, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
  else meth(tex, "SetTexCoord", 0, 1, 0, 1) end
end
-- sacs : la légende des surbrillances (une couleur = une décision, les mêmes mots partout)
Th.LEGEND = {
  bags = {
    { id = "keep", token = "keep", label = "À garder", tip = "Sert à toi, à ton groupe ou à ton métier." },
    { id = "use", token = "use", label = "À utiliser", tip = "Mieux que ce que tu portes, ou à apprendre / consommer." },
    { id = "sell", token = "sell", label = "À vendre au marchand", tip = "Ne sert à personne : le marchand en donne le plus." },
    { id = "ah", token = "ah", label = "À mettre à l'HV", tip = "Vaut plus cher à l'hôtel des ventes qu'au marchand." },
    { id = "bank", token = "bank", label = "À la banque", tip = "Servira plus tard (métier, niveau) : à ranger." },
    { id = "destroy", token = "destroy", label = "À jeter ?", tip = "Ne vaut rien et prend une place : à toi de décider." },
  },
}

local function edges(f, layer)
  local e = {}
  for i = 1, 4 do e[i] = meth(f, "CreateTexture", nil, layer or "BORDER") end
  if e[1] then meth(e[1], "SetPoint", "TOPLEFT", f, "TOPLEFT"); meth(e[1], "SetPoint", "TOPRIGHT", f, "TOPRIGHT"); meth(e[1], "SetHeight", 1) end
  if e[2] then meth(e[2], "SetPoint", "BOTTOMLEFT", f, "BOTTOMLEFT"); meth(e[2], "SetPoint", "BOTTOMRIGHT", f, "BOTTOMRIGHT"); meth(e[2], "SetHeight", 1) end
  if e[3] then meth(e[3], "SetPoint", "TOPLEFT", f, "TOPLEFT"); meth(e[3], "SetPoint", "BOTTOMLEFT", f, "BOTTOMLEFT"); meth(e[3], "SetWidth", 1) end
  if e[4] then meth(e[4], "SetPoint", "TOPRIGHT", f, "TOPRIGHT"); meth(e[4], "SetPoint", "BOTTOMRIGHT", f, "BOTTOMRIGHT"); meth(e[4], "SetWidth", 1) end
  return e
end
local function edgeColor(e, r, g, b, a) for i = 1, 4 do setColorTex(e[i], r, g, b, a) end end
Th._Edges, Th._EdgeColor, Th._ColorTex = edges, edgeColor, setColorTex

--- Pastille de nombre (rouge, chiffre blanc) : badge:Set(n) — cachée à 0 / nil ; « 99+ » au-delà
function Th:CountBadge(parent)
  local f = newFrame("Frame", parent)
  if not f then return nil end
  local k = self.TOKENS.badge
  meth(f, "SetSize", k.w, k.h)
  meth(f, "EnableMouse", false)
  f.bg = meth(f, "CreateTexture", nil, "ARTWORK")
  if f.bg then meth(f.bg, "SetAllPoints", f); setColorTex(f.bg, self:Color("count")) end
  f.text = meth(f, "CreateFontString", nil, "OVERLAY", "GameFontHighlightSmall")
  if f.text then meth(f.text, "SetPoint", "CENTER", f, "CENTER", 0, 0); meth(f.text, "SetTextColor", 0.03, 0.05, 0.08, 1) end
  function f.Set(self2, n)
    n = tonumber(n)
    self2.value = (n and n > 0) and n or nil
    if not self2.value then meth(self2, "Hide") return end
    local s = n > 99 and "99+" or tostring(math.floor(n))
    if self2.text then meth(self2.text, "SetText", s) end
    meth(self2, "SetWidth", k.w + (#s - 1) * 6)
    meth(self2, "Show")
  end
  f:Set(nil)
  return f
end

--- États d'un bouton d'action (les mêmes partout) : choisi (or), coupé (gris, icône désaturée), survol, enfoncé
function Th:PaintAction(b)
  local st = b and b.__st
  if not st then return end
  local kind = st.kind
  local slim = kind == "slim" or kind == "icon"
  if st.disabled then
    if slim then setColorTex(b.bg, 0, 0, 0, 0) else setColorTex(b.bg, self:Color("tileOff")) end
    edgeColor(b.edges, 0, 0, 0, 0)
  elseif st.primary and not slim then
    -- (5.1) L'action principale d'une page : la seule teinte pleine (accent), une par page
    local r, g, bl = self:Color("accent")
    setColorTex(b.bg, r, g, bl, 0.22)
    edgeColor(b.edges, r, g, bl, 0.55)
  elseif st.active then
    setColorTex(b.bg, self:Color("tileOn"))
    if slim then edgeColor(b.edges, 0, 0, 0, 0)
    else local r, g, bl = self:Color("accent") edgeColor(b.edges, r, g, bl, 0.45) end
  else
    if slim then setColorTex(b.bg, 0, 0, 0, 0) edgeColor(b.edges, 0, 0, 0, 0)
    else
      setColorTex(b.bg, self:Color("tile"))
      edgeColor(b.edges, self:Color("smallBorder"))
    end
  end
  if b.under then
    if st.active and not st.disabled and kind == "slim" then setColorTex(b.under, self:Color("accent")); meth(b.under, "Show")
    else meth(b.under, "Hide") end
  end
  if b.icon then
    meth(b.icon, "SetDesaturated", st.disabled and true or false)
    meth(b.icon, "SetAlpha", st.disabled and self.TOKENS.alpha.disabled or 1)
  end
  if b.label then
    if st.disabled then meth(b.label, "SetTextColor", self:Color("disabled"))
    elseif st.active then meth(b.label, "SetTextColor", self:Color("accent"))
    else meth(b.label, "SetTextColor", self:Color("text")) end
  end
  if b.keyText then meth(b.keyText, "SetAlpha", st.disabled and 0.5 or 1) end
  if b.countText then meth(b.countText, "SetAlpha", st.disabled and 0.5 or 1) end
end

--- LE bouton d'action TreeAI. opts = { name, layout = "tile" (icône + mot dessous) | "bar" (icône + mot à droite) |
--- "slim" (5.1 : petite icône + mot court + nombre, sans fond — la bande) | "icon" (icône seule), w, h, icon, label,
--- onClick(self, bouton de souris), onEnter(self), onLeave(self) }.
--- b:Set{ label, icon, count, key, active, disabled, primary } repeint ; b:Fit() rend la largeur utile ; b.icon, b.label,
--- b.keyText (touche, coin haut gauche), b.badge (nombre, coin haut droit). Jamais protégé : un clic = une fonction.
function Th:ActionButton(parent, opts)
  opts = type(opts) == "table" and opts or {}
  if type(G.CreateFrame) ~= "function" then return nil end
  local ok, b = pcall(G.CreateFrame, "Button", opts.name, parent or G.UIParent)
  if not ok or not b then return nil end
  local K = self.TOKENS
  local kind = (opts.layout == "bar" or opts.layout == "icon" or opts.layout == "slim") and opts.layout or "tile"
  b.__st = { kind = kind }
  local w = opts.w or (kind == "tile" and K.tile.w or kind == "icon" and K.tile.compact or 120)
  local h = opts.h or (kind == "tile" and K.tile.h or kind == "icon" and K.tile.compact or kind == "slim" and K.slim.h or K.button.h)
  meth(b, "SetSize", w, h)
  meth(b, "RegisterForClicks", "LeftButtonUp", "RightButtonUp")
  b.bg = meth(b, "CreateTexture", nil, "BACKGROUND")
  if b.bg then meth(b.bg, "SetAllPoints", b) end
  b.edges = edges(b, "BORDER")
  b.hl = meth(b, "CreateTexture", nil, "HIGHLIGHT")
  if b.hl then meth(b.hl, "SetAllPoints", b); setColorTex(b.hl, 1, 1, 1, K.alpha.hover) end
  b.push = meth(b, "CreateTexture", nil, "OVERLAY")
  if b.push then meth(b.push, "SetAllPoints", b); setColorTex(b.push, self:Color("pressed")); meth(b.push, "Hide") end
  b.icon = meth(b, "CreateTexture", nil, "ARTWORK")
  b.label = self:Text(b, opts.font or (kind == "bar" and "M" or kind == "slim" and "S" or "N"), "text")
  if b.label then meth(b.label, "SetWordWrap", false) end
  b.keyText = meth(b, "CreateFontString", nil, "OVERLAY", "NumberFontNormalSmallGray")
  if b.keyText then meth(b.keyText, "SetTextColor", self:Color("key")) end
  b.badge = self:CountBadge(b)
  -- (5.1) forme « slim » : le nombre est un petit chiffre d'accent après le mot, et le mode choisi un trait dessous
  b.countText = self:Text(b, "S", "count")
  if b.countText then meth(b.countText, "SetWordWrap", false); meth(b.countText, "Hide") end
  b.under = meth(b, "CreateTexture", nil, "ARTWORK")
  if b.under then
    meth(b.under, "SetHeight", 1)
    meth(b.under, "SetPoint", "BOTTOMLEFT", b, "BOTTOMLEFT", 4, 1); meth(b.under, "SetPoint", "BOTTOMRIGHT", b, "BOTTOMRIGHT", -4, 1)
    meth(b.under, "Hide")
  end
  --- forme du bouton : "tile" (icône + mot dessous), "icon" (icône seule, compact), "bar" (icône + mot à droite).
  --- Le même bouton change de forme (la bande passe en mode compact sans créer un 2e jeu de boutons).
  function b.SetLayout(self2, k2)
    local st = self2.__st
    k2 = (k2 == "icon" or k2 == "bar" or k2 == "slim") and k2 or "tile"
    if st.laid == k2 then return end
    st.kind, st.laid = k2, k2
    local isz = opts.iconSize or (k2 == "tile" and K.tile.icon or k2 == "icon" and (K.tile.compact - 8) or k2 == "slim" and K.slim.icon or K.button.icon)
    st.isz = isz
    meth(self2, "SetHeight", opts.h or (k2 == "tile" and K.tile.h or k2 == "icon" and K.tile.compact or k2 == "slim" and K.slim.h or K.button.h))
    if self2.icon then
      meth(self2.icon, "ClearAllPoints")
      meth(self2.icon, "SetSize", isz, isz)
      if k2 == "tile" then meth(self2.icon, "SetPoint", "TOP", self2, "TOP", 0, -4)
      elseif k2 == "icon" then meth(self2.icon, "SetPoint", "CENTER", self2, "CENTER", 0, 0)
      elseif k2 == "slim" then meth(self2.icon, "SetPoint", "LEFT", self2, "LEFT", K.slim.pad - 1, 0)
      else meth(self2.icon, "SetPoint", "LEFT", self2, "LEFT", K.button.pad - 2, 0) end
    end
    if self2.label then
      meth(self2.label, "ClearAllPoints")
      if k2 == "icon" then meth(self2.label, "Hide")
      elseif k2 == "tile" then
        meth(self2.label, "SetPoint", "BOTTOM", self2, "BOTTOM", 0, 3); meth(self2.label, "SetJustifyH", "CENTER"); meth(self2.label, "Show")
      elseif k2 == "slim" then
        meth(self2.label, "SetPoint", "LEFT", self2, "LEFT", K.slim.pad - 1 + isz + 4, 0); meth(self2.label, "SetJustifyH", "LEFT"); meth(self2.label, "Show")
      else
        meth(self2.label, "SetPoint", "LEFT", self2, "LEFT", K.button.pad + isz + 2, 0); meth(self2.label, "SetJustifyH", "LEFT"); meth(self2.label, "Show")
      end
    end
    if self2.keyText then
      meth(self2.keyText, "ClearAllPoints")
      if k2 == "bar" then meth(self2.keyText, "SetPoint", "RIGHT", self2, "RIGHT", -K.button.pad + 2, 0)
      else meth(self2.keyText, "SetPoint", "TOPLEFT", self2, "TOPLEFT", 2, -2) end
      if k2 == "slim" or k2 == "icon" then meth(self2.keyText, "Hide") else meth(self2.keyText, "Show") end   -- (bande : la touche est dans l'infobulle)
    end
    if self2.badge then
      meth(self2.badge, "ClearAllPoints")
      if k2 == "bar" then meth(self2.badge, "SetPoint", "TOPLEFT", self2.icon or self2, "TOPRIGHT", -5, 4)
      else meth(self2.badge, "SetPoint", "TOPRIGHT", self2, "TOPRIGHT", -1, -1) end
      if k2 == "slim" then meth(self2.badge, "Hide") else self2.badge:Set(st.count) end
    end
    if self2.countText then
      meth(self2.countText, "ClearAllPoints")
      if k2 == "slim" and self2.label then
        meth(self2.countText, "SetPoint", "LEFT", self2.label, "RIGHT", 3, 0)
        local n = tonumber(st.count)
        if n and n > 0 then meth(self2.countText, "Show") else meth(self2.countText, "Hide") end
      else meth(self2.countText, "Hide") end
    end
    if st.painted then Th:PaintAction(self2) end
  end
  b:SetLayout(kind)
  meth(b, "SetScript", "OnMouseDown", function(self2) if self2.push and not self2.__st.disabled then meth(self2.push, "Show") end end)
  meth(b, "SetScript", "OnMouseUp", function(self2) if self2.push then meth(self2.push, "Hide") end end)
  meth(b, "SetScript", "OnHide", function(self2) if self2.push then meth(self2.push, "Hide") end end)
  if opts.onClick then meth(b, "SetScript", "OnClick", opts.onClick) end
  if opts.onEnter then meth(b, "SetScript", "OnEnter", opts.onEnter) end
  meth(b, "SetScript", "OnLeave", opts.onLeave or function() Th:HideTooltip() end)
  function b.Set(self2, v)
    local st = self2.__st
    if v.icon ~= nil and v.icon ~= st.icon then st.icon = v.icon Th:SetIcon(self2.icon, v.icon) end
    if v.label ~= nil and self2.label and v.label ~= st.label then st.label = v.label meth(self2.label, "SetText", v.label) end
    if v.key ~= st.key or v.key == false then
      st.key = v.key or nil
      if self2.keyText then meth(self2.keyText, "SetText", st.key or "") end
    end
    if self2.badge and v.count ~= st.count then
      st.count = v.count
      local n = tonumber(v.count)
      if st.kind == "slim" then
        meth(self2.badge, "Hide")
        if self2.countText then
          if n and n > 0 then meth(self2.countText, "SetText", n > 99 and "99+" or tostring(math.floor(n))); meth(self2.countText, "Show")
          else meth(self2.countText, "SetText", ""); meth(self2.countText, "Hide") end
        end
      else self2.badge:Set(v.count) end
    end
    local a, d, pr = v.active and true or false, v.disabled and true or false, v.primary and true or false
    if a ~= st.active or d ~= st.disabled or pr ~= st.primary or not st.painted then
      st.active, st.disabled, st.primary, st.painted = a, d, pr, true
      Th:PaintAction(self2)
    end
  end
  --- largeur utile (mot mesuré par le jeu, sinon estimé) ; min = largeur plancher
  function b.Fit(self2, minW)
    local st = self2.__st
    local k2 = st.kind
    if k2 == "icon" then
      local need = minW or K.tile.compact
      meth(self2, "SetWidth", need)
      return need
    end
    -- largeur du mot : mesurée par le jeu une fois par libellé (gardée), sinon estimée
    if st.twFor ~= st.label then
      local tw = self2.label and tonumber(meth(self2.label, "GetStringWidth")) or 0
      if not tw or tw <= 0 then
        local _, n = tostring(st.label or ""):gsub("[^\128-\191]", "")
        tw = n * (k2 == "tile" and 5.6 or 6.2)
      end
      st.tw, st.twFor = tw, st.label
    end
    local need
    if k2 == "tile" then need = math.max(minW or K.tile.w, math.floor(st.tw + 8 + 0.5))
    elseif k2 == "slim" then
      local n = tonumber(st.count)
      local cw = 0
      if n and n > 0 then
        cw = self2.countText and tonumber(meth(self2.countText, "GetStringWidth")) or 0
        if not cw or cw <= 0 then cw = #tostring(math.floor(n)) * 6 end
        cw = cw + 3
      end
      need = math.max(minW or 0, math.floor(K.slim.pad - 1 + (st.isz or K.slim.icon) + 4 + st.tw + cw + K.slim.pad + 0.5))
    else need = math.max(minW or 0, math.floor(K.button.pad + (st.isz or K.button.icon) + 2 + st.tw + K.button.pad + 0.5)) end
    meth(self2, "SetWidth", need)
    return need
  end
  if opts.icon then b:Set({ icon = opts.icon, label = opts.label or "" }) else b:Set({ label = opts.label or "" }) end
  self._actions[b] = true
  return b
end
Th._actions = setmetatable({}, { __mode = "k" })

--- Pastille ALLUMÉ / COUPÉ (une bascule qui se lit de loin) : p:SetOn(bool) ; opts = { name, label, onClick, h }
--- « ● Guidage quêtes  ON » (vert) / « ○ Guidage quêtes  OFF » (gris). p:Fit() rend sa largeur.
function Th:Pill(parent, opts)
  opts = type(opts) == "table" and opts or {}
  if type(G.CreateFrame) ~= "function" then return nil end
  local ok, p = pcall(G.CreateFrame, "Button", opts.name, parent or G.UIParent)
  if not ok or not p then return nil end
  local h = opts.h or self.TOKENS.strip.cap
  meth(p, "SetSize", 120, h)
  meth(p, "RegisterForClicks", "LeftButtonUp", "RightButtonUp")
  p.bg = meth(p, "CreateTexture", nil, "BACKGROUND")
  if p.bg then meth(p.bg, "SetAllPoints", p) end
  p.edges = edges(p, "BORDER")
  p.hl = meth(p, "CreateTexture", nil, "HIGHLIGHT")
  if p.hl then meth(p.hl, "SetAllPoints", p); setColorTex(p.hl, 1, 1, 1, self.TOKENS.alpha.hover) end
  p.dot = meth(p, "CreateTexture", nil, "ARTWORK")
  if p.dot then meth(p.dot, "SetSize", 6, 6); meth(p.dot, "SetPoint", "LEFT", p, "LEFT", 6, 0) end
  p.label = self:Text(p, opts.font or "S", "text")
  if p.label then meth(p.label, "SetPoint", "LEFT", p, "LEFT", 16, 0); meth(p.label, "SetWordWrap", false) end
  p.state = self:Text(p, opts.font or "S", "on")
  if p.state then meth(p.state, "SetPoint", "RIGHT", p, "RIGHT", -6, 0); meth(p.state, "SetWordWrap", false) end
  p.text = opts.label or ""
  function p.SetOn(self2, on, label)
    on = on and true or false
    if label then self2.text = label end
    self2.on = on
    setColorTex(self2.bg, Th:Color(on and "pillOn" or "pillOff"))
    local r, g, bl = Th:Color(on and "on" or "off")
    edgeColor(self2.edges, 0, 0, 0, 0)                                    -- (5.1) plus de liseré coloré : un point et un mot
    setColorTex(self2.dot, r, g, bl, on and 1 or 0.6)
    if self2.label then meth(self2.label, "SetText", self2.text); meth(self2.label, "SetTextColor", Th:Color(on and "text" or "muted")) end
    if self2.state then meth(self2.state, "SetText", on and "ON" or "OFF"); meth(self2.state, "SetTextColor", r, g, bl) end
  end
  function p.Fit(self2)
    local tw = self2.label and tonumber(meth(self2.label, "GetStringWidth")) or 0
    if not tw or tw <= 0 then
      local _, n = tostring(self2.text):gsub("[^\128-\191]", "")
      tw = n * 5.4
    end
    local sw = self2.state and tonumber(meth(self2.state, "GetStringWidth")) or 0
    if not sw or sw <= 0 then sw = 18 end
    local need = math.floor(16 + tw + 5 + sw + 6 + 0.5)
    meth(self2, "SetWidth", need)
    return need
  end
  if opts.onClick then meth(p, "SetScript", "OnClick", opts.onClick) end
  if opts.onEnter then meth(p, "SetScript", "OnEnter", opts.onEnter) end
  meth(p, "SetScript", "OnLeave", opts.onLeave or function() Th:HideTooltip() end)
  p:SetOn(opts.on ~= false)
  self._pills[p] = true
  return p
end
Th._pills = setmetatable({}, { __mode = "k" })

--- Carte : menu = opaque (fenêtres, menus), small = posée sur l'écran ou sous un cadre du jeu (à moitié transparente).
--- opts = { name, kind = "card" | "small", w, h, title, strata }. c.title (texte or), c.body (ancre du contenu).
function Th:Card(parent, opts)
  opts = type(opts) == "table" and opts or {}
  if type(G.CreateFrame) ~= "function" then return nil end
  local ok, c = pcall(G.CreateFrame, "Frame", opts.name, parent or G.UIParent, backdropTemplate())
  if not ok or not c then return nil end
  local K = self.TOKENS.card
  meth(c, "SetSize", opts.w or 240, opts.h or 80)
  if opts.strata then meth(c, "SetFrameStrata", opts.strata) end
  if not self:Skin(c, opts.kind == "small" and "small" or "card") then
    c.bgt = meth(c, "CreateTexture", nil, "BACKGROUND")
    if c.bgt then meth(c.bgt, "SetAllPoints", c); setColorTex(c.bgt, 0.025, 0.025, 0.03, opts.kind == "small" and 0.45 or 0.88) end
  end
  local top = K.pad
  if opts.title then
    c.title = self:Text(c, "M", "gold")
    if c.title then
      meth(c.title, "SetPoint", "TOPLEFT", c, "TOPLEFT", K.pad, -K.pad + 1)
      meth(c.title, "SetPoint", "RIGHT", c, "RIGHT", -K.pad, 0)
      meth(c.title, "SetJustifyH", "LEFT"); meth(c.title, "SetWordWrap", false)
      meth(c.title, "SetText", opts.title)
    end
    top = K.head
  end
  c.inset = { left = K.pad, right = K.pad, top = top, bottom = K.pad }
  return c
end

--- Légende (une pastille de couleur + un mot par ligne) : Th:Legend(parent, Th.LEGEND.bags[, cols, w]) → cadre
function Th:Legend(parent, items, cols, w)
  local f = newFrame("Frame", parent)
  if not f then return nil end
  cols = math.max(1, tonumber(cols) or 2)
  w = tonumber(w) or 320
  local cw, n = math.floor(w / cols), 0
  f.rows = {}
  for _, it in ipairs(type(items) == "table" and items or {}) do
    n = n + 1
    local c, r = (n - 1) % cols, math.floor((n - 1) / cols)
    local sw = meth(f, "CreateTexture", nil, "ARTWORK")
    if sw then
      meth(sw, "SetSize", 10, 10)
      meth(sw, "SetPoint", "TOPLEFT", f, "TOPLEFT", c * cw, -r * 16 - 3)
      setColorTex(sw, self:Color(it.token or "text"))
    end
    local fs = self:Text(f, "S", "text")
    if fs then
      meth(fs, "SetPoint", "TOPLEFT", f, "TOPLEFT", c * cw + 16, -r * 16 - 2)
      meth(fs, "SetWidth", cw - 20); meth(fs, "SetJustifyH", "LEFT"); meth(fs, "SetWordWrap", false)
      meth(fs, "SetText", it.label or "")
    end
    f.rows[n] = { swatch = sw, text = fs, item = it }
  end
  meth(f, "SetSize", w, math.max(1, math.ceil(n / cols)) * 16 + 2)
  return f
end
--- la même légende en lignes de texte (infobulles) : un carré de couleur + « À vendre au marchand — … »
function Th:LegendLines(items)
  local out = {}
  for _, it in ipairs(type(items) == "table" and items or {}) do
    -- (un carré de couleur dessiné par le jeu : les polices de WoW n'ont pas de glyphe « carré plein »)
    local r, g, b = self:Color(it.token or "text")
    out[#out + 1] = ("|TInterface\\Buttons\\WHITE8X8:10:10:0:0:8:8:0:8:0:8:%d:%d:%d|t "):format(math.floor(r * 255 + 0.5),
      math.floor(g * 255 + 0.5), math.floor(b * 255 + 0.5)) .. tostring(it.label or "") .. (it.tip and (" — " .. it.tip) or "")
  end
  return out
end

-- alertes (encadré du cœur) : UNE à la fois, 8 à 15 s, la même apparence que les petits cadres ------------------
--- durée d'une alerte selon sa longueur (8 s, +1 s par 12 lettres au-delà de 40, plafond 15 s)
function Th:ToastSeconds(text)
  local K = self.TOKENS.toast
  local _, n = tostring(text or ""):gsub("[^\128-\191]", "")
  if self:ToastQuiet() then   -- (5.1) « discret » : 5 s, +1 s par 20 lettres au-delà de 30, plafond 8 s
    return math.max(K.quietMin, math.min(K.quietMax, K.quietMin + math.floor(math.max(0, n - 30) / 20)))
  end
  return math.max(K.min, math.min(K.max, K.min + math.floor(math.max(0, n - 40) / 12)))
end
--- (5.1 LOOK) alertes « discrètes » (d'office) : petites, courtes, sans couleur vive. Réglage : Affichage → Alertes discrètes
function Th:ToastQuiet()
  local s = T.settings and T.settings.alert
  return not (type(s) == "table" and s.quiet == false)
end
--- (5.1 LOOK) la place d'un petit cadre d'avis (alerte, ligne du 1er lancement) quand la boussole TreeAI n'est pas à
--- l'écran : en haut au centre, SOUS ce qui s'y trouve déjà (barre d'XP, flèche du jeu ou d'un autre addon), jamais
--- au milieu de l'écran (au-dessus de 34 % de la hauteur), jamais sur un cadre du jeu, d'un autre addon ou de TreeAI.
--- → gauche, bas (unités d'UIParent) ou nil. Lecture seule, au moment de l'afficher seulement.
function Th:ToastSpot(w, h)
  local W, H
  if T._ScreenSize then W, H = T._ScreenSize() end
  if not (W and H and W > 200 and H > 200) or type(T.SpotIsFree) ~= "function" then return nil end
  local own = {}
  local ue = tonumber(meth(G.UIParent, "GetEffectiveScale")) or 1
  for _, n in ipairs({ "TreeAIStrip", "TreeAINow", "TreeAICompassFrame", "TreeAIAlert", "TreeAIHello", "TreeAICard" }) do
    local f = rawget(G, n)
    if type(f) == "table" and meth(f, "IsVisible") then
      local l, b, fw, fh = meth(f, "GetRect")
      local k = (tonumber(meth(f, "GetEffectiveScale")) or ue) / ue
      if tonumber(l) and tonumber(b) and tonumber(fw) and tonumber(fh) then own[#own + 1] = { l * k, b * k, fw * k, fh * k } end
    end
  end
  local l = math.floor((W - w) / 2 + 0.5)
  local top = math.floor(H * 0.08)
  local last = math.floor(H * 0.34 - h)
  while top <= last do
    local b = H - top - h
    local okF, free = pcall(T.SpotIsFree, T, l, b, w, h, 4)
    if okF and free then
      local hit = false
      for _, r in ipairs(own) do
        if l < r[1] + r[3] + 4 and r[1] < l + w + 4 and b < r[2] + r[4] + 4 and r[2] < b + h + 4 then hit = true break end
      end
      if not hit then return l, b end
    end
    top = top + 10
  end
  return nil
end
--- l'alerte se pose juste sous la boussole (le seul endroit où le joueur regarde déjà), sauf s'il l'a placée lui-même,
--- si la boussole n'est pas à l'écran, ou si elle tomberait sur la bande d'actions : alors la place du cœur est gardée.
--- Ancrée sur UIParent à la position LUE de la boussole (jamais sur elle).
local function nearCompass(a)
  if T.IsPlayerPlaced and T:IsPlayerPlaced("alert") then return false end
  local cf = rawget(G, "TreeAICompassFrame")
  if type(cf) ~= "table" or not meth(cf, "IsVisible") then return false end
  local l, b, w = meth(cf, "GetRect")
  local W, H
  if T._ScreenSize then W, H = T._ScreenSize() end
  local ue = tonumber(meth(G.UIParent, "GetEffectiveScale")) or 1
  local k = (tonumber(meth(cf, "GetEffectiveScale")) or ue) / ue
  local as = tonumber(meth(a, "GetScale")) or 1
  local aw, ah = (tonumber(meth(a, "GetWidth")) or 300) * as, (tonumber(meth(a, "GetHeight")) or 23) * as
  if not (tonumber(l) and tonumber(b) and tonumber(w) and W and H and k > 0 and as > 0) then return false end
  local cx, top = (l + w / 2) * k, b * k - 6
  if top - ah < H * 0.5 or cx - aw / 2 < 4 or cx + aw / 2 > W - 4 then return false end
  local Sc = T.Shortcuts
  local sr = type(Sc) == "table" and type(Sc.StripRect) == "function" and Sc.Strip and Sc.Strip() and meth(Sc.Strip(), "IsShown") and Sc.StripRect() or nil
  if sr and cx - aw / 2 < sr.l + sr.w and sr.l < cx + aw / 2 and top - ah < sr.b + sr.h and sr.b < top then return false end
  -- (5.0 INTEG) jamais sur le cadre d'un autre addon (flèche d'un guide, compteur… nommé ou non) : sous la boussole si
  -- c'est libre, sinon à sa droite ou à sa gauche, sinon la place du cœur (choisie sur tout l'écran)
  local left = cx - aw / 2
  if type(T.SpotIsFree) == "function" then
    local cw = (tonumber(w) or 0) * k
    local cands = { { left, top - ah }, { l * k + cw + 8, top + 6 + 4 }, { l * k - 8 - aw, top + 6 + 4 } }
    local pick
    for _, c in ipairs(cands) do
      if c[1] >= 4 and c[1] + aw <= W - 4 and c[2] >= H * 0.5 and c[2] + ah <= H - 2 then
        local okF, free = pcall(T.SpotIsFree, T, c[1], c[2], aw, ah, 2)
        local onStrip = sr and c[1] < sr.l + sr.w and sr.l < c[1] + aw and c[2] < sr.b + sr.h and sr.b < c[2] + ah
        if okF and free and not onStrip then pick = c break end
      end
    end
    if not pick then return false end
    meth(a, "ClearAllPoints")
    meth(a, "SetPoint", "TOPLEFT", G.UIParent, "TOPLEFT", pick[1] / as, (pick[2] + ah - H) / as)
    return true
  end
  meth(a, "ClearAllPoints")
  meth(a, "SetPoint", "TOP", G.UIParent, "TOP", (cx - W / 2) / as, (top - H) / as)
  return true
end
Th._ToastNearCompass = nearCompass
local function oneToast(e)
  local a = T.GetAlert and T:GetAlert()
  if type(a) ~= "table" or type(a.lines) ~= "table" then return end
  -- une seule ligne (la plus récente) : jamais une pile d'avis
  local l2 = a.lines[2]
  if l2 then meth(l2, "SetText", ""); meth(l2, "Hide") end
  meth(a, "SetHeight", 10 + 13)
  -- (5.1) discret : texte blanc cassé, un trait fin d'accent à gauche (rouge doux si danger) — plus de cadre rouge ;
  -- largeur ajustée au texte (jamais plus que la largeur d'origine)
  local quiet = Th:ToastQuiet()
  local l1 = a.lines[1]
  if l1 then
    if quiet then meth(l1, "SetTextColor", Th:Color("text")) else meth(l1, "SetTextColor", 1, 0.45, 0.4) end
  end
  if not a._bar then
    a._bar = meth(a, "CreateTexture", nil, "ARTWORK")
    if a._bar then
      meth(a._bar, "SetWidth", 2)
      meth(a._bar, "SetPoint", "TOPLEFT", a, "TOPLEFT", 1, -3); meth(a._bar, "SetPoint", "BOTTOMLEFT", a, "BOTTOMLEFT", 1, 3)
    end
  end
  if a._bar then
    local danger = e and T.IsDanger and T:IsDanger(e)
    setColorTex(a._bar, Th:Color(danger and "prio3" or "accent"))
    if quiet then meth(a._bar, "Show") else meth(a._bar, "Hide") end
  end
  if quiet and l1 then
    local tw = tonumber(meth(l1, "GetUnboundedStringWidth")) or tonumber(meth(l1, "GetStringWidth")) or 0
    if tw > 20 then meth(a, "SetWidth", math.max(140, math.min(300, math.floor(tw + 18)))) end
  elseif not quiet then meth(a, "SetWidth", 300) end
  local s = T.settings and T.settings.alert
  if not (s and tonumber(s.duration) and tonumber(s.duration) > Th.TOKENS.toast.max) then
    a.left = math.max(tonumber(a.left) or 0, Th:ToastSeconds(e and e.text))
    if a.left > Th.TOKENS.toast.max then a.left = Th.TOKENS.toast.max end
  end
  if meth(a, "IsShown") then
    local okN, placed = pcall(nearCompass, a)
    -- (5.1) pas de boussole TreeAI à l'écran (Suivi Forever guide) : en haut au centre, sous la flèche du jeu
    if not (okN and placed) and not (T.IsPlayerPlaced and T:IsPlayerPlaced("alert")) then
      local as = tonumber(meth(a, "GetScale")) or 1
      local aw, ah = (tonumber(meth(a, "GetWidth")) or 300) * as, (tonumber(meth(a, "GetHeight")) or 23) * as
      local okS, sl, sb = pcall(Th.ToastSpot, Th, aw, ah)
      if okS and sl and as > 0 then
        meth(a, "ClearAllPoints")
        meth(a, "SetPoint", "BOTTOMLEFT", G.UIParent, "BOTTOMLEFT", sl / as, sb / as)
        placed = true
      end
    end
    -- pas de place libre : la place du cœur (choisie sur tout l'écran), pas celle d'une alerte d'avant
    if not (okN and placed) and not (T.IsPlayerPlaced and T:IsPlayerPlaced("alert")) and T.ApplyAlertSettings then pcall(T.ApplyAlertSettings, T) end
  end
end
Th._OneToast = oneToast

--- relit les couleurs des boutons d'action et des pastilles (changement de thème)
function Th:RefreshActions()
  for b in pairs(self._actions) do
    if b.badge and b.badge.bg then setColorTex(b.badge.bg, self:Color("count")) end
    pcall(self.PaintAction, self, b)
  end
  for p in pairs(self._pills) do pcall(p.SetOn, p, p.on) end
end

--- ce que valent les jetons (tests, research/look5.md) : lignes « nom = valeur »
function Th:Describe()
  local K = self.TOKENS
  local out = { "thème = " .. tostring(self:Name()) .. " (v" .. self.V .. ")", "grille = " .. K.grid .. " px, liseré = " .. K.border .. " px" }
  out[#out + 1] = ("polices = S %d · N %d · M %d · L %d · H %d px"):format(K.font.S, K.font.N, K.font.M, K.font.L, K.font.H)
  out[#out + 1] = ("espaces = xs %d · s %d · m %d · l %d · xl %d"):format(K.space.xs, K.space.s, K.space.m, K.space.l, K.space.xl)
  for _, tk in ipairs({ "bg", "panel", "small", "tile", "tileOn", "border", "smallBorder", "gold", "accent", "on", "off", "count", "key" }) do
    local r, g, b, a = self:Color(tk)
    out[#out + 1] = ("couleur %s = %.2f %.2f %.2f, alpha %.2f"):format(tk, r, g, b, a)
  end
  return out
end

function M:OnEnable()
  self:RegisterEvent("UPDATE_BINDINGS", function() Th:RefreshHotKeys() end)
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
