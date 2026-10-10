// Sun position for the viewer's light, the same formula as the QGIS shade
// analysis (core/solar_shadow.py: Cooper declination, 15 degrees per hour of
// local solar time), so the shadows in 3D match the computed shade.

const DEG = Math.PI / 180;

export const DEFAULT_SUN = { dayOfYear: 172, solarHour: 15 }; // 21 June, mid-afternoon

// {azimuthDeg (clockwise from north), elevationDeg}; elevation is negative
// below the horizon (the analysis clamps it to 0, the viewer dims the light).
export function sunPosition(latDeg, dayOfYear, solarHour) {
  const declination = 23.45 * Math.sin(DEG * (360 / 365) * (284 + dayOfYear));
  const dec = declination * DEG;
  const lat = latDeg * DEG;
  const hourAngle = (solarHour - 12) * 15;
  const h = hourAngle * DEG;
  const sinEl = Math.max(-1, Math.min(1, Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(h)));
  const el = Math.asin(sinEl);
  const cosAz = (Math.sin(dec) - Math.sin(lat) * sinEl) / Math.max(0.001, Math.cos(lat) * Math.cos(el));
  let azimuth = Math.acos(Math.max(-1, Math.min(1, cosAz))) / DEG;
  if (hourAngle > 0) azimuth = 360 - azimuth;
  return { azimuthDeg: azimuth, elevationDeg: el / DEG };
}

// Unit vector from the ground toward the sun in scene axes (x east, y up,
// z south).
export function sunDirection(azimuthDeg, elevationDeg) {
  const az = azimuthDeg * DEG;
  const el = elevationDeg * DEG;
  return { x: Math.sin(az) * Math.cos(el), y: Math.sin(el), z: -Math.cos(az) * Math.cos(el) };
}

// Day of year (1-366) of a yyyy-mm-dd string, or null.
export function dayOfYearFromIso(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(text || ''));
  if (!m) return null;
  const date = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  if (!Number.isFinite(date)) return null;
  return Math.round((date - Date.UTC(Number(m[1]), 0, 1)) / 86400000) + 1;
}

// "HH:MM" for a fractional hour.
export function formatSolarHour(hour) {
  const total = Math.round(hour * 60);
  const hh = Math.floor(total / 60) % 24;
  const mm = total % 60;
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}

// yyyy-mm-dd of a day of year (a non-leap year, so day 172 is 21 June).
export function isoFromDayOfYear(dayOfYear, year = 2026) {
  const date = new Date(Date.UTC(year, 0, Math.max(1, Math.min(366, Math.round(dayOfYear)))));
  return date.toISOString().slice(0, 10);
}

// "?sun=172,15.5" (day of year, solar hour) -> {dayOfYear, solarHour} or null.
export function parseSunParam(text) {
  const m = /^\s*(\d{1,3})\s*,\s*(\d{1,2}(?:\.\d+)?)\s*$/.exec(String(text || ''));
  if (!m) return null;
  const dayOfYear = Number(m[1]);
  const solarHour = Number(m[2]);
  if (dayOfYear < 1 || dayOfYear > 366 || solarHour < 0 || solarHour > 24) return null;
  return { dayOfYear, solarHour };
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

export function compassPoint(azimuthDeg) {
  return COMPASS[Math.round((((azimuthDeg % 360) + 360) % 360) / 45) % 8];
}
