# Third-party notices

TreeAI Coach includes code adapted from the open-source projects below. Their licenses require that
the copyright and permission notices be kept with any copy of that code.

## League Client (LCU) access — `treeaicoach/lcu.py`

Lockfile / `LeagueClientUx.exe` command-line parsing, basic-auth connection and the match-history
endpoint choice are adapted from:

- **lcu-driver** — https://github.com/sousa-andre/lcu-driver (MIT)
- **Willump** — https://github.com/elliejs/Willump (MIT)
- **League Akari** — https://github.com/LeagueAkari/LeagueAkari (MIT)

### lcu-driver

```
MIT License

Copyright (c) 2019 André Sousa

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### willump

```
MIT License

Copyright (c) 2021 Eleanor Silver

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

```

### LeagueAkari

```
MIT License

Copyright (c) 2026 Hanxven

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

```

## Minimap tracking — `treeaicoach/tracker.py` (and its use in `gank.py` / `fog_tracker.py`)

The per-champion constant-velocity Kalman filter, the occlusion-aware "stacked" hold (an icon that
vanishes next to another icon is held at that icon instead of being declared lost) and the
"champion locker" confirmation (frame density + mean confidence before an anonymous track counts)
are adapted from:

- **DeepestLeague** — https://github.com/bsowlx/DeepestLeague (MIT),
  `scripts/pipeline/run_minimap_pipeline.py` (`_KalmanTrack`, `_apply_kalman`, `ChampionLocker`)

### DeepestLeague

```
MIT License

Copyright (c) 2026 Baiastan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Game data sources — `treeaicoach/assets/*.json`, `tools/fetch_*.py`, `tools/validate_data.py`

TreeAI Coach isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or
anyone officially involved in producing or managing Riot Games properties. Riot Games, and all
associated properties are trademarks or registered trademarks of Riot Games, Inc.

| Data | Source | Terms |
|---|---|---|
| Items, champions, base stats, names (fr_FR / en_US) | Riot **Data Dragon** (ddragon.leagueoflegends.com) | Riot Developer Policies / "Legal Jibber Jabber" |
| Champion playstyle ratings, damage / attack type, recommended positions; Summoner's Rift CLASSIC shop lists; Faelight pads and camp positions | Riot game / client files served by **CommunityDragon** (raw.communitydragon.org) | Same Riot policy; CommunityDragon is a community project, not affiliated with Riot |
| Champion class roles (JUGGERNAUT, CATCHER...), fallback ratings | **Meraki Analytics** `champions.json` (github.com/meraki-analytics/lolstaticdata), cached as they ask | Code: MIT (Copyright (c) 2020 Meraki Analytics, LLC); data derived from the League of Legends Wiki, **CC BY-SA 3.0** |
| Class roles of Locke / Zaahen, Faelight descriptions, 2026 objective timers (cross-check) | **League of Legends Wiki** (wiki.leagueoflegends.com), read by hand, never crawled (its robots.txt disallows `?action=` URLs) | **CC BY-SA 3.0** — https://creativecommons.org/licenses/by-sa/3.0/ |
| Power curve, level-6 spike, waveclear, splitpush, sustain, lane classes, matchup tips, build preferences, classic ward spots | TreeAI Coach curation (`tools/data/champion_curation.json`, `assets/matchups.json`, `tools/fetch_builds.py`, `assets/ward_spots.json`) | Project license |

Because `assets/champion_meta.json` contains data adapted from the League of Legends Wiki (via
Meraki), **that file is shared under CC BY-SA 3.0** (attribution: League of Legends Wiki
contributors, Meraki Analytics); its header repeats this. Every profile field records its source
(`src`: dd = Data Dragon, rc = Riot client data, rr = Riot rune recommendations, mk = Meraki,
wk = wiki, cu = curation, ru = rule). No site whose terms forbid automated access (op.gg, u.gg,
Mobalytics...) is scraped.
