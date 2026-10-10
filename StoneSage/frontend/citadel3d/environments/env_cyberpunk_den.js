/**
 * Aevum-3D: Cyberpunk Neon Workshop / Hacker Den (Ultra-High Fidelity)
 * Faithful realization of Reference Image 5:
 * - Multi-tier vertical electronics lab with industrial catwalk
 * - Procedural cyber metal wall panels with ventilation louvers & circuit traces
 * - Bundles of physical drooping catenary cables across ceilings and racks
 * - Recessed server cabinets with status LED rows & multi-monitor CRT diagnostic console
 * - Vivid dual-tone neon lighting (magenta floor wash & electric cyan server bays)
 */

import { createDroopingCable } from './env_base.js';
import { createCyberPanelTexture } from '../texture_generator.js';

const THREE = window.THREE;

export function buildCyberpunkDenEnvironment(scene) {
  const envGroup = new THREE.Group();
  envGroup.name = 'env_cyberpunk_den';

  const config = {
    bg: 0x090514,
    fogColor: 0x170b28,
    fogDensity: 0.018,
    cameraPos: [34, 28, 34],
    cameraTarget: [0, 4, 0]
  };

  // Atmospheric Deep Purple & Dual-Tone Neon Lighting
  const ambient = new THREE.AmbientLight(0x2e1065, 0.65);
  envGroup.add(ambient);

  const hemi = new THREE.HemisphereLight(0x06b6d4, 0xec4899, 0.45);
  envGroup.add(hemi);

  // Intense Neon Floor Flood Lights
  const magentaFlood = new THREE.PointLight(0xec4899, 3.8, 30);
  magentaFlood.position.set(0, 4.0, 0);
  magentaFlood.castShadow = true;
  envGroup.add(magentaFlood);

  const cyanServerLight = new THREE.PointLight(0x06b6d4, 3.0, 24);
  cyanServerLight.position.set(-8, 3.5, -8);
  envGroup.add(cyanServerLight);

  const amberDoorwayLight = new THREE.PointLight(0xf59e0b, 2.6, 20);
  amberDoorwayLight.position.set(8, 8.5, -10);
  envGroup.add(amberDoorwayLight);

  // Procedural Materials
  const cyberPanelTex = createCyberPanelTexture();
  const cyberWallMat = new THREE.MeshStandardMaterial({ map: cyberPanelTex, roughness: 0.4, metalness: 0.8 });
  const asphaltFloorMat = new THREE.MeshStandardMaterial({ color: 0x09090b, roughness: 0.18, metalness: 0.85 }); // Wet reflective asphalt
  const darkMetalMat = new THREE.MeshStandardMaterial({ color: 0x18181b, roughness: 0.4, metalness: 0.85 });
  const catwalkMat = new THREE.MeshStandardMaterial({ color: 0x27272a, roughness: 0.5, metalness: 0.9 });

  const neonCyanMat = new THREE.MeshBasicMaterial({ color: 0x06b6d4 });
  const neonPinkMat = new THREE.MeshBasicMaterial({ color: 0xf43f5e });
  const crtGreenMat = new THREE.MeshStandardMaterial({ color: 0x22c55e, emissive: 0x22c55e, emissiveIntensity: 0.95 });
  const crtCyanMat = new THREE.MeshStandardMaterial({ color: 0x38bdf8, emissive: 0x38bdf8, emissiveIntensity: 0.95 });

  // --- 1. LOWER TERMINAL FLOOR (Y = 0.0) ---
  const floorMesh = new THREE.Mesh(new THREE.BoxGeometry(30, 0.8, 28), asphaltFloorMat);
  floorMesh.position.set(0, -0.4, 0);
  floorMesh.receiveShadow = true;
  envGroup.add(floorMesh);

  // Cutaway Back Wall with Cyber Panel Louvers
  const backWall = new THREE.Mesh(new THREE.BoxGeometry(26, 14, 0.6), cyberWallMat);
  backWall.position.set(0, 7, -13);
  backWall.receiveShadow = true;
  envGroup.add(backWall);

  const leftWall = new THREE.Mesh(new THREE.BoxGeometry(0.6, 14, 20), cyberWallMat);
  leftWall.position.set(-13, 7, -3);
  leftWall.receiveShadow = true;
  envGroup.add(leftWall);

  // --- 2. UPPER INDUSTRIAL CATWALK (Y = 5.5) ---
  const catwalk = new THREE.Mesh(new THREE.BoxGeometry(16, 0.45, 14), catwalkMat);
  catwalk.position.set(4, 5.5 - 0.22, -5);
  catwalk.receiveShadow = true;
  catwalk.castShadow = true;
  envGroup.add(catwalk);

  // Neon Pink Catwalk Railing
  const railMat = new THREE.MeshStandardMaterial({ color: 0xf43f5e, metalness: 0.8 });
  const rail = new THREE.Mesh(new THREE.BoxGeometry(16, 1.1, 0.1), railMat);
  rail.position.set(4, 6.05, 2.0);
  envGroup.add(rail);

  // Support Columns
  const colGeo = new THREE.BoxGeometry(0.55, 5.5, 0.55);
  [[-3.8, 1.8], [11.8, 1.8], [11.8, -11.8]].forEach(([cx, cz]) => {
    const col = new THREE.Mesh(colGeo, darkMetalMat);
    col.position.set(cx, 2.75, cz);
    col.castShadow = true;
    envGroup.add(col);
  });

  // --- 3. RECESSED SERVER CABINETS WITH LED ACTIVITY BARS ---
  function createServerCabinet() {
    const cab = new THREE.Group();
    const frame = new THREE.Mesh(new THREE.BoxGeometry(1.8, 4.4, 1.5), darkMetalMat);
    frame.position.y = 2.2;
    frame.castShadow = true;
    cab.add(frame);

    // Blinking status LED rows on front
    for (let r = 0; r < 9; r++) {
      const isCyan = (r % 2 === 0);
      const ledRow = new THREE.Mesh(
        new THREE.BoxGeometry(1.4, 0.12, 0.05),
        isCyan ? neonCyanMat : neonPinkMat
      );
      ledRow.position.set(0, 0.6 + r * 0.44, 0.77);
      cab.add(ledRow);
    }
    return cab;
  }

  // Row of 4 Server Racks along left wall
  for (let s = 0; s < 4; s++) {
    const cab = createServerCabinet();
    cab.position.set(-11.5, 0, -10 + s * 2.5);
    envGroup.add(cab);
  }

  // --- 4. CRT MONITOR BANKS & WORKBENCH ---
  function createCRTMonitor(colorMat) {
    const mon = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.8, 0.85), darkMetalMat);
    body.position.y = 0.4;
    mon.add(body);

    const screen = new THREE.Mesh(new THREE.PlaneGeometry(0.85, 0.65), colorMat);
    screen.position.set(0, 0.4, 0.44);
    mon.add(screen);
    return mon;
  }

  // Lower Diagnostic Console Bench
  const bench = new THREE.Mesh(new THREE.BoxGeometry(5.2, 0.16, 2.4), darkMetalMat);
  bench.position.set(1.5, 1.1, -9.5);
  bench.castShadow = true;
  envGroup.add(bench);

  // 3 Angled Glowing CRT Terminals
  const mon1 = createCRTMonitor(crtGreenMat);
  mon1.position.set(-0.3, 1.2, -9.5);
  mon1.rotation.y = 0.25;
  envGroup.add(mon1);

  const mon2 = createCRTMonitor(crtCyanMat);
  mon2.position.set(1.5, 1.2, -9.7);
  envGroup.add(mon2);

  const mon3 = createCRTMonitor(crtGreenMat);
  mon3.position.set(3.3, 1.2, -9.5);
  mon3.rotation.y = -0.25;
  envGroup.add(mon3);

  // Upper Catwalk Hacker Desk
  const upDesk = new THREE.Mesh(new THREE.BoxGeometry(3.8, 0.14, 1.8), darkMetalMat);
  upDesk.position.set(7, 5.5 + 1.1, -9);
  envGroup.add(upDesk);

  const upMon = createCRTMonitor(crtCyanMat);
  upMon.position.set(7, 5.5 + 1.2, -9);
  envGroup.add(upMon);

  // --- 5. PHYSICAL DROOPING CONDUIT & CABLES (CatmullRomCurve3) ---
  const cable1 = createDroopingCable([
    [-11, 4.0, -8],
    [-6, 3.2, -6],
    [0, 3.8, -8],
    [6, 5.5, -9]
  ], 0.045, 0x06b6d4);
  envGroup.add(cable1);

  const cable2 = createDroopingCable([
    [-11, 4.5, -4],
    [-4, 3.4, -4],
    [2, 3.1, -6],
    [7, 5.5, -8]
  ], 0.05, 0xec4899);
  envGroup.add(cable2);

  const cable3 = createDroopingCable([
    [-12, 10, 0],
    [-6, 8.5, -4],
    [4, 9.0, -8],
    [10, 11, -10]
  ], 0.06, 0x1e293b);
  envGroup.add(cable3);

  // Vertical Neon Light Pylons
  const neonBar1 = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 8.0, 6), neonPinkMat);
  neonBar1.position.set(-12.5, 4.0, 5);
  envGroup.add(neonBar1);

  const neonBar2 = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 8.0, 6), neonCyanMat);
  neonBar2.position.set(11.5, 4.0, 2);
  envGroup.add(neonBar2);

  // Glowing Amber Doorway on Upper Level
  const door = new THREE.Mesh(new THREE.BoxGeometry(2.4, 4.2, 0.1), new THREE.MeshBasicMaterial({ color: 0xf59e0b }));
  door.position.set(8, 7.6, -12.9);
  envGroup.add(door);

  // Agent Placements for Cyberpunk Den
  const agentSlots = {
    'coord-alpha': { pos: [7, 5.5, -7], rotY: Math.PI },          // Upper mezzanine hacker terminal
    'worker-beta': { pos: [1.5, 0, -7.5], rotY: 0 },              // Diagnostic workbench console
    'worker-gamma': { pos: [-8, 0, -4], rotY: Math.PI / 2 },       // Inspecting server rack bays
    'ally-delta': { pos: [-1, 0, 4], rotY: -Math.PI / 3 }          // Lower terminal floor
  };

  // Kanban Board Placement
  const kanbanConfig = {
    pos: [1.5, 0, 2],
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
