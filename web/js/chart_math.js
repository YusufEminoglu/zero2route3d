// Pure chart helpers for the elevation chart and the metric ribbons.

// "Nice" axis ticks (1, 2, 5 x 10^n steps) covering [lo, hi].
export function niceTicks(lo, hi, target) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0];
  if (hi - lo < 1e-9) return [lo];
  const raw = (hi - lo) / Math.max(1, target);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((k) => k * mag).find((v) => v >= raw) || raw;
  const ticks = [];
  for (let t = Math.floor(lo / step) * step; t <= hi + step * 0.5; t += step) {
    ticks.push(Number(t.toFixed(10)));
  }
  return ticks;
}

// Closed SVG path of a "worm" ribbon whose thickness follows the values.
export function wormPath(values, width, height) {
  const n = values.length;
  if (n < 2) return '';
  const known = values.filter((v) => v !== null && v !== undefined && !isNaN(v));
  if (!known.length) return '';
  const minVal = Math.min(...known);
  const maxVal = Math.max(...known);
  const valRange = Math.max(0.001, maxVal - minVal);
  const yMid = height / 2.0;
  const maxHalfT = Math.max(2.0, Math.min(8.0, (height - 6.0) / 2.0));
  const minHalfT = 1.4;

  const topPoints = [];
  const bottomPoints = [];

  for (let i = 0; i < n; i++) {
    const v = values[i];
    const s = i / Math.max(1, n - 1);
    const x = s * width;
    const cleanV = (v !== null && v !== undefined && !isNaN(v)) ? v : minVal;
    const u = Math.max(0.0, Math.min(1.0, (cleanV - minVal) / valRange));
    const env = Math.pow(Math.sin(Math.PI * s), 0.65);
    const halfT = (minHalfT + u * (maxHalfT - minHalfT)) * env;
    topPoints.push(`${x.toFixed(1)},${(yMid - halfT).toFixed(1)}`);
    bottomPoints.push(`${x.toFixed(1)},${(yMid + halfT).toFixed(1)}`);
  }

  let d = `M ${topPoints[0]} `;
  for (let i = 1; i < topPoints.length; i++) {
    d += `L ${topPoints[i]} `;
  }
  for (let i = bottomPoints.length - 1; i >= 0; i--) {
    d += `L ${bottomPoints[i]} `;
  }
  d += 'Z';
  return d;
}
