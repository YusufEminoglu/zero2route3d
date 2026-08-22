import * as THREE from './three.module.js';

export class SolarNightSystem {
  constructor(scene, renderer) {
    this.scene = scene;
    this.renderer = renderer;
    this.solarHour = 14.0;
    this.isNight = false;

    this.streetlampsGroup = new THREE.Group();
    this.scene.add(this.streetlampsGroup);

    this.streetlampPoles = [];
    this.spotLights = [];
    this.coneMeshes = [];

    this.poleMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.8, roughness: 0.3 });
    this.luminaireMatOff = new THREE.MeshBasicMaterial({ color: 0x475569 });
    this.luminaireMatOn = new THREE.MeshBasicMaterial({ color: 0xfef08a });

    this.lightConeMat = new THREE.MeshBasicMaterial({
      color: 0xfbbf24,
      transparent: true,
      opacity: 0.18,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
  }

  setSolarHour(hour, sunLight, ambientLight) {
    this.solarHour = Math.max(0, Math.min(24, hour));
    this.isNight = this.solarHour < 6.8 || this.solarHour > 19.2;

    const hourAngle = ((this.solarHour - 12.0) * 15.0 * Math.PI) / 180.0;
    const elevation = Math.sin(Math.PI * (1.0 - Math.abs(this.solarHour - 12.0) / 12.0));

    if (this.solarHour >= 6.0 && this.solarHour <= 19.5) {
      const sunDist = 2000.0;
      sunLight.position.set(Math.sin(hourAngle) * sunDist, Math.max(80.0, elevation * sunDist), Math.cos(hourAngle) * sunDist);
      
      if (this.solarHour < 7.5 || this.solarHour > 18.0) {
        sunLight.color.setHex(0xf97316);
        sunLight.intensity = 1.1;
        ambientLight.color.setHex(0x431407);
        ambientLight.intensity = 0.4;
        if (this.scene.background) this.scene.background.setHex(0x1a0f24);
        if (this.scene.fog) this.scene.fog.color.setHex(0x1a0f24);
      } else {
        sunLight.color.setHex(0xfffaed);
        sunLight.intensity = 1.4;
        ambientLight.color.setHex(0xffffff);
        ambientLight.intensity = 0.75;
        if (this.scene.background) this.scene.background.setHex(0x090d16);
        if (this.scene.fog) this.scene.fog.color.setHex(0x090d16);
      }
    } else {
      sunLight.position.set(200, 800, -300);
      sunLight.color.setHex(0x1e3a8a);
      sunLight.intensity = 0.28;
      ambientLight.color.setHex(0x0a1128);
      ambientLight.intensity = 0.15;
      if (this.scene.background) this.scene.background.setHex(0x030712);
      if (this.scene.fog) this.scene.fog.color.setHex(0x030712);
    }

    this.toggleStreetlights(this.isNight);
  }

  buildStreetlampsAlongRoute(curve, scenePoints, intervalMeters = 35.0) {
    this.clearStreetlamps();
    if (!curve || scenePoints.length < 2) return;

    const totalDist = curve.getLength();
    const count = Math.max(3, Math.floor(totalDist / intervalMeters));

    for (let i = 0; i <= count; i++) {
      const u = i / count;
      const pt = curve.getPointAt(u);
      const tangent = curve.getTangentAt(u).normalize();
      const normal = new THREE.Vector3(-tangent.z, 0, tangent.x).normalize();

      [-12.0, 12.0].forEach((offsetSide, sideIdx) => {
        const lampGroup = new THREE.Group();
        const lampPos = pt.clone().add(normal.clone().multiplyScalar(offsetSide));

        const poleGeo = new THREE.CylinderGeometry(0.3, 0.45, 14, 8);
        const poleMesh = new THREE.Mesh(poleGeo, this.poleMat);
        poleMesh.position.y = 7;
        lampGroup.add(poleMesh);

        const armGeo = new THREE.CylinderGeometry(0.2, 0.2, 4.5, 6);
        armGeo.rotateZ(offsetSide > 0 ? -Math.PI / 4 : Math.PI / 4);
        const armMesh = new THREE.Mesh(armGeo, this.poleMat);
        armMesh.position.set(offsetSide > 0 ? -1.5 : 1.5, 14.5, 0);
        lampGroup.add(armMesh);

        const headGeo = new THREE.BoxGeometry(1.2, 0.4, 0.8);
        const headMesh = new THREE.Mesh(headGeo, this.luminaireMatOff);
        headMesh.position.set(offsetSide > 0 ? -3.0 : 3.0, 15.5, 0);
        lampGroup.add(headMesh);

        const coneGeo = new THREE.ConeGeometry(8.5, 15, 16, 1, true);
        coneGeo.translate(0, -7.5, 0);
        const coneMesh = new THREE.Mesh(coneGeo, this.lightConeMat);
        coneMesh.position.set(offsetSide > 0 ? -3.0 : 3.0, 15.5, 0);
        coneMesh.visible = false;
        lampGroup.add(coneMesh);

        let pLight = null;
        if (i % 2 === 0 && sideIdx === 0) {
          pLight = new THREE.PointLight(0xfbbf24, 0.0, 50, 1.8);
          pLight.position.set(offsetSide > 0 ? -3.0 : 3.0, 14.0, 0);
          lampGroup.add(pLight);
          this.spotLights.push(pLight);
        }

        lampGroup.position.copy(lampPos);
        lampGroup.lookAt(lampPos.clone().add(tangent));
        this.streetlampsGroup.add(lampGroup);

        this.streetlampPoles.push({ group: lampGroup, headMesh, coneMesh, pLight });
      });
    }
  }

  toggleStreetlights(turnOn) {
    this.streetlampPoles.forEach((item) => {
      item.headMesh.material = turnOn ? this.luminaireMatOn : this.luminaireMatOff;
      item.coneMesh.visible = turnOn;
      if (item.pLight) {
        item.pLight.intensity = turnOn ? 2.8 : 0.0;
      }
    });
  }

  clearStreetlamps() {
    while (this.streetlampsGroup.children.length) {
      this.streetlampsGroup.remove(this.streetlampsGroup.children[0]);
    }
    this.streetlampPoles = [];
    this.spotLights = [];
  }
}
