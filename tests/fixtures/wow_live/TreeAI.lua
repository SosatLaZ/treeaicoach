-- Fake TreeAIDB for tests (invented names / realm).
TreeAIDB = {
	["schema"] = 1,
	["version"] = "1.0.0",
	["modules"] = {
		["Auction"] = {
			["realms"] = {
				["Royaumetest-Alliance"] = {
					["n"] = 2,
					["items"] = {
						[2589] = {
							["v"] = 13,
							["h"] = { 20398, 90, 100, 40, 3, 5, 24, 20399, 95, 110, 38, 3, 6, 24, 20400, 100, 120, 35, 2, 4, 24 },
						},
						[2318] = {
							["v"] = 15,
							["h"] = { 20399, 300, 320, 10, 1, 1, 24 },
						},
					},
				},
			},
		},
	},
	["chars"] = {
		["Ancien-Royaumetest"] = {
			["summary"] = { ["class"] = "MAGE", ["level"] = 12, ["updated"] = 1700000000 },
		},
		["Testeur-Royaumetest"] = {
			["summary"] = {
				["class"] = "HUNTER", ["className"] = "Chasseur", ["level"] = 26,
				["xp"] = 12000, ["xpMax"] = 25000, ["rested"] = 3000,
				["zone"] = "Zone Test", ["subzone"] = "Camp Factice",
				["money"] = 123456, ["inGroup"] = true, ["groupSize"] = 4,
				["updated"] = 1762500000,
				["session"] = { ["minutes"] = 42, ["xp"] = 12600, ["quests"] = 3, ["levels"] = 0, ["money"] = 5400, ["deaths"] = 1 },
				["fight"] = { ["deaths"] = 2, ["lastDeath"] = { ["killer"] = "Sanglier Factice", ["level"] = 27, ["elite"] = true,
					["advice"] = "Tire-les un par un.", ["zone"] = "Zone Test", ["time"] = 1762499000, ["duration"] = 25, ["startHP"] = 90 } },
				["boost"] = { ["raresSeen"] = 1, ["lastRare"] = "Rare Factice" },
				["quests"] = {
					["count"] = 4, ["complete"] = 1, ["questsToLevel"] = 6, ["next"] = "Rends « Quête Une » au camp.",
					["list"] = {
						{ ["id"] = 101, ["title"] = "Quête Une", ["level"] = 25, ["complete"] = true },
						{ ["id"] = 102, ["title"] = "Vieille Quête", ["level"] = 18 },
						{ ["id"] = 103, ["title"] = "Élite Factice", ["level"] = 27, ["hard"] = true },
						{ ["id"] = 104, ["title"] = "Longue Marche", ["level"] = 25 },
						{ ["id"] = 105, ["title"] = "Quête Normale", ["level"] = 26 },
					},
				},
				["tracker"] = {
					["totalMin"] = 75, ["plan"] = true,
					["route"] = {
						{ ["id"] = 101, ["title"] = "Quête Une", ["ready"] = true, ["zone"] = "Zone Test", ["dist"] = 120, ["xpm"] = 400 },
						{ ["id"] = 105, ["title"] = "Quête Normale", ["done"] = 3, ["total"] = 8, ["xpm"] = 300, ["mates"] = 1 },
						{ ["id"] = 103, ["title"] = "Élite Factice", ["done"] = 0, ["total"] = 1, ["xpm"] = 250 },
						{ ["id"] = 104, ["title"] = "Longue Marche", ["done"] = 0, ["total"] = 10, ["xpm"] = 40 },
					},
					["pickup"] = { ["id"] = 200, ["title"] = "Quête En Chemin", ["giver"] = "PNJ Factice" },
					["easyNearby"] = { { ["id"] = 201, ["title"] = "Petite Course", ["dist"] = 80 } },
				},
				["missions"] = {
					["v"] = 1, ["updated"] = 1762500000, ["size"] = 4, ["noAddon"] = 0,
					["members"] = {
						{ ["name"] = "Testeur", ["class"] = "Chasseur", ["level"] = 26 },
						{ ["name"] = "Alpha", ["class"] = "Prêtre", ["level"] = 25 },
						{ ["name"] = "Bravo", ["class"] = "Guerrier", ["level"] = 26 },
						{ ["name"] = "Charlie", ["class"] = "Mage", ["level"] = 24 },
					},
					["board"] = {
						{ ["id"] = 105, ["title"] = "Quête Normale", ["holders"] = 4, ["done"] = 1, ["pct"] = 40, ["status"] = "Charlie : 8 restants, Bravo : terminé" },
						{ ["id"] = 101, ["title"] = "Quête Une", ["holders"] = 2, ["done"] = 2, ["pct"] = 100, ["ready"] = true },
					},
					["next"] = "Rendre « Quête Une » ensemble",
					["assign"] = { "Alpha loote d'abord" },
					["claims"] = { { ["quest"] = "Quête Normale", ["by"] = "Bravo" } },
					["session"] = {
						["start"] = 1762490000, ["duration"] = 5400, ["turned"] = 7, ["missions"] = 2, ["deaths"] = 1,
						["levels"] = { { ["name"] = "Alpha", ["from"] = 24, ["to"] = 25 } },
						["goal"] = { ["kind"] = "level", ["text"] = "Niveau 30 pour tous", ["pct"] = 35, ["eta"] = 7200 },
						["leaderboard"] = { { ["name"] = "Bravo", ["xp"] = 30000 } },
						["events"] = { "Alpha passe niveau 25" },
					},
				},
				["auction"] = {
					["updated"] = 1762500000, ["neutral"] = false, ["scanAge"] = 600,
					["deals"] = {
						{ ["name"] = "Étoffe Test", ["id"] = 2589, ["kind"] = "flip", ["count"] = 20, ["buy"] = 2000, ["sell"] = 4000,
						  ["profit"] = 1700, ["roi"] = 0.85, ["conf"] = 0.7, ["deposit"] = 156 },
						{ ["name"] = "Cuir Test", ["id"] = 2318, ["kind"] = "vendor", ["count"] = 1, ["buy"] = 10, ["sell"] = 15, ["profit"] = 5 },
					},
					["owned"] = {
						{ ["name"] = "Peau Test", ["id"] = 4234, ["count"] = 5, ["unit"] = 300, ["lowest"] = 250, ["undercut"] = true,
						  ["advice"] = "annuler, reposter 2 pa 49 pc" },
					},
					["thin"] = { "Gemme Rare" },
					["realized"] = { ["today"] = 1200, ["week"] = 5000, ["total"] = 9000, ["top"] = { { ["item"] = "Étoffe Test", ["profit"] = 4000 } } },
					["gold"] = { ["total"] = 123456, ["reserve"] = 20000, ["invested"] = 3000 },
					["budgetLeft"] = 100456,
				},
			},
			["modules"] = {
				["Auction"] = {
					["ledger"] = {
						{ ["type"] = "buy", ["item"] = "Étoffe Test", ["qty"] = 20, ["money"] = 2000, ["day"] = "2026-11-05" },
						{ ["type"] = "sell", ["item"] = "Étoffe Test", ["qty"] = 20, ["money"] = 4000, ["cut"] = 200, ["day"] = "2026-11-06" },
						{ ["type"] = "sell", ["item"] = "Lin Test", ["qty"] = 5, ["money"] = 500, ["cut"] = 25, ["day"] = "2026-11-07" },
					},
				},
			},
		},
	},
}
