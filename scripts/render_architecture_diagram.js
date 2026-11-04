// Renders docs/assets/qasight-architecture-detailed-{dark,light}.png.
// The diagram is drawn as SVG below; edit nodes/edges here, then run from the
// repo root (needs Playwright and network access for the Google Fonts):
//
//   node scripts/render_architecture_diagram.js docs/assets
//
// See docs/Architecture/System Diagram.md.
const { chromium } = require('playwright');
const path = require('path');
const out = process.argv[2];

const C1 = 60, C2 = 420, C3 = 780, W = 300, R = 1150, RW = 230;
const nodes = [];
const n = (cls, x, y, w, h, t, ...sub) => nodes.push({ cls, x, y, w, h, t, sub });
// who uses it
n('user', C1, 40, W, 64, 'React dashboard', 'Vite · React · Tailwind');
n('user', C2, 40, W, 64, 'CI pipeline', 'qasight run --ci → NDJSON');
n('user', C3, 40, W, 64, 'Developer terminal', 'qasight run · report · diff');
// interfaces
n('iface', C1, 160, W, 84, 'REST API', 'qasight_api · FastAPI · /api/v1', 'SSE · one background thread per run');
n('iface', C2, 160, 660, 84, 'qasight CLI', 'cli/runner.py · Typer', 'Rich or NDJSON output · exit codes 0–4');
// core rows
n('core', C1, 320, W, 84, 'Config', 'config/', 'loader · resolver · triggers');
n('hub', C2, 320, W, 84, 'CycleRunService', 'services/cycle_run.py', 'trigger gate → engine → reporters');
n('core', C3, 320, W, 84, 'Reporting', 'reporting/ · report archive', 'Allure · Extent · ReportPortal · TestBeats');
n('core', C1, 490, W, 84, 'Persistence', 'persistence/', 'JsonBackend + DbBackend');
n('core', C2, 490, W, 84, 'Engine', 'engine/', 'run_plan → run_stage · typed events');
n('core', C3, 490, W, 84, 'Framework adapters', 'frameworks/', 'pytest · behave · behavex · command');
n('core', C1, 680, W, 84, 'Insight services', 'services/', 'dashboard KPIs · delta · AI summaries');
n('core', C2, 680, W, 84, 'Run history', 'history/', 'read side: views · queries · links');
n('core', C3, 680, W, 84, 'Repository', 'repository/', 'the only code that opens the DB');
// outside world
n('ext', R, 320, RW, 84, 'Report outputs', 'HTML under', 'static/history/<run_id>/');
n('ext', R, 490, RW, 84, 'Target repo', 'the tests being run', 'one subprocess per stage');
n('ext', R, 594, RW, 64, 'artifacts/', 'run.log · allure · events');
n('ext', R, 680, RW, 84, 'SQL database', 'SQLite (default)', 'Postgres · MySQL');
n('ext', C1, 860, W, 64, 'LLM provider', 'OpenAI or Anthropic (BYOK)');

const edges = [
  ['M210,104 V158', 'HTTP + live SSE', 220, 136, 'start'],
  ['M570,104 V158'], ['M930,104 V158'],
  ['M300,244 V282 H500 V318', 'start runs', 310, 274, 'start'],
  ['M660,244 V318', 'qasight run', 670, 290, 'start'],
  ['M360,362 H418'],
  ['M720,362 H778'],
  ['M1080,362 H1148'],
  ['M570,404 V488', 'run_plan()', 580, 452, 'start'],
  ['M420,532 H362', 'results', 391, 524, 'middle'],
  ['M720,532 H778'],
  ['M1080,532 H1148', 'runs', 1114, 524, 'middle'],
  ['M1040,574 V626 H1148', 'logs · results', 1030, 604, 'end'],
  ['M210,574 V622 H930 V678', 'RunRecord', 560, 614, 'middle'],
  ['M360,722 H418'],
  ['M720,722 H778'],
  ['M1080,722 H1148'],
  ['M210,764 V858'],
  ['M60,202 H44 V722 H58'],
];

function svg() {
  let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1420 1000" width="1420" height="1000">
<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="ah" d="M0,0 L10,5 L0,10 z"/></marker></defs>
<rect class="bg" x="0" y="0" width="1420" height="1000" rx="18"/>
<text class="layer" x="60" y="28">WHO USES IT</text>
<text class="layer" x="60" y="148">INTERFACES</text>
<text class="layer" x="${R}" y="308">OUTSIDE WORLD</text>
<rect class="corebox" x="30" y="262" width="1080" height="540" rx="16"/>
<text class="corelabel" x="60" y="294">qasight · the Python library</text>
`;
  for (const e of edges) {
    s += `<path class="edge" d="${e[0]}" marker-end="url(#a)"/>`;
    if (e[1]) s += `<text class="el" x="${e[2]}" y="${e[3]}" text-anchor="${e[4]}">${e[1]}</text>`;
  }
  s += `<text class="el" transform="translate(38,470) rotate(-90)" text-anchor="middle">dashboard · compare · AI summaries</text>`;
  for (const v of nodes) {
    const cx = v.x + v.w / 2;
    s += `<g class="node ${v.cls}"><rect x="${v.x}" y="${v.y}" width="${v.w}" height="${v.h}" rx="10"/>`;
    const tY = v.sub.length === 2 ? v.y + 31 : v.y + 29;
    s += `<text class="t" x="${cx}" y="${tY}">${v.t}</text>`;
    v.sub.forEach((line, i) => {
      s += `<text class="s" x="${cx}" y="${tY + 21 + i * 17}">${line.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</text>`;
    });
    s += `</g>`;
  }
  const lg = [['user', 'People and systems that use it'], ['iface', 'Interfaces'], ['hub', 'The one run use case'], ['core', 'Core library'], ['ext', 'External systems and files']];
  let lx = 40;
  for (const [c, label] of lg) {
    s += `<g class="node ${c}"><rect x="${lx}" y="962" width="22" height="14" rx="3"/></g><text class="legend" x="${lx + 30}" y="974">${label}</text>`;
    lx += 30 + label.length * 8.6 + 34;
  }
  return s + '</svg>';
}

const themes = {
  dark: { bg: '#1a1f1d', fg: '#e3e8e4', muted: '#9ba79f', edge: '#7f8c85', userF: '#1c2236', userS: '#8d9ce0', ifF: '#13262f', ifS: '#5fb0d6', coreBg: '#16231c', coreS: '#2f5a45', nodeF: '#1a211d', nodeS: '#5cc79c', hubF: '#1f3a2d', hubS: '#7fe0b5', extF: '#221f17', extS: '#b3a780', accent: '#5cc79c' },
  light: { bg: '#ffffff', fg: '#1c2320', muted: '#5a6560', edge: '#7d8a83', userF: '#eef1fb', userS: '#6476c4', ifF: '#e7f2f7', ifS: '#2f7ea3', coreBg: '#eef6f1', coreS: '#9cc7b1', nodeF: '#ffffff', nodeS: '#1f7a5a', hubF: '#d9efe3', hubS: '#1f7a5a', extF: '#f7f5ef', extS: '#a49a7c', accent: '#1f7a5a' },
};
const css = (p) => `
body{margin:0;background:${p.bg}}
.bg{fill:${p.bg};stroke:${p.coreS};stroke-width:1}
.layer{font:500 11px 'JetBrains Mono',monospace;fill:${p.muted};letter-spacing:.1em}
.corebox{fill:${p.coreBg};stroke:${p.coreS};stroke-width:1.5}
.corelabel{font:500 12.5px 'JetBrains Mono',monospace;fill:${p.accent}}
.corelabel.dim{font-size:11px;fill:${p.muted}}
.node rect{stroke-width:1.5}
.user rect{fill:${p.userF};stroke:${p.userS}}
.iface rect{fill:${p.ifF};stroke:${p.ifS}}
.core rect{fill:${p.nodeF};stroke:${p.nodeS}}
.hub rect{fill:${p.hubF};stroke:${p.hubS};stroke-width:2.5}
.ext rect{fill:${p.extF};stroke:${p.extS};stroke-dasharray:5 4}
.t{font:600 16px 'Source Sans 3',sans-serif;fill:${p.fg};text-anchor:middle}
.s{font:11.5px 'JetBrains Mono',monospace;fill:${p.muted};text-anchor:middle}
.edge{fill:none;stroke:${p.edge};stroke-width:1.6}
.ah{fill:${p.edge}}
.el{font:11px 'JetBrains Mono',monospace;fill:${p.muted}}
.legend{font:13px 'JetBrains Mono',monospace;fill:${p.muted}}`;

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' }).catch(() => chromium.launch());
  const page = await browser.newPage({ viewport: { width: 1420, height: 1000 }, deviceScaleFactor: 2 });
  for (const [name, p] of Object.entries(themes)) {
    await page.setContent(`<!doctype html><html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;500&display=block"><style>${css(p)}</style></head><body>${svg()}</body></html>`);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(500);
    await page.locator('svg').screenshot({ path: path.join(out, `qasight-architecture-detailed-${name}.png`), omitBackground: false });
  }
  await browser.close();
})();
