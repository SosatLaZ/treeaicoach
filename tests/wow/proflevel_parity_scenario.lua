-- Lua side of the planner parity test (tests/wow/test_proflevel.py): reads the cases written by pytest
-- (PROFLEVEL_PARITY_IN = Lua file returning { {name=, inp=, have=, reserve=, vinfo=, price=} }), runs
-- Prof.Engine.Session + Split and writes one canonical line per case to PROFLEVEL_PARITY_OUT.
return function(M, ctx)
  local inPath, outPath = os.getenv("PROFLEVEL_PARITY_IN"), os.getenv("PROFLEVEL_PARITY_OUT")
  ctx.check(inPath and outPath, "parity: PROFLEVEL_PARITY_IN / _OUT set")
  if not (inPath and outPath) then return end
  local P = TreeAI.modules.Prof
  local E = P and P.Engine
  ctx.check(E and E.Session and E.Split, "parity: engine loaded")
  if not E then return end
  local cases = dofile(inPath)
  local function kv(t)
    local keys = {}
    for k in pairs(t or {}) do keys[#keys + 1] = k end
    table.sort(keys)
    local out = {}
    for _, k in ipairs(keys) do out[#out + 1] = k .. "=" .. tostring(t[k]) end
    return table.concat(out, ",")
  end
  local function canon(res, sp)
    local parts = { tostring(res.reached), tostring(res.stop), tostring(res.crafts), string.format("%d", math.floor(res.cost + 0.5)) }
    local st = {}
    for _, x in ipairs(res.seq) do
      if x.kind == "train" then st[#st + 1] = "T" .. x.at .. ":" .. x.rank
      else st[#st + 1] = x.spell .. ":" .. x.from .. ":" .. x.to .. ":" .. x.crafts .. ":" .. x.color .. ":" .. x.colorTo .. ":" .. tostring(x.learn or "-") end
    end
    parts[#parts + 1] = table.concat(st, ";")
    local ln = {}
    for _, l in ipairs(res.learn) do ln[#ln + 1] = l.spell .. ":" .. l.at .. ":" .. l.kind end
    parts[#parts + 1] = table.concat(ln, ";")
    parts[#parts + 1] = kv(res.use)
    parts[#parts + 1] = kv(res.buy)
    parts[#parts + 1] = kv(res.unknown)
    parts[#parts + 1] = kv(sp.bank)
    parts[#parts + 1] = kv(sp.mail)
    local v = {}
    for _, e in ipairs(sp.vendor) do v[#v + 1] = e.id .. ":" .. e.n .. ":" .. e.buy .. ":" .. e.margin .. ":" .. e.unit end
    parts[#parts + 1] = table.concat(v, ";")
    local a = {}
    for _, e in ipairs(sp.ah) do a[#a + 1] = e.id .. ":" .. e.n .. ":" .. e.unit end
    parts[#parts + 1] = table.concat(a, ";")
    local f = {}
    for _, e in ipairs(sp.farm) do f[#f + 1] = e.id .. ":" .. e.n end
    parts[#parts + 1] = table.concat(f, ";")
    return table.concat(parts, "|")
  end
  local fh = io.open(outPath, "w")
  for _, c in ipairs(cases) do
    local res = E.Session(c.inp)
    local sp = E.Split(res, c.have, c.reserve, c.vinfo, c.price)
    fh:write(c.name, "\t", canon(res, sp), "\n")
  end
  fh:close()
  ctx.check(true, "parity: " .. #cases .. " cases written")
end
