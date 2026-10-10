// Basemap: web tiles drawn into one texture (Mercator strips), with an
// offline hillshade from the DEM grid when tiles cannot load.
// Methods of Studio3DApp (app3d.js), mixed into its prototype.
import * as THREE from './three.module.js';
import { basemapTileRange, localToLonLat, METRES_PER_DEG_LAT, METRES_PER_DEG_LON_EQUATOR, tile2lat, tile2lon } from './geo.js';

export const basemapMethods = {
  updateBasemapTexture() {
    if (!this.terrainMesh || !this.terrainWidth) return;
    if (this.terrainMesh.material && this.terrainMesh.material.map) {
      this.terrainMesh.material.map.dispose();
    }
    const newTex = this.loadRealBasemapTiles(this.terrainWidth, this.terrainDepth, this.terrainCenter);
    this.terrainMesh.material.map = newTex;
    this.terrainMesh.material.needsUpdate = true;
  },

  loadRealBasemapTiles(width, depth, center) {
    const textureResolution = 4096;
    const canvas = document.createElement('canvas');
    canvas.width = textureResolution;
    canvas.height = textureResolution;
    const ctx = canvas.getContext('2d');

    // Background color based on provider; no fake grid is drawn while tiles load.
    const isDark = this.basemapProvider === 'dark';
    const isSat = this.basemapProvider === 'satellite';
    ctx.fillStyle = isDark ? '#0f172a' : (isSat ? '#1e293b' : '#f8fafc');
    ctx.fillRect(0, 0, textureResolution, textureResolution);

    const tex = new THREE.CanvasTexture(canvas);
    tex.wrapS = THREE.ClampToEdgeWrapping;
    tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.generateMipmaps = true;
    tex.minFilter = THREE.LinearMipmapLinearFilter;
    tex.magFilter = THREE.LinearFilter;
    if (this.renderer?.capabilities) {
      tex.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
    }

    if (!this.originLonLat) return tex;

    const { lon: minLon, lat: minLat } = localToLonLat(this.originLonLat, center.x - width / 2, center.z + depth / 2);
    const { lon: maxLon, lat: maxLat } = localToLonLat(this.originLonLat, center.x + width / 2, center.z - depth / 2);

    // Highest zoom where the whole model area fits in <= 8 tiles per axis:
    // crisp detail and no gaps across the terrain surface.
    const { zoom, minX: minTileX, maxX: maxTileX, minY: minTileY, maxY: maxTileY } = basemapTileRange(
      minLon, minLat, maxLon, maxLat, 8,
    );

    const getTileUrl = (x, y, z) => {
      if (this.basemapProvider === 'satellite') {
        return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;
      } else if (this.basemapProvider === 'voyager') {
        return `https://basemaps.cartocdn.com/rastertiles/voyager/${z}/${x}/${y}.png`;
      } else if (this.basemapProvider === 'dark') {
        return `https://basemaps.cartocdn.com/rastertiles/dark_all/${z}/${x}/${y}.png`;
      }
      return `https://tile.openstreetmap.org/${z}/${x}/${y}.png`;
    };

    const lonSpan = maxLon - minLon || 1.0;
    const latSpan = maxLat - minLat || 1.0;

    // Tiles requested for a previous scene must not write into a texture that
    // rebuildScene() has already disposed. Track this build and abort the old one.
    this.cancelPendingTiles();
    const buildToken = { cancelled: false, images: [], uploadTimer: null };
    this.pendingTileBuild = buildToken;
    const totalTiles = (maxTileX - minTileX + 1) * (maxTileY - minTileY + 1);
    let failedTiles = 0;
    let doneTiles = 0;
    if (this.elBasemapNote) this.elBasemapNote.textContent = '';

    // Uploading a 4096 px texture after every tile re-sent 64 MB to the GPU up
    // to 64 times; uploads are now batched every 250 ms and once at the end.
    const scheduleUpload = (final) => {
      if (final) {
        clearTimeout(buildToken.uploadTimer);
        buildToken.uploadTimer = null;
        tex.needsUpdate = true;
        this.requestRender();
        return;
      }
      if (buildToken.uploadTimer !== null) return;
      buildToken.uploadTimer = setTimeout(() => {
        buildToken.uploadTimer = null;
        if (buildToken.cancelled) return;
        tex.needsUpdate = true;
        this.requestRender();
      }, 250);
    };
    const tileFinished = () => {
      doneTiles += 1;
      if (doneTiles < totalTiles) {
        scheduleUpload(false);
        return;
      }
      if (failedTiles === totalTiles) {
        // Offline or blocked: show the real relief instead of a blank plate.
        this.drawHillshade(ctx, textureResolution, { minLon, maxLon, minLat, maxLat }, isDark);
        if (this.elBasemapNote) {
          this.elBasemapNote.textContent = this.terrainGrid
            ? 'Basemap tiles unavailable (offline?) - showing DEM hillshade.'
            : 'Basemap tiles unavailable (offline?).';
        }
      } else if (failedTiles > 0 && this.elBasemapNote) {
        this.elBasemapNote.textContent = `${failedTiles} of ${totalTiles} basemap tiles failed to load.`;
      }
      scheduleUpload(true);
    };

    // Map tiles are Web Mercator: within a tile, rows are not evenly spaced in
    // latitude, while the terrain is. Each tile is drawn in horizontal strips
    // placed at their true latitudes so roads line up with the route.
    const STRIPS = 8;
    for (let tx = minTileX; tx <= maxTileX; tx++) {
      for (let ty = minTileY; ty <= maxTileY; ty++) {
        const img = new Image();
        buildToken.images.push(img);
        img.crossOrigin = 'anonymous';
        img.onload = () => {
          if (buildToken.cancelled) return;
          const px0 = ((tile2lon(tx, zoom) - minLon) / lonSpan) * textureResolution;
          const px1 = ((tile2lon(tx + 1, zoom) - minLon) / lonSpan) * textureResolution;
          const srcStrip = img.height / STRIPS;
          for (let k = 0; k < STRIPS; k++) {
            const latTop = tile2lat(ty + k / STRIPS, zoom);
            const latBottom = tile2lat(ty + (k + 1) / STRIPS, zoom);
            const py0 = ((maxLat - latTop) / latSpan) * textureResolution;
            const py1 = ((maxLat - latBottom) / latSpan) * textureResolution;
            ctx.drawImage(
              img, 0, k * srcStrip, img.width, srcStrip,
              px0, py0, Math.max(1, px1 - px0), Math.max(0.5, py1 - py0),
            );
          }
          tileFinished();
        };
        img.onerror = () => {
          if (buildToken.cancelled) return;
          failedTiles += 1;
          tileFinished();
        };
        img.src = getTileUrl(tx, ty, zoom);
      }
    }

    return tex;
  },

  // Shaded relief from the DEM grid, drawn into the basemap canvas when no
  // tiles could be loaded. Without a grid, a neutral plate stays.
  drawHillshade(ctx, size, extent, dark) {
    const g = this.terrainGrid;
    if (!g) return;
    const [west, south, east, north] = g.bbox;
    const midLat = ((south + north) / 2) * Math.PI / 180;
    const dx = ((east - west) / (g.cols - 1)) * METRES_PER_DEG_LON_EQUATOR * Math.cos(midLat);
    const dy = ((north - south) / (g.rows - 1)) * METRES_PER_DEG_LAT;
    const img = ctx.createImageData(g.cols, g.rows);
    const h = (r, c) => g.heights[Math.max(0, Math.min(g.rows - 1, r)) * g.cols + Math.max(0, Math.min(g.cols - 1, c))];
    // Light from the north-west, 45 degrees up (the cartographic convention).
    const lx = -0.5;
    const ly = 0.5;
    const lz = Math.SQRT1_2;
    for (let r = 0; r < g.rows; r++) {
      for (let c = 0; c < g.cols; c++) {
        // z-factor 5: city-scale relief is gentle and would barely show.
        const sx = (h(r, c + 1) - h(r, c - 1)) / (2 * dx) * 5.0;
        const sy = (h(r - 1, c) - h(r + 1, c)) / (2 * dy) * 5.0;
        const len = Math.hypot(sx, sy, 1);
        const shade = Math.max(0, (-sx * lx - sy * ly + lz) / len);
        const v = dark ? 25 + shade * 110 : 95 + shade * 160;
        const i = (r * g.cols + c) * 4;
        img.data[i] = v;
        img.data[i + 1] = v;
        img.data[i + 2] = v + (dark ? 10 : 0);
        img.data[i + 3] = 255;
      }
    }
    const tmp = document.createElement('canvas');
    tmp.width = g.cols;
    tmp.height = g.rows;
    tmp.getContext('2d').putImageData(img, 0, 0);
    const lonSpan = extent.maxLon - extent.minLon;
    const latSpan = extent.maxLat - extent.minLat;
    const x0 = ((west - extent.minLon) / lonSpan) * size;
    const x1 = ((east - extent.minLon) / lonSpan) * size;
    const y0 = ((extent.maxLat - north) / latSpan) * size;
    const y1 = ((extent.maxLat - south) / latSpan) * size;
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(tmp, x0, y0, x1 - x0, y1 - y0);
  },

  updateBasemapAttribution() {
    if (!this.elBasemapCredit) return;
    // Each provider requires its own credit line to be displayed.
    const credits = {
      osm: '© OpenStreetMap contributors',
      voyager: '© OpenStreetMap contributors · © CARTO',
      dark: '© OpenStreetMap contributors · © CARTO',
      satellite: 'Imagery © Esri, Maxar, Earthstar Geographics',
    };
    this.elBasemapCredit.textContent = credits[this.basemapProvider] || credits.osm;
    if (this.elBasemapNote) this.elBasemapNote.textContent = '';
  },

  cancelPendingTiles() {
    const pending = this.pendingTileBuild;
    if (!pending) return;
    pending.cancelled = true;
    clearTimeout(pending.uploadTimer);
    // Dropping the src aborts the in-flight request in every current browser.
    pending.images.forEach((img) => {
      img.onload = null;
      img.onerror = null;
      img.src = '';
    });
    this.pendingTileBuild = null;
  },
};
