/**
 * Procedural Low-Poly 3D Models & Worker Archetypes for Aevum Citadel
 * Pure Three.js primitives - Zero external asset downloads
 */

const THREE = window.THREE;

export function createWorkerCharacter(options = {}) {
  const {
    archetype = 'engineer', // 'engineer' | 'robot' | 'animal' | 'hero' | 'monster' | 'cyber_synth'
    color = 0x2563eb,
    hasHardHat = true,
    hardHatColor = 0xfacc15,
    hasClipboard = false,
    hasMegaphone = false,
    isSitting = false,
    agentId = 'agent-1'
  } = options;

  const character = new THREE.Group();
  character.userData.agentId = agentId;
  character.userData.archetype = archetype;
  character.userData.animTimer = Math.random() * 10;
  character.userData.baseY = isSitting ? -0.4 : 0;
  character.userData.isSitting = isSitting;

  // Base materials
  const skinMat = new THREE.MeshStandardMaterial({ color: 0xd4a571, roughness: 0.8 });
  const clothesMat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.6 });
  const darkMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.9 });
  const hatMat = new THREE.MeshStandardMaterial({ color: hardHatColor, roughness: 0.4 });

  let torso, head, leftArm, rightArm, leftLeg, rightLeg;

  // --- 1. ARCHETYPE: ROBOT ---
  if (archetype === 'robot') {
    const metalMat = new THREE.MeshStandardMaterial({ color: 0x94a3b8, metalness: 0.8, roughness: 0.3 });
    const accentMat = new THREE.MeshStandardMaterial({ color: color, metalness: 0.6, roughness: 0.4 });
    const visorMat = new THREE.MeshStandardMaterial({ color: 0x22c55e, emissive: 0x16a34a, emissiveIntensity: 0.8 });

    // Boxy metallic torso
    const torsoGeo = new THREE.BoxGeometry(0.85, 1.0, 0.5);
    torso = new THREE.Mesh(torsoGeo, accentMat);
    torso.position.y = 1.3;
    torso.castShadow = true;
    character.add(torso);

    // Chest meter / dial
    const dialGeo = new THREE.BoxGeometry(0.5, 0.35, 0.05);
    const dialMat = new THREE.MeshBasicMaterial({ color: 0x0f172a });
    const dial = new THREE.Mesh(dialGeo, dialMat);
    dial.position.set(0, 0.1, 0.26);
    torso.add(dial);

    // Cube robot head
    const headGeo = new THREE.BoxGeometry(0.6, 0.6, 0.6);
    head = new THREE.Mesh(headGeo, metalMat);
    head.position.y = 1.95;
    head.castShadow = true;
    character.add(head);

    // Glowing LED visor
    const visorGeo = new THREE.BoxGeometry(0.48, 0.16, 0.1);
    const visor = new THREE.Mesh(visorGeo, visorMat);
    visor.position.set(0, 0.08, 0.28);
    head.add(visor);

    // Antenna on top
    const antGeo = new THREE.CylinderGeometry(0.02, 0.02, 0.35, 6);
    const ant = new THREE.Mesh(antGeo, metalMat);
    ant.position.y = 0.45;
    head.add(ant);
    const tipGeo = new THREE.SphereGeometry(0.06, 6, 6);
    const tip = new THREE.Mesh(tipGeo, visorMat);
    tip.position.y = 0.62;
    head.add(tip);

    // Limbs with ball joints
    const armGeo = new THREE.BoxGeometry(0.2, 0.75, 0.2);
    armGeo.translate(0, -0.35, 0);
    leftArm = new THREE.Mesh(armGeo, metalMat);
    leftArm.position.set(-0.54, 1.7, 0);
    character.add(leftArm);

    // Clamp hand
    const clampGeo = new THREE.TorusGeometry(0.08, 0.03, 6, 8, Math.PI);
    const clampMat = new THREE.MeshStandardMaterial({ color: 0x475569, metalness: 0.9 });
    const clampL = new THREE.Mesh(clampGeo, clampMat);
    clampL.rotation.x = Math.PI / 2;
    clampL.position.set(0, -0.75, 0);
    leftArm.add(clampL);

    rightArm = new THREE.Mesh(armGeo.clone(), metalMat);
    rightArm.position.set(0.54, 1.7, 0);
    character.add(rightArm);
    const clampR = clampL.clone();
    rightArm.add(clampR);

    const legGeo = new THREE.BoxGeometry(0.26, 0.85, 0.26);
    legGeo.translate(0, -0.42, 0);
    leftLeg = new THREE.Mesh(legGeo, metalMat);
    leftLeg.position.set(-0.25, 0.8, 0);
    character.add(leftLeg);

    rightLeg = new THREE.Mesh(legGeo.clone(), metalMat);
    rightLeg.position.set(0.25, 0.8, 0);
    character.add(rightLeg);
  }

  // --- 2. ARCHETYPE: CUTE ANIMAL (Fox/Cat/Bear) ---
  else if (archetype === 'animal') {
    const furMat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.9 }); // e.g. orange for fox
    const bellyMat = new THREE.MeshStandardMaterial({ color: 0xffedd5, roughness: 0.9 });
    const noseMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.5 });
    const eyeMat = new THREE.MeshBasicMaterial({ color: 0x0f172a });

    // Rounded soft torso
    const torsoGeo = new THREE.BoxGeometry(0.75, 0.9, 0.5);
    torso = new THREE.Mesh(torsoGeo, furMat);
    torso.position.y = 1.25;
    torso.castShadow = true;
    character.add(torso);

    // Light belly patch
    const bellyGeo = new THREE.BoxGeometry(0.5, 0.65, 0.05);
    const belly = new THREE.Mesh(bellyGeo, bellyMat);
    belly.position.set(0, 0, 0.24);
    torso.add(belly);

    // Head with snout
    const headGeo = new THREE.BoxGeometry(0.6, 0.55, 0.55);
    head = new THREE.Mesh(headGeo, furMat);
    head.position.y = 1.9;
    head.castShadow = true;
    character.add(head);

    // Snout
    const snoutGeo = new THREE.BoxGeometry(0.3, 0.2, 0.25);
    const snout = new THREE.Mesh(snoutGeo, bellyMat);
    snout.position.set(0, -0.1, 0.32);
    head.add(snout);

    // Cute nose
    const noseGeo = new THREE.SphereGeometry(0.06, 6, 6);
    const nose = new THREE.Mesh(noseGeo, noseMat);
    nose.position.set(0, -0.05, 0.44);
    head.add(nose);

    // Two big cute eyes
    const eyeGeo = new THREE.SphereGeometry(0.05, 6, 6);
    const eyeL = new THREE.Mesh(eyeGeo, eyeMat);
    eyeL.position.set(-0.16, 0.06, 0.28);
    head.add(eyeL);
    const eyeR = new THREE.Mesh(eyeGeo, eyeMat);
    eyeR.position.set(0.16, 0.06, 0.28);
    head.add(eyeR);

    // Pointy animal ears
    const earGeo = new THREE.ConeGeometry(0.14, 0.28, 4);
    const earL = new THREE.Mesh(earGeo, furMat);
    earL.position.set(-0.22, 0.38, 0.05);
    earL.rotation.z = 0.2;
    head.add(earL);
    const earR = new THREE.Mesh(earGeo, furMat);
    earR.position.set(0.22, 0.38, 0.05);
    earR.rotation.z = -0.2;
    head.add(earR);

    // Cute bushy tail at back of torso
    const tailGeo = new THREE.CylinderGeometry(0.06, 0.12, 0.5, 6);
    const tail = new THREE.Mesh(tailGeo, furMat);
    tail.rotation.x = -Math.PI / 3;
    tail.position.set(0, -0.3, -0.3);
    torso.add(tail);

    // Paws
    const armGeo = new THREE.BoxGeometry(0.2, 0.7, 0.2);
    armGeo.translate(0, -0.32, 0);
    leftArm = new THREE.Mesh(armGeo, furMat);
    leftArm.position.set(-0.48, 1.6, 0);
    character.add(leftArm);

    rightArm = new THREE.Mesh(armGeo.clone(), furMat);
    rightArm.position.set(0.48, 1.6, 0);
    character.add(rightArm);

    const legGeo = new THREE.BoxGeometry(0.24, 0.75, 0.24);
    legGeo.translate(0, -0.38, 0);
    leftLeg = new THREE.Mesh(legGeo, furMat);
    leftLeg.position.set(-0.22, 0.75, 0);
    character.add(leftLeg);

    rightLeg = new THREE.Mesh(legGeo.clone(), furMat);
    rightLeg.position.set(0.22, 0.75, 0);
    character.add(rightLeg);
  }

  // --- 3. ARCHETYPE: 8-BIT RPG HERO / MAGE ---
  else if (archetype === 'hero') {
    const armorMat = new THREE.MeshStandardMaterial({ color: 0x475569, metalness: 0.7, roughness: 0.3 });
    const capeMat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.6, side: THREE.DoubleSide });
    const goldMat = new THREE.MeshStandardMaterial({ color: 0xfacc15, metalness: 0.8, roughness: 0.2 });

    const torsoGeo = new THREE.BoxGeometry(0.8, 1.0, 0.45);
    torso = new THREE.Mesh(torsoGeo, armorMat);
    torso.position.y = 1.3;
    torso.castShadow = true;
    character.add(torso);

    // Crest emblem on chest
    const crestGeo = new THREE.OctahedronGeometry(0.15, 0);
    const crest = new THREE.Mesh(crestGeo, goldMat);
    crest.position.set(0, 0.1, 0.25);
    torso.add(crest);

    // Flowing hero cape on back
    const capeGeo = new THREE.PlaneGeometry(0.7, 1.1);
    const cape = new THREE.Mesh(capeGeo, capeMat);
    cape.position.set(0, 0.05, -0.26);
    cape.rotation.x = 0.1;
    torso.add(cape);

    // Knight helmet / Hero Head
    const headGeo = new THREE.BoxGeometry(0.55, 0.6, 0.55);
    head = new THREE.Mesh(headGeo, armorMat);
    head.position.y = 1.95;
    head.castShadow = true;
    character.add(head);

    // Helmet visor slit
    const visorGeo = new THREE.BoxGeometry(0.42, 0.1, 0.06);
    const visorMat = new THREE.MeshBasicMaterial({ color: 0x0f172a });
    const visor = new THREE.Mesh(visorGeo, visorMat);
    visor.position.set(0, 0.05, 0.28);
    head.add(visor);

    // Plume on helmet
    const plumeGeo = new THREE.BoxGeometry(0.1, 0.3, 0.35);
    const plume = new THREE.Mesh(plumeGeo, capeMat);
    plume.position.set(0, 0.4, 0);
    head.add(plume);

    // Limbs
    const armGeo = new THREE.BoxGeometry(0.22, 0.8, 0.22);
    armGeo.translate(0, -0.35, 0);
    leftArm = new THREE.Mesh(armGeo, armorMat);
    leftArm.position.set(-0.52, 1.7, 0);
    character.add(leftArm);

    rightArm = new THREE.Mesh(armGeo.clone(), armorMat);
    rightArm.position.set(0.52, 1.7, 0);
    character.add(rightArm);

    const legGeo = new THREE.BoxGeometry(0.28, 0.9, 0.28);
    legGeo.translate(0, -0.45, 0);
    leftLeg = new THREE.Mesh(legGeo, darkMat);
    leftLeg.position.set(-0.25, 0.8, 0);
    character.add(leftLeg);

    rightLeg = new THREE.Mesh(legGeo.clone(), darkMat);
    rightLeg.position.set(0.25, 0.8, 0);
    character.add(rightLeg);
  }

  // --- 4. ARCHETYPE: CUTE MONSTER / CYCLOPS BLOB ---
  else if (archetype === 'monster') {
    const skinColorMat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.5 });
    const eyeWhiteMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
    const pupilMat = new THREE.MeshBasicMaterial({ color: 0x0f172a });
    const hornMat = new THREE.MeshStandardMaterial({ color: 0xfde047, roughness: 0.3 });

    // Pear-shaped chubby torso
    const torsoGeo = new THREE.SphereGeometry(0.55, 12, 12);
    torsoGeo.scale(1.0, 1.3, 0.9);
    torso = new THREE.Mesh(torsoGeo, skinColorMat);
    torso.position.y = 1.3;
    character.add(torso);

    // Large single cyclops eye right in center-top of body
    head = new THREE.Group();
    head.position.y = 1.8;
    character.add(head);

    const bigEyeGeo = new THREE.SphereGeometry(0.22, 12, 12);
    const bigEye = new THREE.Mesh(bigEyeGeo, eyeWhiteMat);
    bigEye.position.set(0, 0, 0.22);
    head.add(bigEye);

    const pupilGeo = new THREE.SphereGeometry(0.1, 8, 8);
    const pupil = new THREE.Mesh(pupilGeo, pupilMat);
    pupil.position.set(0, 0, 0.38);
    head.add(pupil);

    // Two little curved horns
    const hornGeo = new THREE.ConeGeometry(0.08, 0.3, 6);
    const hornL = new THREE.Mesh(hornGeo, hornMat);
    hornL.position.set(-0.25, 0.25, 0);
    hornL.rotation.z = 0.4;
    head.add(hornL);
    const hornR = new THREE.Mesh(hornGeo, hornMat);
    hornR.position.set(0.25, 0.25, 0);
    hornR.rotation.z = -0.4;
    head.add(hornR);

    // Short stubby arms
    const armGeo = new THREE.SphereGeometry(0.18, 8, 8);
    armGeo.scale(0.8, 2.0, 0.8);
    leftArm = new THREE.Mesh(armGeo, skinColorMat);
    leftArm.position.set(-0.55, 1.4, 0);
    character.add(leftArm);

    rightArm = new THREE.Mesh(armGeo.clone(), skinColorMat);
    rightArm.position.set(0.55, 1.4, 0);
    character.add(rightArm);

    // Short stubby legs
    const legGeo = new THREE.CylinderGeometry(0.15, 0.18, 0.5, 8);
    leftLeg = new THREE.Mesh(legGeo, skinColorMat);
    leftLeg.position.set(-0.22, 0.5, 0);
    character.add(leftLeg);

    rightLeg = new THREE.Mesh(legGeo.clone(), skinColorMat);
    rightLeg.position.set(0.22, 0.5, 0);
    character.add(rightLeg);
  }

  // --- 5. ARCHETYPE: CYBER SYNTH (Sleek Android) ---
  else if (archetype === 'cyber_synth') {
    const sleekMat = new THREE.MeshStandardMaterial({ color: 0x09090b, roughness: 0.2, metalness: 0.9 });
    const neonLineMat = new THREE.MeshBasicMaterial({ color: color });
    const visorMat = new THREE.MeshBasicMaterial({ color: color });

    const torsoGeo = new THREE.BoxGeometry(0.78, 0.95, 0.42);
    torso = new THREE.Mesh(torsoGeo, sleekMat);
    torso.position.y = 1.3;
    character.add(torso);

    // Glowing chest circuit line
    const circuitGeo = new THREE.BoxGeometry(0.06, 0.7, 0.04);
    const circuit = new THREE.Mesh(circuitGeo, neonLineMat);
    circuit.position.set(0, 0, 0.22);
    torso.add(circuit);

    // Sleek geometric head
    const headGeo = new THREE.BoxGeometry(0.48, 0.55, 0.48);
    head = new THREE.Mesh(headGeo, sleekMat);
    head.position.y = 1.95;
    character.add(head);

    // Horizontal cyber visor
    const visorGeo = new THREE.BoxGeometry(0.45, 0.08, 0.1);
    const visor = new THREE.Mesh(visorGeo, visorMat);
    visor.position.set(0, 0.06, 0.24);
    head.add(visor);

    const armGeo = new THREE.BoxGeometry(0.18, 0.75, 0.18);
    armGeo.translate(0, -0.35, 0);
    leftArm = new THREE.Mesh(armGeo, sleekMat);
    leftArm.position.set(-0.5, 1.7, 0);
    character.add(leftArm);

    rightArm = new THREE.Mesh(armGeo.clone(), sleekMat);
    rightArm.position.set(0.5, 1.7, 0);
    character.add(rightArm);

    const legGeo = new THREE.BoxGeometry(0.24, 0.85, 0.24);
    legGeo.translate(0, -0.42, 0);
    leftLeg = new THREE.Mesh(legGeo, sleekMat);
    leftLeg.position.set(-0.22, 0.8, 0);
    character.add(leftLeg);

    rightLeg = new THREE.Mesh(legGeo.clone(), sleekMat);
    rightLeg.position.set(0.22, 0.8, 0);
    character.add(rightLeg);
  }

  // --- 6. DEFAULT ARCHETYPE: HUMAN ENGINEER ---
  else {
    const torsoGeo = new THREE.BoxGeometry(0.8, 1.0, 0.45);
    torso = new THREE.Mesh(torsoGeo, clothesMat);
    torso.position.y = 1.3;
    torso.castShadow = true;
    torso.receiveShadow = true;
    character.add(torso);

    const headGeo = new THREE.BoxGeometry(0.5, 0.55, 0.5);
    head = new THREE.Mesh(headGeo, skinMat);
    head.position.y = 1.95;
    head.castShadow = true;
    character.add(head);

    if (hasHardHat) {
      const hatGroup = new THREE.Group();
      const capGeo = new THREE.CylinderGeometry(0.32, 0.36, 0.25, 8);
      const cap = new THREE.Mesh(capGeo, hatMat);
      cap.position.y = 0.25;
      hatGroup.add(cap);

      const brimGeo = new THREE.BoxGeometry(0.68, 0.05, 0.72);
      const brim = new THREE.Mesh(brimGeo, hatMat);
      brim.position.y = 0.14;
      hatGroup.add(brim);

      hatGroup.position.set(0, 0.12, 0);
      head.add(hatGroup);
      character.userData.hardHat = hatGroup;
    }

    const armGeo = new THREE.BoxGeometry(0.22, 0.8, 0.22);
    armGeo.translate(0, -0.35, 0);
    leftArm = new THREE.Mesh(armGeo, clothesMat);
    leftArm.position.set(-0.52, 1.7, 0);
    leftArm.castShadow = true;
    character.add(leftArm);

    rightArm = new THREE.Mesh(armGeo.clone(), clothesMat);
    rightArm.position.set(0.52, 1.7, 0);
    rightArm.castShadow = true;
    character.add(rightArm);

    const legGeo = new THREE.BoxGeometry(0.28, 0.9, 0.28);
    legGeo.translate(0, -0.45, 0);
    leftLeg = new THREE.Mesh(legGeo, darkMat);
    leftLeg.position.set(-0.25, 0.8, 0);
    leftLeg.castShadow = true;
    character.add(leftLeg);

    rightLeg = new THREE.Mesh(legGeo.clone(), darkMat);
    rightLeg.position.set(0.25, 0.8, 0);
    rightLeg.castShadow = true;
    character.add(rightLeg);
  }

  // Bind limbs to userData for animation loops
  character.userData.torso = torso;
  character.userData.head = head;
  character.userData.leftArm = leftArm;
  character.userData.rightArm = rightArm;
  character.userData.leftLeg = leftLeg;
  character.userData.rightLeg = rightLeg;

  // Sitting pose adjustments
  if (isSitting && leftLeg && rightLeg && leftArm && rightArm && torso && head) {
    leftLeg.rotation.x = -Math.PI / 2;
    rightLeg.rotation.x = -Math.PI / 2;
    leftArm.rotation.x = -Math.PI / 3;
    rightArm.rotation.x = -Math.PI / 3;
    torso.position.y = 0.85;
    head.position.y = 1.5;
    leftLeg.position.y = 0.45;
    rightLeg.position.y = 0.45;
    leftArm.position.y = 1.25;
    rightArm.position.y = 1.25;
  }

  // Accessories (Clipboard / Megaphone)
  if (hasClipboard && rightArm) {
    const clipGeo = new THREE.BoxGeometry(0.35, 0.45, 0.04);
    const clipMat = new THREE.MeshStandardMaterial({ color: 0x854d0e });
    const clipboard = new THREE.Mesh(clipGeo, clipMat);
    clipboard.position.set(0, -0.6, 0.15);
    clipboard.rotation.x = 0.3;
    rightArm.add(clipboard);
  }

  if (hasMegaphone && rightArm) {
    const coneGeo = new THREE.ConeGeometry(0.2, 0.4, 8);
    const coneMat = new THREE.MeshStandardMaterial({ color: 0xef4444 });
    const mega = new THREE.Mesh(coneGeo, coneMat);
    mega.rotation.x = Math.PI / 2;
    mega.position.set(0, -0.65, 0.25);
    rightArm.add(mega);
    character.userData.megaphoneMesh = mega;
  }

  // TOUCH HITBOX
  const hitGeo = new THREE.CylinderGeometry(1.2, 1.2, 2.6, 8);
  const hitMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false });
  const hitbox = new THREE.Mesh(hitGeo, hitMat);
  hitbox.position.y = 1.2;
  hitbox.userData.isAgentHitbox = true;
  hitbox.userData.agentId = agentId;
  character.add(hitbox);
  character.userData.hitbox = hitbox;

  return character;
}

// --- PROCEDURAL THEME DECORATIONS ---
export function createThemeDecorations(themeKey) {
  const decorGroup = new THREE.Group();
  decorGroup.name = 'themeDecorGroup';

  // 1. GREENHOUSE: Lush potted ferns and flowering shrubs
  if (themeKey === 'greenhouse') {
    const potMat = new THREE.MeshStandardMaterial({ color: 0x9a3412, roughness: 0.8 }); // Terracotta
    const leafMat = new THREE.MeshStandardMaterial({ color: 0x22c55e, roughness: 0.6 });

    const plantCoords = [[-14, 0, 10], [-10, 0, 14], [14, 0, 12], [8, 3.5, 5], [-6, 8.5, -4]];
    plantCoords.forEach(([x, y, z]) => {
      const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.35, 0.6, 8), potMat);
      pot.position.set(x, y + 0.3, z);
      decorGroup.add(pot);

      for (let i = 0; i < 5; i++) {
        const leaf = new THREE.Mesh(new THREE.ConeGeometry(0.3, 0.9, 5), leafMat);
        leaf.position.set(x, y + 0.7, z);
        leaf.rotation.z = (Math.random() - 0.5) * 0.8;
        leaf.rotation.x = (Math.random() - 0.5) * 0.8;
        decorGroup.add(leaf);
      }
    });
  }

  // 2. WINTER: Low-poly pine trees and a cute snowman
  else if (themeKey === 'winter') {
    const snowMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.5 });
    const pineMat = new THREE.MeshStandardMaterial({ color: 0x166534, roughness: 0.7 });
    const woodMat = new THREE.MeshStandardMaterial({ color: 0x78350f });

    // Pine Trees
    const treeCoords = [[-18, 0, 10], [16, 0, -14], [18, 0, 14]];
    treeCoords.forEach(([x, y, z]) => {
      const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.3, 1.2, 6), woodMat);
      trunk.position.set(x, y + 0.6, z);
      decorGroup.add(trunk);

      for (let lvl = 0; lvl < 3; lvl++) {
        const bough = new THREE.Mesh(new THREE.ConeGeometry(1.6 - lvl * 0.4, 1.4, 6), pineMat);
        bough.position.set(x, y + 1.4 + lvl * 0.8, z);
        decorGroup.add(bough);
      }
    });

    // Cute Snowman on Sanctuary Plaza
    const snowman = new THREE.Group();
    snowman.position.set(-14, 0, 8);
    const bottom = new THREE.Mesh(new THREE.SphereGeometry(0.9, 10, 10), snowMat);
    bottom.position.y = 0.9;
    snowman.add(bottom);
    const middle = new THREE.Mesh(new THREE.SphereGeometry(0.65, 10, 10), snowMat);
    middle.position.y = 2.0;
    snowman.add(middle);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.45, 10, 10), snowMat);
    head.position.y = 2.8;
    snowman.add(head);

    // Carrot nose
    const carrot = new THREE.Mesh(new THREE.ConeGeometry(0.08, 0.3, 6), new THREE.MeshStandardMaterial({ color: 0xf97316 }));
    carrot.rotation.x = Math.PI / 2;
    carrot.position.set(0, 2.8, 0.55);
    snowman.add(carrot);

    // Top hat
    const hat = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.4, 8), new THREE.MeshStandardMaterial({ color: 0x0f172a }));
    hat.position.set(0, 3.3, 0);
    snowman.add(hat);
    decorGroup.add(snowman);
  }

  // 3. BEACH: Palm tree and coconut props
  else if (themeKey === 'beach') {
    const trunkMat = new THREE.MeshStandardMaterial({ color: 0x92400e, roughness: 0.8 });
    const frondMat = new THREE.MeshStandardMaterial({ color: 0x15803d, roughness: 0.5 });

    const palm = new THREE.Group();
    palm.position.set(-15, 0, 12);
    for (let seg = 0; seg < 5; seg++) {
      const trunkSeg = new THREE.Mesh(new THREE.CylinderGeometry(0.28 - seg * 0.03, 0.32 - seg * 0.03, 1.2, 6), trunkMat);
      trunkSeg.position.set(seg * 0.15, seg * 1.1 + 0.6, 0);
      trunkSeg.rotation.z = -0.12;
      palm.add(trunkSeg);
    }
    for (let f = 0; f < 6; f++) {
      const frond = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.04, 0.6), frondMat);
      frond.position.set(0.6, 5.8, 0);
      frond.rotation.y = f * (Math.PI / 3);
      frond.rotation.z = -0.3;
      palm.add(frond);
    }
    decorGroup.add(palm);
  }

  // 4. HAUNTED: Tombstones & flickering teal candle pedestals
  else if (themeKey === 'haunted') {
    const stoneMat = new THREE.MeshStandardMaterial({ color: 0x3f3f46, roughness: 0.9 });
    const tealFlameMat = new THREE.MeshBasicMaterial({ color: 0x2dd4bf });

    [[-14, 0, 10], [-10, 0, 15], [14, 0, 12]].forEach(([x, y, z]) => {
      const tomb = new THREE.Mesh(new THREE.BoxGeometry(0.8, 1.2, 0.25), stoneMat);
      tomb.position.set(x, y + 0.6, z);
      tomb.rotation.y = (Math.random() - 0.5) * 0.4;
      decorGroup.add(tomb);

      const flame = new THREE.Mesh(new THREE.ConeGeometry(0.08, 0.25, 6), tealFlameMat);
      flame.position.set(x, y + 1.35, z);
      decorGroup.add(flame);
    });
  }

  // 5. CYBERPUNK: Glowing neon pylons
  else if (themeKey === 'cyberpunk') {
    const neonCyan = new THREE.MeshBasicMaterial({ color: 0x06b6d4 });
    const neonMagenta = new THREE.MeshBasicMaterial({ color: 0xf43f5e });

    [[-16, 0, 6, neonCyan], [16, 0, 6, neonMagenta], [-12, 0, 18, neonMagenta], [12, 0, 18, neonCyan]].forEach(([x, y, z, mat]) => {
      const pylon = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 4.0, 6), mat);
      pylon.position.set(x, y + 2.0, z);
      decorGroup.add(pylon);
    });
  }

  return decorGroup;
}

export function createScissorLift(options = {}) {
  const group = new THREE.Group();

  // Wheeled Base Cart
  const baseGeo = new THREE.BoxGeometry(3.0, 0.5, 2.2);
  const baseMat = new THREE.MeshStandardMaterial({ color: 0xd97706, roughness: 0.5 });
  const base = new THREE.Mesh(baseGeo, baseMat);
  base.position.y = 0.4;
  base.castShadow = true;
  group.add(base);

  // 4 Rubber Wheels
  const wheelGeo = new THREE.CylinderGeometry(0.3, 0.3, 0.3, 12);
  const wheelMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.9 });
  [[-1.1, -0.9], [-1.1, 0.9], [1.1, -0.9], [1.1, 0.9]].forEach(([x, z]) => {
    const w = new THREE.Mesh(wheelGeo, wheelMat);
    w.rotation.z = Math.PI / 2;
    w.position.set(x, 0.3, z);
    group.add(w);
  });

  // Scissor Trusses (X-bars)
  const trussMat = new THREE.MeshStandardMaterial({ color: 0x475569, metalness: 0.6 });
  const trussBarGeo = new THREE.BoxGeometry(0.12, 2.4, 0.12);

  const trussGroup = new THREE.Group();
  trussGroup.position.y = 0.65;
  group.add(trussGroup);

  const bar1 = new THREE.Mesh(trussBarGeo, trussMat);
  bar1.rotation.z = 0.6;
  bar1.position.set(0, 1.0, 0.8);
  trussGroup.add(bar1);

  const bar2 = new THREE.Mesh(trussBarGeo, trussMat);
  bar2.rotation.z = -0.6;
  bar2.position.set(0, 1.0, 0.8);
  trussGroup.add(bar2);

  // Top Railed Basket Platform
  const basketGroup = new THREE.Group();
  basketGroup.position.y = 2.4;
  group.add(basketGroup);

  const platGeo = new THREE.BoxGeometry(2.8, 0.15, 2.0);
  const platMat = new THREE.MeshStandardMaterial({ color: 0xd97706 });
  const plat = new THREE.Mesh(platGeo, platMat);
  basketGroup.add(plat);

  // Yellow Safety Railings around basket
  const railGeo = new THREE.BoxGeometry(2.8, 1.0, 0.08);
  const railMat = new THREE.MeshStandardMaterial({ color: 0xfacc15, roughness: 0.4 });
  const railFront = new THREE.Mesh(railGeo, railMat);
  railFront.position.set(0, 0.5, 0.96);
  basketGroup.add(railFront);

  const railBack = new THREE.Mesh(railGeo, railMat);
  railBack.position.set(0, 0.5, -0.96);
  basketGroup.add(railBack);

  // Elevation control method
  group.userData.setElevation = (h) => {
    basketGroup.position.y = 1.0 + h;
    trussGroup.scale.y = Math.max(0.4, h / 2.0);
  };

  return group;
}

export function createStepladder(height = 3.5) {
  const ladder = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: 0x94a3b8, metalness: 0.5 });

  const railGeo = new THREE.CylinderGeometry(0.06, 0.06, height, 6);
  const leftRail = new THREE.Mesh(railGeo, mat);
  leftRail.position.set(-0.5, height / 2, 0);
  ladder.add(leftRail);

  const rightRail = new THREE.Mesh(railGeo, mat);
  rightRail.position.set(0.5, height / 2, 0);
  ladder.add(rightRail);

  const rungCount = Math.floor(height / 0.5);
  for (let i = 1; i <= rungCount; i++) {
    const rungGeo = new THREE.CylinderGeometry(0.04, 0.04, 1.0, 6);
    const rung = new THREE.Mesh(rungGeo, mat);
    rung.rotation.z = Math.PI / 2;
    rung.position.set(0, i * 0.45, 0);
    ladder.add(rung);
  }

  return ladder;
}

export function createComputerDesk(options = {}) {
  const { dualMonitor = true, isHighTech = false } = options;
  const desk = new THREE.Group();

  const topGeo = new THREE.BoxGeometry(3.2, 0.12, 1.6);
  const topMat = new THREE.MeshStandardMaterial({
    color: isHighTech ? 0x1e293b : 0x78350f,
    roughness: 0.5
  });
  const top = new THREE.Mesh(topGeo, topMat);
  top.position.y = 1.1;
  top.castShadow = true;
  desk.add(top);

  const legGeo = new THREE.CylinderGeometry(0.06, 0.06, 1.1, 8);
  const legMat = new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.7 });
  [[-1.4, -0.6], [-1.4, 0.6], [1.4, -0.6], [1.4, 0.6]].forEach(([x, z]) => {
    const leg = new THREE.Mesh(legGeo, legMat);
    leg.position.set(x, 0.55, z);
    desk.add(leg);
  });

  const monCount = dualMonitor ? 2 : 1;
  const screenMat = new THREE.MeshStandardMaterial({
    color: 0x0284c7,
    emissive: 0x0284c7,
    emissiveIntensity: 0.6,
    roughness: 0.2
  });
  const standMat = new THREE.MeshStandardMaterial({ color: 0x0f172a });

  for (let i = 0; i < monCount; i++) {
    const monGroup = new THREE.Group();
    const screenGeo = new THREE.BoxGeometry(1.0, 0.65, 0.06);
    const screen = new THREE.Mesh(screenGeo, screenMat);
    screen.position.y = 0.55;
    monGroup.add(screen);

    const standGeo = new THREE.CylinderGeometry(0.04, 0.04, 0.4, 8);
    const stand = new THREE.Mesh(standGeo, standMat);
    stand.position.y = 0.2;
    monGroup.add(stand);

    const posX = dualMonitor ? (i === 0 ? -0.65 : 0.65) : 0;
    monGroup.position.set(posX, 1.16, -0.3);
    if (dualMonitor) monGroup.rotation.y = (i === 0 ? 0.15 : -0.15);
    desk.add(monGroup);
  }

  const chairGeo = new THREE.BoxGeometry(0.9, 0.1, 0.9);
  const chairMat = new THREE.MeshStandardMaterial({ color: 0x1e293b });
  const chair = new THREE.Mesh(chairGeo, chairMat);
  chair.position.set(0, 0.6, 0.4);
  desk.add(chair);

  const backGeo = new THREE.BoxGeometry(0.9, 0.9, 0.1);
  const chairBack = new THREE.Mesh(backGeo, chairMat);
  chairBack.position.set(0, 1.05, 0.85);
  desk.add(chairBack);

  return desk;
}

export function createMemoryMonolith(options = {}) {
  const { color = 0x38bdf8, emissive = 0x0284c7 } = options;
  const monolith = new THREE.Group();

  const pillarGeo = new THREE.BoxGeometry(1.6, 5.0, 1.6);
  const pillarMat = new THREE.MeshPhysicalMaterial({
    color: color,
    emissive: emissive,
    emissiveIntensity: 0.5,
    roughness: 0.1,
    metalness: 0.2,
    transparent: true,
    opacity: 0.85,
    transmission: 0.5
  });
  const pillar = new THREE.Mesh(pillarGeo, pillarMat);
  pillar.position.y = 2.5;
  pillar.castShadow = true;
  monolith.add(pillar);

  const baseGeo = new THREE.BoxGeometry(2.4, 0.4, 2.4);
  const baseMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.7 });
  const base = new THREE.Mesh(baseGeo, baseMat);
  base.position.y = 0.2;
  monolith.add(base);

  return monolith;
}