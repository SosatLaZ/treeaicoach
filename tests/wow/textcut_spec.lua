-- TreeAI.Cut (Core.lua) — THE text cut of the addon: accented strings cut at EVERY length, with « … », « ... » and
-- a dry cut: the result is always valid UTF-8, never longer than n characters, the start of the text, and the text
-- itself when it fits (the old byte cuts showed « Les loups-garous de la for\195… »). Pure, no WoW call.
-- Usage: lua5.1 tests/wow/textcut_spec.lua [Core.lua]
local core = arg[1] or "wow_addon/TreeAI/Core.lua"
local Cut = dofile(((arg[0] or ""):match("^(.*)[/\\]") or ".") .. "/corecut.lua")(core)
local fails, checks = 0, 0
local function ok(cond, msg)
  checks = checks + 1
  if not cond then fails = fails + 1; print("FAIL " .. msg) end
end

-- strict UTF-8 (no overlong 2-byte lead, no lone continuation byte, no truncated sequence)
local function valid(s)
  local i, n = 1, #s
  while i <= n do
    local c = s:byte(i)
    local w = (c < 0x80 and 1) or (c >= 0xC2 and c <= 0xDF and 2) or (c >= 0xE0 and c <= 0xEF and 3)
      or (c >= 0xF0 and c <= 0xF4 and 4) or nil
    if not w or i + w - 1 > n then return false end
    for j = i + 1, i + w - 1 do
      local d = s:byte(j)
      if d < 0x80 or d > 0xBF then return false end
    end
    i = i + w
  end
  return true
end
local function clen(s) local _, c = s:gsub("[\128-\191]", "") return #s - c end
local function show(s) return (s:gsub("[\128-\255]", function(ch) return ("\\%d"):format(ch:byte()) end)) end

local TEXTS = {
  "Les loups-garous de la forêt",                          -- the reported one: « for\195… » at 27 bytes
  "Élixir élémentaire de l'été éternel à Hautebrande",
  "L'œuf étrange — Cœurs tonnants",
  "àéèêëîïôöùûüç àéèêëîïôöùûüç",                            -- 2-byte letters only: every odd byte cut splits one
  "Marécage d'Âprefange · ≈ 3 min → ★",                   -- 3-byte glyphs (≈ → ★ ·)
  "« Le cœur d'Âprefange » : une tâche très, très longue",
  "Gnoll Rivepatte",
  "",
}
local TAILS = { { nil, 1 }, { "...", 3 }, { "", 0 } }
for _, s in ipairs(TEXTS) do
  local L = clen(s)
  for n = 1, L + 3 do
    for _, tl in ipairs(TAILS) do
      local tail, tw = tl[1], tl[2]
      local r = Cut(s, n, tail)
      local label = ("Cut(%q, %d, %s) = %q"):format(show(s), n, tostring(tail), show(r))
      ok(type(r) == "string", label .. " : a string")
      ok(valid(r), label .. " : valid UTF-8")
      if L <= n then
        ok(r == s, label .. " : fits, untouched")
      else
        ok(clen(r) <= math.max(n, tw), label .. " : at most n characters")
        local t = tail or "…"
        ok(r:sub(-#t) == t or t == "", label .. " : ends with the tail")
        local head = t == "" and r or r:sub(1, #r - #t)
        ok(s:sub(1, #head) == head, label .. " : the start of the text")
        ok(not head:find("%s$"), label .. " : no space before the tail")
        if n > tw then ok(clen(head) >= 1 or s:find("^%s"), label .. " : keeps something") end
      end
    end
  end
end

-- the exact strings players see
ok(Cut("Les loups-garous de la forêt", 27) == "Les loups-garous de la for…", "the reported title, cut at 27")
ok(Cut("Les loups-garous de la forêt", 28) == "Les loups-garous de la forêt", "28 characters fit in 28")
ok(Cut("Élixir de douleur", 3) == "Él…", "accented first letters count as one")
ok(Cut("abc def", 5) == "abc…", "no space before « … »")
ok(Cut("abcdef", 4, "...") == "a...", "ASCII tail for the bug report")
ok(Cut("Crin-de-Chardon", 6, "") == "Crin-d", "dry cut (stored names)")
ok(Cut(nil, 3) == "" and Cut(false, 3) == "" and Cut(12345, 3) == "12…", "nil / false / numbers")
ok(Cut("abc", nil) == "abc", "no n: untouched")
-- escape codes: never half a colour / texture code, an open colour is closed
ok(Cut("|cffff0000Rouge vif et long|r", 15) == "|cffff0000Roug…|r", "open colour closed after the tail")
ok(not Cut("|cffff0000Rouge|r", 6):find("|c", 1, true), "no half colour code")
ok(Cut("x|Tpath:0|t y z w", 6) == "x…", "no half texture code")

print(("textcut_spec: %d checks, %d failures"):format(checks, fails))
os.exit(fails == 0 and 0 or 1)
