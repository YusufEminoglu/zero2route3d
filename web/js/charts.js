// Elevation chart, metric ribbons, live readout and radar map.
// Methods of Studio3DApp (app3d.js), mixed into its prototype.
import * as THREE from './three.module.js';
import { niceTicks, wormPath } from './chart_math.js';
import { SAFE_COLOR } from './colors.js';

export const chartMethods = {
  updateMetricLiveReadout() {
    if (!this.activeMetrics || !this.activeMetrics.length || !this.routeData) return;
    const safeProgress = Math.max(0.0, Math.min(1.0, this.progress));

    this.activeMetrics.forEach((m) => {
      if (!m.elVal) return;
      const n = m.values.length;
      if (n === 0) {
        m.elVal.textContent = '—';
        return;
      }
      // Value at the walker's distance along the route.
      const total = m.distances[n - 1] || 1;
      const target = safeProgress * total;
      let idx = 0;
      while (idx < n - 1 && m.distances[idx + 1] <= target) idx += 1;
      const val = m.values[idx];
      m.elVal.textContent = (val !== null && val !== undefined && !isNaN(val)) ? m.formatter(val) : 'no data';
    });
  },

  updateRadarMap() {
    if (!this.radarCtx) return;
    const ctx = this.radarCtx;
    ctx.clearRect(0, 0, 130, 130);
    if (!this.curve || !this.scenePoints.length) return;

    const box = new THREE.Box3().setFromPoints(this.scenePoints);
    const size = new THREE.Vector3();
    box.getSize(size);
    const min = box.min;
    const maxDim = Math.max(size.x, size.z, 50);

    const toRadar = (p) => {
      const rx = 15 + ((p.x - min.x) / maxDim) * 100;
      const ry = 115 - ((p.z - min.z) / maxDim) * 100;
      return { x: rx, y: ry };
    };

    ctx.beginPath();
    ctx.strokeStyle = this.routeData?.properties?.profile_color || '#0284c7';
    ctx.lineWidth = 3.5;
    this.scenePoints.forEach((p, i) => {
      const r = toRadar(p);
      if (i === 0) ctx.moveTo(r.x, r.y);
      else ctx.lineTo(r.x, r.y);
    });
    ctx.stroke();

    if (this.scenePoints.length >= 2 && this.activePath) {
      const safeProgress = Math.max(0.0, Math.min(1.0, this.progress));
      const av = toRadar(this.activePath.at(safeProgress).point);
      ctx.beginPath();
      ctx.fillStyle = '#ef4444';
      ctx.arc(av.x, av.y, 4.5, 0, Math.PI * 2);
      ctx.fill();
    }
  },

  // Per-point profile of the active route: distance (m), elevation, slope,
  // speed and the optional raster values. QGIS sends it densified (~6 m); the
  // old code read it by route-vertex index, so the values came from the
  // wrong places along the route.
  profileSeries() {
    const profList = (this.routeData?.properties?.elevation_profile || []).filter(
      (p) => p && Number.isFinite(Number(p.distance_m)) && Number.isFinite(Number(p.elevation_m)),
    );
    if (profList.length >= 2) {
      return profList.map((p) => ({
        d: Number(p.distance_m),
        z: Number(p.elevation_m),
        slope: Number(p.slope_pct) || 0,
        speed: Number(p.speed_kmh),
        lst: p.lst_normalized,
        green: p.ndvi_normalized,
      }));
    }
    // No profile sent (older payloads): fall back to the vertices.
    const coords = this.routeData?.geometry?.coordinates || [];
    const cumulative = this.activePath ? this.activePath.cumulative : coords.map((_, i) => i);
    return coords.map((c, i) => ({ d: cumulative[i] || 0, z: Number(c[2]) || 0, slope: 0, speed: NaN }));
  },

  renderProfileChart() {
    if (this.elMultiMetricRows) this.elMultiMetricRows.innerHTML = '';
    this.activeMetrics = [];
    this.renderElevationChart();
    if (!this.routeData || !this.elMultiMetricRows) return;
    const series = this.profileSeries();
    if (series.length < 2) return;

    const trackWidth = Math.max(100, (this.elMultiMetricRows.clientWidth || 600) - 190);
    const trackHeight = 22;
    const metricsDef = [
      { id: 'slope', name: 'Slope', icon: '📐', color: '#f59e0b', key: 'slope',
        formatter: (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` },
      { id: 'speed', name: 'Speed', icon: '⚡', color: '#6366f1', key: 'speed',
        formatter: (v) => `${v.toFixed(1)} km/h` },
    ];
    // Heat and greenery appear only when a real raster was supplied.
    if (series.some((p) => p.lst !== undefined && p.lst !== null)) {
      metricsDef.push({ id: 'lst', name: 'Heat (LST)', icon: '🌡️', color: '#ef4444', key: 'lst', scale: 100,
        formatter: (v) => `${v.toFixed(0)}%` });
    }
    if (series.some((p) => p.green !== undefined && p.green !== null)) {
      metricsDef.push({ id: 'greenery', name: 'Greenery', icon: '🌳', color: '#10b981', key: 'green', scale: 100,
        formatter: (v) => `${v.toFixed(0)}%` });
    }

    metricsDef.forEach((metric) => {
      const values = series.map((p) => {
        const v = p[metric.key];
        return v === null || v === undefined || !Number.isFinite(Number(v)) ? null : Number(v) * (metric.scale || 1);
      });
      const pathD = wormPath(values, trackWidth, trackHeight);
      if (!pathD) return;
      const safeColor = SAFE_COLOR.test(metric.color) ? metric.color : '#0284c7';

      const row = document.createElement('div');
      row.className = 'metric-worm-row';
      row.dataset.metric = metric.id;
      const info = document.createElement('div');
      info.className = 'metric-row-info';
      const iconSpan = document.createElement('span');
      iconSpan.className = 'metric-row-icon';
      iconSpan.setAttribute('aria-hidden', 'true');
      iconSpan.textContent = metric.icon;
      const nameSpan = document.createElement('span');
      nameSpan.className = 'metric-row-name';
      nameSpan.textContent = metric.name;
      const valSpan = document.createElement('span');
      valSpan.className = 'metric-row-val';
      valSpan.id = `readout_${metric.id}`;
      valSpan.textContent = '—';
      info.append(iconSpan, nameSpan, valSpan);

      const track = document.createElement('div');
      track.className = 'metric-ribbon-track';
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'metric-ribbon-svg');
      svg.setAttribute('preserveAspectRatio', 'none');
      svg.setAttribute('viewBox', `0 0 ${trackWidth} ${trackHeight}`);
      svg.setAttribute('aria-hidden', 'true');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', pathD);
      path.setAttribute('fill', safeColor);
      path.setAttribute('fill-opacity', '0.82');
      path.setAttribute('stroke', safeColor);
      path.setAttribute('stroke-width', '1.0');
      svg.appendChild(path);
      const needle = document.createElement('div');
      needle.className = 'metric-needle';
      const endDot = document.createElement('div');
      endDot.className = 'metric-end-dot';
      endDot.style.background = safeColor;
      track.append(svg, endDot, needle);
      track.addEventListener('click', (e) => {
        const rect = track.getBoundingClientRect();
        this.setProgress((e.clientX - rect.left) / Math.max(1, rect.width));
      });
      row.append(info, track);
      this.elMultiMetricRows.appendChild(row);
      this.activeMetrics.push({ ...metric, values, distances: series.map((p) => p.d), elVal: valSpan, elNeedle: needle });
    });
    this.updateMetricLiveReadout();
  },

  // Elevation profile with real axes: distance (km) across, elevation (m) up,
  // the area coloured by slope class. Hovering or arrow keys move the walker
  // in 3D; playback moves the chart cursor.
  renderElevationChart() {
    const host = document.getElementById('elevationChart');
    if (!host) return;
    host.innerHTML = '';
    this.chart = null;
    if (!this.routeData) return;
    const series = this.profileSeries();
    if (series.length < 2) return;
    const totalD = series[series.length - 1].d || 1;
    const step = Math.max(1, Math.floor(series.length / 600));
    const pts = series.filter((_, i) => i % step === 0 || i === series.length - 1);

    // The host is hidden while empty, so measure the panel it sits in.
    const W = Math.max(240, host.clientWidth || (host.parentElement?.clientWidth || 632) - 32);
    const H = 96;
    const m = { l: 46, r: 12, t: 8, b: 20 };
    const pw = W - m.l - m.r;
    const ph = H - m.t - m.b;
    let zMin = Infinity;
    let zMax = -Infinity;
    pts.forEach((p) => { zMin = Math.min(zMin, p.z); zMax = Math.max(zMax, p.z); });
    const yTicks = niceTicks(zMin, zMax, 3);
    const yLo = Math.min(zMin, yTicks[0]);
    const yHi = Math.max(zMax, yTicks[yTicks.length - 1], yLo + 1);
    const x = (d) => m.l + (d / totalD) * pw;
    const y = (z) => m.t + (1 - (z - yLo) / (yHi - yLo)) * ph;

    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('width', String(W));
    svg.setAttribute('height', String(H));
    svg.setAttribute('class', 'elevation-svg');
    const el = (name, attrs, text) => {
      const node = document.createElementNS(NS, name);
      Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
      if (text !== undefined) node.textContent = text;
      svg.appendChild(node);
      return node;
    };

    yTicks.forEach((t) => {
      el('line', { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), class: 'grid' });
      el('text', { x: m.l - 6, y: y(t) + 3, class: 'tick', 'text-anchor': 'end' }, `${Math.round(t)} m`);
    });
    niceTicks(0, totalD / 1000, 5).forEach((t) => {
      if (t * 1000 > totalD + 1e-6) return;
      el('text', { x: x(t * 1000), y: H - 5, class: 'tick', 'text-anchor': 'middle' }, `${t} km`);
    });

    // Area under the line, one polygon per run of the same slope class.
    const slopeClass = (s) => {
      const a = Math.abs(s);
      return a < 3 ? 0 : a < 6 ? 1 : a < 10 ? 2 : 3;
    };
    const colors = ['#10b981', '#eab308', '#f97316', '#ef4444'];
    let runStart = 0;
    for (let i = 1; i <= pts.length; i++) {
      const cls = slopeClass(pts[Math.min(i, pts.length - 1)].slope);
      const runCls = slopeClass(pts[runStart].slope);
      if (i === pts.length || cls !== runCls) {
        const seg = pts.slice(runStart, Math.min(i + 1, pts.length));
        if (seg.length >= 2) {
          const d = `M${x(seg[0].d)},${y(yLo)} ${seg.map((p) => `L${x(p.d).toFixed(1)},${y(p.z).toFixed(1)}`).join(' ')} L${x(seg[seg.length - 1].d)},${y(yLo)} Z`;
          el('path', { d, fill: colors[runCls], 'fill-opacity': 0.55 });
        }
        runStart = i;
      }
    }
    el('path', {
      d: pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.d).toFixed(1)},${y(p.z).toFixed(1)}`).join(' '),
      class: 'profile-line',
    });
    const cursor = el('line', { x1: m.l, x2: m.l, y1: m.t, y2: m.t + ph, class: 'cursor' });
    const dot = el('circle', { cx: m.l, cy: y(pts[0].z), r: 4, class: 'cursor-dot' });

    const climb = Number(this.routeData.properties?.elevation_gain_m || 0);
    host.setAttribute('role', 'slider');
    host.setAttribute('tabindex', '0');
    host.setAttribute('aria-label',
      `Elevation profile, ${(totalD / 1000).toFixed(2)} km, from ${Math.round(zMin)} to ${Math.round(zMax)} m, climb ${Math.round(climb)} m. Arrow keys move along the route.`);
    host.setAttribute('aria-valuemin', '0');
    host.setAttribute('aria-valuemax', '100');
    host.appendChild(svg);
    const tip = document.createElement('div');
    tip.className = 'chart-tip';
    host.appendChild(tip);

    const fromEvent = (e) => {
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / Math.max(1, rect.width)) * W;
      return Math.max(0, Math.min(1, (px - m.l) / pw));
    };
    svg.addEventListener('pointermove', (e) => this.setProgress(fromEvent(e)));
    svg.addEventListener('pointerdown', (e) => this.setProgress(fromEvent(e)));
    host.addEventListener('keydown', (e) => {
      const stepFrac = e.shiftKey ? 0.05 : 0.01;
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        e.stopPropagation();
        this.setProgress(this.progress + (e.key === 'ArrowRight' ? stepFrac : -stepFrac));
      }
    });
    this.chart = { series, totalD, x, y, cursor, dot, tip, host, W };
    this.updateChartCursor();
  },

  updateChartCursor() {
    const c = this.chart;
    if (!c) return;
    const d = Math.max(0, Math.min(1, this.progress)) * c.totalD;
    const s = c.series;
    let lo = 0;
    let hi = s.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (s[mid].d <= d) lo = mid;
      else hi = mid;
    }
    const span = s[hi].d - s[lo].d;
    const f = span > 0 ? (d - s[lo].d) / span : 0;
    const z = s[lo].z + (s[hi].z - s[lo].z) * f;
    const px = c.x(d);
    c.cursor.setAttribute('x1', px);
    c.cursor.setAttribute('x2', px);
    c.dot.setAttribute('cx', px);
    c.dot.setAttribute('cy', c.y(z));
    const text = `${(d / 1000).toFixed(2)} km · ${z.toFixed(1)} m · ${s[lo].slope >= 0 ? '+' : ''}${s[lo].slope.toFixed(1)}%`;
    c.tip.textContent = text;
    c.tip.style.left = `${Math.min(c.W - 170, Math.max(50, px + 8))}px`;
    c.host.setAttribute('aria-valuenow', String(Math.round(this.progress * 100)));
    c.host.setAttribute('aria-valuetext', text);
  },
};
