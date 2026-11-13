#!/usr/bin/env node
// tools/wow_render.js — LOOK (5.1): turns the dumps of tools/wow_render.lua into PNG files (Playwright, Chromium).
//
//   node tools/wow_render.js --metrics                      → writes tools/wow_render_metrics.lua (glyph advances)
//   node tools/wow_render.js --scenes idle_town,toast --tag after --out DIR [--bg capture.webp] [--root DIR]
//       [--crop x,y,w,h] [--zoom 2]
//
// One PNG per scene: DIR/<tag>_<scene>.png = the player's capture (when --bg is given; never committed: it shows
// other players' names) with TreeAI's frames drawn over it at the real scale, in draw order. Without --bg: a flat
// dark-brown backdrop. A scene name may carry a crop: « toast@700,120,600,200 ».
'use strict';
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { chromium } = require('playwright');

const args = process.argv.slice(2);
const opt = { scenes: 'idle_town', tag: 'after', out: '.', zoom: 1 };
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === '--metrics') opt.metrics = true;
  else if (a.startsWith('--')) { opt[a.slice(2)] = args[i + 1]; i++; }
}
const TOOLS = __dirname;
const ROOT = path.resolve(opt.root || path.join(TOOLS, '..'));
const FONT = "'Inter', 'Liberation Sans', sans-serif";
const WEIGHT = 500;

function rgba(c, a) {
  const al = (c[3] === undefined ? 1 : c[3]) * (a === undefined ? 1 : a);
  return `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${al.toFixed(3)})`;
}
function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

// an icon of the game → a small tile: a known sign (close, plus, quest marks…) or a muted colour from its name
const GLYPH = [
  [/UI-StopButton|CloseButton|UI-Panel-MinimizeButton/i, '×'], [/PlusButton/i, '+'], [/MinusButton/i, '−'],
  [/AvailableQuestIcon/i, '!'], [/ActiveQuestIcon/i, '?'], [/SizeGrabber/i, '◢'], [/UI-CheckBox-Check|ReadyCheck-Ready/i, '✓'],
  [/ArrowDown|ScrollDown|Arrow-Down/i, '▾'], [/ArrowUp|ScrollUp|Arrow-Up/i, '▴'], [/Search/i, '⌕'],
  [/AlertNew|AlertOther|Ping/i, '!'], [/Lock/i, '●'], [/Gear|EngGizmos|Wrench/i, '⚙'],
];
function iconHtml(op) {
  const [x, y, w, h] = op.r;
  const file = String(op.file);
  const base = file.split(/[\\/:]/).pop();
  let hue = 0;
  for (let i = 0; i < base.length; i++) hue = (hue * 31 + base.charCodeAt(i)) % 360;
  let glyph = null;
  for (const [re, g] of GLYPH) if (re.test(file)) { glyph = g; break; }
  const isIcon = /Interface\\Icons|INV_|Ability_|Spell_|Trade_/i.test(file);
  const a = op.a * (op.vc ? (op.vc[3] === undefined ? 1 : op.vc[3]) : 1);
  const sat = op.desat ? 0 : 38;
  const size = Math.max(6, Math.min(w, h) * 0.8);
  if (glyph && !isIcon) {
    const col = op.vc ? rgba([op.vc[0], op.vc[1], op.vc[2], 1], 1) : '#ddd';
    return `<div style="left:${x}px;top:${y}px;width:${w}px;height:${h}px;opacity:${a};color:${col};font:600 ${size}px/${h}px ${FONT};text-align:center">${glyph}</div>`;
  }
  if (!isIcon) {   // a piece of Blizzard art we cannot draw: a faint tinted tile
    const col = op.vc ? rgba([op.vc[0], op.vc[1], op.vc[2], 0.35], 1) : 'rgba(200,200,200,0.25)';
    return `<div title="${esc(base)}" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px;opacity:${a};background:${col};border-radius:${Math.min(w, h) > 10 ? 2 : 0}px"></div>`;
  }
  const letter = esc((base.replace(/^(INV|Ability|Spell|Trade)_?/i, '').replace(/^(Misc|Holy|Nature|Hunter|Rogue)_?/i, '')[0] || '?').toUpperCase());
  return `<div title="${esc(base)}" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px;opacity:${a};` +
    `background:linear-gradient(135deg,hsl(${hue},${sat}%,42%),hsl(${(hue + 40) % 360},${sat}%,22%));box-shadow:inset 0 0 0 1px rgba(0,0,0,.55);` +
    `color:rgba(255,255,255,.75);font:600 ${Math.max(6, size * 0.62)}px/${h}px ${FONT};text-align:center">${letter}</div>`;
}
function textHtml(op) {
  const [x, y, w, h] = op.r;
  const jh = { LEFT: 'left', CENTER: 'center', RIGHT: 'right' }[op.jh] || 'center';
  let inner = '';
  for (const r of op.runs) {
    if (r.icon !== undefined) {
      inner += `<span style="display:inline-block;width:${op.size}px;height:${op.size}px;vertical-align:-12%;background:rgba(190,170,120,.55);box-shadow:inset 0 0 0 1px rgba(0,0,0,.5)"></span>`;
    } else {
      const s = esc(r.s).replace(/\n/g, '<br>');
      inner += r.c ? `<span style="color:${rgba(r.c, 1)}">${s}</span>` : s;
    }
  }
  const shadow = op.outline ? 'text-shadow:-1px 0 #000,1px 0 #000,0 -1px #000,0 1px #000;' : 'text-shadow:1px 1px 0 rgba(0,0,0,.9);';
  let box;
  if (op.bound) {
    // width fixed by the frame: wrap inside it, or cut with « … »
    const wrap = op.wrap ? 'white-space:normal;overflow-wrap:anywhere;' : 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
    const jv = op.jv === 'TOP' ? 'flex-start' : op.jv === 'BOTTOM' ? 'flex-end' : 'center';
    const hh = op.hset ? `height:${h}px;` : `min-height:${h}px;`;
    const clamp = (op.maxl > 0 && op.wrap) ? `display:-webkit-box;-webkit-line-clamp:${op.maxl};-webkit-box-orient:vertical;overflow:hidden;` : '';
    box = `<div style="left:${x}px;top:${y}px;width:${w}px;${hh}display:flex;align-items:${jv};${op.hset ? 'overflow:hidden;' : ''}">` +
      `<div style="position:static;width:100%;text-align:${jh};${wrap}${clamp}">${inner}</div></div>`;
  } else {
    const tx = jh === 'left' ? x : jh === 'right' ? x + w : x + w / 2;
    const tr = jh === 'left' ? '' : jh === 'right' ? 'transform:translateX(-100%);' : 'transform:translateX(-50%);';
    box = `<div style="left:${tx}px;top:${y}px;white-space:pre;${tr}text-align:${jh}">${inner}</div>`;
  }
  return `<div class="t" style="left:0;top:0;opacity:${op.a};color:${rgba(op.c, 1)};font:${WEIGHT} ${op.size}px/${op.lh}px ${FONT};${shadow}">${box}</div>`;
}
function opHtml(op) {
  const [x, y, w, h] = op.r;
  let html = '';
  if (op.k === 'schem') {
    html = `<div style="left:${x}px;top:${y}px;width:${w}px;height:${h}px;background:rgba(18,16,14,.86);border:1px solid rgba(120,105,80,.8);border-radius:4px;` +
      `color:rgba(200,185,150,.7);font:500 11px/22px ${FONT};text-align:center;box-sizing:border-box">${esc(op.label)}</div>`;
  } else if (op.k === 'bd') {
    const bg = op.bg ? `background:${rgba(op.bg, op.a)};` : '';
    const bw = Math.max(1, Math.round(op.edge));
    const bo = op.border ? `box-shadow:inset 0 0 0 ${bw}px ${rgba(op.border, op.a)};` : '';
    html = `<div style="left:${x}px;top:${y}px;width:${w}px;height:${h}px;${bg}${bo}border-radius:${op.round || 0}px"></div>`;
  } else if (op.k === 'tex') {
    let bg = `background:${rgba(op.c, op.a)};`;
    if (op.grad && op.grad[1] && op.grad[2]) {
      const dir = op.grad[0] === 'HORIZONTAL' ? 'to right' : 'to top';
      bg = `background:linear-gradient(${dir},${rgba(op.grad[1], op.a)},${rgba(op.grad[2], op.a)});`;
    }
    html = `<div style="left:${x}px;top:${y}px;width:${Math.max(w, 0.6)}px;height:${Math.max(h, 0.6)}px;${bg}"></div>`;
  } else if (op.k === 'icon') html = iconHtml(op);
  else if (op.k === 'text') html = textHtml(op);
  if (op.clip && html) {
    const [cx, cy, cw, ch] = op.clip;
    html = `<div style="left:${cx}px;top:${cy}px;width:${cw}px;height:${ch}px;overflow:hidden"><div style="left:${-cx}px;top:${-cy}px;width:${cx + cw}px;height:${cy + ch}px">${html}</div></div>`;
  }
  return html;
}
function pageHtml(dump, bgData) {
  const bg = bgData ? `<img src="${bgData}" style="position:absolute;left:0;top:0;width:${dump.w}px;height:${dump.h}px">`
    : `<div style="left:0;top:0;width:${dump.w}px;height:${dump.h}px;background:radial-gradient(ellipse at 50% 60%,#5a4632 0%,#3a2a1e 45%,#1c1612 100%)"></div>`;
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    html,body{margin:0;padding:0;background:#000}
    #s{position:relative;width:${dump.w}px;height:${dump.h}px;overflow:hidden}
    #s div{position:absolute;box-sizing:border-box}
    #s .t{pointer-events:none}
  </style></head><body><div id="s">${bg}${dump.ops.map(opHtml).join('\n')}</div></body></html>`;
}

async function metrics(browser) {
  const page = await browser.newPage();
  await page.setContent(`<canvas id="c"></canvas>`);
  const adv = await page.evaluate(([font, weight]) => {
    const ctx = document.getElementById('c').getContext('2d');
    ctx.font = `${weight} 1000px ${font}`;
    const out = {};
    const add = (cp) => { out[cp] = Math.round(ctx.measureText(String.fromCodePoint(cp)).width); };
    for (let cp = 32; cp < 127; cp++) add(cp);
    for (let cp = 160; cp < 384; cp++) add(cp);
    for (const cp of [0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2026, 0x2022, 0x2192, 0x2190, 0x2248, 0x00D7, 0x2264, 0x2265, 0x25A1, 0x2713, 0x25B6, 0x25CF]) add(cp);
    return out;
  }, [FONT, WEIGHT]);
  const keys = Object.keys(adv).map(Number).sort((a, b) => a - b);
  let lua = '-- tools/wow_render_metrics.lua — written by « node tools/wow_render.js --metrics » : glyph advances (1/1000 em) of the\n' +
    `-- font the renders are drawn with (${FONT.replace(/'/g, '')}, weight ${WEIGHT}). k = size factor (a WoW size 12 is drawn at 12 × k px).\n` +
    'return { k = 0.94, line = 1.18, fallback = 560, adv = {\n';
  let line = ' ';
  for (const k of keys) {
    const item = ` [${k}] = ${adv[k]},`;
    if (line.length + item.length > 116) { lua += line + '\n'; line = ' '; }
    line += item;
  }
  lua += line + '\n} }\n';
  fs.writeFileSync(path.join(TOOLS, 'wow_render_metrics.lua'), lua);
  await page.close();
  console.log('metrics: ' + keys.length + ' glyphs');
}

(async () => {
  const browser = await chromium.launch();
  try {
    if (opt.metrics) { await metrics(browser); return; }
    fs.mkdirSync(opt.out, { recursive: true });
    let bgData = null;
    if (opt.bg && fs.existsSync(opt.bg)) {
      const ext = path.extname(opt.bg).slice(1).toLowerCase();
      bgData = `data:image/${ext === 'jpg' ? 'jpeg' : ext};base64,` + fs.readFileSync(opt.bg).toString('base64');
    }
    const work = opt.work || fs.mkdtempSync(path.join(require('os').tmpdir(), 'wowrender-'));
    fs.mkdirSync(work, { recursive: true });
    for (const spec of String(opt.scenes).split(',').filter(Boolean)) {
      const [scene, cropSpec] = spec.split('@');
      const json = path.join(work, `${opt.tag}_${scene}.json`);
      let log = '';
      try {
        log = execFileSync('lua5.1', [path.join(TOOLS, 'wow_render.lua'), '--scene', scene, '--out', json, '--root', ROOT],
          { cwd: ROOT, encoding: 'utf8', env: Object.assign({}, process.env), stdio: ['ignore', 'pipe', 'pipe'] });
      } catch (e) { log = String(e.stdout || '') + String(e.stderr || ''); }
      const lines = log.split('\n').filter((l) => /^(RENDER|ERROR|VIOLATION|RESULT)/.test(l));
      console.log(`== ${opt.tag}_${scene}\n` + lines.join('\n'));
      if (!fs.existsSync(json)) continue;
      const dump = JSON.parse(fs.readFileSync(json, 'utf8'));
      const zoom = Number(opt.zoom) || 1;
      const page = await browser.newPage({ viewport: { width: dump.w, height: dump.h }, deviceScaleFactor: zoom });
      await page.setContent(pageHtml(dump, bgData));
      await page.evaluate(() => document.fonts.ready);
      const file = path.join(opt.out, `${opt.tag}_${scene}.png`);
      const crop = (cropSpec || opt.crop || '').split(/[x,]/).map(Number);
      if (crop.length === 4 && crop.every((n) => !Number.isNaN(n))) {
        await page.screenshot({ path: file, clip: { x: crop[0], y: crop[1], width: crop[2], height: crop[3] } });
      } else await page.screenshot({ path: file });
      await page.close();
      console.log('   → ' + file);
    }
  } finally { await browser.close(); }
})().catch((e) => { console.error(e); process.exit(1); });
