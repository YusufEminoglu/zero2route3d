// Geographic helpers: the viewer's local metre frame around the route
// origin, Web Mercator tile math for the basemap, and the DEM height grid.

export const METRES_PER_DEG_LAT = 110574.0;
export const METRES_PER_DEG_LON_EQUATOR = 111320.0;
export const MERCATOR_MAX_LAT = 85.0511;

// Scene x grows east and z grows south (three.js looks down -z).
export function lonLatToLocal(origin, lon, lat) {
  const meanLat = (origin.lat * Math.PI) / 180.0;
  return {
    x: (lon - origin.lon) * METRES_PER_DEG_LON_EQUATOR * Math.cos(meanLat),
    z: -(lat - origin.lat) * METRES_PER_DEG_LAT,
  };
}

export function localToLonLat(origin, x, z) {
  const meanLat = (origin.lat * Math.PI) / 180.0;
  return {
    lon: origin.lon + x / (METRES_PER_DEG_LON_EQUATOR * Math.cos(meanLat)),
    lat: origin.lat - z / METRES_PER_DEG_LAT,
  };
}

export function lon2tile(lon, zoom) {
  const numTiles = Math.pow(2, zoom);
  return Math.max(0, Math.min(numTiles - 1, Math.floor(((lon + 180) / 360) * numTiles)));
}

export function lat2tile(lat, zoom) {
  const numTiles = Math.pow(2, zoom);
  const clampedLat = Math.max(-MERCATOR_MAX_LAT, Math.min(MERCATOR_MAX_LAT, lat));
  const rad = (clampedLat * Math.PI) / 180.0;
  const val = (1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2;
  return Math.max(0, Math.min(numTiles - 1, Math.floor(val * numTiles)));
}

// West edge of tile column x (fractional x gives positions inside the tile).
export function tile2lon(x, zoom) {
  return (x / Math.pow(2, zoom)) * 360 - 180;
}

// North edge of tile row y (fractional y gives Mercator-correct positions).
export function tile2lat(y, zoom) {
  const n = Math.PI - (2 * Math.PI * y) / Math.pow(2, zoom);
  return (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
}

// The highest zoom (maxZoom..minZoom) at which the extent is covered by at
// most maxTilesAxis tiles in each direction, with the tile range at it.
export function basemapTileRange(minLon, minLat, maxLon, maxLat, maxTilesAxis = 8, maxZoom = 19, minZoom = 10) {
  let zoom = maxZoom;
  while (zoom > minZoom) {
    const countX = lon2tile(maxLon, zoom) - lon2tile(minLon, zoom) + 1;
    const countY = lat2tile(minLat, zoom) - lat2tile(maxLat, zoom) + 1;
    if (countX <= maxTilesAxis && countY <= maxTilesAxis) break;
    zoom--;
  }
  return {
    zoom,
    minX: lon2tile(minLon, zoom),
    maxX: lon2tile(maxLon, zoom),
    minY: lat2tile(maxLat, zoom),
    maxY: lat2tile(minLat, zoom),
  };
}

// A DEM grid from QGIS: {bbox: [w, s, e, n], cols, rows, heights (row-major,
// north row first)}. Anything else is rejected rather than half-used.
export function isValidTerrainGrid(grid) {
  return Boolean(grid && Array.isArray(grid.bbox) && grid.bbox.length === 4
    && Number.isInteger(grid.cols) && Number.isInteger(grid.rows) && grid.cols >= 2 && grid.rows >= 2
    && Array.isArray(grid.heights) && grid.heights.length === grid.cols * grid.rows
    && grid.bbox.every(Number.isFinite) && grid.heights.every(Number.isFinite));
}

// Bilinear height (metres) at lon/lat; points outside the grid take the edge.
export function sampleTerrainGrid(grid, lon, lat) {
  const [west, south, east, north] = grid.bbox;
  const fx = Math.max(0, Math.min(grid.cols - 1, ((lon - west) / (east - west)) * (grid.cols - 1)));
  const fy = Math.max(0, Math.min(grid.rows - 1, ((north - lat) / (north - south)) * (grid.rows - 1)));
  const c0 = Math.floor(fx);
  const r0 = Math.floor(fy);
  const c1 = Math.min(grid.cols - 1, c0 + 1);
  const r1 = Math.min(grid.rows - 1, r0 + 1);
  const tx = fx - c0;
  const ty = fy - r0;
  const h = (r, c) => grid.heights[r * grid.cols + c];
  const top = h(r0, c0) * (1 - tx) + h(r0, c1) * tx;
  const bottom = h(r1, c0) * (1 - tx) + h(r1, c1) * tx;
  return top * (1 - ty) + bottom * ty;
}
