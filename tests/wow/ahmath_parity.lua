-- ahmath_parity.lua — runs Modules/AHMath.lua (the desk's maths) on the cases test_ahmath_parity.py wrote, one JSON
-- line per case, so the Python twin (treeaicoach/wow/ahmath.py) is checked against the addon to the copper.
-- usage: lua5.1 tests/wow/ahmath_parity.lua <AHMath.lua> <cases.lua>
local argv = arg or {}
_G.TreeAI = {}
dofile(argv[1])
local X = _G.TreeAI.AHMath
local cases = dofile(argv[2])

local function isArray(t)
  local n = 0
  for _ in pairs(t) do n = n + 1 end
  for i = 1, n do if t[i] == nil then return false end end
  return true, n
end
local enc
local function str(s)
  return '"' .. s:gsub('[%c"\\]', function(c)
    if c == '"' then return '\\"' elseif c == "\\" then return "\\\\" end
    return ("\\u%04x"):format(c:byte())
  end) .. '"'
end
function enc(v)
  local t = type(v)
  if v == nil then return "null"
  elseif t == "boolean" then return v and "true" or "false"
  elseif t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    if v == math.floor(v) and math.abs(v) < 2 ^ 53 then return ("%d"):format(v) end
    return ("%.17g"):format(v)
  elseif t == "string" then return str(v)
  elseif t == "table" then
    local arr, n = isArray(v)
    local parts = {}
    if arr and n > 0 then
      for i = 1, n do parts[i] = enc(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local keys = {}
    for k in pairs(v) do keys[#keys + 1] = k end
    table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
    for _, k in ipairs(keys) do parts[#parts + 1] = str(tostring(k)) .. ":" .. enc(v[k]) end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "null"
end
for _, c in ipairs(cases) do
  local fn = X[c.fn]
  local r = { n = 0 }
  local ok, err = pcall(function()
    local function pack(...) return { n = select("#", ...), ... } end
    r = pack(fn(unpack(c.args, 1, c.args.n)))
  end)
  if not ok then io.write('{"error":', str(tostring(err)), '}\n')
  else
    local parts = {}
    for i = 1, r.n do parts[i] = r[i] == nil and "null" or enc(r[i]) end
    io.write("[", table.concat(parts, ","), "]\n")
  end
end
