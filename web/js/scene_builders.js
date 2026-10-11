// Scene content: route ribbons, pins, the avatar, other profiles, and the
// corridor buildings and trees.
// Methods of Studio3DApp (app3d.js), mixed into its prototype.
import * as THREE from './three.module.js';
import { KinematicAvatarRig } from './KinematicAvatarRig.js';
import { disposeHierarchy, mergeGeometries, ribbonGeometry } from './scene_geometry.js';

export const sceneBuilderMethods = {
  corridorBuildings() {
    const list = this.routeData?.properties?.corridor_buildings || this.routeCollection?.properties?.corridor_buildings;
    return Array.isArray(list) ? list : [];
  },

  corridorTrees() {
    const list = this.routeData?.properties?.corridor_trees || this.routeCollection?.properties?.corridor_trees;
    return Array.isArray(list) ? list : [];
  },

  buildClassyRoadRibbon() {
    if (!this.activePath || this.scenePoints.length < 2) return;
    const profColor = new THREE.Color(this.profileColor(this.routeData));
    const side = THREE.DoubleSide;

    // Layer 1: asphalt band, 7.5 m wide, 0.8 m above the route line.
    this.roadMesh = new THREE.Mesh(
      ribbonGeometry(this.activePath, 7.5, 0.8),
      new THREE.MeshStandardMaterial({
        color: 0x1e293b, roughness: 0.7, metalness: 0.15, side,
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2,
      }),
    );
    this.roadMesh.receiveShadow = true;
    this.roadMesh.renderOrder = 8;
    this.scene.add(this.roadMesh);

    // Layer 2: translucent glow band in the profile colour.
    this.glowTubeMesh = new THREE.Mesh(
      ribbonGeometry(this.activePath, 2.6, 1.1),
      new THREE.MeshBasicMaterial({
        color: profColor, transparent: true, opacity: 0.35, side,
        polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4,
      }),
    );
    this.glowTubeMesh.renderOrder = 9;
    this.scene.add(this.glowTubeMesh);

    // Layer 3: bright centre line.
    this.centerlineMesh = new THREE.Mesh(
      ribbonGeometry(this.activePath, 0.9, 1.4),
      new THREE.MeshStandardMaterial({
        color: 0xffffff, emissive: profColor, emissiveIntensity: 0.95, roughness: 0.15, side,
        polygonOffset: true, polygonOffsetFactor: -3, polygonOffsetUnits: -6,
      }),
    );
    this.centerlineMesh.renderOrder = 10;
    this.scene.add(this.centerlineMesh);

    // Layer 4: pulse beacon (moves ahead of the walker during playback).
    this.pulseBeacon = new THREE.Mesh(
      new THREE.SphereGeometry(1.6, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.9 }),
    );
    this.scene.add(this.pulseBeacon);
  },

  buildPinMarkers() {
    if (!this.scenePoints.length) return;
    const pStart = this.scenePoints[0].clone();
    const pEnd = this.scenePoints[this.scenePoints.length - 1].clone();

    // Helper: Canvas texture for 3D circular letter badge
    const createLetterTexture = (letter, bgColorHex, textColorHex = '#ffffff') => {
      const c = document.createElement('canvas');
      c.width = 256;
      c.height = 256;
      const cx = c.getContext('2d');
      cx.fillStyle = bgColorHex;
      cx.beginPath();
      cx.arc(128, 128, 120, 0, Math.PI * 2);
      cx.fill();
      cx.lineWidth = 14;
      cx.strokeStyle = '#ffffff';
      cx.stroke();
      cx.fillStyle = textColorHex;
      cx.font = 'bold 140px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      cx.textAlign = 'center';
      cx.textBaseline = 'middle';
      cx.fillText(letter, 128, 134);
      const t = new THREE.CanvasTexture(c);
      t.minFilter = THREE.LinearFilter;
      return t;
    };

    // Helper: Floating billboard badge sprite with geolocator icon + Letter + Label
    const createFloatingBadgeSprite = (letter, label, colorHex) => {
      const measureCanvas = document.createElement('canvas');
      const mCtx = measureCanvas.getContext('2d');
      const textFont = 'bold 36px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      mCtx.font = textFont;
      const textMetrics = mCtx.measureText(label);
      const textWidth = Math.ceil(textMetrics.width);

      const circleD = 60;
      const padLeft = 14;
      const spacing = 16;
      const padRight = 26;
      const pillW = padLeft + circleD + spacing + textWidth + padRight;
      const pillH = 86;
      const margin = 14;

      const canvasW = Math.ceil(pillW + margin * 2);
      const canvasH = Math.ceil(pillH + margin * 2);

      const canvas = document.createElement('canvas');
      canvas.width = canvasW;
      canvas.height = canvasH;
      const ctx = canvas.getContext('2d');

      const x = margin;
      const y = margin;
      const r = pillH / 2;

      ctx.save();
      // Drop Shadow
      ctx.shadowColor = 'rgba(0, 0, 0, 0.45)';
      ctx.shadowBlur = 12;
      ctx.shadowOffsetX = 0;
      ctx.shadowOffsetY = 6;

      // Outer pill background
      ctx.fillStyle = colorHex;
      ctx.beginPath();
      if (typeof ctx.roundRect === 'function') {
        ctx.roundRect(x, y, pillW, pillH, r);
      } else {
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + pillW - r, y);
        ctx.quadraticCurveTo(x + pillW, y, x + pillW, y + r);
        ctx.lineTo(x + pillW, y + pillH - r);
        ctx.quadraticCurveTo(x + pillW, y + pillH, x + pillW - r, y + pillH);
        ctx.lineTo(x + r, y + pillH);
        ctx.quadraticCurveTo(x, y + pillH, x, y + pillH - r);
        ctx.lineTo(x, y + r);
        ctx.quadraticCurveTo(x, y, x + r, y);
        ctx.closePath();
      }
      ctx.fill();

      // Crisp white border
      ctx.shadowColor = 'transparent';
      ctx.lineWidth = 4;
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();

      // White circle for letter badge on the left
      const badgeCenterX = x + padLeft + circleD / 2;
      const badgeCenterY = y + pillH / 2;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(badgeCenterX, badgeCenterY, circleD / 2, 0, Math.PI * 2);
      ctx.fill();

      // Bold letter inside circle
      ctx.fillStyle = colorHex;
      ctx.font = 'bold 44px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(letter, badgeCenterX, badgeCenterY + 2);

      // Label text
      ctx.fillStyle = '#ffffff';
      ctx.font = textFont;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, x + padLeft + circleD + spacing, y + pillH / 2 + 1);

      ctx.restore();

      const texture = new THREE.CanvasTexture(canvas);
      texture.minFilter = THREE.LinearFilter;
      texture.magFilter = THREE.LinearFilter;
      const mat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
      const sprite = new THREE.Sprite(mat);
      const spriteHeight = 7.5;
      const spriteWidth = (canvasW / canvasH) * spriteHeight;
      sprite.scale.set(spriteWidth, spriteHeight, 1);
      return sprite;
    };

    const createGeolocatorPin = (letter, label, colorHex, colorCss) => {
      const pinGroup = new THREE.Group();

      // 1. Inverted cone pointer to ground (tip touches ground at y=0)
      const coneGeo = new THREE.ConeGeometry(3.2, 10, 24);
      coneGeo.rotateX(Math.PI);
      const pinMat = new THREE.MeshStandardMaterial({
        color: colorHex,
        roughness: 0.3,
        metalness: 0.2,
        emissive: colorHex,
        emissiveIntensity: 0.25,
      });
      const cone = new THREE.Mesh(coneGeo, pinMat);
      cone.position.y = 5.0;
      cone.castShadow = true;
      pinGroup.add(cone);

      // 2. Upper bulb / circular head
      const sphereGeo = new THREE.SphereGeometry(3.6, 24, 24);
      const sphere = new THREE.Mesh(sphereGeo, pinMat);
      sphere.position.y = 11.5;
      sphere.castShadow = true;
      pinGroup.add(sphere);

      // 3. 3D circular letter badges on front and back of the sphere
      const letterTex = createLetterTexture(letter, colorCss, '#ffffff');
      const discGeo = new THREE.CircleGeometry(2.6, 24);
      const discMat = new THREE.MeshBasicMaterial({ map: letterTex, side: THREE.DoubleSide });

      const frontDisc = new THREE.Mesh(discGeo, discMat);
      frontDisc.position.set(0, 11.5, 3.65);
      pinGroup.add(frontDisc);

      const backDisc = new THREE.Mesh(discGeo, discMat);
      backDisc.position.set(0, 11.5, -3.65);
      backDisc.rotateY(Math.PI);
      pinGroup.add(backDisc);

      // 4. Ground Target / Radar Ring
      const ringGeo = new THREE.RingGeometry(2.0, 4.8, 32);
      ringGeo.rotateX(-Math.PI / 2);
      const ringMat = new THREE.MeshBasicMaterial({
        color: colorHex,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.8,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.position.y = 0.15;
      pinGroup.add(ring);

      // 5. Floating Billboard Badge Sprite (always faces camera, clear at any distance)
      const badgeSprite = createFloatingBadgeSprite(letter, label, colorCss);
      badgeSprite.position.y = 20.5;
      pinGroup.add(badgeSprite);

      return pinGroup;
    };

    // Point A: Emerald Green (#059669)
    const pinA = createGeolocatorPin('A', 'Point A (Origin)', 0x059669, '#059669');
    pinA.position.copy(pStart);
    this.pinsGroup.add(pinA);

    // Point B: Ruby Red (#dc2626)
    const pinB = createGeolocatorPin('B', 'Point B (Destination)', 0xdc2626, '#dc2626');
    pinB.position.copy(pEnd);
    this.pinsGroup.add(pinB);
  },

  buildAvatar() {
    const activeKey = this.activeProfileKey || this.routeData?.properties?.profile_key || 'adult';
    const activeColor = this.profileColor(this.routeData);
    this.avatarRig.setProfile(activeKey, activeColor);
    this.avatarRigs = [this.avatarRig];

    this.routeVisuals.forEach((visual) => {
      const color = this.profileColor(visual.feature);
      if (visual.key === activeKey) {
        visual.rig = this.avatarRig;
        return;
      }
      const rig = new KinematicAvatarRig(this.scene);
      rig.setProfile(visual.key, color);
      visual.rig = rig;
      this.avatarRigs.push(rig);
    });
    this.buildRouteOverlays();
    this.applyProfileAppearance();
  },

  // The other profiles: coloured ribbons wide enough to compare at any zoom
  // (WebGL draws THREE.Line one pixel wide on most systems), stacked a few
  // centimetres apart so overlapping routes do not flicker.
  buildRouteOverlays() {
    while (this.routeOverlayGroup.children.length) {
      const child = this.routeOverlayGroup.children[0];
      this.routeOverlayGroup.remove(child);
      disposeHierarchy(child);
    }
    let layer = 0;
    this.routeVisuals.forEach((visual) => {
      if (visual.key === this.activeProfileKey || visual.points.length < 2) return;
      layer += 1;
      const material = new THREE.MeshBasicMaterial({
        color: this.profileColor(visual.feature), transparent: true, opacity: 0.85, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: -1 - layer, polygonOffsetUnits: -2 - layer,
      });
      const ribbon = new THREE.Mesh(ribbonGeometry(visual.path, 2.4, 0.9 + layer * 0.12), material);
      ribbon.userData.profileKey = visual.key;
      ribbon.renderOrder = 7;
      this.routeOverlayGroup.add(ribbon);
    });
  },

  buildUrbanEnvironment() {
    this.buildBuildings(this.corridorBuildings());
    this.buildTrees(this.corridorTrees());
    this.buildingsGroup.visible = this.showBuildings;
    this.treesGroup.visible = this.showTrees;
  },

  // Buildings: one merged mesh per facade material (five draw calls in all)
  // instead of one mesh per building. Each footprint sits on the lowest ground
  // under its corners, so no building floats above a slope.
  buildBuildings(buildings) {
    const palette = [0xf8fafc, 0xe2e8f0, 0xcbd5e1, 0x94a3b8, 0x64748b];
    const buckets = palette.map(() => []);
    buildings.forEach((bld, idx) => {
      const coords = bld.polygon || bld.coordinates || [];
      if (coords.length < 3) return;
      const scenePts = coords.map(([lon, lat]) => this.lonLatToSceneMeters(lon, lat, this.baseElevation));
      let groundY = Infinity;
      scenePts.forEach((p) => {
        groundY = Math.min(groundY, this.groundY(p.x, p.z));
      });
      if (!Number.isFinite(groundY)) {
        const fallbackEle = bld.base_elevation_m !== undefined ? Number(bld.base_elevation_m) : this.baseElevation;
        groundY = (fallbackEle - this.baseElevation) * this.elevationExaggeration;
      }
      const shape = new THREE.Shape();
      shape.moveTo(scenePts[0].x, -scenePts[0].z);
      for (let i = 1; i < scenePts.length; i++) shape.lineTo(scenePts[i].x, -scenePts[i].z);
      shape.closePath();
      const height = Math.max(6.0, Number(bld.height_m) || (bld.levels ? bld.levels * 3.2 : 12.0));
      const geo = new THREE.ExtrudeGeometry(shape, {
        depth: height, bevelEnabled: true, bevelSegments: 1, steps: 1, bevelSize: 0.2, bevelThickness: 0.2,
      });
      geo.rotateX(-Math.PI / 2);
      geo.translate(0, groundY, 0);
      buckets[idx % palette.length].push(geo);
    });
    buckets.forEach((geos, i) => {
      if (!geos.length) return;
      const merged = mergeGeometries(geos);
      geos.forEach((g) => g.dispose());
      const mat = new THREE.MeshStandardMaterial({ color: palette[i], roughness: 0.7, metalness: 0.08 });
      const mesh = new THREE.Mesh(merged, mat);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      this.buildingsGroup.add(mesh);
    });
  },

  // Trees: one InstancedMesh per part and colour (seven draw calls for any
  // number of trees) instead of four to five meshes, each with its own
  // material, per tree.
  buildTrees(trees) {
    const parts = {
      trunk: { geo: new THREE.CylinderGeometry(0.75, 1.15, 1, 7).translate(0, 0.5, 0), color: 0x452b19, flat: false, mats: [] },
      leaf0: { geo: new THREE.DodecahedronGeometry(1, 1), color: 0x15803d, flat: true, mats: [] },
      leaf1: { geo: new THREE.DodecahedronGeometry(1, 1), color: 0x16a34a, flat: true, mats: [] },
      leaf2: { geo: new THREE.DodecahedronGeometry(1, 1), color: 0x22c55e, flat: true, mats: [] },
      pine0: { geo: new THREE.ConeGeometry(1, 1, 7), color: 0x14532d, flat: true, mats: [] },
      pine1: { geo: new THREE.ConeGeometry(1, 1, 7), color: 0x166534, flat: true, mats: [] },
      pine2: { geo: new THREE.ConeGeometry(1, 1, 7), color: 0x15803d, flat: true, mats: [] },
    };
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const place = (part, x, y, z, sx, sy, sz) => {
      parts[part].mats.push(m.compose(new THREE.Vector3(x, y, z), q, new THREE.Vector3(sx, sy, sz)).clone());
    };
    trees.forEach((tree) => {
      const coords = tree.coordinates || [];
      if (coords.length < 2) return;
      const pos = this.lonLatToSceneMeters(Number(coords[0]), Number(coords[1]), this.baseElevation);
      const y0 = this.groundY(pos.x, pos.z);
      const height = Math.max(4.5, Number(tree.height_m || 8.0));
      const canopyR = Math.max(1.8, Number(tree.canopy_radius_m || 3.2));
      const trunkH = Math.max(1.2, Number(tree.trunk_height_m || (height * 0.35)));
      const trunkR = Math.max(0.18, Number(tree.trunk_radius_m || 0.28));
      place('trunk', pos.x, y0, pos.z, trunkR, trunkH, trunkR);
      if (tree.tree_type === 'conifer' || tree.tree_type === 'pine') {
        const crown = height - trunkH;
        place('pine0', pos.x, y0 + trunkH + crown * 0.26, pos.z, canopyR, crown * 0.52, canopyR);
        place('pine1', pos.x, y0 + trunkH + crown * 0.52, pos.z, canopyR * 0.78, crown * 0.48, canopyR * 0.78);
        place('pine2', pos.x, y0 + trunkH + crown * 0.78, pos.z, canopyR * 0.52, crown * 0.42, canopyR * 0.52);
      } else {
        place('leaf0', pos.x, y0 + trunkH + canopyR * 0.72, pos.z, canopyR * 0.95 * 1.1, canopyR * 0.95 * 0.82, canopyR * 0.95 * 1.05);
        place('leaf1', pos.x, y0 + trunkH + canopyR * 1.18, pos.z, canopyR * 0.82 * 0.96, canopyR * 0.82 * 0.88, canopyR * 0.82 * 0.96);
        place('leaf2', pos.x, y0 + trunkH + canopyR * 1.58, pos.z, canopyR * 0.58 * 0.85, canopyR * 0.58 * 0.95, canopyR * 0.58 * 0.85);
      }
    });
    Object.values(parts).forEach((part) => {
      if (!part.mats.length) {
        part.geo.dispose();
        return;
      }
      const mat = new THREE.MeshStandardMaterial({
        color: part.color, roughness: part.flat ? 0.78 : 0.92, metalness: 0.04, flatShading: part.flat,
      });
      const mesh = new THREE.InstancedMesh(part.geo, mat, part.mats.length);
      part.mats.forEach((matrix, i) => mesh.setMatrixAt(i, matrix));
      mesh.instanceMatrix.needsUpdate = true;
      mesh.computeBoundingSphere();
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      this.treesGroup.add(mesh);
    });
  },
};
