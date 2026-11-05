// Renders the QASight logo files: the web UI's open eye next to the QASight
// name. Run from the repo root (needs Playwright and
// network access for the Google Font):
//
//   node scripts/render_logo.js docs/assets
//
// Writes qasight-mark.svg, qasight-logo-dark.png, qasight-logo-light.png,
// qasight-social-preview.png and logo.png (the docs site logo and favicon).
// See docs/Specs & ADRs/QASight Logo - 2026-11-05.md.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const out = process.argv[2] || 'docs/assets';

// The same eye the web UI draws (frontend/src/components/mascot/EyeLogo.tsx),
// with the green iris it shows on a healthy Project health page.
const IRIS = '#34d399'; // success-400 in frontend/tailwind.config.ts
const streaks = Array.from({ length: 16 }, (_, i) => {
  const a = (i * Math.PI * 2) / 16;
  const [c, s] = [Math.cos(a), Math.sin(a)];
  const f = (v) => v.toFixed(2);
  return `<line x1="${f(50 + c * 11.5)}" y1="${f(30 + s * 11.5)}" x2="${f(50 + c * 16.5)}" y2="${f(30 + s * 16.5)}"/>`;
}).join('');

// On a light background the white of the eye gets a thin ink-200 outline.
function mark(outline = false) {
  const stroke = outline ? ' stroke="#cbd2e1" stroke-width="1.6" stroke-linejoin="round"' : '';
  return `<path d="M3 30 Q50 -10 97 30 Q50 70 3 30 Z" fill="#f5f7fb"${stroke}/><circle cx="50" cy="30" r="20" fill="${IRIS}"/><g stroke="#ffffff" stroke-opacity="0.32" stroke-width="2.6" stroke-linecap="round">${streaks}</g><circle cx="50" cy="30" r="9" fill="#0b1026"/><circle cx="53.6" cy="26.4" r="2.6" fill="#ffffff"/>`;
}

const markSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60" role="img" aria-label="QASight"><title>QASight</title>${mark()}</svg>\n`;

const FONT = `<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;700;800&display=swap" rel="stylesheet">`;
const page = (body, css) => `<!doctype html><html><head>${FONT}<style>
  *{margin:0;box-sizing:border-box} body{font-family:'Plus Jakarta Sans',sans-serif} ${css}
</style></head><body>${body}</body></html>`;

const wordmark = (ink, outline) =>
  page(
    `<div id="c"><svg viewBox="0 0 100 60" width="300" height="180">${mark(outline)}</svg><span>QASight</span></div>`,
    `#c{display:inline-flex;align-items:center;gap:44px;padding:24px}
     span{font-size:148px;font-weight:700;letter-spacing:-2px;color:${ink};line-height:1}`,
  );

const social = page(
  `<div id="c"><div class="row"><svg viewBox="0 0 100 60" width="230" height="138">${mark()}</svg><span>QASight</span></div>
   <h1>Run and track your tests, compare and export results,<br>and catch the flaky ones.</h1>
   <p>Get deeper insight with AI, using your own model.</p><code>pip install qasight</code></div>`,
  `#c{width:1280px;height:640px;background:radial-gradient(ellipse at 50% 0%,#1a2352 0%,#0b1437 45%,#060b26 100%);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:34px;color:#f0f3fa;text-align:center}
   .row{display:flex;align-items:center;gap:34px} .row span{font-size:112px;font-weight:700;letter-spacing:-2px;line-height:1}
   h1{font-size:36px;font-weight:700;max-width:1200px;line-height:1.35} p{font-size:28px;font-weight:500;color:#a0aec0;margin-top:-10px;line-height:1.5} code{font:600 22px ui-monospace,Menlo,monospace;color:#4a9bff;background:#111a45;border:1px solid #252f66;border-radius:10px;padding:8px 16px}`,
);

const favicon = page(
  `<div id="c"><svg viewBox="0 0 100 60" width="440" height="264">${mark()}</svg></div>`,
  `#c{width:512px;height:512px;display:flex;align-items:center;justify-content:center;background:#0b1437;border-radius:112px}`,
);

(async () => {
  fs.mkdirSync(out, { recursive: true });
  fs.writeFileSync(path.join(out, 'qasight-mark.svg'), markSvg);
  const browser = await chromium.launch();
  const tab = await browser.newPage({ deviceScaleFactor: 1 });
  const shots = [
    ['qasight-logo-dark.png', wordmark('#f0f3fa', false), true],
    ['qasight-logo-light.png', wordmark('#0b1437', true), true],
    ['qasight-social-preview.png', social, false],
    ['logo.png', favicon, true],
  ];
  for (const [name, html, omitBackground] of shots) {
    await tab.setContent(html, { waitUntil: 'networkidle' });
    await tab.evaluate(() => document.fonts.ready);
    await tab.locator('#c').screenshot({ path: path.join(out, name), omitBackground });
  }
  await browser.close();
})();
