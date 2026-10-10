// Route layers: which profiles are shown, animated and compared, and the
// layer panel and comparison table.
// Methods of Studio3DApp (app3d.js), mixed into its prototype.


export const routeLayerMethods = {
  selectProfile(profileKey) {
    if (!this.routeFeatures.some((feature) => (feature?.properties?.profile_key || 'adult') === profileKey)) return;
    if (this.activeProfileKey === profileKey && this.selectedProfileKeys.has(profileKey)) return;
    this.activeProfileKey = profileKey;
    this.selectedProfileKeys.add(profileKey);
    this.refreshActiveRoute(true);
  },

  toggleProfileAnimation(profileKey) {
    if (this.selectedProfileKeys.has(profileKey)) this.selectedProfileKeys.delete(profileKey);
    else this.selectedProfileKeys.add(profileKey);
    if (profileKey === this.activeProfileKey && !this.selectedProfileKeys.has(profileKey)) {
      this.activeProfileKey = [...this.selectedProfileKeys][0] || null;
    }
    this.refreshActiveRoute(false);
    this.updateAvatarPosition();
  },

  setProfileOpacity(profileKey, value) {
    const opacity = Math.max(0.0, Math.min(1.0, Number(value)));
    this.profileOpacity.set(profileKey, opacity);
    this.applyProfileAppearance();
  },

  applyProfileAppearance() {
    const opacityFor = (key) => this.profileOpacity.has(key) ? this.profileOpacity.get(key) : 1.0;
    this.routeVisuals.forEach((visual) => {
      const opacity = opacityFor(visual.key);
      if (visual.rig?.subMeshGroup) {
        visual.rig.subMeshGroup.traverse((node) => {
          if (!node.material) return;
          const materials = Array.isArray(node.material) ? node.material : [node.material];
          materials.forEach((material) => {
            material.transparent = opacity < 1.0;
            material.opacity = opacity;
            material.needsUpdate = true;
          });
        });
      }
    });
    this.routeOverlayGroup.children.forEach((line) => {
      const key = line.userData?.profileKey;
      if (key && line.material) {
        line.material.opacity = opacityFor(key);
        line.material.transparent = line.material.opacity < 1.0;
      }
    });
    const activeOpacity = opacityFor(this.activeProfileKey);
    [this.centerlineMesh, this.glowTubeMesh].forEach((mesh) => {
      if (!mesh?.material) return;
      mesh.material.opacity = activeOpacity;
      mesh.material.transparent = activeOpacity < 1.0;
      mesh.material.needsUpdate = true;
    });
  },

  profileColor(feature) {
    const palette = ['#0284c7', '#8b5cf6', '#d946ef', '#10b981', '#f59e0b', '#ef4444', '#14b8a6', '#6366f1'];
    const color = feature?.properties?.profile_color;
    if (typeof color === 'string' && color.trim()) return color;
    const index = Math.max(0, this.routeFeatures.indexOf(feature));
    return palette[index % palette.length];
  },

  renderLayerPanel() {
    if (!this.elLayerList) return;
    this.elLayerList.innerHTML = '';
    if (this.elLayerCount) this.elLayerCount.textContent = String(this.routeFeatures.length);
    if (!this.routeFeatures.length) {
      this.elLayerList.innerHTML = '<div class="layer-empty">Waiting for QGIS routes...</div>';
      return;
    }
    this.routeFeatures.forEach((feature) => {
      const props = feature.properties || {};
      const key = props.profile_key || 'adult';
      const color = this.profileColor(feature);
      const row = document.createElement('div');
      row.className = `layer-row${key === this.activeProfileKey ? ' active' : ''}${this.selectedProfileKeys.has(key) ? ' selected' : ''}`;
      row.style.setProperty('--layer-color', color);
      row.title = `${props.profile_name || key} | ${Number(props.distance_km || 0).toFixed(2)} km | ${Number(props.duration_min || 0).toFixed(1)} min | +${Number(props.elevation_gain_m || 0).toFixed(1)} m | ${Number(props.max_slope_pct || 0).toFixed(1)}% | ${Number(props.calories_kcal || 0).toFixed(0)} kcal`;
      row.addEventListener('click', () => this.selectProfile(key));

      const toggle = document.createElement('button');
      toggle.className = `layer-toggle${this.selectedProfileKeys.has(key) ? ' on' : ''}`;
      toggle.type = 'button';
      toggle.textContent = this.selectedProfileKeys.has(key) ? '●' : '○';
      toggle.setAttribute('aria-label', `Toggle ${props.profile_name || key} animation`);
      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        this.toggleProfileAnimation(key);
      });

      const text = document.createElement('div');
      text.className = 'layer-name';
      text.textContent = props.profile_name || key;
      const meta = document.createElement('span');
      meta.className = 'layer-meta';
      meta.textContent = `${Number(props.distance_km || 0).toFixed(2)} km · ${Number(props.duration_min || 0).toFixed(1)} min`;
      text.appendChild(meta);

      const opacity = document.createElement('input');
      opacity.type = 'range';
      opacity.className = 'layer-opacity';
      opacity.min = '0';
      opacity.max = '100';
      opacity.step = '1';
      opacity.value = String(Math.round((this.profileOpacity.get(key) ?? 1.0) * 100));
      opacity.title = 'Layer opacity';
      opacity.addEventListener('click', (event) => event.stopPropagation());
      opacity.addEventListener('input', (event) => {
        event.stopPropagation();
        this.setProfileOpacity(key, Number(event.target.value) / 100.0);
      });

      const swatch = document.createElement('span');
      swatch.className = 'layer-swatch';
      row.appendChild(toggle);
      row.appendChild(text);
      row.appendChild(opacity);
      row.appendChild(swatch);
      this.elLayerList.appendChild(row);
    });
    this.renderComparisonTable();
  },

  // Side-by-side numbers for every computed profile, with the difference to
  // the selected one, so routes can be compared without hovering each layer.
  renderComparisonTable() {
    const host = document.getElementById('compareTable');
    if (!host) return;
    host.innerHTML = '';
    if (this.routeFeatures.length < 2) return;
    const active = this.routeFeatures.find((f) => (f?.properties?.profile_key || 'adult') === this.activeProfileKey);
    const ref = active?.properties || {};
    const cols = [
      ['Profile', null, null],
      ['km', 'distance_km', 2],
      ['min', 'duration_min', 1],
      ['climb m', 'elevation_gain_m', 0],
      ['max %', 'max_slope_pct', 1],
    ];
    const table = document.createElement('table');
    table.className = 'compare-table';
    const caption = document.createElement('caption');
    caption.textContent = 'Compared with the selected profile';
    table.appendChild(caption);
    const head = table.createTHead().insertRow();
    cols.forEach(([label]) => {
      const th = document.createElement('th');
      th.scope = 'col';
      th.textContent = label;
      head.appendChild(th);
    });
    const body = table.createTBody();
    this.routeFeatures.forEach((feature) => {
      const props = feature.properties || {};
      const key = props.profile_key || 'adult';
      const tr = body.insertRow();
      if (key === this.activeProfileKey) tr.className = 'active';
      cols.forEach(([, field, digits], i) => {
        const cell = i === 0 ? document.createElement('th') : tr.insertCell();
        if (i === 0) {
          cell.scope = 'row';
          cell.style.setProperty('--layer-color', this.profileColor(feature));
          cell.textContent = props.profile_name || key;
          tr.appendChild(cell);
          return;
        }
        const value = Number(props[field] || 0);
        cell.textContent = value.toFixed(digits);
        if (key !== this.activeProfileKey && Number.isFinite(Number(ref[field]))) {
          const diff = value - Number(ref[field]);
          if (Math.abs(diff) >= Math.pow(10, -digits) / 2) {
            const delta = document.createElement('span');
            delta.className = diff > 0 ? 'delta up' : 'delta down';
            delta.textContent = ` ${diff > 0 ? '+' : ''}${diff.toFixed(digits)}`;
            cell.appendChild(delta);
          }
        }
      });
    });
    host.appendChild(table);
  },

  updateHudMetrics() {
    if (!this.routeData || !this.routeData.properties) {
      if (this.elDist) this.elDist.textContent = '—';
      if (this.elTime) this.elTime.textContent = '—';
      if (this.elClimb) this.elClimb.textContent = '—';
      if (this.elSlope) this.elSlope.textContent = '—';
      if (this.elKcal) this.elKcal.textContent = '—';
      return;
    }
    const props = this.routeData.properties || {};
    const dist = Number(props.distance_km || 0.0);
    const dur = Number(props.duration_min || 0.0);
    const climb = Number(props.elevation_gain_m || 0.0);
    const slope = Number(props.max_slope_pct || 0.0);
    const kcal = Number(props.calories_kcal || 0.0);

    if (this.elDist) this.elDist.textContent = `${dist.toFixed(2)} km`;
    if (this.elTime) this.elTime.textContent = `${dur.toFixed(1)} min`;
    if (this.elClimb) this.elClimb.textContent = `+${climb.toFixed(1)} m`;
    if (this.elSlope) this.elSlope.textContent = `${slope.toFixed(1)}%`;
    if (this.elKcal) this.elKcal.textContent = `${kcal.toFixed(0)} kcal`;
  },
};
