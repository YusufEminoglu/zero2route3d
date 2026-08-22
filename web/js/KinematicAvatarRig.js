import * as THREE from './three.module.js';

export class KinematicAvatarRig {
  constructor(scene) {
    this.scene = scene;
    this.root = new THREE.Group();
    this.scene.add(this.root);

    this.activeType = 'walker';
    this.subMeshGroup = new THREE.Group();
    this.root.add(this.subMeshGroup);

    this.walkPhase = 0.0;
    this.wheelRotation = 0.0;

    this.headlightLeft = new THREE.SpotLight(0xfffaed, 0.0, 120, Math.PI / 6, 0.35, 1.2);
    this.headlightRight = new THREE.SpotLight(0xfffaed, 0.0, 120, Math.PI / 6, 0.35, 1.2);
    this.headlightTarget = new THREE.Object3D();
    this.root.add(this.headlightLeft);
    this.root.add(this.headlightRight);
    this.root.add(this.headlightTarget);
    this.headlightTarget.position.set(0, 0, 80);
    this.headlightLeft.target = this.headlightTarget;
    this.headlightRight.target = this.headlightTarget;

    this.animNodes = {};
  }

  setProfile(profileKey = 'adult') {
    while (this.subMeshGroup.children.length) {
      this.subMeshGroup.remove(this.subMeshGroup.children[0]);
    }
    this.animNodes = {};

    const key = (profileKey || '').toLowerCase();
    if (key.includes('car') || key.includes('van') || key.includes('truck') || key.includes('paramedic')) {
      this.activeType = 'vehicle';
      this.buildVehicleRig(key.includes('paramedic'));
    } else if (key.includes('bike') || key.includes('bicycle') || key.includes('mtb') || key.includes('scooter')) {
      this.activeType = 'cyclist';
      this.buildCyclistRig();
    } else {
      this.activeType = 'walker';
      this.buildWalkerRig(key === 'senior', key === 'child');
    }
  }

  buildWalkerRig(isSenior = false, isChild = false) {
    const scale = isChild ? 0.7 : 1.0;
    const rig = new THREE.Group();
    rig.scale.set(scale, scale, scale);

    const skinMat = new THREE.MeshStandardMaterial({ color: 0xfbcfe8, roughness: 0.6 });
    const shirtMat = new THREE.MeshStandardMaterial({ color: isSenior ? 0x0284c7 : 0x0ea5e9, roughness: 0.5 });
    const pantsMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.7 });

    const torsoGeo = new THREE.BoxGeometry(3.2, 4.8, 1.8);
    const torsoMesh = new THREE.Mesh(torsoGeo, shirtMat);
    torsoMesh.position.y = 7.4;
    rig.add(torsoMesh);

    const headGeo = new THREE.SphereGeometry(1.4, 16, 16);
    const headMesh = new THREE.Mesh(headGeo, skinMat);
    headMesh.position.y = 11.2;
    rig.add(headMesh);

    const legGeo = new THREE.BoxGeometry(1.2, 3.2, 1.2);
    const leftThigh = new THREE.Group();
    leftThigh.position.set(-1.0, 5.2, 0);
    const lThighMesh = new THREE.Mesh(legGeo, pantsMat);
    lThighMesh.position.y = -1.6;
    leftThigh.add(lThighMesh);

    const leftShin = new THREE.Group();
    leftShin.position.set(0, -3.2, 0);
    const lShinMesh = new THREE.Mesh(legGeo, pantsMat);
    lShinMesh.position.y = -1.6;
    leftShin.add(lShinMesh);
    leftThigh.add(leftShin);
    rig.add(leftThigh);

    const rightThigh = new THREE.Group();
    rightThigh.position.set(1.0, 5.2, 0);
    const rThighMesh = new THREE.Mesh(legGeo, pantsMat);
    rThighMesh.position.y = -1.6;
    rightThigh.add(rThighMesh);

    const rightShin = new THREE.Group();
    rightShin.position.set(0, -3.2, 0);
    const rShinMesh = new THREE.Mesh(legGeo, pantsMat);
    rShinMesh.position.y = -1.6;
    rightShin.add(rShinMesh);
    rightThigh.add(rightShin);
    rig.add(rightThigh);

    const armGeo = new THREE.BoxGeometry(0.9, 3.8, 0.9);
    const leftArm = new THREE.Group();
    leftArm.position.set(-2.2, 9.2, 0);
    const lArmMesh = new THREE.Mesh(armGeo, shirtMat);
    lArmMesh.position.y = -1.9;
    leftArm.add(lArmMesh);
    rig.add(leftArm);

    const rightArm = new THREE.Group();
    rightArm.position.set(2.2, 9.2, 0);
    const rArmMesh = new THREE.Mesh(armGeo, shirtMat);
    rArmMesh.position.y = -1.9;
    rightArm.add(rArmMesh);
    rig.add(rightArm);

    this.subMeshGroup.add(rig);
    this.animNodes = { rig, leftThigh, rightThigh, leftShin, rightShin, leftArm, rightArm };
  }

  buildCyclistRig() {
    const bike = new THREE.Group();
    const frameMat = new THREE.MeshStandardMaterial({ color: 0x06b6d4, metalness: 0.7, roughness: 0.3 });
    const wheelMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.8 });

    const wheelGeo = new THREE.TorusGeometry(2.4, 0.35, 10, 24);
    const wheelFront = new THREE.Mesh(wheelGeo, wheelMat);
    wheelFront.position.set(0, 2.4, 4.2);
    bike.add(wheelFront);

    const wheelRear = new THREE.Mesh(wheelGeo, wheelMat);
    wheelRear.position.set(0, 2.4, -4.2);
    bike.add(wheelRear);

    const frameGeo = new THREE.CylinderGeometry(0.2, 0.2, 6.0);
    const topTube = new THREE.Mesh(frameGeo, frameMat);
    topTube.rotateX(Math.PI / 2);
    topTube.position.set(0, 5.0, 0);
    bike.add(topTube);

    const barGeo = new THREE.CylinderGeometry(0.2, 0.2, 3.2);
    barGeo.rotateZ(Math.PI / 2);
    const bar = new THREE.Mesh(barGeo, frameMat);
    bar.position.set(0, 6.4, 3.8);
    bike.add(bar);

    this.subMeshGroup.add(bike);
    this.animNodes = { bike, wheelFront, wheelRear };
  }

  buildVehicleRig(isEmergency = false) {
    const car = new THREE.Group();
    const bodyMat = new THREE.MeshStandardMaterial({
      color: isEmergency ? 0xef4444 : 0x0284c7,
      metalness: 0.85,
      roughness: 0.2,
    });
    const glassMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.1, metalness: 0.9 });
    const wheelMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.7 });

    const bodyGeo = new THREE.BoxGeometry(6.4, 2.6, 13.0);
    const bodyMesh = new THREE.Mesh(bodyGeo, bodyMat);
    bodyMesh.position.y = 2.4;
    car.add(bodyMesh);

    const cabinGeo = new THREE.BoxGeometry(5.4, 2.2, 6.8);
    const cabinMesh = new THREE.Mesh(cabinGeo, glassMat);
    cabinMesh.position.set(0, 4.4, -0.6);
    car.add(cabinMesh);

    const wGeo = new THREE.CylinderGeometry(1.6, 1.6, 1.1, 16);
    wGeo.rotateZ(Math.PI / 2);

    const wFL = new THREE.Mesh(wGeo, wheelMat);
    wFL.position.set(3.3, 1.6, 4.2);
    car.add(wFL);

    const wFR = new THREE.Mesh(wGeo, wheelMat);
    wFR.position.set(-3.3, 1.6, 4.2);
    car.add(wFR);

    const wRL = new THREE.Mesh(wGeo, wheelMat);
    wRL.position.set(3.3, 1.6, -4.2);
    car.add(wRL);

    const wRR = new THREE.Mesh(wGeo, wheelMat);
    wRR.position.set(-3.3, 1.6, -4.2);
    car.add(wRR);

    this.headlightLeft.position.set(-2.4, 2.4, 6.5);
    this.headlightRight.position.set(2.4, 2.4, 6.5);

    this.subMeshGroup.add(car);
    this.animNodes = { car, wFL, wFR, wRL, wRR };
  }

  updateKinematics(delta, speedKmh, tangent, slopeAngle = 0, isNight = false) {
    const spd = Math.max(0.1, speedKmh);

    if (this.activeType === 'walker') {
      this.walkPhase += delta * spd * 1.8;
      const bounce = Math.abs(Math.sin(this.walkPhase)) * 0.45;
      if (this.animNodes.rig) this.animNodes.rig.position.y = bounce;

      if (this.animNodes.leftThigh) {
        this.animNodes.leftThigh.rotation.x = Math.sin(this.walkPhase) * 0.65;
        this.animNodes.rightThigh.rotation.x = -Math.sin(this.walkPhase) * 0.65;
        this.animNodes.leftShin.rotation.x = Math.max(0.0, -Math.sin(this.walkPhase) * 0.7);
        this.animNodes.rightShin.rotation.x = Math.max(0.0, Math.sin(this.walkPhase) * 0.7);
        this.animNodes.leftArm.rotation.x = -Math.sin(this.walkPhase) * 0.55;
        this.animNodes.rightArm.rotation.x = Math.sin(this.walkPhase) * 0.55;
      }
    } else if (this.activeType === 'cyclist') {
      const wheelSpd = (spd * 1000.0) / 3600.0 / 2.4;
      this.wheelRotation += delta * wheelSpd;
      if (this.animNodes.wheelFront) {
        this.animNodes.wheelFront.rotation.x = this.wheelRotation;
        this.animNodes.wheelRear.rotation.x = this.wheelRotation;
      }
    } else if (this.activeType === 'vehicle') {
      const wheelSpd = (spd * 1000.0) / 3600.0 / 1.6;
      this.wheelRotation += delta * wheelSpd;
      if (this.animNodes.wFL) {
        this.animNodes.wFL.rotation.x = this.wheelRotation;
        this.animNodes.wFR.rotation.x = this.wheelRotation;
        this.animNodes.wRL.rotation.x = this.wheelRotation;
        this.animNodes.wRR.rotation.x = this.wheelRotation;
      }
      this.headlightLeft.intensity = isNight ? 4.5 : 0.0;
      this.headlightRight.intensity = isNight ? 4.5 : 0.0;
    }
  }
}
