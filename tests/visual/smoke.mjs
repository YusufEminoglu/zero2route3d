// Functional browser checks of the 3D viewer:
//   - the live viewer loads a route offline without page errors;
//   - the sun follows date and solar time (direction, night) and the
//     shadows actually move with it;
//   - the viewer does not render while idle;
//   - the standalone HTML export (one classic-script file) runs.
//
//   node tests/visual/smoke.mjs
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { captureCanvas, comparePngs, focusOnBuildings, launch, makeSite, makeStandalone, openViewer, serve } from './harness.mjs';
import { sunDirection, sunPosition } from '../../web/js/solar.js';

const PORT = 8781;
const results = [];
function check(name, ok, detail = '') {
  results.push(ok);
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` -- ${detail}` : ''}`);
}

const site = makeSite();
const server = await serve(site, PORT);
const browser = await launch();
try {
  const base = `http://127.0.0.1:${PORT}/index.html`;

  // 1. Live viewer, afternoon sun.
  const { page, errors } = await openViewer(browser, `${base}?sun=172,15`);
  const info = await page.evaluate(() => {
    const app = window.app3d;
    const light = app.sunLight;
    const d = light.position.clone().sub(light.target.position).normalize();
    return {
      buildings: app.buildingsGroup.children.length,
      origin: app.originLonLat,
      dir: { x: d.x, y: d.y, z: d.z },
      castShadow: light.castShadow,
      intensity: light.intensity,
      note: document.getElementById('sunNote').textContent,
      live: document.getElementById('liveStatus').textContent,
      time: document.getElementById('lblSunTime').textContent,
      date: document.getElementById('sunDate').value,
    };
  });
  check('live viewer loads the route without page errors', info.buildings > 0 && errors.length === 0 && !info.live,
    errors.slice(0, 3).join(' | ') || `${info.buildings} building mesh(es)`);
  const sun = sunPosition(info.origin.lat, 172, 15);
  const expected = sunDirection(sun.azimuthDeg, sun.elevationDeg);
  const err = Math.hypot(info.dir.x - expected.x, info.dir.y - expected.y, info.dir.z - expected.z);
  check('sun light points along the computed sun direction', err < 1e-3, `${JSON.stringify(info.dir)} vs ${JSON.stringify(expected)}`);
  check('sun controls show the date, time and position',
    info.date === '2026-06-21' && info.time === '15:00' && /from the W/.test(info.note) && info.castShadow,
    `${info.date} ${info.time} "${info.note}"`);

  // 2. Shadows move with the sun: morning and afternoon frames differ.
  await focusOnBuildings(page);
  const afternoon = await captureCanvas(page);
  await page.evaluate(() => window.app3d.setSun({ solarHour: 9 }));
  const morning = await captureCanvas(page);
  const moved = await page.evaluate(comparePngs, { a: afternoon, b: morning, tolerance: 24 });
  check('shadows move between morning and afternoon', moved.share > 0.01, `${(moved.share * 100).toFixed(2)}% pixels changed`);

  // 3. Night: the sun light is off and says so.
  await page.fill('#sunHour', '21');
  await page.dispatchEvent('#sunHour', 'input');
  const night = await page.evaluate(() => ({
    intensity: window.app3d.sunLight.intensity,
    cast: window.app3d.sunLight.castShadow,
    note: document.getElementById('sunNote').textContent,
  }));
  check('at night the sun light is off', night.intensity === 0 && !night.cast && /below the horizon/.test(night.note),
    JSON.stringify(night));

  // 4. Changing the date through the control moves the sun too.
  await page.fill('#sunHour', '12');
  await page.dispatchEvent('#sunHour', 'input');
  const summer = await page.evaluate(() => window.app3d.sunState.elevationDeg);
  await page.fill('#sunDate', '2026-12-21');
  await page.dispatchEvent('#sunDate', 'change');
  const winter = await page.evaluate(() => window.app3d.sunState.elevationDeg);
  check('the date control changes the sun height', summer - winter > 40, `noon ${summer.toFixed(1)} -> ${winter.toFixed(1)} deg`);

  // 5. Idle: no frames are drawn when nothing changes.
  const f0 = await page.evaluate(() => window.app3d.framesRendered);
  await page.waitForTimeout(1200);
  const f1 = await page.evaluate(() => window.app3d.framesRendered);
  check('no frames are rendered while idle', f1 - f0 <= 1, `${f1 - f0} frame(s) in 1.2 s`);
  await page.close();

  // 6. The standalone export runs from a file:// URL as classic scripts.
  const standalone = makeStandalone(site);
  const { page: report, errors: reportErrors } = await openViewer(browser, pathToFileURL(standalone).href);
  const reportInfo = await report.evaluate(() => ({
    hasRoute: Boolean(window.app3d.routeData),
    sunNote: document.getElementById('sunNote') ? 'present' : 'absent',
    elevation: window.app3d.sunState?.elevationDeg,
  }));
  check('standalone HTML export runs without errors',
    reportInfo.hasRoute && Number.isFinite(reportInfo.elevation) && reportErrors.length === 0,
    reportErrors.slice(0, 3).join(' | ') || `${(fs.statSync(standalone).size / 1e6).toFixed(1)} MB`);
  await report.close();
} finally {
  await browser.close();
  server.kill();
  fs.rmSync(site, { recursive: true, force: true });
}
const failed = results.filter((ok) => !ok).length;
console.log(`${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
