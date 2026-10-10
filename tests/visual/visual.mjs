// Visual regression: renders the deterministic test site (tests/viewer/
// make_site.py) from fixed cameras and sun positions and compares the WebGL
// frame with the baselines in tests/visual/baseline/.
//
// Offline (tile requests aborted, so the ground shows the DEM hillshade),
// SwiftShader WebGL, the canvas only (no HTML overlays, so fonts do not
// matter). A view fails when more than MAX_DIFF_SHARE of its pixels differ by
// more than PIXEL_TOLERANCE in any channel; the actual frame and a diff image
// go to tests/visual/output/ (uploaded by CI).
//
//   node tests/visual/visual.mjs            # compare
//   node tests/visual/visual.mjs --update   # write new baselines
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { captureCanvas, comparePngs, focusOnBuildings, launch, makeSite, openViewer, serve } from './harness.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const baselineDir = path.join(here, 'baseline');
const outputDir = path.join(here, 'output');
const update = process.argv.includes('--update');
const PORT = 8783;
const PIXEL_TOLERANCE = 24;
const MAX_DIFF_SHARE = 0.015;

const VIEWS = [
  { name: 'overview-afternoon', sun: '172,15' },
  { name: 'street-afternoon', sun: '172,15', focus: true },
  { name: 'street-winter-morning', sun: '355,10', focus: true },
  { name: 'overview-night', sun: '172,22' },
];

const site = makeSite();
const server = await serve(site, PORT);
const browser = await launch();
let failed = false;
try {
  fs.mkdirSync(baselineDir, { recursive: true });
  fs.mkdirSync(outputDir, { recursive: true });
  for (const view of VIEWS) {
    const { page, errors } = await openViewer(browser, `http://127.0.0.1:${PORT}/index.html?sun=${view.sun}`, { width: 800, height: 500 });
    if (view.focus) await focusOnBuildings(page);
    const dataUrl = await captureCanvas(page);
    const png = Buffer.from(dataUrl.split(',')[1], 'base64');
    const file = path.join(baselineDir, `${view.name}.png`);
    if (errors.length) {
      failed = true;
      console.log(`${view.name}: page errors`, errors);
    }
    if (update || !fs.existsSync(file)) {
      fs.writeFileSync(file, png);
      console.log(`${view.name}: baseline written`);
    } else {
      const toUrl = (buf) => `data:image/png;base64,${buf.toString('base64')}`;
      const result = await page.evaluate(comparePngs, { a: toUrl(png), b: toUrl(fs.readFileSync(file)), tolerance: PIXEL_TOLERANCE });
      const ok = !result.sizeMismatch && result.share <= MAX_DIFF_SHARE;
      console.log(`${view.name}: ${(result.share * 100).toFixed(2)}% pixels differ ${ok ? 'ok' : 'FAIL'}`);
      if (!ok) {
        failed = true;
        fs.writeFileSync(path.join(outputDir, `${view.name}.actual.png`), png);
        if (result.diff) fs.writeFileSync(path.join(outputDir, `${view.name}.diff.png`), Buffer.from(result.diff.split(',')[1], 'base64'));
      }
    }
    await page.close();
  }
} finally {
  await browser.close();
  server.kill();
  fs.rmSync(site, { recursive: true, force: true });
}
if (failed) {
  console.error('Visual regression. Inspect tests/visual/output/; if the change is intended, run: node tests/visual/visual.mjs --update');
  process.exit(1);
}
