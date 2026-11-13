-- tools/wow_render.lua — LOOK (5.1): SEE what TreeAI draws. A tool, not a test.
--
--   lua5.1 tools/wow_render.lua --scene NAME --out DUMP.json [--root DIR] [--scale 0.64] [--metrics FILE]
--   (normally run by tools/wow_render.js, which turns DUMP.json into a PNG with Playwright, drawn over a capture of
--    the player's screen when one is given)
--
-- How: the addon's own code runs in the WoW: Forever mock with the real-client geometry (tests/wow/harness.lua,
-- wowmock.lua, geometry.lua : 2000×1125, UI scale 0.64 = the player's screen, tests/wow/realui_scenario.lua). The mock
-- is instrumented BEFORE the addon loads (the harness's dofile of wowmock.lua is intercepted): every drawing call the
-- mock ignores (SetColorTexture, SetBackdrop*, SetTextColor, SetFont, SetJustifyH, draw layers…) is recorded, and
-- GetStringWidth / GetStringHeight answer from the metrics of the font the PNG is drawn with, so the addon lays itself
-- out as it would in the client. A scene is then played and every VISIBLE TreeAI frame is dumped in draw order
-- (strata, level, layer, sublevel, creation): rectangles in real pixels, colours × alpha, texts with size / colour /
-- justification. Blizzard art is not available: an icon becomes a small labelled tile, a white texture its colour.
-- The other addons' frames of the player's screen are invisible obstacles (they are in the capture); game windows a
-- scene opens (merchant, bags, auction house) are drawn as plain schematic panels.
--
-- Scenes: idle_town, questing_on, questing_off, merchant, bags, ah, toast, first_login, window_<page id>, pages (list).

local MOCK = rawget(_G, "WowMock")
if type(MOCK) == "table" and type(rawget(MOCK, "__render")) == "table" then
  return MOCK.__render.scenario          -- second load: the harness runs this file as its --scenario
end

local opts = { scene = "idle_town", scale = 0.64, w = 2000, h = 1125 }
do
  local a = arg or {}
  local i = 1
  while i <= #a do
    if a[i] == "--out" then opts.out = a[i + 1]; i = i + 1
    elseif a[i] == "--scene" then opts.scene = a[i + 1]; i = i + 1
    elseif a[i] == "--root" then opts.root = a[i + 1]; i = i + 1
    elseif a[i] == "--scale" then opts.scale = tonumber(a[i + 1]) or opts.scale; i = i + 1
    elseif a[i] == "--metrics" then opts.metrics = a[i + 1]; i = i + 1
    elseif a[i] == "-h" or a[i] == "--help" then
      io.write("usage: lua5.1 tools/wow_render.lua --scene NAME --out DUMP.json [--root DIR] [--scale 0.64]\n")
      os.exit(0)
    end
    i = i + 1
  end
end
local SELF = (arg and arg[0]) or "tools/wow_render.lua"
local TOOLDIR = SELF:match("^(.*)[/\\][^/\\]*$") or "."
local ROOT = opts.root or (TOOLDIR .. "/..")
opts.out = opts.out or "wow_render_dump.json"
opts.metrics = opts.metrics or (TOOLDIR .. "/wow_render_metrics.lua")

---------------------------------------------------------------------------------------------------- metrics
-- { adv = { [codepoint] = advance in 1/1000 em }, fallback = n, k = size factor, line = line advance / size }
local MET = { adv = {}, fallback = 560, k = 0.94, line = 1.18 }
do
  local chunk = loadfile(opts.metrics)
  if chunk then
    local ok, t = pcall(chunk)
    if ok and type(t) == "table" then for k, v in pairs(t) do MET[k] = v end end
  end
end
local UCHAR = "[%z\1-\127\194-\244][\128-\191]*"
local function cpOf(ch)
  local b1 = ch:byte(1)
  if b1 < 0x80 then return b1 end
  if b1 < 0xE0 then return (b1 - 0xC0) * 64 + ((ch:byte(2) or 0x80) - 0x80) end
  if b1 < 0xF0 then return ((b1 - 0xE0) * 64 + ((ch:byte(2) or 0x80) - 0x80)) * 64 + ((ch:byte(3) or 0x80) - 0x80) end
  return 0x25A1
end

-- the client's font objects: size, colour
local GOLD, WHITE, GREY = { 1, 0.82, 0, 1 }, { 1, 1, 1, 1 }, { 0.5, 0.5, 0.5, 1 }
local FONTS = {
  GameFontNormal = { 12, GOLD }, GameFontNormalSmall = { 10, GOLD }, GameFontNormalLarge = { 16, GOLD },
  GameFontNormalHuge = { 20, GOLD }, GameFontHighlight = { 12, WHITE }, GameFontHighlightSmall = { 10, WHITE },
  GameFontHighlightLarge = { 16, WHITE }, GameFontHighlightHuge = { 20, WHITE }, GameFontDisable = { 12, GREY },
  GameFontDisableSmall = { 10, GREY }, GameFontRed = { 12, { 1, 0.1, 0.1, 1 } }, GameFontGreen = { 12, { 0.1, 1, 0.1, 1 } },
  GameFontWhite = { 12, WHITE }, NumberFontNormal = { 14, WHITE }, NumberFontNormalSmall = { 12, WHITE },
  ChatFontNormal = { 14, WHITE }, ChatFontSmall = { 12, WHITE }, GameTooltipText = { 12, WHITE },
  GameFontNormalMed3 = { 14, GOLD }, GameFontHighlightMedium = { 14, WHITE },
}
local R = { opts = opts, seq = 0, schem = {} }
local function nextSeq() R.seq = R.seq + 1 return R.seq end
local REGION = { Texture = true, FontString = true, Line = true, MaskTexture = true }

local function fontNameOf(obj)
  if type(obj) == "string" then return obj end
  if type(obj) == "table" then return rawget(obj, "__name") end
end
local function fontOf(w)
  local base = FONTS[fontNameOf(rawget(w, "__fobj")) or ""] or FONTS[fontNameOf(rawget(w, "__tpl")) or ""]
  local size = rawget(w, "__fsize") or (base and base[1]) or 12
  return size, rawget(w, "__tcolor") or (base and base[2]) or WHITE
end
local function plainOf(text)
  text = tostring(text or "")
  text = text:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "\1"):gsub("|A.-|a", "\1"):gsub("|H.-|h", ""):gsub("|h", "")
  return (text:gsub("|n", "\n"):gsub("||", "|"))
end
local function strW(text, size)
  local w, k = 0, size * MET.k / 1000
  for ch in text:gmatch(UCHAR) do
    if ch == "\1" then w = w + size else w = w + (MET.adv[cpOf(ch)] or MET.fallback) * k end
  end
  return w
end
--- widest line and number of lines of `text` at `size`, wrapped at maxW (nil = never)
local function measure(text, size, maxW, wrap)
  text = plainOf(text)
  if text == "" then return 0, 0 end
  local lines, widest = 0, 0
  for para in (text .. "\n"):gmatch("([^\n]*)\n") do
    local full = strW(para, size)
    if not maxW or wrap == false or full <= maxW + 0.5 then
      lines = lines + 1
      if full > widest then widest = math.min(full, maxW or full) end
    else
      local cur, space = 0, strW(" ", size)
      for word in para:gmatch("%S+") do
        local ww = strW(word, size)
        if cur > 0 and cur + space + ww > maxW + 0.5 then
          lines = lines + 1
          if cur > widest then widest = cur end
          cur = ww
        else cur = (cur > 0) and (cur + space + ww) or ww end
      end
      lines = lines + 1
      if cur > widest then widest = math.min(cur, maxW) end
    end
  end
  return widest, lines
end
local function lineH(size) return math.floor(size * MET.line + 0.5) end

---------------------------------------------------------------------------------------------------- geometry (mirror of geometry.lua + regions)
local HX = { TOPLEFT = 0, LEFT = 0, BOTTOMLEFT = 0, TOP = 0.5, CENTER = 0.5, BOTTOM = 0.5, TOPRIGHT = 1, RIGHT = 1, BOTTOMRIGHT = 1 }
local VY = { TOPLEFT = 1, TOP = 1, TOPRIGHT = 1, LEFT = 0.5, CENTER = 0.5, RIGHT = 0.5, BOTTOMLEFT = 0, BOTTOM = 0, BOTTOMRIGHT = 0 }
local function geo() return R.M and R.M.geo end
local function effScale(f) local g = geo() return g and g.EffScale(f) or 1 end
local rectOf
local function fsWidthLimit(w, depth)   -- constrained width (own units) of a font string, or nil
  if rawget(w, "__wset") and (rawget(w, "__w") or 0) > 0 then return rawget(w, "__w") end
  local seen, n = {}, 0
  for _, p in ipairs(rawget(w, "__points") or {}) do local x = HX[p[1]] if x and not seen[x] then seen[x] = true n = n + 1 end end
  if n >= 2 then
    local _, _, wd = rectOf(w, depth, true)
    if wd then return wd / effScale(w) end
  end
  return nil
end
local function fsSize(w, depth)
  local size = fontOf(w)
  local text = rawget(w, "__text")
  local M = R.M
  if M and M.IsSecret and M.IsSecret(text) then text = M.Unsecret(text) end
  local limit = fsWidthLimit(w, depth)
  local wrap = rawget(w, "__wrap") ~= false
  local wd, lines = measure(text, size, limit, wrap)
  local maxl = rawget(w, "__maxl")
  if maxl and maxl > 0 and lines > maxl then lines = maxl end
  return wd, (lines > 0) and (size + (lines - 1) * lineH(size)) or 0, limit, size
end
--- rectangle in « abs » units (768 high), y up: left, bottom, width, height — or nil
rectOf = function(f, depth, widthOnly)
  local g = geo()
  if not g then return nil end
  local UIP = rawget(_G, "UIParent")
  if f == UIP then return 0, 0, g.absW, g.absH end
  depth = depth or 0
  if type(f) ~= "table" or depth > 30 then return nil end
  local es = effScale(f)
  local par = rawget(f, "__parent")
  if par and rawget(par, "__scrollChild") == f then   -- a scroll child sits at its scroll frame's top-left
    local pl, pb, _, ph = rectOf(par, depth + 1)
    if not pl then return nil end
    local cw, ch = (rawget(f, "__w") or 0) * es, (rawget(f, "__h") or 0) * es
    return pl, pb + ph + (rawget(par, "__vscroll") or 0) * es - ch, cw, ch
  end
  local pts = rawget(f, "__points")
  if not pts or #pts == 0 then return nil end
  local L, Rr, CX, T, B, CY
  for _, p in ipairs(pts) do
    local rel = p[2]
    if type(rel) == "string" then rel = rawget(_G, rel) end
    if rel == nil then rel = par or UIP end
    local rl, rb, rw, rh = rectOf(rel, depth + 1)
    if not rl then return nil end
    local rp = p[3] or p[1]
    if not HX[rp] or not HX[p[1]] then return nil end
    local ax = rl + rw * HX[rp] + (tonumber(p[4]) or 0) * es
    local ay = rb + rh * VY[rp] + (tonumber(p[5]) or 0) * es
    local hx, vy = HX[p[1]], VY[p[1]]
    if hx == 0 then L = ax elseif hx == 1 then Rr = ax else CX = ax end
    if vy == 1 then T = ay elseif vy == 0 then B = ay else CY = ay end
  end
  local kind = rawget(f, "__kind")
  local w, h
  if kind == "FontString" then
    if widthOnly then w, h = 0, 0
    else
      local sw, sh, limit = fsSize(f, depth + 1)
      w = (rawget(f, "__wset") and (rawget(f, "__w") or 0) > 0) and rawget(f, "__w") or (limit or sw)
      h = (rawget(f, "__hset") and (rawget(f, "__h") or 0) > 0) and rawget(f, "__h") or sh
    end
  elseif REGION[kind or ""] then
    w = rawget(f, "__wset") and (rawget(f, "__w") or 0) or 0
    h = rawget(f, "__hset") and (rawget(f, "__h") or 0) or 0
  else
    w, h = rawget(f, "__w") or 0, rawget(f, "__h") or 0
  end
  w, h = w * es, h * es
  local left, width, bottom, height
  if L and Rr then left, width = L, Rr - L elseif L then left, width = L, w
  elseif Rr then left, width = Rr - w, w elseif CX then left, width = CX - w / 2, w else return nil end
  if T and B then bottom, height = B, T - B elseif B then bottom, height = B, h
  elseif T then bottom, height = T - h, h elseif CY then bottom, height = CY - h / 2, h else return nil end
  if rawget(f, "__clamped") and not REGION[kind or ""] then
    if left < 0 then left = 0 elseif left + width > g.absW then left = math.max(0, g.absW - width) end
    if bottom < 0 then bottom = 0 elseif bottom + height > g.absH then bottom = math.max(0, g.absH - height) end
  end
  return left, bottom, width, height
end
R.rectOf = rectOf

---------------------------------------------------------------------------------------------------- instrumentation
local function srcOf()
  for lvl = 3, 12 do
    local info = debug.getinfo(lvl, "S")
    if not info then break end
    local rel = tostring(info.short_src or ""):match("wow_addon[/\\]TreeAI[/\\](.+)$")
    if rel then return rel end
  end
  return nil
end
local function instrument(M)
  local Wg = assert(M.Widget, "wowmock: M.Widget missing")
  R.M = M
  M.__render = R
  local function before(name, fn)
    local orig = rawget(Wg, name)
    Wg[name] = function(self, ...) fn(self, ...) if orig then return orig(self, ...) end end
  end
  local function num(v, d) return tonumber(v) or d end
  for _, kind in ipairs({ "CreateTexture", "CreateFontString" }) do
    local orig = Wg[kind]
    Wg[kind] = function(self, name, layer, tpl, sub)
      local r = orig(self, name, layer, tpl, sub)
      r.__seq, r.__layer, r.__w, r.__h = nextSeq(), layer, 0, 0
      if kind == "CreateFontString" then r.__tpl = tpl else r.__sub = tonumber(sub) or 0 end
      return r
    end
  end
  before("SetWidth", function(self, w) if w then self.__wset = (tonumber(w) or 0) > 0 end end)
  before("SetHeight", function(self, h) if h then self.__hset = (tonumber(h) or 0) > 0 end end)
  before("SetSize", function(self, w, h)
    if w then self.__wset = (tonumber(w) or 0) > 0 end
    if h or w then self.__hset = (tonumber(h or w) or 0) > 0 end
  end)
  -- textures
  before("SetTexture", function(self, tex, g2, b2, a2)
    if type(tex) == "number" and g2 then self.__color, self.__file = { tex, g2, b2 or 0, a2 or 1 }, nil   -- old SetTexture(r, g, b, a)
    else self.__file, self.__color = tex, nil end
    self.__atlas = nil
  end)
  Wg.SetColorTexture = function(self, r, g2, b2, a2) self.__color, self.__file, self.__texture = { num(r, 1), num(g2, 1), num(b2, 1), num(a2, 1) }, nil, nil end
  Wg.SetVertexColor = function(self, r, g2, b2, a2) self.__vc = { num(r, 1), num(g2, 1), num(b2, 1), num(a2, 1) } end
  Wg.GetVertexColor = function(self) local c = self.__vc or WHITE return c[1], c[2], c[3], c[4] end
  Wg.SetTexCoord = function(self, ...) self.__tc = { ... } end
  Wg.SetAtlas = function(self, atlas) self.__atlas, self.__file, self.__color = atlas, "atlas:" .. tostring(atlas), nil end
  Wg.SetDesaturated = function(self, v) self.__desat = v and true or false end
  Wg.SetBlendMode = function(self, m) self.__blend = m end
  Wg.SetGradient = function(self, orient, c1, c2)
    local function col(c) if type(c) == "table" then return { c.r or c[1] or 1, c.g or c[2] or 1, c.b or c[3] or 1, c.a or c[4] or 1 } end end
    self.__grad = { tostring(orient):upper(), col(c1), col(c2) }
  end
  Wg.SetGradientAlpha = function(self, orient, r1, g1, b1, a1, r2, g2, b2, a2)
    self.__grad = { tostring(orient):upper(), { r1, g1, b1, a1 or 1 }, { r2, g2, b2, a2 or 1 } }
  end
  Wg.SetDrawLayer = function(self, layer, sub) self.__layer = layer if sub then self.__sub = tonumber(sub) or 0 end end
  Wg.SetRotation = function(self, rad) self.__rot = tonumber(rad) or 0 end
  -- backdrop
  Wg.SetBackdrop = function(self, bd) self.__bd = bd end
  Wg.GetBackdrop = function(self) return self.__bd end
  Wg.SetBackdropColor = function(self, r, g2, b2, a2) self.__bdc = { num(r, 1), num(g2, 1), num(b2, 1), num(a2, 1) } end
  Wg.GetBackdropColor = function(self) local c = self.__bdc or WHITE return c[1], c[2], c[3], c[4] end
  Wg.SetBackdropBorderColor = function(self, r, g2, b2, a2) self.__bdb = { num(r, 1), num(g2, 1), num(b2, 1), num(a2, 1) } end
  Wg.GetBackdropBorderColor = function(self) local c = self.__bdb or WHITE return c[1], c[2], c[3], c[4] end
  -- fonts / text
  Wg.SetFont = function(self, file, size, flags) self.__ffile, self.__fsize, self.__fflags = file, tonumber(size), flags or "" return true end
  Wg.GetFont = function(self) local size = fontOf(self) return rawget(self, "__ffile") or "Fonts\\FRIZQT__.TTF", size, rawget(self, "__fflags") or "" end
  Wg.SetFontObject = function(self, obj)
    if type(obj) == "string" then obj = rawget(_G, obj) or obj end
    self.__fobj, self.__fsize, self.__fflags, self.__tcolor = obj, nil, nil, nil
  end
  Wg.GetFontObject = function(self) return self.__fobj end
  Wg.SetTextColor = function(self, r, g2, b2, a2) self.__tcolor = { num(r, 1), num(g2, 1), num(b2, 1), num(a2, 1) } end
  Wg.GetTextColor = function(self) local _, c = fontOf(self) return c[1], c[2], c[3], c[4] or 1 end
  Wg.SetJustifyH = function(self, j) self.__jh = j and tostring(j):upper() end
  Wg.SetJustifyV = function(self, j) self.__jv = j and tostring(j):upper() end
  Wg.GetJustifyH = function(self) return self.__jh or "CENTER" end
  Wg.SetWordWrap = function(self, v) self.__wrap = v and true or false end
  Wg.SetNonSpaceWrap = function() end
  Wg.SetMaxLines = function(self, n) self.__maxl = tonumber(n) end
  Wg.SetShadowOffset = function(self, x, y) self.__shadow = { tonumber(x) or 0, tonumber(y) or 0 } end
  Wg.SetShadowColor = function() end
  local origSW = Wg.GetStringWidth
  Wg.GetStringWidth = function(self)
    if M.IsSecret and M.IsSecret(rawget(self, "__text")) then return origSW(self) end
    local wd = fsSize(self, 0)
    return wd
  end
  Wg.GetUnboundedStringWidth = function(self)
    local size = fontOf(self)
    return (measure(rawget(self, "__text"), size, nil, false))
  end
  Wg.GetStringHeight = function(self) local _, h = fsSize(self, 0) return h end
  Wg.GetNumLines = function(self)
    local size = fontOf(self)
    local _, n = measure(rawget(self, "__text"), size, fsWidthLimit(self, 0), rawget(self, "__wrap") ~= false)
    return n
  end
  Wg.IsTruncated = function(self)
    local size = fontOf(self)
    local limit = fsWidthLimit(self, 0)
    if not limit then return false end
    return (measure(rawget(self, "__text"), size, nil, false)) > limit + 0.5 and rawget(self, "__wrap") == false
  end
  local baseGW, baseGH = Wg.GetWidth, Wg.GetHeight
  Wg.GetWidth = function(self)
    if rawget(self, "__kind") == "FontString" and not rawget(self, "__wset") then return (fsSize(self, 0)) end
    return baseGW(self)
  end
  Wg.GetHeight = function(self)
    if rawget(self, "__kind") == "FontString" and not rawget(self, "__hset") then local _, h = fsSize(self, 0) return h end
    return baseGH(self)
  end
  -- buttons / status bars / misc
  for _, n in ipairs({ "Normal", "Pushed", "Highlight", "Disabled", "Checked" }) do
    Wg["Set" .. n .. "Texture"] = function(self, tex) self["__" .. n:lower() .. "Tex"] = tex end
  end
  Wg.SetStatusBarTexture = function(self, tex) self.__sbtex = tex end
  Wg.SetStatusBarColor = function(self, r, g2, b2, a2) self.__sbc = { num(r, 1), num(g2, 1), num(b2, 1), num(a2, 1) } end
  Wg.GetStatusBarColor = function(self) local c = self.__sbc or WHITE return c[1], c[2], c[3], c[4] end
  Wg.SetClipsChildren = function(self, v) self.__clips = v and true or false end
  Wg.SetIgnoreParentAlpha = function(self, v) self.__ignoreAlpha = v and true or false end
  local origInstall = M.Install
  M.Install = function(...)
    local res = origInstall(...)
    local G = _G
    for name in pairs(FONTS) do
      if type(rawget(G, name)) ~= "table" then rawset(G, name, M.NewWidget("Font", name, nil)) end
    end
    local cf = G.CreateFrame
    G.CreateFrame = function(kind, name, parent, template, ...)
      local f = cf(kind, name, parent, template, ...)
      if type(f) == "table" then
        f.__seq, f.__src, f.__template = nextSeq(), srcOf(), template
        if kind == "ScrollFrame" then f.__clips = true end
      end
      return f
    end
    return res
  end
end

---------------------------------------------------------------------------------------------------- JSON
local function jstr(s)
  return '"' .. (tostring(s):gsub('[%c"\\]', function(c)
    if c == '"' then return '\\"' elseif c == "\\" then return "\\\\" elseif c == "\n" then return "\\n" end
    return string.format("\\u%04x", c:byte())
  end)) .. '"'
end
local function jenc(v, out)
  local tv = type(v)
  if tv == "table" then
    if #v > 0 or next(v) == nil then
      out[#out + 1] = "["
      for i = 1, #v do if i > 1 then out[#out + 1] = "," end jenc(v[i], out) end
      out[#out + 1] = "]"
    else
      out[#out + 1] = "{"
      local keys = {}
      for k in pairs(v) do keys[#keys + 1] = tostring(k) end
      table.sort(keys)
      for i, k in ipairs(keys) do
        if i > 1 then out[#out + 1] = "," end
        out[#out + 1] = jstr(k) .. ":"
        jenc(v[k], out)
      end
      out[#out + 1] = "}"
    end
  elseif tv == "number" then
    if v ~= v or v == math.huge or v == -math.huge then out[#out + 1] = "0" else out[#out + 1] = string.format("%.3f", v):gsub("%.?0+$", "") end
  elseif tv == "boolean" then out[#out + 1] = v and "true" or "false"
  elseif tv == "nil" then out[#out + 1] = "null"
  else out[#out + 1] = jstr(v) end
end

---------------------------------------------------------------------------------------------------- dump
local STRATA = { BACKGROUND = 1, LOW = 2, MEDIUM = 3, HIGH = 4, DIALOG = 5, FULLSCREEN = 6, FULLSCREEN_DIALOG = 7, TOOLTIP = 8 }
local LAYER = { BACKGROUND = 1, BORDER = 2, ARTWORK = 3, OVERLAY = 4, HIGHLIGHT = 5 }
local function strataOf(f)
  local n = 0
  while f and n < 40 do local s = rawget(f, "__strata") if s then return STRATA[tostring(s):upper()] or 3 end f = rawget(f, "__parent") n = n + 1 end
  return 3
end
local function levelOf(f)
  local add, n = 0, 0
  while f and n < 40 do local l = rawget(f, "__level") if l then return l + add end f = rawget(f, "__parent") add = add + 1 n = n + 1 end
  return add
end
local function visible(f)
  local n = 0
  while f and n < 40 do
    if rawget(f, "__shown") == false then return false end
    f = rawget(f, "__parent") n = n + 1
  end
  return true
end
local function alphaOf(f)
  local a, n = 1, 0
  while f and n < 40 do
    a = a * (tonumber(rawget(f, "__alpha")) or 1)
    if rawget(f, "__ignoreAlpha") then break end
    f = rawget(f, "__parent") n = n + 1
  end
  return a
end
local function topOf(f)
  local UIP, n = rawget(_G, "UIParent"), 0
  while f and n < 40 do local p = rawget(f, "__parent") if p == UIP or p == nil then return f end f = p n = n + 1 end
  return f
end
local function runsOf(text, base)
  local runs, stack, cur, buf = {}, {}, nil, {}
  local function flush() if #buf > 0 then runs[#runs + 1] = { s = table.concat(buf), c = cur or false } buf = {} end end
  local i, n = 1, #text
  while i <= n do
    local ch = text:sub(i, i)
    if ch == "|" then
      local nx = text:sub(i + 1, i + 1)
      if nx == "c" and text:sub(i + 2, i + 9):match("^%x%x%x%x%x%x%x%x$") then
        flush() stack[#stack + 1] = cur or false
        local h = text:sub(i + 2, i + 9)
        cur = { tonumber(h:sub(3, 4), 16) / 255, tonumber(h:sub(5, 6), 16) / 255, tonumber(h:sub(7, 8), 16) / 255, 1 }
        i = i + 10
      elseif nx == "r" then flush() cur = table.remove(stack) or nil i = i + 2
      elseif nx == "n" then buf[#buf + 1] = "\n" i = i + 2
      elseif nx == "T" or nx == "A" then
        local j = text:find(nx == "T" and "|t" or "|a", i + 2, true)
        if j then flush() runs[#runs + 1] = { icon = (text:sub(i + 2, j - 1):match("^([^:]*)") or "") } i = j + 2 else buf[#buf + 1] = ch i = i + 1 end
      elseif nx == "H" then local j = text:find("|h", i + 2, true) i = j and (j + 2) or (i + 2)
      elseif nx == "h" then i = i + 2
      elseif nx == "|" then buf[#buf + 1] = "|" i = i + 2
      else buf[#buf + 1] = ch i = i + 1 end
    else buf[#buf + 1] = ch i = i + 1 end
  end
  flush()
  return runs
end

function R.Dump()
  local M, g = R.M, geo()
  local PXA = opts.h / 768                         -- real pixels per abs unit
  local SH = g.absH
  local function px(l, b, w, h) return { l * PXA, (SH - b - h) * PXA, w * PXA, h * PXA } end
  local frames = {}
  local list, inList = {}, {}
  for i = 1, #M.frames do list[#list + 1] = M.frames[i] inList[M.frames[i]] = true end
  for _, f in ipairs(R.schem) do if not inList[f] then list[#list + 1] = f inList[f] = true end end
  for i = 1, #list do
    local f = list[i]
    local schem = rawget(f, "__schem")
    local ours = (rawget(f, "__src") ~= nil) and not rawget(f, "__occ")
    if (ours or schem) and rawget(f, "__kind") ~= "GameTooltip" and visible(f) and alphaOf(f) > 0.01 then
      local l, b, w, h = rectOf(f)
      if l then frames[#frames + 1] = { f = f, l = l, b = b, w = w, h = h, strata = strataOf(f), level = levelOf(f), seq = rawget(f, "__seq") or i } end
    end
  end
  table.sort(frames, function(a, b2)
    if a.strata ~= b2.strata then return a.strata < b2.strata end
    if a.level ~= b2.level then return a.level < b2.level end
    return a.seq < b2.seq
  end)
  local function clipOf(f)
    local p, n, clip = rawget(f, "__parent"), 0, nil
    while p and n < 40 do
      if rawget(p, "__clips") then
        local l, b, w, h = rectOf(p)
        if l then
          local c = px(l, b, w, h)
          if clip then
            local x0, y0 = math.max(clip[1], c[1]), math.max(clip[2], c[2])
            local x1, y1 = math.min(clip[1] + clip[3], c[1] + c[3]), math.min(clip[2] + clip[4], c[2] + c[4])
            clip = { x0, y0, math.max(0, x1 - x0), math.max(0, y1 - y0) }
          else clip = c end
        end
      end
      p = rawget(p, "__parent") n = n + 1
    end
    return clip
  end
  local ops, tops = {}, {}
  for _, e in ipairs(frames) do
    local f = e.f
    local alpha = alphaOf(f)
    local es = effScale(f)
    local clip = clipOf(f)
    local rect = px(e.l, e.b, e.w, e.h)
    local name = rawget(f, "__name")
    local top = topOf(f)
    if top == f and e.w > 2 and e.h > 2 then
      tops[#tops + 1] = { name = name or "?", rect = rect, alpha = alpha, src = rawget(f, "__src") or "", strata = e.strata }
    end
    local schem = rawget(f, "__schem")
    if schem then
      ops[#ops + 1] = { k = "schem", r = rect, label = schem, a = alpha }
    else
      local bd = rawget(f, "__bd")
      if type(bd) == "table" and (e.w > 0 and e.h > 0) then
        local edge = tostring(bd.edgeFile or "")
        ops[#ops + 1] = { k = "bd", r = rect, clip = clip, bg = bd.bgFile and (rawget(f, "__bdc") or WHITE) or false,
          border = (bd.edgeFile and rawget(f, "__bdb")) or false, a = alpha,
          edge = math.max(1, (edge:find("Tooltip") and 1.5 or (tonumber(bd.edgeSize) or 1)) * es * PXA),
          round = edge:find("Tooltip") and 4 or 0 }
      end
      if rawget(f, "__kind") == "StatusBar" and e.w > 0 and e.h > 0 then
        local mn, mx, v = tonumber(rawget(f, "__min")) or 0, tonumber(rawget(f, "__max")) or 1, tonumber(rawget(f, "__value")) or 0
        local k = (mx > mn) and math.max(0, math.min(1, (v - mn) / (mx - mn))) or 0
        local c = rawget(f, "__sbc") or { 0.2, 0.7, 0.2, 1 }
        ops[#ops + 1] = { k = "tex", r = { rect[1], rect[2], rect[3] * k, rect[4] }, clip = clip, c = c, a = alpha }
      end
      local regs = {}
      for _, r in ipairs(rawget(f, "__children") or {}) do
        local kind = rawget(r, "__kind")
        if (kind == "Texture" or kind == "FontString") and rawget(r, "__shown") ~= false then
          local layer = LAYER[tostring(rawget(r, "__layer") or "ARTWORK"):upper()] or 3
          if layer ~= 5 or rawget(f, "__hover") then regs[#regs + 1] = { r = r, layer = layer, sub = rawget(r, "__sub") or 0, seq = rawget(r, "__seq") or 0 } end
        end
      end
      table.sort(regs, function(a, b2)
        if a.layer ~= b2.layer then return a.layer < b2.layer end
        if a.sub ~= b2.sub then return a.sub < b2.sub end
        return a.seq < b2.seq
      end)
      for _, re in ipairs(regs) do
        local r = re.r
        local ra = alpha * (tonumber(rawget(r, "__alpha")) or 1)
        local l, b, w, h = rectOf(r)
        if l and ra > 0.01 then
          local rr = px(l, b, w, h)
          if rawget(r, "__kind") == "Texture" then
            if w > 0.05 and h > 0.05 then
              local vc = rawget(r, "__vc") or WHITE
              local c = rawget(r, "__color")
              local file = rawget(r, "__file") or rawget(r, "__texture")
              if c then
                ops[#ops + 1] = { k = "tex", r = rr, clip = clip, c = { c[1] * vc[1], c[2] * vc[2], c[3] * vc[3], (c[4] or 1) * (vc[4] or 1) }, a = ra,
                  grad = rawget(r, "__grad") or nil }
              elseif type(file) == "string" and file:upper():find("WHITE8X8", 1, true) then
                ops[#ops + 1] = { k = "tex", r = rr, clip = clip, c = vc, a = ra, grad = rawget(r, "__grad") or nil }
              elseif file ~= nil and file ~= "" then
                ops[#ops + 1] = { k = "icon", r = rr, clip = clip, file = tostring(file), vc = vc, a = ra, desat = rawget(r, "__desat") or false,
                  add = (rawget(r, "__blend") == "ADD") or false }
              end
            end
          else
            local text = rawget(r, "__text")
            if M.IsSecret and M.IsSecret(text) then text = M.Unsecret(text) end
            text = tostring(text or "")
            if text ~= "" then
              local size, col = fontOf(r)
              local limit = fsWidthLimit(r, 0)
              ops[#ops + 1] = { k = "text", r = rr, clip = clip, runs = runsOf(text, col), c = col, a = ra,
                size = size * MET.k * es * PXA, lh = lineH(size) * es * PXA, jh = rawget(r, "__jh") or "CENTER", jv = rawget(r, "__jv") or "MIDDLE",
                wrap = (limit ~= nil) and rawget(r, "__wrap") ~= false, bound = limit ~= nil, maxl = rawget(r, "__maxl") or 0,
                outline = tostring(rawget(r, "__fflags") or ""):find("OUTLINE") ~= nil,
                hset = (rawget(r, "__hset") and true) or false }
            end
          end
        end
      end
    end
  end
  local outT = { w = opts.w, h = opts.h, scale = opts.scale, pxPerUnit = opts.scale * opts.h / 768, scene = opts.scene, ops = ops, tops = tops,
    notes = R.notes or {}, chat = {} }
  for i = (R.chat0 or 0) + 1, #M.output do outT.chat[#outT.chat + 1] = tostring(M.output[i]) end
  local buf = {}
  jenc(outT, buf)
  local fh = assert(io.open(opts.out, "w"))
  fh:write(table.concat(buf), "\n")
  fh:close()
  for _, t in ipairs(tops) do
    io.write(("RENDER %-28s x %4d y %4d  %4d×%-4d px  alpha %.2f  %s\n"):format(t.name, t.rect[1], t.rect[2], t.rect[3], t.rect[4], t.alpha, t.src))
  end
end

---------------------------------------------------------------------------------------------------- the scenes
local function scenario(M, ctx)
  local T = rawget(_G, "TreeAI")
  local g = M.geo
  local UIP = rawget(_G, "UIParent")
  R.chat0 = #M.output
  R.notes = {}
  local function note(s) R.notes[#R.notes + 1] = s io.write("RENDER note: ", s, "\n") end
  rawset(_G, "GetPhysicalScreenSize", function() return opts.w, opts.h end)
  g.SetUIScale(opts.scale)
  local PX = opts.scale * opts.h / 768
  local scene = opts.scene
  local function try(fn, ...) if type(fn) == "function" then local ok, a, b = pcall(fn, ...) if not ok then note("erreur : " .. tostring(a)) end return ok and a, b end end

  -- the player's screen (tests/wow/realui_scenario.lua): invisible obstacles, they are in the capture
  local function occupy(name, x0, y0, x1, y1, schem)
    local f = name and rawget(_G, name) or nil
    if type(f) ~= "table" then f = CreateFrame("Frame", name, UIP) end
    f.__parent = UIP
    f.__w, f.__h, f.__shown = (x1 - x0) / PX, (y1 - y0) / PX, true
    f.__points = { { "TOPLEFT", UIP, "TOPLEFT", x0 / PX, -y0 / PX } }
    f.__occ, f.__schem = true, schem
    if schem then R.schem[#R.schem + 1] = f end
    return f
  end
  for _, n in ipairs({ "PlayerFrame", "TargetFrame", "PetFrame", "PartyMemberFrame1", "PartyMemberFrame2", "PartyMemberFrame3",
    "PartyMemberFrame4", "MainMenuBar", "MultiBarBottomLeft", "MultiBarRight", "CastingBarFrame" }) do
    local f = rawget(_G, n)
    if type(f) == "table" then f.__shown = false f.__points = {} end
  end
  occupy(nil, 0, 0, 262, 110) occupy(nil, 262, 0, 525, 110)
  occupy("ShortestPathForeverTrackerFrame", 0, 400, 250, 930)
  if rawget(_G, "ShortestPathForever") == nil then rawset(_G, "ShortestPathForever", { API = {} }) end
  occupy("ChatFrame1", 0, 900, 490, 1125)
  occupy(nil, 810, 18, 1190, 36) occupy(nil, 880, 40, 1120, 245)
  occupy("MinimapCluster", 1780, 30, 2000, 250)
  local mm = rawget(_G, "Minimap")
  if type(mm) == "table" then mm.__parent = MinimapCluster mm.__w, mm.__h = 200 / PX, 200 / PX mm.__points = { { "CENTER", MinimapCluster, "CENTER", 0, 0 } } end
  occupy("ObjectiveTrackerFrame", 1745, 250, 2000, 570)
  occupy(nil, 615, 738, 840, 915) occupy(nil, 740, 840, 1265, 1125) occupy(nil, 1275, 1080, 1850, 1125)
  occupy("BuffFrame", 1590, 0, 1730, 40)

  -- the character: level-30 hunter, group of 4
  M.LeaveCombat()
  M.player.level = 30
  M.player.zone, M.player.subzone = "Orgrimmar", "La Vallée de l'Honneur"
  M.player.resting = true
  M.mapNames[1454] = "Orgrimmar"
  M.pos = { mapID = 1454, x = 0.50, y = 0.60 }
  M.skills = { { name = "Travail du cuir", rank = 116, max = 150 }, { name = "Dépeçage", rank = 140, max = 150 },
    { name = "Cuisine", rank = 94, max = 150 }, { name = "Secourisme", rank = 125, max = 150 } }
  local function item(id, name, t) t.name = name M.AddItem(id, t) end
  item(9101, "Dent ébréchée", { quality = 0, stack = 20, sellPrice = 12 })
  item(9102, "Plastron de plaques terni", { quality = 1, stack = 1, sellPrice = 300, classID = 4, subclassID = 4, equipLoc = "INVTYPE_CHEST" })
  item(9103, "Collier du traqueur", { quality = 2, stack = 1, sellPrice = 900, classID = 4, subclassID = 0, equipLoc = "INVTYPE_NECK", minLevel = 20,
    stats = { ITEM_MOD_AGILITY_SHORT = 6 } })
  item(2319, "Cuir moyen", { stack = 20, sellPrice = 50, classID = 7 })
  item(2604, "Teinture rouge", { stack = 20, sellPrice = 50, classID = 7 })
  item(929, "Potion de soins", { stack = 5, sellPrice = 75, classID = 0, subclassID = 1, minLevel = 12 })
  item(2515, "Flèche acérée", { stack = 200, sellPrice = 0, classID = 6, subclassID = 2 })
  item(2504, "Arc court usé", { stack = 1, sellPrice = 50, classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED" })
  M.inventory[18] = { id = 2504, count = 1, dur = 20, durMax = 60 }
  for b = 0, 4 do M.bags[b].slots = {} end
  M.SetBagItem(0, 1, 6948, 1) M.SetBagItem(0, 2, 929, 3) M.SetBagItem(0, 3, 9101, 7) M.SetBagItem(0, 4, 9102, 1)
  M.SetBagItem(0, 5, 9103, 1) M.SetBagItem(0, 6, 2319, 6) M.SetBagItem(0, 7, 2604, 5) M.SetBagItem(1, 1, 2515, 150)
  M.quests = {
    { header = "Désolace" },
    { id = 801, title = "Chasse de test", level = 39, complete = true, objectives = {} },
    { id = 802, title = "Collection de test", level = 39, objectives = { { text = "Os de test : 0/10", done = false, have = 0, need = 10 } } },
    { id = 803, title = "En selle de test", level = 40, objectives = { { text = "Trouver un éclaireur de test", done = false } } },
  }
  M.group = { { name = "Amie", class = "PRIEST", level = 28, online = true }, { name = "Compagnon", class = "WARRIOR", level = 29, online = true },
    { name = "Camarade", class = "MAGE", level = 30, online = true } }
  local first = (scene == "first_login")
  if not first and T.Tour and T.Tour.Store then T.Tour.Store().done = 999 end
  ctx.fire("PLAYER_ENTERING_WORLD", true, false)
  ctx.fire("GROUP_ROSTER_UPDATE") ctx.fire("QUEST_LOG_UPDATE") ctx.fire("SKILL_LINES_CHANGED") ctx.fire("PLAYER_UPDATE_RESTING")
  ctx.fire("BAG_UPDATE", 0) ctx.fire("BAG_UPDATE_DELAYED")
  if first then
    M.Advance(12, 0.1)                   -- what a new player has on screen 12 s after his first login
    R.Dump()
    return
  end
  M.Advance(30, 0.1)
  local wz = T.GetWizard and T:GetWizard()
  if wz and wz.Hide then wz:Hide() end
  if T.Options and T.Options.CloseWelcome then try(T.Options.CloseWelcome) end
  M.Advance(4, 0.1)
  local Sc, W = T.Shortcuts, T.Window

  local function questing()
    M.player.resting = false
    M.player.zone, M.player.subzone = "Désolace", "Poste de Ghost Walker"
    M.mapNames[1443] = "Désolace"
    M.pos = { mapID = 1443, x = 0.55, y = 0.55 }
    ctx.fire("ZONE_CHANGED_NEW_AREA") ctx.fire("PLAYER_UPDATE_RESTING") ctx.fire("QUEST_LOG_UPDATE")
    M.Advance(6, 0.1)
    local C = T.modules and T.modules.Compass
    if C and C.SetTarget then pcall(C.SetTarget, { mapID = 1443, x = 0.60, y = 0.40, title = "Chasse de test : rendre" }) end
    if Sc and Sc.Do then M.Hw(function() T:UserAction(function() Sc.Do("rendre") end) end) end
    M.Advance(8, 0.1)
  end

  if scene == "idle_town" then
    M.Advance(20, 0.1)
  elseif scene == "questing_on" then
    questing()
  elseif scene == "questing_off" then
    questing()
    if T.SetQuestGuide then T:SetQuestGuide(false) end
    M.Advance(6, 0.1)
  elseif scene == "combat" then
    questing()
    M.EnterCombat() ctx.fire("PLAYER_REGEN_DISABLED")
    M.Advance(8, 0.1)
  elseif scene == "merchant" then
    M.units.npc = { name = "Marchand-Test" }
    M.merchantItems = { { id = 2515, price = 10, stack = 200, maxStack = 200 }, { id = 2321, price = 100, stack = 1, maxStack = 20 } }
    M.merchantRepair = true
    local mf = occupy("MerchantFrame", 20, 130, 380, 610, "Marchand (fenêtre du jeu)")
    mf.IsShown = nil mf.IsVisible = nil
    M.SetMerchant(true)
    M.Advance(4, 0.1)
  elseif scene == "bags" then
    local bf = occupy("ContainerFrameCombinedBags", 1560, 640, 1990, 1060, "Sacs (fenêtre du jeu)")
    bf.__occ = true
    if Sc and Sc.Do then M.Hw(function() T:UserAction(function() Sc.Do("sacs") end) end) end
    ctx.fire("BAG_OPEN", 0)
    M.Advance(4, 0.1)
  elseif scene == "ah" then
    occupy("AuctionHouseFrame", 20, 130, 880, 620, "Hôtel des ventes (fenêtre du jeu)")
    occupy("AuctionFrame", 20, 130, 880, 620, nil).__shown = false
    M.SetAuctionHouse(true)
    M.Advance(5, 0.1)
  elseif scene == "toast" then
    T:ShowAlert({ id = "render", text = "Sacs presque pleins : 2 places", prio = 3 })
    M.Advance(1.5, 0.1)
  elseif scene == "toast2" then
    T:ShowAlert({ id = "render", text = "Quête finie : Chasse de test — va la rendre", prio = 2 })
    M.Advance(1.5, 0.1)
  elseif scene == "pages" then
    local ids = {}
    if W and W.NavPages then for _, p in ipairs(try(W.NavPages) or {}) do ids[#ids + 1] = p.id end end
    for _, extra in ipairs({ "display", "addons", "keys", "profile", "modules", "home", "now" }) do ids[#ids + 1] = extra end
    io.write("RENDER pages: ", table.concat(ids, " "), "\n")
  elseif scene:match("^window_") then
    local id = scene:match("^window_(.+)$")
    if W and W.Open then
      M.Hw(function() T:UserAction(function() if id == "default" then W.Open() else W.Open(id) end end) end)
      M.Advance(3, 0.1)
      note("page ouverte : " .. tostring(W.CurrentPage and (function() local p = W.CurrentPage() return type(p) == "table" and p.id or p end)() or "?"))
    else note("pas de fenêtre") end
  elseif scene == "hub" then
    local H = T.Hub
    if H and H.Toggle then M.Hw(function() T:UserAction(function() H.Toggle() end) end) end
    M.Advance(2, 0.1)
  elseif scene:match("^strip_hover") then
    local s = rawget(_G, "TreeAIStrip")
    if s and s.__scripts and s.__scripts.OnEnter then M.SafeCall("OnEnter", s.__scripts.OnEnter, s) end
    if Sc and Sc.Hover then try(Sc.Hover, true) end
    M.Advance(1, 0.1)
  end
  R.Dump()
end
R.scenario = scenario

---------------------------------------------------------------------------------------------------- driver
local realDofile = dofile
dofile = function(p, ...)   -- luacheck: ignore (the harness loads the mock with dofile: instrument it before the addon loads)
  local res = realDofile(p, ...)
  if type(p) == "string" and p:match("wowmock%.lua$") and type(res) == "table" then instrument(res) end
  return res
end
local profile = "sur"
if opts.scene == "first_login" then profile = "sur" end
if os.getenv("RENDER_PROFILE") then profile = os.getenv("RENDER_PROFILE") end
arg = { [0] = ROOT .. "/tests/wow/harness.lua", "--flavor", "forever", "--no-storm", "--class", "HUNTER", "--level", "30",   -- luacheck: ignore
  "--geometry", ("ready=1,w=%d,h=%d"):format(opts.w, opts.h), "--profile", profile, "--allow", "ShortestPathForever",
  "--allow", "GetPhysicalScreenSize", "--scenario", SELF, "-q", ROOT .. "/wow_addon/TreeAI" }
realDofile(ROOT .. "/tests/wow/harness.lua")
