/**
 * Aevum-3D: Botanical Greenhouse Loft (Ultra-High Fidelity)
 * Faithful realization of Reference Image 1:
 * - True herringbone parquet flooring via procedural canvas texture
 * - Mathematically exact stairs connecting sunken lounge, mid-terrace & upper loft
 * - Multi-pane arched greenhouse windows with warm sunbeams
 * - Layered climbing ivy, tiered A-frame plant ladders, and fluted amber glass balustrades
 */

import {
  createExactStaircase,
  createDetailedWindow,
  createDetailedBookshelf,
  createDetailedSofa,
  createDetailedPlant
} from './env_base.js';

import { createHerringboneWoodTexture, createOrientalRugTexture } from '../texture_generator.js';

const THREE = window.THREE;

export function buildGreenhouseEnvironment(scene) {
  const envGroup = new THREE.Group();
  envGroup.name = 'env_greenhouse';

  const config = {
    bg: 0x1f1914,
    fogColor: 0x2e251e,
    fogDensity: 0.010,
    cameraPos: [36, 30, 36],
    cameraTarget: [0, 4, 0]
  };

  // Lighting: Warm Morning Sunbeams & Gentle Sky Fill
  const ambient = new THREE.AmbientLight(0xfef3c7, 0.85);
  envGroup.add(ambient);

  const hemi = new THREE.HemisphereLight(0xfef08a, 0x14532d, 0.65);
  envGroup.add(hemi);

  const sun = new THREE.DirectionalLight(0xfef08a, 1.25);
  sun.position.set(35, 48, 25);
  sun.castShadow = true;
  sun.shadow.mapSize.width = 2048;
  sun.shadow.mapSize.height = 2048;
  sun.shadow.bias = -0.0003;
  envGroup.add(sun);

  // Procedural Materials
  const woodTexture = createHerringboneWoodTexture('#78350f', 24, 88);
  const floorMat = new THREE.MeshStandardMaterial({ map: woodTexture, roughness: 0.5 });
  const terracottaMat = new THREE.MeshStandardMaterial({ color: 0x9a3412, roughness: 0.8 });
  const wallMat = new THREE.MeshStandardMaterial({ color: 0xfefce8, roughness: 0.85 }); // Warm cream plaster
  const timberMat = new THREE.MeshStandardMaterial({ color: 0x451a03, roughness: 0.6 });

  const amberGlassMat = new THREE.MeshPhysicalMaterial({
    color: 0xfbbf24,
    transparent: true,
    opacity: 0.65,
    roughness: 0.15,
    transmission: 0.75
  });

  // --- LEVEL 1: SUNKEN CONSERVATORY LOUNGE (Y = 0.0) ---
  const l1FloorGeo = new THREE.BoxGeometry(32, 0.8, 30);
  const l1Floor = new THREE.Mesh(l1FloorGeo, floorMat);
  l1Floor.position.set(0, -0.4, 0);
  l1Floor.receiveShadow = true;
  envGroup.add(l1Floor);

  // Amber Glass Railing along Sunken Lounge Foreground
  const glassRailGeo = new THREE.BoxGeometry(16, 1.1, 0.12);
  const glassRail = new THREE.Mesh(glassRailGeo, amberGlassMat);
  glassRail.position.set(-6, 0.55, 11);
  envGroup.add(glassRail);

  const topBrassRailGeo = new THREE.BoxGeometry(16.2, 0.08, 0.16);
  const topBrassRail = new THREE.Mesh(topBrassRailGeo, new THREE.MeshStandardMaterial({ color: 0xd97706, metalness: 0.8 }));
  topBrassRail.position.set(-6, 1.14, 11);
  envGroup.add(topBrassRail);

  // Oriental Area Rug in Lounge
  const rugTex = createOrientalRugTexture('#78350f', '#fde68a');
  const rugGeo = new THREE.PlaneGeometry(7.5, 9.5);
  const rugMat = new THREE.MeshStandardMaterial({ map: rugTex, roughness: 0.9 });
  const rug = new THREE.Mesh(rugGeo, rugMat);
  rug.rotation.x = -Math.PI / 2;
  rug.position.set(-7, 0.02, 3);
  rug.receiveShadow = true;
  envGroup.add(rug);

  // Sectional Sofa in Lounge
  const sofa = createDetailedSofa({ color: 0xb45309, cushionColor: 0xfef08a, pillowColor: 0x15803d });
  sofa.position.set(-7, 0, 3);
  sofa.rotation.y = Math.PI / 4;
  envGroup.add(sofa);

  // Coffee Table with Ceramic Teapot & Cups
  const coffeeTableGeo = new THREE.BoxGeometry(2.4, 0.1, 1.4);
  const coffeeTable = new THREE.Mesh(coffeeTableGeo, timberMat);
  coffeeTable.position.set(-5.5, 0.45, 1.5);
  coffeeTable.castShadow = true;
  envGroup.add(coffeeTable);

  const teapot = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.22, 0.25, 8), new THREE.MeshStandardMaterial({ color: 0x047857 }));
  teapot.position.set(-5.5, 0.65, 1.5);
  envGroup.add(teapot);

  // --- LEVEL 2: MID-TERRACE KITCHEN & TEA WORKBENCH (Y = 3.2) ---
  const l2Geo = new THREE.BoxGeometry(18, 0.8, 16);
  const l2Floor = new THREE.Mesh(l2Geo, terracottaMat);
  l2Floor.position.set(6, 3.2 - 0.4, -4);
  l2Floor.receiveShadow = true;
  l2Floor.castShadow = true;
  envGroup.add(l2Floor);

  // EXACT MATHEMATICAL STAIRS from Level 1 (y=0) to Level 2 (y=3.2)
  // Starts at [-4.0, 0.0, 0.0] and lands exactly flush at [-3.0, 3.2, -4.0] on the L2 floor edge!
  const stairsToL2 = createExactStaircase(
    [-4.5, 0.0, 0.0],
    [-3.0, 3.2, -4.0],
    2.2,
    { treadColor: 0x92400e, stringerColor: 0x451a03, railColor: 0x78350f, isOpenRiser: true }
  );
  envGroup.add(stairsToL2);

  // --- LEVEL 3: UPPER BEDROOM & BOTANICAL STUDY LOFT (Y = 6.4) ---
  const l3Geo = new THREE.BoxGeometry(14, 0.8, 13);
  const l3Floor = new THREE.Mesh(l3Geo, floorMat);
  l3Floor.position.set(7, 6.4 - 0.4, -9);
  l3Floor.receiveShadow = true;
  l3Floor.castShadow = true;
  envGroup.add(l3Floor);

  // EXACT MATHEMATICAL STAIRS from Level 2 (y=3.2) to Level 3 (y=6.4)
  // Starts at [13.0, 3.2, -2.0] and ascends to [13.0, 6.4, -7.0]
  const stairsToL3 = createExactStaircase(
    [13.0, 3.2, -1.5],
    [13.0, 6.4, -6.5],
    1.8,
    { treadColor: 0xb45309, stringerColor: 0x451a03, railColor: 0x78350f, isOpenRiser: true }
  );
  envGroup.add(stairsToL3);

  // Loft Timber Railing with Balusters
  const loftRailGeo = new THREE.BoxGeometry(12, 1.1, 0.12);
  const loftRail = new THREE.Mesh(loftRailGeo, timberMat);
  loftRail.position.set(7, 6.95, -2.5);
  envGroup.add(loftRail);

  // --- ARCHITECTURAL WALLS & 3 ARCHED SUNLIT WINDOWS ---
  const backWall = new THREE.Mesh(new THREE.BoxGeometry(28, 14, 0.6), wallMat);
  backWall.position.set(1, 7, -15);
  backWall.receiveShadow = true;
  envGroup.add(backWall);

  // 3 Multi-Pane Arched Greenhouse Windows
  [-7, 1, 9].forEach(x => {
    const win = createDetailedWindow({
      width: 3.4,
      height: 5.4,
      isArched: true,
      frameColor: 0x78350f,
      glassColor: 0xfef08a,
      emissiveIntensity: 0.65,
      mullionCols: 3,
      mullionRows: 4
    });
    win.position.set(x, 4.2, -14.7);
    envGroup.add(win);

    // Warm Sunbeam Point Light
    const sunbeam = new THREE.PointLight(0xfef08a, 1.4, 22);
    sunbeam.position.set(x, 7.0, -12);
    envGroup.add(sunbeam);
  });

  // Pitched Timber Rafters across ceiling
  for (let r = 0; r < 5; r++) {
    const rafter = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.35, 14), timberMat);
    rafter.position.set(-6 + r * 3.6, 12.8, -8);
    rafter.rotation.x = 0.15;
    envGroup.add(rafter);
  }

  // --- DETAILED FLORA (Tiered Plant Ladders, Monsteras, Vines) ---
  // A-Frame Plant Ladder in garden lounge
  const plantLadder1 = createDetailedPlant('plant_ladder', 1.2);
  plantLadder1.position.set(-11, 0, 7);
  envGroup.add(plantLadder1);

  // A-Frame Plant Ladder on mid-terrace
  const plantLadder2 = createDetailedPlant('plant_ladder', 1.0);
  plantLadder2.position.set(1, 3.2, -11);
  envGroup.add(plantLadder2);

  // Large Potted Monsteras with Multi-Lobed Leaves
  const monstera1 = createDetailedPlant('monstera', 1.3);
  monstera1.position.set(-12, 0, 0);
  envGroup.add(monstera1);

  const monstera2 = createDetailedPlant('monstera', 1.1);
  monstera2.position.set(12, 0, 10);
  envGroup.add(monstera2);

  const monstera3 = createDetailedPlant('monstera', 1.0);
  monstera3.position.set(12, 6.4, -13);
  envGroup.add(monstera3);

  // Bookshelf with Crown Molding in the Study Loft
  const shelf = createDetailedBookshelf({ width: 4.8, height: 5.2, depth: 0.9, woodColor: 0x451a03, shelfCount: 4 });
  shelf.position.set(7, 6.4, -14.2);
  envGroup.add(shelf);

  // Agent Placements for Greenhouse
  const agentSlots = {
    'coord-alpha': { pos: [6, 6.4, -8], rotY: Math.PI },       // High in the botanical study loft
    'worker-beta': { pos: [4, 3.2, -4], rotY: -Math.PI / 4 },    // At the mid-terrace tea table
    'worker-gamma': { pos: [-5, 0, 4], rotY: Math.PI / 3 },     // Relaxing in the sunken garden lounge
    'ally-delta': { pos: [-2, 0, 9], rotY: -Math.PI / 3 }       // Overlooking the fluted glass balustrade
  };

  // Kanban Board Placement
  const kanbanConfig = {
    pos: [2, 3.2, 1],
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
