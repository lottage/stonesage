/**
 * Aevum-3D: Modern Split-Level Architect Loft (Ultra-High Fidelity)
 * Faithful realization of Reference Image 2:
 * - Crisp architectural cutaway walls with clean white finish and dark columns
 * - Floating open-riser timber staircase with matte black stringers and vertical wire balusters
 * - Upper mezzanine master suite with platform bed, oak parquet, and clear glass railing
 * - Sunken conversation pit with Scandinavian geometric rug, sectional sofa, and panoramic windows
 */

import {
  createExactStaircase,
  createDetailedWindow,
  createDetailedSofa,
  createDetailedPlant
} from './env_base.js';

import {
  createModernTileTexture,
  createHerringboneWoodTexture,
  createGeometricRugTexture
} from '../texture_generator.js';

const THREE = window.THREE;

export function buildModernLoftEnvironment(scene) {
  const envGroup = new THREE.Group();
  envGroup.name = 'env_modern_loft';

  const config = {
    bg: 0xe0f2fe,
    fogColor: 0xf1f5f9,
    fogDensity: 0.008,
    cameraPos: [35, 28, 35],
    cameraTarget: [0, 4, 0]
  };

  // Clean, high-key architectural daylight
  const ambient = new THREE.AmbientLight(0xffffff, 0.95);
  envGroup.add(ambient);

  const hemi = new THREE.HemisphereLight(0xffffff, 0x94a3b8, 0.55);
  envGroup.add(hemi);

  const sun = new THREE.DirectionalLight(0xffffff, 1.2);
  sun.position.set(30, 52, 22);
  sun.castShadow = true;
  sun.shadow.mapSize.width = 2048;
  sun.shadow.mapSize.height = 2048;
  sun.shadow.bias = -0.0003;
  envGroup.add(sun);

  // Textures & Materials
  const tileTex = createModernTileTexture('#f8fafc', '#cbd5e1', 64);
  const groundTileMat = new THREE.MeshStandardMaterial({ map: tileTex, roughness: 0.35 });

  const woodTex = createHerringboneWoodTexture('#d97706', 20, 80);
  const mezWoodMat = new THREE.MeshStandardMaterial({ map: woodTex, roughness: 0.4 });

  const whiteWallMat = new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.9 });
  const darkSteelMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.25, metalness: 0.85 });
  const glassMat = new THREE.MeshPhysicalMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.35,
    roughness: 0.05,
    transmission: 0.92
  });

  // --- LOWER GROUND FLOOR (Y = 0.0) ---
  const groundMesh = new THREE.Mesh(new THREE.BoxGeometry(32, 0.8, 30), groundTileMat);
  groundMesh.position.set(0, -0.4, 0);
  groundMesh.receiveShadow = true;
  envGroup.add(groundMesh);

  // Sunken Conversation Pit Area with Scandinavian Geometric Rug
  const rugTex = createGeometricRugTexture('#334155', '#e2e8f0');
  const rug = new THREE.Mesh(
    new THREE.PlaneGeometry(8.5, 10.5),
    new THREE.MeshStandardMaterial({ map: rugTex, roughness: 0.9 })
  );
  rug.rotation.x = -Math.PI / 2;
  rug.position.set(4, 0.02, 6);
  rug.receiveShadow = true;
  envGroup.add(rug);

  // 4-Piece Modern Sectional Couch
  const sofa = createDetailedSofa({ color: 0x475569, cushionColor: 0x94a3b8, pillowColor: 0x0284c7 });
  sofa.position.set(4, 0, 6);
  sofa.rotation.y = -Math.PI / 4;
  envGroup.add(sofa);

  // Modern Minimalist Coffee Table
  const coffeeTable = new THREE.Mesh(
    new THREE.CylinderGeometry(1.1, 1.1, 0.35, 24),
    new THREE.MeshStandardMaterial({ color: 0xd97706, roughness: 0.4 })
  );
  coffeeTable.position.set(4, 0.18, 6);
  coffeeTable.castShadow = true;
  envGroup.add(coffeeTable);

  // Dining Area on Lower Left
  const diningTable = new THREE.Mesh(new THREE.BoxGeometry(4.4, 0.1, 2.2), new THREE.MeshStandardMaterial({ color: 0x92400e, roughness: 0.4 }));
  diningTable.position.set(-8, 1.1, 4);
  diningTable.castShadow = true;
  envGroup.add(diningTable);

  [[-9.8, 3.1], [-6.2, 3.1], [-9.8, 4.9], [-6.2, 4.9]].forEach(([x, z]) => {
    const leg = new THREE.Mesh(new THREE.BoxGeometry(0.08, 1.1, 0.08), darkSteelMat);
    leg.position.set(x, 0.55, z);
    envGroup.add(leg);
  });

  // 4 Modern Dining Chairs
  for (let c = 0; c < 4; c++) {
    const chair = new THREE.Group();
    const seat = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.08, 0.8), new THREE.MeshStandardMaterial({ color: 0x334155 }));
    seat.position.y = 0.6;
    chair.add(seat);

    const back = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.65, 0.06), new THREE.MeshStandardMaterial({ color: 0x334155 }));
    back.position.set(0, 0.95, -0.38);
    chair.add(back);

    const cx = (c % 2 === 0 ? -9.1 : -6.9);
    const cz = (c < 2 ? 2.6 : 5.4);
    chair.position.set(cx, 0, cz);
    if (c >= 2) chair.rotation.y = Math.PI;
    envGroup.add(chair);
  }

  // --- UPPER MEZZANINE DECK (Y = 5.0) ---
  const mezMesh = new THREE.Mesh(new THREE.BoxGeometry(18, 0.6, 15), mezWoodMat);
  mezMesh.position.set(3, 5.0 - 0.3, -7.5);
  mezMesh.receiveShadow = true;
  mezMesh.castShadow = true;
  envGroup.add(mezMesh);

  // Clear Glass Mezzanine Railing with Black Cap
  const mezGlass = new THREE.Mesh(new THREE.BoxGeometry(18, 1.1, 0.06), glassMat);
  mezGlass.position.set(3, 5.55, 0);
  envGroup.add(mezGlass);

  const mezTopCap = new THREE.Mesh(new THREE.BoxGeometry(18.2, 0.06, 0.12), darkSteelMat);
  mezTopCap.position.set(3, 6.1, 0);
  envGroup.add(mezTopCap);

  // EXACT MATHEMATICAL FLOATING STAIRCASE
  // From ground floor [-9.0, 0.0, 3.0] to mezzanine landing [-6.0, 5.0, 0.0]!
  const stairs = createExactStaircase(
    [-9.0, 0.0, 3.5],
    [-6.0, 5.0, 0.0],
    2.2,
    {
      treadColor: 0xd97706,
      stringerColor: 0x0f172a,
      railColor: 0x0f172a,
      isOpenRiser: true,
      hasRailing: true
    }
  );
  envGroup.add(stairs);

  // Mezzanine Platform Bed with Duvet
  const bedGroup = new THREE.Group();
  const bedFrame = new THREE.Mesh(new THREE.BoxGeometry(3.8, 0.4, 4.4), darkSteelMat);
  bedFrame.position.y = 0.2;
  bedGroup.add(bedFrame);

  const mattress = new THREE.Mesh(new THREE.BoxGeometry(3.4, 0.35, 4.0), new THREE.MeshStandardMaterial({ color: 0x38bdf8, roughness: 0.6 }));
  mattress.position.set(0, 0.55, 0);
  bedGroup.add(mattress);

  [-1.0, 1.0].forEach(px => {
    const pil = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.18, 0.65), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.8 }));
    pil.position.set(px, 0.75, -1.45);
    bedGroup.add(pil);
  });
  bedGroup.position.set(7, 5.0, -8);
  envGroup.add(bedGroup);

  // Floating Bedside Consoles
  [-1.8, 1.8].forEach(sx => {
    const consoleTable = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.3, 0.8), new THREE.MeshStandardMaterial({ color: 0xd97706 }));
    consoleTable.position.set(7 + sx * 1.5, 5.5, -9.5);
    envGroup.add(consoleTable);
  });

  // --- CUTAWAY WALLS & PANORAMIC PICTURE WINDOWS ---
  const backWall = new THREE.Mesh(new THREE.BoxGeometry(28, 13, 0.6), whiteWallMat);
  backWall.position.set(1, 6.5, -15);
  backWall.receiveShadow = true;
  envGroup.add(backWall);

  const leftWall = new THREE.Mesh(new THREE.BoxGeometry(0.6, 13, 18), new THREE.MeshStandardMaterial({ color: 0x334155 }));
  leftWall.position.set(-13, 6.5, -6);
  leftWall.receiveShadow = true;
  envGroup.add(leftWall);

  // Large Black-Framed Panoramic Picture Window on Upper Mezzanine
  const windowFrame = createDetailedWindow({
    width: 11.0,
    height: 4.8,
    frameColor: 0x0f172a,
    glassColor: 0xffffff,
    emissiveIntensity: 0.2,
    mullionCols: 4,
    mullionRows: 2
  });
  windowFrame.position.set(4, 8.5, -14.7);
  envGroup.add(windowFrame);

  // Flat Screen TV on Lower Living Room Wall
  const tv = new THREE.Mesh(
    new THREE.BoxGeometry(4.0, 2.2, 0.08),
    new THREE.MeshStandardMaterial({ color: 0x0284c7, emissive: 0x0284c7, emissiveIntensity: 0.35 })
  );
  tv.position.set(4, 8.0, 0.05);
  tv.rotation.y = Math.PI;
  envGroup.add(tv);

  // Indoor Statement Plants (Fiddle-Leaf Fig & Monstera)
  const monstera1 = createDetailedPlant('monstera', 1.3);
  monstera1.position.set(-11.5, 0, 8);
  envGroup.add(monstera1);

  const monstera2 = createDetailedPlant('monstera', 1.1);
  monstera2.position.set(12, 0, 11);
  envGroup.add(monstera2);

  const monstera3 = createDetailedPlant('monstera', 0.9);
  monstera3.position.set(12, 5.0, -13);
  envGroup.add(monstera3);

  // Recessed Ceiling Spotlights
  const spot1 = new THREE.PointLight(0xfef08a, 1.3, 18);
  spot1.position.set(4, 10.0, 5);
  envGroup.add(spot1);

  const spot2 = new THREE.PointLight(0x38bdf8, 1.1, 18);
  spot2.position.set(-8, 10.0, 4);
  envGroup.add(spot2);

  // Agent Placements for Modern Loft
  const agentSlots = {
    'coord-alpha': { pos: [3, 5.0, -4], rotY: Math.PI },        // At the upper mezzanine desk
    'worker-beta': { pos: [-8, 0, 4], rotY: -Math.PI / 4 },      // At the dining conference table
    'worker-gamma': { pos: [4, 0, 6], rotY: Math.PI / 4 },       // In the sunken conversation pit
    'ally-delta': { pos: [-5, 0, 8], rotY: -Math.PI / 3 }        // Near the floating open-riser staircase
  };

  // Kanban Board Placement (along the mezzanine front edge)
  const kanbanConfig = {
    pos: [-2, 5.0, 1.5],
    rotY: 0
  };

  scene.add(envGroup);

  return {
    group: envGroup,
    config,
    agentSlots,
    kanbanConfig
  };
}
