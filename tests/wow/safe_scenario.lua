-- safe_scenario.lua — REVIEW: « mode sûr » (3.9.9). Run with --profile sur (nothing seeded: the one-time migration
-- runs as for a new player). Only the robust modules run, every other module is « bêta » (off, one click to turn
-- on: « Plus → Modules » and the minimap hub), one-line notice, migration done once, « tout » still reachable.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local function mod(n) return T.modules[n] end
  check(TreeAIDB.profileV == T.PROFILE_V and T:ModuleProfile() == "sur", "migration: mode sûr set once")
  for n in pairs(T.SAFE_MODULES) do
    local m = mod(n)
    if m and m._classOK ~= false then check(m._active == true, n .. " on in mode sûr") end
  end
  local nBeta = 0
  for _, n in ipairs(T.moduleOrder) do
    if T:IsBeta(n) then
      nBeta = nBeta + 1
      check(mod(n)._active ~= true and mod(n).db.enabled == false, n .. " (bêta) off in mode sûr")
    end
  end
  check(nBeta >= 26, "most modules are bêta (" .. nBeta .. ")")   -- (4.0.1) ZonePlan, PathDraw, Highlight : mode sûr
  check(not T:IsBeta("Quests") and not T:IsBeta("Compass") and not T:IsBeta("Speed") and T:IsBeta("Auction"), "safe set")

  -- one-line notice
  M.Advance(5, 0.5)
  local notice
  -- (4.1 LAYOUT) avis non demandé : seulement en tête de la page « Fonctions » (ownPage), jamais dans « Maintenant »
  for _, e in ipairs(T:GetTabLines("now")) do if e.id == "core:safe" then notice = e end end
  check(notice == nil, "« Mode sûr » : pas dans « Maintenant »")
  for _, e in ipairs(T:GetSuggestions()) do if e.id == "core:safe" then notice = e end end
  check(notice and notice.ownPage == "modules" and notice.text == "Mode sûr : les fonctions bêta sont à activer dans le menu"
    and notice.onClick, "notice « Mode sûr : … » (page « Fonctions »)")

  -- Plus → Modules: bêta tag, one click toggles
  local ml, boost, head = T:GetTabLines("modules"), nil, nil
  for _, e in ipairs(ml) do
    if e.id == "mod:Boost" then boost = e end
    if e.id == "core:profile" then head = e end
  end
  check(head and head.text:find("Mode sûr", 1, true), "modules tab: profile line says mode sûr")
  check(boost and boost.text:find("bêta", 1, true) and boost.text:find("OFF", 1, true), "Boost line: OFF + bêta")
  T:UserAction(boost.onClick)
  check(mod("Boost")._active == true, "one click: Boost on (no /reload)")
  check(T:ModuleProfile() == "sur", "still mode sûr after one bêta module")
  T:SetModuleEnabled("Boost", false)

  -- minimap hub: « Fonctions bêta » list, click keeps the menu open and toggles
  local H = T.Hub
  if H and H.Entries then
    local entry
    for _, e in ipairs(H.Entries()) do if e.id == "profil" then entry = e end end
    check(entry and entry.title:find("bêta", 1, true), "hub: « Fonctions bêta » entry")
    H.Toggle()
    for _, e in ipairs(H.layout) do if e.id == "profil" then e.button:Click("LeftButton") end end
    check(H.IsOpen() and H.mode == "beta", "hub: bêta list opened, menu still open")
    local row
    for _, e in ipairs(H.layout) do if e.id == "beta:Fight" then row = e end end
    check(row, "hub: Fight listed as bêta")
    if row then
      row.button:Click("LeftButton")
      check(mod("Fight")._active == true and H.IsOpen(), "hub: click = Fight on, menu stays open")
    end
    T:SetModuleEnabled("Fight", false)
    H.Close()
  end

  -- migration runs once: the player's choice « tout » survives a new login
  T:SetModuleProfile("tout")
  check(mod("Boost")._active == true, "tout: bêta modules on")
  T:_ApplyProfileOnce()
  check(T:ModuleProfile() == "tout", "migration not repeated")
  T:SetModuleProfile("sur")
  check(mod("Boost")._active ~= true and mod("Quests")._active == true, "back to mode sûr")
  M.Advance(10, 0.5)

  -- (4.0.1) ZonePlan, PathDraw, Highlight (+ Glow, la bibliothèque des lueurs) : mode sûr. Un joueur 4.0 en mode sûr
  -- les reçoit UNE fois, sauf ceux qu'il a coupés lui-même (un clic = db.modChoice)
  for n in pairs(T.SAFE_ADDED) do check(T.SAFE_MODULES[n] and not T:IsBeta(n) and mod(n)._active == true, n .. " on in mode sûr (4.0.1)") end
  check(T.SAFE_MODULES.Glow, "Glow (lueurs de Highlight) in the safe set")
  local db = TreeAIDB
  for _, n in ipairs({ "ZonePlan", "PathDraw" }) do mod(n).db.enabled = false; T._ModuleDisable(mod(n)) end   -- état 4.0 (bêta)
  db.safeV, db.modChoice = nil, { PathDraw = false }                       -- PathDraw coupé par le joueur en 4.0.1
  T:_ApplyProfileOnce()
  check(mod("ZonePlan").db.enabled == true and mod("ZonePlan")._active == true, "4.0 -> 4.0.1: ZonePlan turned on once")
  check(mod("PathDraw").db.enabled == false and mod("PathDraw")._active ~= true, "4.0 -> 4.0.1: PathDraw kept off (player's choice)")
  check(db.safeV == T.SAFE_V and T:ModuleProfile() == "sur", "4.0.1 migration recorded, still mode sûr")
  mod("ZonePlan").db.enabled = false; T._ModuleDisable(mod("ZonePlan"))
  T:_ApplyProfileOnce()
  check(mod("ZonePlan").db.enabled == false, "4.0.1 migration runs once")
  T:SetModuleEnabled("ZonePlan", true); T:SetModuleEnabled("PathDraw", true)
  check(db.modChoice.ZonePlan == true and mod("PathDraw")._active == true, "a click records the player's choice")
  M.Advance(5, 0.5)
end
