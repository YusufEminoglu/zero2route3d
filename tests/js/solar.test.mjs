import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  compassPoint, dayOfYearFromIso, formatSolarHour, isoFromDayOfYear, parseSunParam, sunDirection, sunPosition,
} from '../../web/js/solar.js';

// Values from core/solar_shadow.calculate_solar_position: the viewer's light
// must agree with the QGIS shade analysis.
const PYTHON = [
  [38.42, 172, 15.0, 261.985323, 49.072381],
  [38.42, 355, 12.0, 179.999999, 28.130217],
  [-33.9, 172, 10.5, 23.614492, 28.786435],
  [60.0, 80, 8.0, 116.75119, 14.116401],
  [38.42, 172, 9.0, 98.014677, 49.072381],
];

test('sun position matches the Python shade analysis', () => {
  for (const [lat, day, hour, az, el] of PYTHON) {
    const sun = sunPosition(lat, day, hour);
    assert.ok(Math.abs(sun.azimuthDeg - az) < 1e-4, `azimuth ${lat} ${day} ${hour}: ${sun.azimuthDeg}`);
    assert.ok(Math.abs(sun.elevationDeg - el) < 1e-4, `elevation ${lat} ${day} ${hour}: ${sun.elevationDeg}`);
  }
});

test('the sun is below the horizon at night and in polar winter', () => {
  assert.ok(sunPosition(38.42, 172, 23).elevationDeg < 0);
  assert.ok(sunPosition(78, 355, 12).elevationDeg < 0);
});

test('sun direction points up and toward the azimuth', () => {
  const south = sunDirection(180, 30);
  assert.ok(south.z > 0.8 && Math.abs(south.x) < 1e-9 && south.y > 0.49, JSON.stringify(south));
  const east = sunDirection(90, 0);
  assert.ok(east.x > 0.999 && Math.abs(east.y) < 1e-9);
  const length = Math.hypot(south.x, south.y, south.z);
  assert.ok(Math.abs(length - 1) < 1e-9);
});

test('dates and times round-trip', () => {
  assert.equal(dayOfYearFromIso('2026-06-21'), 172);
  assert.equal(dayOfYearFromIso('2024-12-31'), 366);
  assert.equal(dayOfYearFromIso('not a date'), null);
  assert.equal(isoFromDayOfYear(172), '2026-06-21');
  assert.equal(dayOfYearFromIso(isoFromDayOfYear(45)), 45);
  assert.equal(formatSolarHour(15.25), '15:15');
  assert.equal(formatSolarHour(9.999), '10:00');
});

test('the ?sun= parameter is validated', () => {
  assert.deepEqual(parseSunParam('172,15.5'), { dayOfYear: 172, solarHour: 15.5 });
  assert.equal(parseSunParam('400,12'), null);
  assert.equal(parseSunParam('172,25'), null);
  assert.equal(parseSunParam('172;12'), null);
  assert.equal(parseSunParam(null), null);
});

test('compass points', () => {
  assert.equal(compassPoint(0), 'N');
  assert.equal(compassPoint(359), 'N');
  assert.equal(compassPoint(262), 'W');
  assert.equal(compassPoint(-45), 'NW');
});
