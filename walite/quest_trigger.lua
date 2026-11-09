-- TreeAI Lite · Quêtes — déclencheur personnalisé (TSU). Événements : voir build_walite.py.
function(allstates, event)
  local E = aura_env
  if event ~= "TREEAI_LITE_TICK" then E.dirty = true end
  if InCombatLockdown and InCombatLockdown() then return false end -- en combat : on garde l'affichage
  if not E.Build then return false end
  local txt = E.Build()
  local s = allstates[""]
  if not txt then
    if s and s.show then s.show, s.changed = false, true return true end
    return false
  end
  if s and s.show and s.name == txt then return false end
  allstates[""] = { show = true, changed = true, name = txt }
  return true
end
