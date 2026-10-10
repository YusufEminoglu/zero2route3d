// Snapshots and video recording, with a caption drawn over the frame.
// Methods of Studio3DApp (app3d.js), mixed into its prototype.
import * as THREE from './three.module.js';
import { setLiveStatus } from './live_sync.js';
import { SAFE_COLOR } from './colors.js';

export const captureMethods = {
  // Caption burnt into snapshots and videos: profile, totals, the walker's
  // position, and the basemap credit the tile providers require.
  drawCaptureHud(ctx, w, h, scale) {
    const props = this.routeData?.properties || {};
    const pad = 14 * scale;
    const font = (size, weight = 600) => `${weight} ${size * scale}px -apple-system, "Segoe UI", Roboto, sans-serif`;
    const lines = [];
    if (this.routeData) {
      lines.push([font(15, 700), props.profile_name || props.profile_key || 'Route']);
      lines.push([font(13), `${Number(props.distance_km || 0).toFixed(2)} km · ${Number(props.duration_min || 0).toFixed(1)} min · +${Number(props.elevation_gain_m || 0).toFixed(0)} m`]);
      if (this.chart) lines.push([font(12, 500), this.chart.tip.textContent]);
    }
    if (lines.length) {
      ctx.save();
      let boxW = 0;
      lines.forEach(([f, t]) => { ctx.font = f; boxW = Math.max(boxW, ctx.measureText(t).width); });
      const lineH = 20 * scale;
      const boxH = lines.length * lineH + pad;
      ctx.fillStyle = 'rgba(15, 23, 42, 0.72)';
      ctx.fillRect(pad, h - boxH - pad, boxW + pad * 2, boxH);
      ctx.fillStyle = SAFE_COLOR.test(String(props.profile_color || '')) ? props.profile_color : '#38bdf8';
      ctx.fillRect(pad, h - boxH - pad, 4 * scale, boxH);
      ctx.fillStyle = '#ffffff';
      ctx.textBaseline = 'top';
      lines.forEach(([f, t], i) => {
        ctx.font = f;
        ctx.fillText(t, pad * 2, h - boxH - pad + pad / 2 + i * lineH);
      });
      ctx.restore();
    }
    const credit = this.elBasemapCredit?.textContent || '';
    if (credit && this.showBasemap) {
      ctx.save();
      ctx.font = font(11, 500);
      const tw = ctx.measureText(credit).width;
      ctx.fillStyle = 'rgba(255, 255, 255, 0.8)';
      ctx.fillRect(w - tw - pad * 1.5, h - 22 * scale, tw + pad, 18 * scale);
      ctx.fillStyle = '#334155';
      ctx.textBaseline = 'middle';
      ctx.fillText(credit, w - tw - pad, h - 13 * scale);
      ctx.restore();
    }
  },

  // Snapshot at twice the screen resolution (Shift-click: four times),
  // capped by the GPU's maximum texture size, with the caption.
  takeSnapshot(scale = 2) {
    const size = this.renderer.getSize(new THREE.Vector2());
    const oldRatio = this.renderer.getPixelRatio();
    const maxTex = this.renderer.capabilities.maxTextureSize || 4096;
    const ratio = Math.max(1, Math.min(scale, maxTex / Math.max(size.x, size.y, 1)));
    this.renderer.setPixelRatio(ratio);
    this.renderer.setSize(size.x, size.y, false);
    this.renderer.render(this.scene, this.camera);
    const src = this.renderer.domElement;
    const out = document.createElement('canvas');
    out.width = src.width;
    out.height = src.height;
    const ctx = out.getContext('2d');
    ctx.drawImage(src, 0, 0);
    this.drawCaptureHud(ctx, out.width, out.height, ratio);
    this.renderer.setPixelRatio(oldRatio);
    this.renderer.setSize(size.x, size.y, false);
    this.requestRender();
    out.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `02Route3D_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    }, 'image/png');
  },

  pickVideoFormat() {
    if (typeof MediaRecorder === 'undefined') return null;
    // Safari records MP4/H.264 only; Chrome and Firefox record WebM.
    const candidates = [
      ['video/webm;codecs=vp9', 'webm'],
      ['video/webm;codecs=vp8', 'webm'],
      ['video/webm', 'webm'],
      ['video/mp4;codecs=avc1', 'mp4'],
      ['video/mp4', 'mp4'],
    ];
    const hit = candidates.find(([type]) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(type));
    return hit ? { mimeType: hit[0], ext: hit[1] } : null;
  },

  drawRecordingFrame() {
    const rec = this.recording;
    if (!rec) return;
    const src = this.renderer.domElement;
    if (rec.canvas.width !== src.width || rec.canvas.height !== src.height) {
      rec.canvas.width = src.width;
      rec.canvas.height = src.height;
    }
    rec.ctx.drawImage(src, 0, 0);
    this.drawCaptureHud(rec.ctx, rec.canvas.width, rec.canvas.height, this.renderer.getPixelRatio());
  },

  // Video with the caption: frames are composited onto a 2D canvas right
  // after each render (no preserveDrawingBuffer needed) and that canvas is
  // recorded.
  toggleRecordVideo() {
    const btnRecord = document.getElementById('btnRecord');
    if (this.isRecording) {
      if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') this.mediaRecorder.stop();
      this.isRecording = false;
      if (btnRecord) {
        btnRecord.classList.remove('recording');
        btnRecord.setAttribute('aria-pressed', 'false');
      }
      return;
    }
    const format = this.pickVideoFormat();
    const canvas = document.createElement('canvas');
    if (!format || typeof canvas.captureStream !== 'function') {
      setLiveStatus('Video recording is not supported in this browser.', 'warning');
      return;
    }
    this.recording = { canvas, ctx: canvas.getContext('2d'), format };
    this.drawRecordingFrame();
    this.recordedChunks = [];
    try {
      this.mediaRecorder = new MediaRecorder(canvas.captureStream(30), { mimeType: format.mimeType });
    } catch (err) {
      setLiveStatus(`Video recording failed to start: ${err.message}`, 'warning');
      this.recording = null;
      return;
    }
    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.recordedChunks.push(e.data);
    };
    this.mediaRecorder.onstop = () => {
      const blob = new Blob(this.recordedChunks, { type: format.mimeType.split(';')[0] });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `02Route3D_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.${format.ext}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      this.recording = null;
    };
    this.mediaRecorder.start(1000);
    this.isRecording = true;
    if (btnRecord) {
      btnRecord.classList.add('recording');
      btnRecord.setAttribute('aria-pressed', 'true');
    }
  },
};
