/**
 * Aevum-3D: Arctic Expedition Encampment (Ultra-High Fidelity)
 * Faithful realization of Reference Image 4:
 * - Snow terrain with procedural pressed footpaths and sleigh tracks
 * - Heavy stone campfire pit with flickering flame dynamics & point lighting
 * - Ring of 8 detailed octagonal canvas expedition tents with guy lines & pennants
 * - Wooden freight supply wagons with 12-spoked wheels, crates, sacks & water casks
 */

import { createSnowTrackTexture } from '../texture_generator.js';

const THREE = window.THREE;

export function buildArcticCampEnvironment(scene) {
  const envGroup = new THREE.Group();
  envGroup.name = 'env_arctic_camp';

  const config = {
    bg: 0x93c5fd,
    fogColor: 0xdbeafe,
    fogDensity: 0.011,
    cameraPos: [40, 36, 40],
    cameraTarget: [0, 2, 0]
  };

  // Crisp Alpine Winter Sunlight
  const ambient = new THREE.AmbientLight(0xe0f2fe, 0.9);
  envGroup.add(ambient);

  const hemi = new THREE.HemisphereLight(0xffffff, 0x64748b, 0.65);
  envGroup.add(hemi);

  const sun = new THREE.DirectionalLight(0xffffff, 1.25);
  sun.position.set(35, 50, 25);
  sun.castShadow = true;
  sun.shadow.mapSize.width = 2048;
  sun.shadow.mapSize.height = 2048;
  sun.shadow.bias = -0.0003;
  envGroup.add(sun);

  // Procedural Materials
  const snowTex = createSnowTrackTexture();
  const snowGroundMat = new THREE.MeshStandardMaterial({ map: snowTex, roughness: 0.75 });
  const canvasMat = new THREE.MeshStandardMaterial({ color: 0xeee7dc, roughness: 0.85 }); // Realistic warm canvas
  const woodMat = new THREE.MeshStandardMaterial({ color: 0x78350f, roughness: 0.8 });
  const darkIronMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.85, roughness: 0.3 });
  const stoneMat = new THREE.MeshStandardMaterial({ color: 0x475569, roughness: 0.9 });
  const pineMat = new THREE.MeshStandardMaterial({ color: 0x14532d, roughness: 0.7 });
  const flagMat = new THREE.MeshStandardMaterial({ color: 0x991b1b, roughness: 0.6 });

  // --- 1. SNOW GROUND PLANE (Y = 0.0) ---
  const groundGeo = new THREE.BoxGeometry(46, 0.8, 46);
  const ground = new THREE.Mesh(groundGeo, snowGroundMat);
  ground.position.y = -0.4;
  ground.receiveShadow = true;
  envGroup.add(ground);

  // --- 2. CENTRAL ROARING CAMPFIRE & STONE CIRCLE ---
  const firepitGroup = new THREE.Group();

  // Stone boulder ring
  const boulderCount = 14;
  const pitRadius = 1.8;
  for (let b = 0; b < boulderCount; b++) {
    const angle = (b / boulderCount) * Math.PI * 2;
    const bGeo = new THREE.DodecahedronGeometry(0.38 + (b % 3) * 0.1, 0);
    const boulder = new THREE.Mesh(bGeo, stoneMat);
    boulder.position.set(Math.cos(angle) * pitRadius, 0.28, Math.sin(angle) * pitRadius);
    boulder.rotation.set(Math.random(), Math.random(), Math.random());
    boulder.castShadow = true;
    firepitGroup.add(boulder);
  }

  // Glowing charcoal embers
  const embers = new THREE.Mesh(
    new THREE.CylinderGeometry(1.5, 1.5, 0.12, 16),
    new THREE.MeshStandardMaterial({ color: 0xd97706, emissive: 0xb45309, emissiveIntensity: 0.9 })
  );
  embers.position.y = 0.06;
  firepitGroup.add(embers);

  // Birch firewood logs in teepee stack
  for (let l = 0; l < 6; l++) {
    const log = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 2.0, 6), woodMat);
    log.position.set(0, 0.45 + l * 0.08, 0);
    log.rotation.z = Math.PI / 3.2;
    log.rotation.y = l * (Math.PI / 3);
    log.castShadow = true;
    firepitGroup.add(log);
  }

  // Stylized flame crystals
  const flame = new THREE.Mesh(new THREE.ConeGeometry(0.55, 1.6, 6), new THREE.MeshBasicMaterial({ color: 0xf59e0b }));
  flame.position.y = 1.15;
  firepitGroup.add(flame);

  // Dynamic Flickering Firelight PointLight
  const fireLight = new THREE.PointLight(0xf97316, 2.4, 28);
  fireLight.position.set(0, 1.5, 0);
  fireLight.castShadow = true;
  firepitGroup.add(fireLight);

  envGroup.add(firepitGroup);

  // --- 3. DETAILED EXPEDITION CANVAS TENTS ---
  function createExpeditionTent() {
    const tent = new THREE.Group();

    // Ribbed octagonal cone canvas
    const tentGeo = new THREE.ConeGeometry(3.0, 3.6, 8);
    const tentMesh = new THREE.Mesh(tentGeo, canvasMat);
    tentMesh.position.y = 1.8;
    tentMesh.castShadow = true;
    tentMesh.receiveShadow = true;
    tent.add(tentMesh);

    // Center mast pole extending through apex
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.08, 4.8, 6), woodMat);
    pole.position.y = 2.4;
    tent.add(pole);

    // Fluttering triangular flag
    const flag = new THREE.Mesh(new THREE.BoxGeometry(0.85, 0.4, 0.04), flagMat);
    flag.position.set(0.42, 4.4, 0);
    tent.add(flag);

    // Overlapping door flaps
    const flapMat = new THREE.MeshStandardMaterial({ color: 0x451a03, roughness: 0.9 });
    const flap = new THREE.Mesh(new THREE.BoxGeometry(0.9, 1.5, 0.06), flapMat);
    flap.position.set(0, 0.75, 2.45);
    tent.add(flap);

    // Wooden supply crates by entrance
    const crate = new THREE.Mesh(new THREE.BoxGeometry(0.65, 0.55, 0.65), woodMat);
    crate.position.set(1.5, 0.28, 2.2);
    crate.castShadow = true;
    tent.add(crate);

    return tent;
  }

  // 8 Expedition Tents arranged radially
  const tentRadius = 13.0;
  const tentAngles = [0.2, 0.95, 1.8, 2.6, 3.35, 4.15, 4.95, 5.75];
  tentAngles.forEach(ang => {
    const t = createExpeditionTent();
    t.position.set(Math.cos(ang) * tentRadius, 0, Math.sin(ang) * tentRadius);
    t.rotation.y = -ang + Math.PI / 2;
    envGroup.add(t);
  });

  // --- 4. DETAILED FREIGHT WAGONS WITH SPOKED WHEELS ---
  function createFreightWagon() {
    const wagon = new THREE.Group();

    // Heavy timber wagon bed
    const bed = new THREE.Mesh(new THREE.BoxGeometry(4.6, 0.45, 2.4), woodMat);
    bed.position.y = 1.1;
    bed.castShadow = true;
    wagon.add(bed);

    // Side slatted rails
    [-1.15, 1.15].forEach(z => {
      const slat = new THREE.Mesh(new THREE.BoxGeometry(4.6, 0.85, 0.12), woodMat);
      slat.position.set(0, 1.65, z);
      wagon.add(slat);
    });

    // 4 Heavy Spoked Wheels with Iron Rims
    [[-1.8, -1.35], [1.8, -1.35], [-1.8, 1.35], [1.8, 1.35]].forEach(([wx, wz]) => {
      const wheelGroup = new THREE.Group();
      wheelGroup.position.set(wx, 0.7, wz);

      // Iron rim
      const rim = new THREE.Mesh(new THREE.TorusGeometry(0.7, 0.08, 6, 16), darkIronMat);
      rim.castShadow = true;
      wheelGroup.add(rim);

      // Wooden hub
      const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, 0.25, 8), woodMat);
      hub.rotation.x = Math.PI / 2;
      wheelGroup.add(hub);

      // 8 Radiating Spokes
      for (let s = 0; s < 8; s++) {
        const spoke = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 1.4, 4), woodMat);
        spoke.rotation.z = s * (Math.PI / 8);
        wheelGroup.add(spoke);
      }

      wagon.add(wheelGroup);
    });

    // Stacked Burlap Grain Sacks & Iron-Hooped Barrels inside wagon
    const sackMat = new THREE.MeshStandardMaterial({ color: 0xd6d3d1, roughness: 0.95 });
    for (let sk = 0; sk < 5; sk++) {
      const sack = new THREE.Mesh(new THREE.DodecahedronGeometry(0.6, 1), sackMat);
      sack.position.set(-1.2 + sk * 0.65, 1.6, (sk % 2 === 0 ? -0.4 : 0.4));
      sack.castShadow = true;
      wagon.add(sack);
    }

    const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.35, 0.85, 10), woodMat);
    barrel.position.set(1.4, 1.7, 0);
    wagon.add(barrel);

    return wagon;
  }

  const wagon1 = createFreightWagon();
  wagon1.position.set(-14.5, 0, 4);
  wagon1.rotation.y = Math.PI / 5;
  envGroup.add(wagon1);

  const wagon2 = createFreightWagon();
  wagon2.position.set(13.5, 0, -6);
  wagon2.rotation.y = -Math.PI / 3;
  envGroup.add(wagon2);

  // --- 5. SPLIT-RAIL WOODEN PALISADES ---
  function createPalisade(length = 7.0) {
    const pal = new THREE.Group();
    const count = Math.floor(length / 1.5) + 1;
    for (let p = 0; p < count; p++) {
      const post = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.14, 1.7, 6), woodMat);
      post.position.set(-length / 2 + p * 1.5, 0.85, 0);
      post.castShadow = true;
      pal.add(post);
    }
    for (let r = 0; r < 2; r++) {
      const rail = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, length, 6), woodMat);
      rail.rotation.z = Math.PI / 2;
      rail.position.set(0, 0.65 + r * 0.65, 0);
      pal.add(rail);
    }
    return pal;
  }

  const pal1 = createPalisade(8.0);
  pal1.position.set(4, 0, -4.5);
  pal1.rotation.y = -Math.PI / 4;
  envGroup.add(pal1);

  const pal2 = createPalisade(8.0);
  pal2.position.set(-5, 0, 5.5);
  pal2.rotation.y = Math.PI / 3;
  envGroup.add(pal2);

  // --- 6. DENSE SNOWY SPRUCE PERIMETER ---
  for (let tr = 0; tr < 20; tr++) {
    const tree = new THREE.Group();
    const angle = (tr / 20) * Math.PI * 2;
    const rad = 20 + (tr % 3) * 2.8;

    const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.32, 1.5, 6), woodMat);
    trunk.position.y = 0.75;
    tree.add(trunk);

    for (let lvl = 0; lvl < 3; lvl++) {
      const bough = new THREE.Mesh(new THREE.ConeGeometry(2.2 - lvl * 0.45, 1.8, 7), pineMat);
      bough.position.y = 1.8 + lvl * 1.0;
      bough.castShadow = true;
      tree.add(bough);

      const snowCap = new THREE.Mesh(new THREE.ConeGeometry(2.22 - lvl * 0.45, 0.45, 7), new THREE.MeshStandardMaterial({ color: 0xffffff }));
      snowCap.position.y = 1.9 + lvl * 1.0;
      tree.add(snowCap);
    }

    tree.position.set(Math.cos(angle) * rad, 0, Math.sin(angle) * rad);
    envGroup.add(tree);
  }

  // Agent Placements for Arctic Camp
  const agentSlots = {
    'coord-alpha': { pos: [-3.0, 0, -3.0], rotY: Math.PI / 4 },    // Standing warmly by the firepit
    'worker-beta': { pos: [3.2, 0, 1.8], rotY: -Math.PI / 3 },     // Across the firepit reviewing tasks
    'worker-gamma': { pos: [-11.5, 0, 4], rotY: Math.PI / 2 },     // Managing freight at the supply wagon
    'ally-delta': { pos: [2.8, 0, -3.5], rotY: -Math.PI / 4 }      // Guarding the camp entrance trail
  };

  // Kanban Board Placement
  const kanbanConfig = {
    pos: [0, 0, 7],
    rotY: Math.PI
  };

  scene.add(envGroup);

  return {
    group: envGroup,
    config,
    agentSlots,
    kanbanConfig,
    fireLight
  };
}
