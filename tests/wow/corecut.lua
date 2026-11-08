-- Core's TreeAI.Cut (THE text cut of the addon: n UTF-8 characters, never half an accented letter) for the specs
-- that load one module without Core (plain_spec, abandon_spec, textcut_spec): the real function, read from
-- Core.lua as it is — never a copy that could drift. Usage: local Cut = dofile("tests/wow/corecut.lua")(corePath)
return function(core)
  local f = assert(io.open(core, "rb"))
  local src = f:read("*a")
  f:close()
  local block = src:match("\n(local function charEnd.-\nTreeAI%.Cut = cutText)\n")
  assert(block, core .. ": the TreeAI.Cut block (local function charEnd … TreeAI.Cut = cutText) was not found")
  local env = { strfind = string.find, strsub = string.sub, strgsub = string.gsub, tostring = tostring,
    tonumber = tonumber, TreeAI = {} }
  local chunk = assert(loadstring(block, "=Core.lua (TreeAI.Cut)"))
  setfenv(chunk, env)
  chunk()
  return assert(env.TreeAI.Cut, "TreeAI.Cut not defined by the block")
end
