// User input: buttons, keyboard, pointer and camera controls.
// Methods of Studio3DApp (app3d.js), mixed into its prototype.
import * as THREE from './three.module.js';
import { dayOfYearFromIso } from './solar.js';

export const controlsMethods = {
  bindEvents() {
    const sunHour = document.getElementById('sunHour');
    if (sunHour) sunHour.addEventListener('input', () => this.setSun({ solarHour: Number(sunHour.value) }));
    const sunDate = document.getElementById('sunDate');
    if (sunDate) {
      sunDate.addEventListener('change', () => {
        const day = dayOfYearFromIso(sunDate.value);
        if (day !== null) this.setSun({ dayOfYear: day });
      });
    }
    window.addEventListener('resize', () => {
      this.width = window.innerWidth;
      this.height = window.innerHeight;
      this.camera.aspect = this.width / Math.max(1, this.height);
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(this.width, this.height);
      if (this.routeData) this.renderProfileChart();
      this.requestRender();
    });
    // Any interaction with the panels can change the scene (toggles, opacity,
    // basemap); with on-demand rendering it must ask for a frame.
    ['click', 'input', 'keydown', 'pointerup'].forEach((type) => {
      document.addEventListener(type, () => this.requestRender(), true);
    });

    // Keyboard: Space plays/pauses, arrows move along the route (Shift: 5 %),
    // Home/End jump to the ends, R resets the view.
    window.addEventListener('keydown', (e) => {
      const target = e.target;
      const tag = (target?.tagName || '').toLowerCase();
      if (['input', 'textarea', 'select'].includes(tag) || target?.isContentEditable) return;
      if (target?.closest && target.closest('#elevationChart')) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const step = e.shiftKey ? 0.05 : 0.01;
      if (e.key === ' ' && tag !== 'button') {
        e.preventDefault();
        this.togglePlay();
      } else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        this.setProgress(this.progress + (e.key === 'ArrowRight' ? step : -step));
      } else if (e.key === 'Home' || e.key === 'End') {
        e.preventDefault();
        this.setProgress(e.key === 'Home' ? 0 : 1);
      } else if (e.key === 'r' || e.key === 'R') {
        this.resetCameraView();
      }
    });

    const btnPlay = document.getElementById('btnPlay');
    if (btnPlay) {
      btnPlay.addEventListener('click', () => this.togglePlay());
    }

    const btnLoop = document.getElementById('btnLoop');
    if (btnLoop) {
      btnLoop.addEventListener('click', () => {
        this.loopPlayback = !this.loopPlayback;
        btnLoop.classList.toggle('active', this.loopPlayback);
        btnLoop.setAttribute('aria-pressed', String(this.loopPlayback));
      });
    }

    if (this.elScrubber) {
      this.elScrubber.addEventListener('input', (e) => {
        this.progress = Math.max(0.0, Math.min(1.0, parseFloat(e.target.value) / 1000.0));
        this.updateAvatarPosition();
      });
    }

    // Scenario Switcher
    document.querySelectorAll('.scenario-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.scenario-btn').forEach((b) => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.switchScenario(e.currentTarget.dataset.scenario || 'shortest');
      });
    });

    // Speed options
    document.querySelectorAll('.speed-opt').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.speed-opt').forEach((b) => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.playbackSpeed = parseFloat(e.currentTarget.dataset.speed || 1.0);
      });
    });

    // Camera modes
    document.querySelectorAll('.cam-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.cam-btn').forEach((b) => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.cameraMode = e.currentTarget.dataset.cam || 'orbit';
        this.controls.enabled = (this.cameraMode === 'orbit');
      });
    });

    // Basemap Provider Selection (OSM, Satellite, Voyager, Dark)
    document.querySelectorAll('.bmap-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.bmap-btn').forEach((b) => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.basemapProvider = e.currentTarget.dataset.provider || 'osm';
        this.updateBasemapAttribution();
        this.updateBasemapTexture();
      });
    });

    // Exaggeration button
    const btnExag = document.getElementById('btnExaggeration');
    if (btnExag) {
      btnExag.addEventListener('click', () => {
        const exags = [1.0, 1.5, 2.0, 3.0];
        const nextIdx = (exags.indexOf(this.elevationExaggeration) + 1) % exags.length;
        this.elevationExaggeration = exags[nextIdx];
        if (this.lblExag) this.lblExag.textContent = `${this.elevationExaggeration.toFixed(1)}x`;
        // Scene heights depend on the exaggeration, so the route points (not
        // just the meshes) must be recomputed.
        if (this.routeData) this.refreshActiveRoute(false);
      });
    }

    // Toggle Buildings
    const btnBld = document.getElementById('btnBuildings');
    if (btnBld) {
      btnBld.addEventListener('click', () => {
        this.showBuildings = !this.showBuildings;
        this.buildingsGroup.visible = this.showBuildings;
        btnBld.classList.toggle('active', this.showBuildings);
        const b = btnBld.querySelector('b');
        if (b) b.textContent = this.showBuildings ? 'ON' : 'OFF';
      });
    }

    // Toggle Trees
    const btnTrees = document.getElementById('btnTrees');
    if (btnTrees) {
      btnTrees.addEventListener('click', () => {
        this.showTrees = !this.showTrees;
        this.treesGroup.visible = this.showTrees;
        if (this.parksGroup) this.parksGroup.visible = this.showTrees;
        btnTrees.classList.toggle('active', this.showTrees);
        const b = btnTrees.querySelector('b');
        if (b) b.textContent = this.showTrees ? 'ON' : 'OFF';
      });
    }

    // Cycle 3D Ribbon Color Mode (Profile / Slope / Speed)
    const btnRibbon = document.getElementById('btnRibbonMode');
    if (btnRibbon) {
      btnRibbon.addEventListener('click', () => {
        const modes = ['profile', 'slope', 'speed'];
        const labels = { profile: 'Profile', slope: 'Slope %', speed: 'Speed' };
        const next = modes[(modes.indexOf(this.ribbonColorMode || 'profile') + 1) % modes.length];
        this.ribbonColorMode = next;
        const lbl = document.getElementById('lblRibbonMode');
        if (lbl) lbl.textContent = labels[next];
        btnRibbon.classList.toggle('active', next !== 'profile');
        if (this.routeData) {
          this.rebuildScene();
        }
      });
    }

    // Toggle Basemap
    const btnBasemap = document.getElementById('btnBasemap');
    if (btnBasemap) {
      btnBasemap.addEventListener('click', () => {
        this.showBasemap = !this.showBasemap;
        if (this.terrainMesh) {
          this.terrainMesh.visible = this.showBasemap;
        }
        if (this.skirtMesh) {
          this.skirtMesh.visible = this.showBasemap;
        }
        btnBasemap.classList.toggle('active', this.showBasemap);
        const b = btnBasemap.querySelector('b');
        if (b) b.textContent = this.showBasemap ? 'ON' : 'OFF';
      });
    }

    // Slicer button & height slider
    const btnSlicer = document.getElementById('btnSlicer');
    const slicerControls = document.getElementById('slicerControls');
    const slicerHeight = document.getElementById('slicerHeight');
    const lblSlicerHeight = document.getElementById('lblSlicerHeight');
    if (btnSlicer) {
      btnSlicer.addEventListener('click', () => {
        const active = this.slicerSystem.toggle();
        btnSlicer.classList.toggle('active', active);
        if (slicerControls) slicerControls.hidden = !active;
        if (active && slicerHeight) {
          slicerHeight.min = String(this.slicerSystem.minHeight);
          slicerHeight.max = String(this.slicerSystem.maxHeight);
          slicerHeight.value = String(this.slicerSystem.sliceHeight.toFixed(1));
          if (lblSlicerHeight) lblSlicerHeight.textContent = `${this.slicerSystem.sliceHeight.toFixed(1)} m`;
        }
      });
    }
    if (slicerHeight) {
      slicerHeight.addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        if (Number.isFinite(val)) {
          this.slicerSystem.setSliceHeight(val);
          if (lblSlicerHeight) lblSlicerHeight.textContent = `${this.slicerSystem.sliceHeight.toFixed(1)} m`;
          this.requestRender();
        }
      });
    }

    // Voice cues button
    const btnVoice = document.getElementById('btnVoice');
    if (btnVoice) {
      btnVoice.addEventListener('click', () => {
        const active = this.voiceSystem.toggle();
        btnVoice.classList.toggle('active', active);
        const b = btnVoice.querySelector('b');
        if (b) b.textContent = active ? 'ON' : 'OFF';
      });
    }

    // Reset view
    const btnReset = document.getElementById('btnResetView');
    if (btnReset) {
      btnReset.addEventListener('click', () => this.resetCameraView());
    }

    // Snapshot button
    const btnSnap = document.getElementById('btnSnapshot');
    if (btnSnap) {
      btnSnap.addEventListener('click', (e) => this.takeSnapshot(e.shiftKey ? 4 : 2));
    }

    // Video record button
    const btnRec = document.getElementById('btnRecord');
    if (btnRec) {
      btnRec.addEventListener('click', () => this.toggleRecordVideo());
    }

    // Compass click -> snap to north
    if (this.compassRose) {
      this.compassRose.addEventListener('click', () => this.snapToNorth());
    }
  },

  snapToNorth() {
    if (!this.controls) return;
    const target = this.controls.target.clone();
    const offset = this.camera.position.clone().sub(target);
    const radius = Math.sqrt(offset.x * offset.x + offset.z * offset.z);
    const distance = Math.max(radius, 60);
    this.camera.position.set(target.x, target.y + Math.max(offset.y, 40), target.z + distance);
    this.controls.target.copy(target);
    this.controls.update();
  },

  resetCameraView() {
    if (!this.scenePoints.length) return;
    const box = new THREE.Box3().setFromPoints(this.scenePoints);
    const center = new THREE.Vector3();
    box.getCenter(center);
    const size = new THREE.Vector3();
    box.getSize(size);

    const maxDim = Math.max(size.x, size.z, 120);
    this.camera.position.set(center.x, center.y + maxDim * 0.9, center.z + maxDim * 1.0);
    this.controls.target.copy(center);
    this.controls.update();
    if (this.controls.saveState) {
      this.controls.saveState();
    }
  },
};
