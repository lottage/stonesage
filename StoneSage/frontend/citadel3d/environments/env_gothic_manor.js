/**
 * Aevum-3D: Grand Gothic Manor Atrium (Ultra-High Fidelity)
 * Faithful realization of Reference Image 3:
 * - Medieval flagstone paving with procedural mortar joints
 * - Grand stone imperial staircase mathematically welded to gallery floor
 * - Pointed gothic stone arches, clustered pillars, and carved stone balustrades
 * - Floor-to-ceiling library bookcases, banquet table with velvet runner, and reflection pool
 */

import {
  createExactStaircase,
  createDetailedBookshelf,
  createLantern
} from './env_base.js';

import { createFlagstoneTexture, createOrientalRugTexture } from '../texture_generator.js';

const THREE = window.THREE;

export function buildGothicManorEnvironment(scene) {
  const envGroup = new THREE.Group();
  envGroup.name = 'env_gothic_manor';

  const config = {
    bg: 0x181512,
    fogColor: 0x241d17,
    fogDensity: 0.012,
    cameraPos: [42, 36, 42],
    cameraTarget: [0, 5, 0]
  };

  // Moody Warm Candlelight & Cool Courtyard Atmosphere
  const ambient = new THREE.AmbientLight(0xfef3c7, 0.75);
  envGroup.add(ambient);

  const hemi = new THREE.HemisphereLight(0x93c5fd, 0x451a03, 0.5);
  envGroup.add(hemi);

  const sun = new THREE.DirectionalLight(0xfef08a, 1.0);
  sun.position.set(30, 48, 25);
  sun.castShadow = true;
  sun.shadow.mapSize.width = 2048;
  sun.shadow.mapSize.height = 2048;
  sun.shadow.bias = -0.0004;
  envGroup.add(sun);

  // Procedural Textures & Materials
  const flagstoneTex = createFlagstoneTexture('#57534e', '#292524');
  const stoneFloorMat = new THREE.MeshStandardMaterial({ map: flagstoneTex, roughness: 0.8 });
  const stonePillarMat = new THREE.MeshStandardMaterial({ color: 0x57534e, roughness: 0.85 });
  const darkWoodMat = new THREE.MeshStandardMaterial({ color: 0x3d1a08, roughness: 0.6 });

  const waterMat = new THREE.MeshPhysicalMaterial({
    color: 0x0284c7,
    roughness: 0.1,
    transmission: 0.88,
    transparent: true,
    opacity: 0.82
  });
  const hedgeMat = new THREE.MeshStandardMaterial({ color: 0x14532d, roughness: 0.9 });
  const velvetRedMat = new THREE.MeshStandardMaterial({ color: 0x881337, roughness: 0.7 });

  // --- LEVEL 1: GRAND COURTYARD ATRIUM (Y = 0.0) ---
  const floorMesh = new THREE.Mesh(new THREE.BoxGeometry(34, 0.8, 32), stoneFloorMat);
  floorMesh.position.set(-2, -0.4, -2);
  floorMesh.receiveShadow = true;
  envGroup.add(floorMesh);

  // Grand Persian Area Rug in Atrium Center
  const rugTex = createOrientalRugTexture('#881337', '#fde047');
  const rug = new THREE.Mesh(
    new THREE.PlaneGeometry(9.0, 12.0),
    new THREE.MeshStandardMaterial({ map: rugTex, roughness: 0.9 })
  );
  rug.rotation.x = -Math.PI / 2;
  rug.position.set(-3, 0.02, -2);
  rug.receiveShadow = true;
  envGroup.add(rug);

  // Long Mahogany Banquet Table
  const banquetGroup = new THREE.Group();
  const table = new THREE.Mesh(new THREE.BoxGeometry(6.6, 0.16, 2.6), darkWoodMat);
  table.position.y = 1.1;
  table.castShadow = true;
  banquetGroup.add(table);

  // Red Velvet Table Runner
  const runner = new THREE.Mesh(new THREE.BoxGeometry(6.7, 0.03, 1.3), velvetRedMat);
  runner.position.y = 1.19;
  banquetGroup.add(runner);

  // 6 High-Backed Carved Dining Chairs
  for (let c = 0; c < 6; c++) {
    const chair = new THREE.Group();
    const seat = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.08, 0.8), darkWoodMat);
    seat.position.y = 0.55;
    chair.add(seat);

    const back = new THREE.Mesh(new THREE.BoxGeometry(0.8, 1.3, 0.08), darkWoodMat);
    back.position.set(0, 1.2, -0.36);
    chair.add(back);

    const xPos = -2.2 + (c % 3) * 2.2;
    const zPos = (c < 3 ? -1.6 : 1.6);
    chair.position.set(xPos, 0, zPos);
    if (c >= 3) chair.rotation.y = Math.PI;
    banquetGroup.add(chair);
  }
  banquetGroup.position.set(-3, 0, -2);
  envGroup.add(banquetGroup);

  // Brass Candelabras on Table
  [-2.0, 0.0, 2.0].forEach(cx => {
    const cand = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.12, 0.6, 8), new THREE.MeshStandardMaterial({ color: 0xfacc15, metalness: 0.8 }));
    cand.position.set(-3 + cx, 1.5, -2);
    cand.castShadow = true;
    envGroup.add(cand);

    const candleLight = new THREE.PointLight(0xfef08a, 0.8, 8);
    candleLight.position.set(-3 + cx, 1.8, -2);
    envGroup.add(candleLight);
  });

  // --- LEVEL 2 & 3: GOTHIC MEZZANINE ARCADES ---
  // Level 2 L-shaped Gallery Balcony (Y = 4.5)
  const l2Back = new THREE.Mesh(new THREE.BoxGeometry(26, 0.8, 6.5), stoneFloorMat);
  l2Back.position.set(-4, 4.5 - 0.4, -13);
  l2Back.receiveShadow = true;
  l2Back.castShadow = true;
  envGroup.add(l2Back);

  const l2Side = new THREE.Mesh(new THREE.BoxGeometry(6.5, 0.8, 18), stoneFloorMat);
  l2Side.position.set(-14, 4.5 - 0.4, -2);
  l2Side.receiveShadow = true;
  l2Side.castShadow = true;
  envGroup.add(l2Side);

  // Pointed Gothic Stone Arches & Clustered Pillars
  const pillarGeo = new THREE.BoxGeometry(0.7, 4.5, 0.7);
  for (let a = 0; a < 4; a++) {
    const px = -13 + a * 6.0;
    const p1 = new THREE.Mesh(pillarGeo, stonePillarMat);
    p1.position.set(px, 2.25, -9.8);
    p1.castShadow = true;
    envGroup.add(p1);

    if (a < 3) {
      const arch = new THREE.Mesh(new THREE.TorusGeometry(3.0, 0.3, 8, 16, Math.PI), stonePillarMat);
      arch.position.set(px + 3.0, 3.8, -9.8);
      envGroup.add(arch);
    }
  }

  // Stone Balustrades along Level 2 Gallery
  const balustrade = new THREE.Mesh(new THREE.BoxGeometry(24, 1.1, 0.22), stonePillarMat);
  balustrade.position.set(-4, 5.05, -9.8);
  envGroup.add(balustrade);

  // EXACT MATHEMATICAL IMPERIAL STONE STAIRS
  // From ground [-14.0, 0.0, 6.0] to gallery landing [-14.0, 4.5, -4.0]!
  const stairs = createExactStaircase(
    [-14.0, 0.0, 6.0],
    [-14.0, 4.5, -4.0],
    2.6,
    {
      treadColor: 0x57534e,
      stringerColor: 0x44403c,
      railColor: 0x292524,
      isOpenRiser: false,
      hasRailing: true
    }
  );
  envGroup.add(stairs);

  // 2-Story Tall Mahogany Bookshelves with Textured Book Spines
  const shelf1 = createDetailedBookshelf({ width: 7.2, height: 4.8, depth: 0.9, woodColor: 0x291405, shelfCount: 4 });
  shelf1.position.set(-6, 4.5, -15.8);
  envGroup.add(shelf1);

  const shelf2 = createDetailedBookshelf({ width: 6.0, height: 4.8, depth: 0.9, woodColor: 0x291405, shelfCount: 4 });
  shelf2.position.set(-16.8, 4.5, -6);
  shelf2.rotation.y = Math.PI / 2;
  envGroup.add(shelf2);

  // --- EXTERIOR TERRACE, REFLECTING POOL & HEDGES ---
  const poolBasin = new THREE.Mesh(new THREE.BoxGeometry(14, 0.5, 9), stonePillarMat);
  poolBasin.position.set(12, -0.4, 8);
  envGroup.add(poolBasin);

  const water = new THREE.Mesh(new THREE.PlaneGeometry(13.2, 8.2), waterMat);
  water.rotation.x = -Math.PI / 2;
  water.position.set(12, -0.15, 8);
  envGroup.add(water);

  // Manicured Boxwood Hedges
  const hedge1 = new THREE.Mesh(new THREE.BoxGeometry(1.4, 1.5, 10.5), hedgeMat);
  hedge1.position.set(4.3, 0.75, 8);
  hedge1.castShadow = true;
  envGroup.add(hedge1);

  const hedge2 = new THREE.Mesh(new THREE.BoxGeometry(1.4, 1.5, 10.5), hedgeMat);
  hedge2.position.set(19.7, 0.75, 8);
  hedge2.castShadow = true;
  envGroup.add(hedge2);

  // Grand Gothic Chandelier
  const ch = new THREE.PointLight(0xfef08a, 1.8, 26);
  ch.position.set(-3, 7.5, -2);
  ch.castShadow = true;
  envGroup.add(ch);

  // Standing Candelabras
  const cand1 = createLantern({ color: 0xfef08a, intensity: 1.4, distance: 14, isStanding: true, height: 2.4 });
  cand1.position.set(-8, 0, 3);
  envGroup.add(cand1);

  const cand2 = createLantern({ color: 0xfef08a, intensity: 1.4, distance: 14, isStanding: true, height: 2.4 });
  cand2.position.set(2, 0, 3);
  envGroup.add(cand2);

  // Agent Placements for Gothic Manor
  const agentSlots = {
    'coord-alpha': { pos: [-6, 4.5, -12], rotY: Math.PI },       // High in the gothic library gallery
    'worker-beta': { pos: [-3, 0, 0], rotY: -Math.PI / 4 },       // Presiding at the banquet table
    'worker-gamma': { pos: [-14, 4.5, -1], rotY: Math.PI / 2 },   // Studying at the side arcade gallery
    'ally-delta': { pos: [6, 0, 8], rotY: -Math.PI / 3 }         // Overlooking the azure reflection pool
  };

  // Kanban Board Placement
  const kanbanConfig = {
    pos: [-4, 4.5, -8],
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
