// Render a .bpmn file with bpmn-js in headless Chromium: PNG/SVG + import warnings.
// Usage: node tools/render.mjs in.bpmn out.png [out.svg]
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require('playwright')); }
catch {
  const { execSync } = require('node:child_process');
  const globalRoot = process.env.PLAYWRIGHT_GLOBAL || execSync('npm root -g').toString().trim();
  ({ chromium } = require(path.join(globalRoot, 'playwright')));
}

const here = path.dirname(fileURLToPath(import.meta.url));
const vendor = path.join(here, '..', 'frontend', 'vendor');
const [, , input, outPng, outSvg] = process.argv;
const xml = readFileSync(input, 'utf8');

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const page = await browser.newPage({ viewport: { width: 1800, height: 1200 }, deviceScaleFactor: 1 });
await page.setContent(`<html><head>
<link rel="stylesheet" href="file://${vendor}/assets/diagram-js.css">
<link rel="stylesheet" href="file://${vendor}/assets/bpmn-js.css">
<style>html,body,#c{margin:0;width:100%;height:100%;background:#fff}</style></head>
<body><div id="c"></div></body></html>`);
await page.addScriptTag({ path: path.join(vendor, 'bpmn-modeler.production.min.js') });
const res = await page.evaluate(async (xml) => {
  const m = new BpmnJS({ container: '#c' });
  try {
    const { warnings } = await m.importXML(xml);
    const canvas = m.get('canvas');
    canvas.zoom('fit-viewport');
    const { svg } = await m.saveSVG();
    const vb = canvas.viewbox();
    return { ok: true, warnings: warnings.map(w => w.message), svg, inner: vb.inner };
  } catch (e) {
    return { ok: false, error: String(e.message || e), warnings: (e.warnings || []).map(w => w.message) };
  }
}, xml);
if (res.ok) {
  const w = Math.min(4000, Math.ceil(res.inner.width + 80)), h = Math.min(3000, Math.ceil(res.inner.height + 80));
  await page.setViewportSize({ width: Math.max(w, 600), height: Math.max(h, 300) });
  await page.evaluate(() => {});
  await page.setContent(`<html><body style="margin:0;background:#fff">${res.svg}</body></html>`);
  if (outPng) await page.screenshot({ path: outPng, fullPage: true });
  if (outSvg) writeFileSync(outSvg, res.svg);
}
await browser.close();
console.log(JSON.stringify({ ok: res.ok, error: res.error, warnings: res.warnings }));
process.exit(res.ok && res.warnings.length === 0 ? 0 : 1);
