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

  setProfile(profileKey = 'adult', customColor = null) {
    while (this.subMeshGroup.children.length) {
      const child = this.subMeshGroup.children[0];
      this.subMeshGroup.remove(child);
      child.traverse((node) => {
        if (node.geometry) node.geometry.dispose();
        if (node.material) {
          if (Array.isArray(node.material)) {
            node.material.forEach((m) => m.dispose());
          } else {
            node.material.dispose();
          }
        }
      });
    }
    this.animNodes = {};
    this.headlightLeft.intensity = 0.0;
    this.headlightRight.intensity = 0.0;

    const key = (profileKey || '').toLowerCase();
    if (key.includes('wheelchair')) {
      this.activeType = 'wheelchair';
      this.buildWheelchairRig(customColor);
    } else if (key.includes('stroller')) {
      this.activeType = 'stroller';
      this.buildStrollerRig(customColor);
    } else if (key.includes('scooter')) {
      this.activeType = 'scooter';
      this.buildScooterRig(customColor);
    } else if (key.includes('car') || key.includes('van') || key.includes('truck') || key.includes('paramedic')) {
      this.activeType = 'vehicle';
      const vehicleKind = key.includes('paramedic') ? 'paramedic' : (key.includes('van') ? 'van' : (key.includes('truck') ? 'truck' : 'car'));
      this.buildVehicleRig(vehicleKind, customColor);
    } else if (key.includes('bike') || key.includes('bicycle') || key.includes('mtb')) {
      this.activeType = 'cyclist';
      this.buildCyclistRig(customColor);
    } else {
      this.activeType = 'walker';
      this.buildWalkerRig(key === 'senior', key === 'child', customColor);
    }
  }

  material(color, roughness = 0.5, metalness = 0.05) {
    return new THREE.MeshStandardMaterial({ color, roughness, metalness });
  }

  capsule(radius, length, material, radialSegments = 10) {
    return new THREE.Mesh(
      new THREE.CapsuleGeometry(radius, length, 4, radialSegments),
      material,
    );
  }

  addWheel(parent, radius, width, position, material) {
    const wheel = new THREE.Group();
    wheel.position.copy(position);
    const tire = new THREE.Mesh(
      new THREE.TorusGeometry(radius, Math.max(0.22, radius * 0.12), 10, 24),
      material,
    );
    tire.rotation.y = Math.PI / 2;
    wheel.add(tire);
    const hub = new THREE.Mesh(
      new THREE.CylinderGeometry(radius * 0.16, radius * 0.16, width, 10),
      this.material(0xcbd5e1, 0.28, 0.55),
    );
    hub.rotation.z = Math.PI / 2;
    wheel.add(hub);
    parent.add(wheel);
    return wheel;
  }

  addHuman(parent, options = {}) {
    const skin = options.skin || this.material(0xf6c7a5, 0.62);
    const shirt = options.shirt || this.material(0x0ea5e9, 0.45, 0.08);
    const pants = options.pants || this.material(0x1e293b, 0.72);
    const shoes = options.shoes || this.material(0x111827, 0.62);
    const hair = options.hair || this.material(0x172033, 0.78);
    const human = new THREE.Group();
    human.position.set(...(options.position || [0, 0, 0]));
    human.rotation.set(...(options.rotation || [0, 0, 0]));
    human.scale.setScalar(options.scale || 1);
    parent.add(human);

    const torso = this.capsule(1.35, 2.7, shirt, 12);
    torso.scale.z = 0.72;
    torso.position.y = 7.15;
    human.add(torso);

    const head = new THREE.Mesh(new THREE.SphereGeometry(1.32, 16, 12), skin);
    head.position.y = 10.95;
    human.add(head);
    const hairCap = new THREE.Mesh(new THREE.SphereGeometry(1.36, 16, 8, 0, Math.PI * 2, 0, Math.PI * 0.52), hair);
    hairCap.position.y = 11.3;
    human.add(hairCap);

    const leftThigh = new THREE.Group();
    leftThigh.position.set(-0.75, 5.9, 0);
    const leftThighMesh = this.capsule(0.48, 1.9, pants, 8);
    leftThighMesh.position.y = -1.05;
    leftThigh.add(leftThighMesh);
    const leftShin = new THREE.Group();
    leftShin.position.y = -2.05;
    const leftShinMesh = this.capsule(0.42, 1.8, pants, 8);
    leftShinMesh.position.y = -0.95;
    leftShin.add(leftShinMesh);
    const leftShoe = new THREE.Mesh(new THREE.SphereGeometry(0.58, 10, 8), shoes);
    leftShoe.scale.set(0.9, 0.45, 1.35);
    leftShoe.position.set(0, -2.02, 0.28);
    leftShin.add(leftShoe);
    leftThigh.add(leftShin);
    human.add(leftThigh);

    const rightThigh = leftThigh.clone();
    rightThigh.position.x = 0.75;
    human.add(rightThigh);

    const leftArm = new THREE.Group();
    leftArm.position.set(-1.75, 8.75, 0);
    leftArm.rotation.z = -0.12;
    const leftArmMesh = this.capsule(0.4, 2.3, shirt, 8);
    leftArmMesh.position.y = -1.2;
    leftArm.add(leftArmMesh);
    const leftHand = new THREE.Mesh(new THREE.SphereGeometry(0.43, 10, 8), skin);
    leftHand.position.y = -2.55;
    leftArm.add(leftHand);
    human.add(leftArm);

    const rightArm = leftArm.clone();
    rightArm.position.x = 1.75;
    rightArm.rotation.z = 0.12;
    human.add(rightArm);

    return { human, leftThigh, rightThigh, leftShin, rightShin: rightThigh.children[1], leftArm, rightArm };
  }

  animateHuman(delta, speedKmh, style = 'walk') {
    if (!this.animNodes.human) return;
    const cadence = Math.min(3.4, Math.max(0.75, (speedKmh / 5.0) * 1.45));
    this.walkPhase += delta * cadence * Math.PI * 2;
    const swing = Math.sin(this.walkPhase);
    const human = this.animNodes.human;
    if (style === 'cycle') {
      this.animNodes.leftThigh.rotation.x = swing * 0.95;
      this.animNodes.rightThigh.rotation.x = -swing * 0.95;
      this.animNodes.leftShin.rotation.x = Math.max(0, -swing) * 0.85;
      this.animNodes.rightShin.rotation.x = Math.max(0, swing) * 0.85;
      this.animNodes.leftArm.rotation.x = -0.18;
      this.animNodes.rightArm.rotation.x = -0.18;
      human.position.y = Math.abs(swing) * 0.08;
    } else if (style === 'ride') {
      this.animNodes.leftArm.rotation.x = -0.12 + swing * 0.04;
      this.animNodes.rightArm.rotation.x = -0.12 - swing * 0.04;
      human.position.y = Math.abs(swing) * 0.12;
    } else if (style === 'push') {
      this.animNodes.leftArm.rotation.x = -0.32 + swing * 0.1;
      this.animNodes.rightArm.rotation.x = -0.32 - swing * 0.1;
      human.position.y = Math.abs(swing) * 0.09;
    }
  }

  buildWalkerRig(isSenior = false, isChild = false, customColor = null) {
    const rig = new THREE.Group();
    const shirtColor = customColor ? new THREE.Color(customColor) : (isSenior ? 0x0284c7 : (isChild ? 0xf59e0b : 0x0ea5e9));
    const shirt = this.material(shirtColor, 0.45, 0.08);
    const pose = this.addHuman(rig, { shirt, scale: isChild ? 0.74 : 1.0 });
    if (isSenior) {
      const cane = new THREE.Mesh(new THREE.CylinderGeometry(0.11, 0.11, 5.0, 8), this.material(0x92400e, 0.55));
      cane.position.set(2.3, 4.2, 0.1);
      cane.rotation.z = -0.08;
      rig.add(cane);
    }
    this.subMeshGroup.add(rig);
    this.animNodes = { rig: pose.human, ...pose };
  }

  buildCyclistRig(customColor = null) {
    const bike = new THREE.Group();
    const bikeColor = customColor ? new THREE.Color(customColor) : new THREE.Color(0x06b6d4);
    const frameMat = this.material(bikeColor, 0.35, 0.2);
    const wheelMat = this.material(0x1e293b, 0.75, 0.1);
    const wheelFront = this.addWheel(bike, 2.4, 0.55, new THREE.Vector3(0, 2.4, 4.2), wheelMat);
    const wheelRear = this.addWheel(bike, 2.4, 0.55, new THREE.Vector3(0, 2.4, -4.2), wheelMat);

    const frameGeo = new THREE.CylinderGeometry(0.22, 0.22, 6.0, 10);
    const topTube = new THREE.Mesh(frameGeo, frameMat);
    topTube.rotateX(Math.PI / 2);
    topTube.position.set(0, 5.0, 0);
    bike.add(topTube);
    const seat = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.25, 2.0), this.material(0x172033, 0.7));
    seat.position.set(0, 6.1, -2.0);
    bike.add(seat);

    const barGeo = new THREE.CylinderGeometry(0.2, 0.2, 3.2, 10);
    barGeo.rotateZ(Math.PI / 2);
    const bar = new THREE.Mesh(barGeo, frameMat);
    bar.position.set(0, 6.4, 3.8);
    bike.add(bar);
    const rider = this.addHuman(bike, {
      position: [0, -0.25, 0.55],
      rotation: [-0.28, 0, 0],
      scale: 0.82,
      shirt: this.material(bikeColor, 0.42, 0.08),
      pants: this.material(0x172033, 0.72),
    });

    this.subMeshGroup.add(bike);
    this.animNodes = { bike, wheelFront, wheelRear, ...rider };
  }

  buildWheelchairRig(customColor = null) {
    const rig = new THREE.Group();
    const chairColor = customColor ? new THREE.Color(customColor) : new THREE.Color(0x8b5cf6);
    const frameMat = this.material(chairColor, 0.35, 0.2);
    const seatMat = this.material(0x1e293b, 0.65, 0.05);
    const wheelMat = this.material(0x111827, 0.8, 0.1);

    const seat = new THREE.Mesh(new THREE.BoxGeometry(3.8, 0.65, 3.6), seatMat);
    seat.position.set(0, 3.2, -0.4);
    rig.add(seat);
    const back = new THREE.Mesh(new THREE.BoxGeometry(3.8, 3.2, 0.55), seatMat);
    back.position.set(0, 4.7, -2.0);
    rig.add(back);
    const wheelLeft = this.addWheel(rig, 2.5, 0.55, new THREE.Vector3(-2.35, 2.7, -0.4), wheelMat);
    const wheelRight = this.addWheel(rig, 2.5, 0.55, new THREE.Vector3(2.35, 2.7, -0.4), wheelMat);
    const frontLeft = this.addWheel(rig, 0.85, 0.38, new THREE.Vector3(-1.35, 0.95, 2.5), wheelMat);
    const frontRight = this.addWheel(rig, 0.85, 0.38, new THREE.Vector3(1.35, 0.95, 2.5), wheelMat);
    const footrest = new THREE.Mesh(new THREE.BoxGeometry(3.0, 0.25, 2.0), frameMat);
    footrest.position.set(0, 1.0, 2.0);
    rig.add(footrest);
    const rider = this.addHuman(rig, {
      position: [0, -1.1, -0.45],
      scale: 0.82,
      shirt: this.material(chairColor, 0.44, 0.14),
    });
    rider.leftThigh.rotation.x = -0.55;
    rider.rightThigh.rotation.x = -0.55;

    this.subMeshGroup.add(rig);
    this.animNodes = { rig, wheelLeft, wheelRight, frontLeft, frontRight, ...rider };
  }

  buildStrollerRig(customColor = null) {
    const stroller = new THREE.Group();
    const strollerColor = customColor ? new THREE.Color(customColor) : new THREE.Color(0xd946ef);
    const frameMat = this.material(strollerColor, 0.35, 0.2);
    const fabricMat = this.material(0xf5d0fe, 0.75, 0.05);
    const wheelMat = this.material(0x1f2937, 0.8, 0.1);
    const seat = new THREE.Mesh(new THREE.BoxGeometry(3.4, 2.4, 3.4), fabricMat);
    seat.position.set(0, 4.2, -0.7);
    stroller.add(seat);
    const canopy = new THREE.Mesh(new THREE.CylinderGeometry(2.2, 2.2, 0.35, 24, 1, false, 0, Math.PI), fabricMat);
    canopy.rotation.x = Math.PI / 2;
    canopy.position.set(0, 6.2, -1.5);
    stroller.add(canopy);
    const handle = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.18, 5.8), frameMat);
    handle.rotation.x = Math.PI / 2;
    handle.position.set(0, 6.0, -4.0);
    stroller.add(handle);
    const wheelFront = this.addWheel(stroller, 1.0, 0.38, new THREE.Vector3(0, 1.0, 2.2), wheelMat);
    const wheelRear = this.addWheel(stroller, 1.0, 0.38, new THREE.Vector3(0, 1.0, -2.6), wheelMat);
    const guardian = this.addHuman(stroller, {
      position: [0, -0.85, -4.35],
      scale: 0.72,
      shirt: this.material(strollerColor, 0.45, 0.08),
    });
    this.subMeshGroup.add(stroller);
    this.animNodes = { stroller, wheelFront, wheelRear, ...guardian };
  }

  buildScooterRig(customColor = null) {
    const scooter = new THREE.Group();
    const scooterColor = customColor ? new THREE.Color(customColor) : new THREE.Color(0x3b82f6);
    const deckMat = this.material(scooterColor, 0.35, 0.2);
    const wheelMat = this.material(0x111827, 0.8, 0.1);
    const deck = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.45, 6.8), deckMat);
    deck.position.y = 1.35;
    scooter.add(deck);
    const wheelFront = this.addWheel(scooter, 0.95, 0.38, new THREE.Vector3(0, 1.1, 2.7), wheelMat);
    const wheelRear = this.addWheel(scooter, 0.95, 0.38, new THREE.Vector3(0, 1.1, -2.7), wheelMat);
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.22, 5.0), deckMat);
    stem.position.set(0, 3.7, 2.7);
    stem.rotation.x = -0.12;
    scooter.add(stem);
    const handlebar = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.18, 3.0), deckMat);
    handlebar.rotation.z = Math.PI / 2;
    handlebar.position.set(0, 6.1, 2.7);
    scooter.add(handlebar);
    const rider = this.addHuman(scooter, {
      position: [0, -0.55, -0.55],
      scale: 0.78,
      shirt: this.material(scooterColor, 0.42, 0.1),
    });
    rider.leftThigh.rotation.x = -0.32;
    rider.rightThigh.rotation.x = -0.32;
    this.subMeshGroup.add(scooter);
    this.animNodes = { scooter, wheelFront, wheelRear, stem, ...rider };
  }

  buildVehicleRig(vehicleKind = 'car', customColor = null) {
    const car = new THREE.Group();
    const isEmergency = vehicleKind === 'paramedic';
    const isVan = vehicleKind === 'van';
    const isTruck = vehicleKind === 'truck';

    const defaultCarColor = isEmergency ? 0xef4444 : (isTruck ? 0xf59e0b : (isVan ? 0x6366f1 : 0x0284c7));
    const carColor = customColor ? new THREE.Color(customColor) : new THREE.Color(defaultCarColor);

    const bodyMat = new THREE.MeshStandardMaterial({
      color: carColor,
      roughness: 0.35,
      metalness: 0.18,
    });
    const glassMat = new THREE.MeshStandardMaterial({
      color: 0x93c5fd,
      roughness: 0.18,
      metalness: 0.12,
      transparent: true,
      opacity: 0.85,
    });
    const wheelMat = new THREE.MeshStandardMaterial({
      color: 0x1e293b,
      roughness: 0.7,
      metalness: 0.08,
    });

    const bodyWidth = isTruck ? 7.6 : (isVan ? 7.0 : 6.4);
    const bodyLength = isTruck ? 16.0 : (isVan ? 15.0 : 13.0);
    const bodyHeight = isTruck ? 3.2 : 2.6;
    const bodyGeo = new THREE.BoxGeometry(bodyWidth, bodyHeight, bodyLength);
    const bodyMesh = new THREE.Mesh(bodyGeo, bodyMat);
    bodyMesh.position.y = isTruck ? 2.8 : 2.4;
    bodyMesh.castShadow = true;
    bodyMesh.receiveShadow = true;
    car.add(bodyMesh);

    const cabinWidth = isTruck ? 6.2 : (isVan ? 6.1 : 5.4);
    const cabinHeight = isTruck ? 3.0 : (isVan ? 3.0 : 2.2);
    const cabinLength = isTruck ? 5.0 : (isVan ? 8.0 : 6.8);
    const cabinGeo = new THREE.BoxGeometry(cabinWidth, cabinHeight, cabinLength);
    const cabinMesh = new THREE.Mesh(cabinGeo, glassMat);
    const cabinY = isTruck ? 5.0 : (isVan ? 4.8 : 4.4);
    const cabinZ = isTruck ? 4.2 : -0.6;
    cabinMesh.position.set(0, cabinY, cabinZ);
    cabinMesh.castShadow = true;
    car.add(cabinMesh);

    // Architectural vehicle roof cap
    const roofGeo = new THREE.BoxGeometry(cabinWidth * 0.98, 0.35, cabinLength * 0.98);
    const roofMesh = new THREE.Mesh(roofGeo, bodyMat);
    roofMesh.position.set(0, cabinY + cabinHeight / 2 + 0.16, cabinZ);
    roofMesh.castShadow = true;
    car.add(roofMesh);

    if (isTruck) {
      const cargo = new THREE.Mesh(new THREE.BoxGeometry(7.2, 4.4, 8.0), bodyMat);
      cargo.position.set(0, 5.2, -3.0);
      cargo.castShadow = true;
      cargo.receiveShadow = true;
      car.add(cargo);
    }

    const bumper = new THREE.Mesh(
      new THREE.BoxGeometry(bodyWidth * 0.92, 0.42, 0.5),
      this.material(0x334155, 0.38, 0.48),
    );
    bumper.position.set(0, 1.5, bodyLength / 2 + 0.15);
    car.add(bumper);

    const headlampMat = new THREE.MeshStandardMaterial({
      color: 0xfff7c2,
      emissive: 0xfff7c2,
      emissiveIntensity: 0.65,
      roughness: 0.2,
      metalness: 0.1,
    });
    [-1, 1].forEach((side) => {
      const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.46, 10, 8), headlampMat);
      lamp.scale.z = 0.38;
      lamp.position.set(side * bodyWidth * 0.28, 2.35, bodyLength / 2 + 0.28);
      car.add(lamp);
    });

    if (isEmergency) {
      const beaconBase = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.25, 0.8), this.material(0xffffff, 0.28, 0.2));
      beaconBase.position.set(0, isTruck ? 7.55 : 5.9, 0.5);
      car.add(beaconBase);
      const beacon = new THREE.Mesh(
        new THREE.BoxGeometry(1.45, 0.48, 0.55),
        new THREE.MeshStandardMaterial({
          color: 0xef4444,
          emissive: 0xef4444,
          emissiveIntensity: 0.85,
          roughness: 0.2,
        })
      );
      beacon.position.set(0, beaconBase.position.y + 0.34, 0.5);
      car.add(beacon);
    }

    const wGeo = new THREE.CylinderGeometry(1.6, 1.6, 1.1, 16);
    wGeo.rotateZ(Math.PI / 2);

    const wFL = new THREE.Mesh(wGeo, wheelMat);
    const halfWidth = bodyWidth / 2 + 0.1;
    const frontZ = bodyLength / 2 - 2.0;
    const rearZ = -bodyLength / 2 + 2.0;
    wFL.position.set(halfWidth, 1.6, frontZ);
    wFL.castShadow = true;
    car.add(wFL);

    const wFR = new THREE.Mesh(wGeo, wheelMat);
    wFR.position.set(-halfWidth, 1.6, frontZ);
    wFR.castShadow = true;
    car.add(wFR);

    const wRL = new THREE.Mesh(wGeo, wheelMat);
    wRL.position.set(halfWidth, 1.6, rearZ);
    wRL.castShadow = true;
    car.add(wRL);

    const wRR = new THREE.Mesh(wGeo, wheelMat);
    wRR.position.set(-halfWidth, 1.6, rearZ);
    wRR.castShadow = true;
    car.add(wRR);

    this.headlightLeft.position.set(-halfWidth * 0.7, 2.4, bodyLength / 2 + 0.2);
    this.headlightRight.position.set(halfWidth * 0.7, 2.4, bodyLength / 2 + 0.2);

    this.subMeshGroup.add(car);
    this.animNodes = { car, wFL, wFR, wRL, wRR };
  }

  updateKinematics(delta, speedKmh, tangent, slopeAngle = 0, isNight = false) {
    const spd = Math.max(0.5, speedKmh);

    if (this.activeType === 'walker') {
      // Human cadence: smoothly scales with route speed.
      this.animateHuman(delta, spd, 'walk');

      const bounce = Math.abs(Math.sin(this.walkPhase)) * 0.3;
      if (this.animNodes.rig) {
        this.animNodes.rig.position.y = bounce;
      }

      if (this.animNodes.leftThigh && this.animNodes.rightThigh) {
        const swing = Math.sin(this.walkPhase) * 0.5;
        this.animNodes.leftThigh.rotation.x = swing;
        this.animNodes.rightThigh.rotation.x = -swing;

        // Knees only bend backwards (-X) when leg swings back
        this.animNodes.leftShin.rotation.x = swing < 0 ? swing * 0.85 : 0.0;
        this.animNodes.rightShin.rotation.x = swing > 0 ? -swing * 0.85 : 0.0;

        // Arms swing opposite to legs
        if (this.animNodes.leftArm && this.animNodes.rightArm) {
          this.animNodes.leftArm.rotation.x = -swing * 0.45;
          this.animNodes.rightArm.rotation.x = swing * 0.45;
        }
      }
    } else if (this.activeType === 'cyclist' || this.activeType === 'scooter' || this.activeType === 'wheelchair' || this.activeType === 'stroller') {
      const wheelSpd = (spd * 1000.0) / 3600.0 / 2.4;
      this.wheelRotation += delta * wheelSpd;
      const humanStyle = this.activeType === 'cyclist'
        ? 'cycle'
        : (this.activeType === 'stroller' ? 'push' : 'ride');
      this.animateHuman(delta, spd, humanStyle);
      if (this.animNodes.wheelFront) {
        this.animNodes.wheelFront.rotation.x = this.wheelRotation;
      }
      if (this.animNodes.wheelRear) {
        this.animNodes.wheelRear.rotation.x = this.wheelRotation;
      }
      if (this.animNodes.wheelLeft) {
        this.animNodes.wheelLeft.rotation.x = this.wheelRotation;
        this.animNodes.wheelRight.rotation.x = this.wheelRotation;
        this.animNodes.frontLeft.rotation.x = this.wheelRotation;
        this.animNodes.frontRight.rotation.x = this.wheelRotation;
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

  dispose() {
    while (this.subMeshGroup.children.length) {
      const child = this.subMeshGroup.children[0];
      this.subMeshGroup.remove(child);
      child.traverse((node) => {
        if (node.geometry) node.geometry.dispose();
        if (node.material) {
          if (Array.isArray(node.material)) node.material.forEach((m) => m.dispose());
          else node.material.dispose();
        }
      });
    }
    this.scene.remove(this.root);
  }
}
