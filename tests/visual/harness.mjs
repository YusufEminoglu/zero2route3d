// Shared browser-test plumbing: builds a deterministic viewer site (Python),
// serves it, and opens it in headless Chromium with WebGL on SwiftShader.
// Every https request is aborted, so basemap tiles never make a test depend
// on the network; the viewer then shows its offline hillshade.
import { createRequire } from 'node:module';
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

export const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python = process.env.PYTHON || 'python3';
// The plugin is a package named after its folder (zero2route3d).
const pythonEnv = { ...process.env, PYTHONPATH: path.dirname(repoRoot) };
const pkg = path.basename(repoRoot);

export function makeSite() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'r3d-site-'));
  execFileSync(python, ['-m', `${pkg}.tests.viewer.make_site`, dir], { env: pythonEnv, stdio: 'inherit' });
  return dir;
}

export function makeStandalone(siteDir) {
  const out = path.join(siteDir, 'standalone.html');
  const code = [
    'import json, sys',
    'from pathlib import Path',
    `from ${pkg}.core.html_bundler import StandaloneHtmlBundler`,
    'data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))',
    'StandaloneHtmlBundler().bundle_to_file(data, Path(sys.argv[2]))',
  ].join('\n');
  execFileSync(python, ['-c', code, path.join(siteDir, 'data', 'current_route.json'), out], { env: pythonEnv });
  return out;
}

// The plugin's own local server (core/local_server.py), with its /events
// stream, so the live link to QGIS is exercised too.
export async function serve(dir, port) {
  const code = [
    'import sys, time',
    `from ${pkg}.core.local_server import Route3DLocalServer`,
    'server = Route3DLocalServer(sys.argv[1], start_port=int(sys.argv[2]), end_port=int(sys.argv[2]))',
    'server.start()',
    'while True: time.sleep(1)',
  ].join('\n');
  const server = spawn(python, ['-c', code, dir, String(port)], { env: pythonEnv, stdio: 'ignore' });
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/index.html`);
      if (res.ok) return server;
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  server.kill();
  throw new Error(`viewer server on port ${port} did not start`);
}

export function launch() {
  return chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
}

// Opens the viewer and waits until the route is loaded and the scene built.
export async function openViewer(browser, url, viewport = { width: 960, height: 600 }) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    // Aborted tile requests are expected offline; anything else is a failure.
    if (m.type() === 'error' && !/net::ERR_FAILED|Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  await page.route(/^https:\/\//, (r) => r.abort());
  await page.goto(url);
  await page.waitForFunction(() => window.app3d && window.app3d.routeData && window.app3d.terrainMesh, null, { timeout: 60000 });
  await page.waitForTimeout(1500);
  return { page, errors };
}

// Camera close to a corridor building, looking down at it from the
// south-east, so its shadow on the ground is large enough to compare.
export function focusOnBuildings(page, index = 3) {
  return page.evaluate((i) => {
    const app = window.app3d;
    const building = app.corridorBuildings()[i];
    const [lon, lat] = building.polygon[0];
    const p = app.lonLatToSceneMeters(lon, lat, building.base_elevation_m);
    app.controls.target.set(p.x, p.y, p.z);
    app.camera.position.set(p.x + 45, p.y + 70, p.z + 60);
    app.camera.lookAt(p.x, p.y, p.z);
    app.controls.update();
    app.requestRender();
  }, index);
}

// The WebGL frame alone (no HTML overlays, so fonts do not matter).
export function captureCanvas(page) {
  return page.evaluate(() => {
    const app = window.app3d;
    app.renderer.render(app.scene, app.camera);
    return app.renderer.domElement.toDataURL('image/png');
  });
}

// Runs in the page: share of pixels differing by more than `tolerance` in any
// channel between two PNG data URLs, and a diff image (red where different).
export async function comparePngs({ a, b, tolerance }) {
  const load = (src) => new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
  const [ia, ib] = await Promise.all([load(a), load(b)]);
  if (ia.width !== ib.width || ia.height !== ib.height) return { share: 1, sizeMismatch: true, diff: null };
  const w = ia.width;
  const h = ia.height;
  const read = (img) => {
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    const ctx = c.getContext('2d');
    ctx.drawImage(img, 0, 0);
    return ctx.getImageData(0, 0, w, h).data;
  };
  const da = read(ia);
  const db = read(ib);
  const out = new ImageData(w, h);
  let bad = 0;
  for (let i = 0; i < da.length; i += 4) {
    const d = Math.max(Math.abs(da[i] - db[i]), Math.abs(da[i + 1] - db[i + 1]), Math.abs(da[i + 2] - db[i + 2]));
    const grey = (da[i] + da[i + 1] + da[i + 2]) / 12;
    if (d > tolerance) {
      bad++;
      out.data.set([255, 0, 0, 255], i);
    } else {
      out.data.set([grey, grey, grey, 255], i);
    }
  }
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  c.getContext('2d').putImageData(out, 0, 0);
  return { share: bad / (w * h), diff: c.toDataURL('image/png') };
}
