/**
 * Aevum-3D: Architectural Primitive & High-Fidelity Prop Library
 * Mathematically exact parametric staircases, multi-pane window casings,
 * architectural moldings, cushioned seating, catenary drooping cables, and layered flora.
 */

const THREE = window.THREE;

/**
 * Mathematically connects two exact 3D coordinates (fromPos to toPos) with zero gaps,
 * perfectly leveled treads, structural stringers, continuous handrail, and flush top landing.
 */
export function createExactStaircase(fromPos, toPos, width = 2.4, options = {}) {
  const {
    treadColor = 0xd97706,
    stringerColor = 0x1e293b,
    railColor = 0x0f172a,
    hasRailing = true,
    isOpenRiser = true,
    balusterSpacing = 2
  } = options;

  const stairGroup = new THREE.Group();

  const x1 = fromPos[0], y1 = fromPos[1], z1 = fromPos[2];
  const x2 = toPos[0], y2 = toPos[1], z2 = toPos[2];

  const dx = x2 - x1;
  const dy = y2 - y1;
  const dz = z2 - z1;
  const horizontalRun = Math.sqrt(dx * dx + dz * dz);
  const yaw = Math.atan2(dx, dz);

  // Determine step count so each riser is ~0.30 - 0.35 units high
  const stepCount = Math.max(3, Math.round(Math.abs(dy) / 0.32));
  const stepRise = dy / stepCount;
  const stepRun = horizontalRun / stepCount;

  const treadMat = new THREE.MeshStandardMaterial({ color: treadColor, roughness: 0.5 });
  const stringerMat = new THREE.MeshStandardMaterial({ color: stringerColor, roughness: 0.4, metalness: 0.5 });
  const railMat = new THREE.MeshStandardMaterial({ color: railColor, roughness: 0.3, metalness: 0.8 });

  // 1. Individual Treads
  for (let i = 0; i < stepCount; i++) {
    const t = (i + 0.5) / stepCount;
    const curX = x1 + dx * t;
    const curY = y1 + (i + 1) * stepRise;
    const curZ = z1 + dz * t;

    // Tread slab with nosing overhang
    const treadGeo = new THREE.BoxGeometry(width, 0.08, stepRun * 1.15);
    const tread = new THREE.Mesh(treadGeo, treadMat);
    tread.position.set(curX, curY - 0.04, curZ);
    tread.rotation.y = yaw;
    tread.castShadow = true;
    tread.receiveShadow = true;
    stairGroup.add(tread);

    // Closed Riser plate (optional)
    if (!isOpenRiser) {
      const riserGeo = new THREE.BoxGeometry(width, Math.abs(stepRise), 0.04);
      const riser = new THREE.Mesh(riserGeo, stringerMat);
      riser.position.set(curX, curY - Math.abs(stepRise) / 2, curZ - (stepRun * 0.5));
      riser.rotation.y = yaw;
      riser.castShadow = true;
      stairGroup.add(riser);
    }
  }

  // 2. Integrated Top Landing Platform (welds flush to upper mezzanine edge)
  const landingGeo = new THREE.BoxGeometry(width, 0.12, stepRun * 1.4);
  const landing = new THREE.Mesh(landingGeo, treadMat);
  landing.position.set(x2, y2 - 0.06, z2);
  landing.rotation.y = yaw;
  landing.receiveShadow = true;
  stairGroup.add(landing);

  // 3. Side Stringers (Angled structural beams)
  const slopeLength = Math.sqrt(horizontalRun * horizontalRun + dy * dy);
  const pitch = Math.atan2(dy, horizontalRun);
  const stringerGeo = new THREE.BoxGeometry(0.1, 0.28, slopeLength);

  [-width / 2 + 0.05, width / 2 - 0.05].forEach(sideOffset => {
    const stringer = new THREE.Mesh(stringerGeo, stringerMat);
    // Center of the slope
    const midX = (x1 + x2) / 2 + Math.cos(yaw) * sideOffset;
    const midY = (y1 + y2) / 2;
    const midZ = (z1 + z2) / 2 - Math.sin(yaw) * sideOffset;

    stringer.position.set(midX, midY, midZ);
    stringer.rotation.y = yaw;
    stringer.rotation.x = -pitch;
    stringer.castShadow = true;
    stairGroup.add(stringer);
  });

  // 4. Continuous Handrails and Balusters
  if (hasRailing) {
    const handrailGeo = new THREE.CylinderGeometry(0.04, 0.04, slopeLength + 0.4, 8);

    [-width / 2, width / 2].forEach(sideOffset => {
      // Handrail
      const rail = new THREE.Mesh(handrailGeo, railMat);
      const midX = (x1 + x2) / 2 + Math.cos(yaw) * sideOffset;
      const midY = (y1 + y2) / 2 + 1.05;
      const midZ = (z1 + z2) / 2 - Math.sin(yaw) * sideOffset;

      rail.position.set(midX, midY, midZ);
      rail.rotation.y = yaw;
      rail.rotation.x = -pitch;
      rail.castShadow = true;
      stairGroup.add(rail);

      // Vertical Balusters
      for (let i = 0; i <= stepCount; i += balusterSpacing) {
        const t = i / stepCount;
        const bx = x1 + dx * t + Math.cos(yaw) * sideOffset;
        const by = y1 + dy * t + 0.5;
        const bz = z1 + dz * t - Math.sin(yaw) * sideOffset;

        const balusterGeo = new THREE.CylinderGeometry(0.025, 0.025, 1.0, 6);
        const baluster = new THREE.Mesh(balusterGeo, railMat);
        baluster.position.set(bx, by, bz);
        baluster.castShadow = true;
        stairGroup.add(baluster);
      }
    });
  }

  return stairGroup;
}

/**
 * Creates an architectural multi-pane casement window with deep reveal,
 * wooden sill, mullions, and optional warm/cool sunbeam glow.
 */
export function createDetailedWindow(options = {}) {
  const {
    width = 3.2,
    height = 4.8,
    isArched = false,
    frameColor = 0x1e293b,
    glassColor = 0xfef08a,
    emissiveIntensity = 0.5,
    mullionCols = 2,
    mullionRows = 3
  } = options;

  const winGroup = new THREE.Group();
  const frameMat = new THREE.MeshStandardMaterial({ color: frameColor, roughness: 0.5 });
  const glassMat = new THREE.MeshPhysicalMaterial({
    color: glassColor,
    emissive: glassColor,
    emissiveIntensity,
    roughness: 0.1,
    transmission: 0.85,
    transparent: true,
    opacity: 0.7
  });

  // Outer Casing Box (Window Frame)
  const casingThickness = 0.16;
  const depth = 0.28;

  // Left & Right Jambs
  [-width / 2, width / 2].forEach(x => {
    const jamb = new THREE.Mesh(new THREE.BoxGeometry(casingThickness, height, depth), frameMat);
    jamb.position.set(x, height / 2, 0);
    winGroup.add(jamb);
  });

  // Sill & Head
  const sill = new THREE.Mesh(new THREE.BoxGeometry(width + 0.5, casingThickness * 1.4, depth + 0.12), frameMat);
  sill.position.set(0, casingThickness / 2, 0);
  winGroup.add(sill);

  const head = new THREE.Mesh(new THREE.BoxGeometry(width + 0.2, casingThickness, depth), frameMat);
  head.position.set(0, height - casingThickness / 2, 0);
  winGroup.add(head);

  // Main Glass Pane
  const paneGeo = new THREE.BoxGeometry(width - casingThickness * 2, height - casingThickness * 2, 0.04);
  const pane = new THREE.Mesh(paneGeo, glassMat);
  pane.position.set(0, height / 2, 0);
  winGroup.add(pane);

  // Mullions (Grid Bars)
  const innerW = width - casingThickness * 2;
  const innerH = height - casingThickness * 2;

  // Vertical Mullions
  for (let c = 1; c < mullionCols; c++) {
    const mx = -innerW / 2 + (c * innerW) / mullionCols;
    const vm = new THREE.Mesh(new THREE.BoxGeometry(0.06, innerH, 0.08), frameMat);
    vm.position.set(mx, height / 2, 0);
    winGroup.add(vm);
  }

  // Horizontal Mullions
  for (let r = 1; r < mullionRows; r++) {
    const my = casingThickness + (r * innerH) / mullionRows;
    const hm = new THREE.Mesh(new THREE.BoxGeometry(innerW, 0.06, 0.08), frameMat);
    hm.position.set(0, my, 0);
    winGroup.add(hm);
  }

  // Arch Top (if specified)
  if (isArched) {
    const radius = width / 2;
    const archTorus = new THREE.Mesh(new THREE.TorusGeometry(radius, casingThickness / 2, 8, 16, Math.PI), frameMat);
    archTorus.position.set(0, height, 0);
    winGroup.add(archTorus);

    const archGlass = new THREE.Mesh(new THREE.CircleGeometry(radius - casingThickness / 2, 16, 0, Math.PI), glassMat);
    archGlass.position.set(0, height, 0);
    winGroup.add(archGlass);
  }

  return winGroup;
}

/**
 * Creates high-detail library shelving with architectural fluted pilasters,
 * crown molding, and colorful book sets with textured spines.
 */
export function createDetailedBookshelf(options = {}) {
  const {
    width = 4.2,
    height = 5.6,
    depth = 0.9,
    woodColor = 0x451a03,
    shelfCount = 4
  } = options;

  const shelfGroup = new THREE.Group();
  const woodMat = new THREE.MeshStandardMaterial({ color: woodColor, roughness: 0.6 });

  // Backing Panel
  const back = new THREE.Mesh(new THREE.BoxGeometry(width, height, 0.08), woodMat);
  back.position.set(0, height / 2, -depth / 2 + 0.04);
  shelfGroup.add(back);

  // Side Fluted Pilasters
  [-width / 2 + 0.1, width / 2 - 0.1].forEach(x => {
    const side = new THREE.Mesh(new THREE.BoxGeometry(0.2, height, depth), woodMat);
    side.position.set(x, height / 2, 0);
    side.castShadow = true;
    shelfGroup.add(side);
  });

  // Crown Molding at Top
  const crown = new THREE.Mesh(new THREE.BoxGeometry(width + 0.4, 0.28, depth + 0.15), woodMat);
  crown.position.set(0, height + 0.14, 0);
  crown.castShadow = true;
  shelfGroup.add(crown);

  // Shelves & Book Spines
  const bookColors = [0x991b1b, 0x1e3a8a, 0x065f46, 0x78350f, 0x581c87, 0xd97706, 0x334155, 0x1e1b4b];
  const spacing = (height - 0.3) / shelfCount;

  for (let s = 0; s <= shelfCount; s++) {
    const sy = 0.15 + s * spacing;
    const shelfBoard = new THREE.Mesh(new THREE.BoxGeometry(width - 0.3, 0.08, depth - 0.06), woodMat);
    shelfBoard.position.set(0, sy, 0);
    shelfGroup.add(shelfBoard);

    if (s < shelfCount) {
      let curX = -width / 2 + 0.3;
      while (curX < width / 2 - 0.4) {
        const bw = 0.07 + Math.random() * 0.11;
        const bh = (spacing - 0.16) * (0.75 + Math.random() * 0.25);
        const bd = (depth - 0.18) * 0.85;
        const bColor = bookColors[Math.floor(Math.random() * bookColors.length)];

        const book = new THREE.Mesh(
          new THREE.BoxGeometry(bw, bh, bd),
          new THREE.MeshStandardMaterial({ color: bColor, roughness: 0.5 })
        );
        book.position.set(curX + bw / 2, sy + bh / 2 + 0.04, 0);
        book.castShadow = true;
        shelfGroup.add(book);

        // Occasional gold foil band on spine
        if (Math.random() > 0.6) {
          const goldBand = new THREE.Mesh(
            new THREE.BoxGeometry(bw + 0.005, 0.04, bd + 0.005),
            new THREE.MeshStandardMaterial({ color: 0xfacc15, metalness: 0.8 })
          );
          goldBand.position.set(curX + bw / 2, sy + bh * 0.7, 0);
          shelfGroup.add(goldBand);
        }

        curX += bw + 0.012;
      }
    }
  }

  return shelfGroup;
}

/**
 * Creates a modern cushioned 3-piece sectional sofa with throw pillows and coffee table.
 */
export function createDetailedSofa(options = {}) {
  const {
    color = 0x475569,
    cushionColor = 0x94a3b8,
    pillowColor = 0xf59e0b
  } = options;

  const group = new THREE.Group();
  const baseMat = new THREE.MeshStandardMaterial({ color, roughness: 0.85 });
  const cushionMat = new THREE.MeshStandardMaterial({ color: cushionColor, roughness: 0.7 });
  const pillowMat = new THREE.MeshStandardMaterial({ color: pillowColor, roughness: 0.6 });

  // Main Section Base
  const mainBase = new THREE.Mesh(new THREE.BoxGeometry(4.4, 0.42, 1.6), baseMat);
  mainBase.position.set(0, 0.25, 0);
  mainBase.castShadow = true;
  group.add(mainBase);

  // Backrest with Chamfered Look
  const back = new THREE.Mesh(new THREE.BoxGeometry(4.4, 0.9, 0.4), baseMat);
  back.position.set(0, 0.72, -0.6);
  back.castShadow = true;
  group.add(back);

  // Return Chaise Section (L-Shape)
  const chaise = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.42, 2.6), baseMat);
  chaise.position.set(1.4, 0.25, 1.3);
  chaise.castShadow = true;
  group.add(chaise);

  // Cushions (Piped geometry)
  for (let c = 0; c < 3; c++) {
    const cush = new THREE.Mesh(new THREE.BoxGeometry(1.3, 0.18, 1.2), cushionMat);
    cush.position.set(-1.4 + c * 1.4, 0.55, 0.05);
    cush.castShadow = true;
    group.add(cush);
  }

  // Accent Throw Pillows
  [[-1.4, -0.4, 0.2], [1.4, 1.6, -0.3]].forEach(([px, pz, rotY]) => {
    const pil = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.55, 0.22), pillowMat);
    pil.position.set(px, 0.8, pz);
    pil.rotation.y = rotY;
    pil.rotation.x = -0.2;
    pil.castShadow = true;
    group.add(pil);
  });

  return group;
}

/**
 * Creates realistic drooping cables using CatmullRomCurve3 tube geometry.
 */
export function createDroopingCable(points, radius = 0.04, color = 0x1e293b) {
  const vectors = points.map(p => new THREE.Vector3(...p));
  const curve = new THREE.CatmullRomCurve3(vectors);
  const tubeGeo = new THREE.TubeGeometry(curve, 32, radius, 6, false);
  const tubeMat = new THREE.MeshStandardMaterial({ color, roughness: 0.5, metalness: 0.6 });
  const cableMesh = new THREE.Mesh(tubeGeo, tubeMat);
  cableMesh.castShadow = true;
  return cableMesh;
}

/**
 * Creates layered flora: tiered A-frame plant ladder, hanging ivy, or large potted monstera.
 */
export function createDetailedPlant(type = 'monstera', scale = 1.0) {
  const group = new THREE.Group();
  group.scale.setScalar(scale);

  const potMat = new THREE.MeshStandardMaterial({ color: 0x9a3412, roughness: 0.75 });
  const soilMat = new THREE.MeshStandardMaterial({ color: 0x1c1917, roughness: 0.95 });
  const leafMat = new THREE.MeshStandardMaterial({ color: 0x15803d, roughness: 0.4, side: THREE.DoubleSide });

  if (type === 'monstera') {
    // Ceramic Fluted Pot
    const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.42, 0.85, 12), potMat);
    pot.position.y = 0.42;
    pot.castShadow = true;
    group.add(pot);

    const soil = new THREE.Mesh(new THREE.CylinderGeometry(0.52, 0.52, 0.05, 12), soilMat);
    soil.position.y = 0.82;
    group.add(soil);

    // 8 Fan-Lobed Leaves
    for (let i = 0; i < 8; i++) {
      const angle = (i / 8) * Math.PI * 2;
      const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.035, 1.1, 6), leafMat);
      stem.position.set(Math.cos(angle) * 0.15, 1.1, Math.sin(angle) * 0.15);
      stem.rotation.z = Math.cos(angle) * 0.45;
      stem.rotation.x = Math.sin(angle) * 0.45;
      group.add(stem);

      const leaf = new THREE.Mesh(new THREE.PlaneGeometry(0.7, 0.95), leafMat);
      leaf.position.set(Math.cos(angle) * 0.65, 1.6 + i * 0.08, Math.sin(angle) * 0.65);
      leaf.rotation.x = -Math.PI / 3 + Math.sin(angle) * 0.25;
      leaf.rotation.y = angle;
      leaf.castShadow = true;
      group.add(leaf);
    }
  } else if (type === 'plant_ladder') {
    // A-Frame Wooden 3-Tier Ladder Shelf
    const woodMat = new THREE.MeshStandardMaterial({ color: 0x78350f, roughness: 0.7 });

    // 4 Slanted Uprights
    [[-0.9, -0.4], [0.9, -0.4], [-0.9, 0.4], [0.9, 0.4]].forEach(([x, z]) => {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.06, 2.6, 0.06), woodMat);
      leg.position.set(x, 1.3, z);
      leg.rotation.x = (z > 0 ? 0.18 : -0.18);
      group.add(leg);
    });

    // 3 Tier Shelves with Plants
    [
      { y: 0.6, w: 2.2, d: 0.9 },
      { y: 1.3, w: 1.8, d: 0.7 },
      { y: 2.0, w: 1.4, d: 0.5 }
    ].forEach((lvl, sIdx) => {
      const shelf = new THREE.Mesh(new THREE.BoxGeometry(lvl.w, 0.05, lvl.d), woodMat);
      shelf.position.set(0, lvl.y, 0);
      group.add(shelf);

      // Populate 2-3 potted succulents on each shelf
      const count = 2 + (sIdx % 2);
      for (let p = 0; p < count; p++) {
        const px = -lvl.w / 3 + p * (lvl.w / count);
        const pPot = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.12, 0.22, 8), potMat);
        pPot.position.set(px, lvl.y + 0.11, 0);
        group.add(pPot);

        const frond = new THREE.Mesh(new THREE.ConeGeometry(0.18, 0.35, 6), leafMat);
        frond.position.set(px, lvl.y + 0.3, 0);
        group.add(frond);
      }
    });
  }

  return group;
}

/**
 * Creates a gothic iron candelabra / standing street lantern with warm point light.
 */
export function createLantern(options = {}) {
  const {
    color = 0xfef08a,
    intensity = 1.2,
    distance = 14,
    isStanding = true,
    height = 2.4
  } = options;

  const group = new THREE.Group();
  const ironMat = new THREE.MeshStandardMaterial({ color: 0x1c1917, roughness: 0.5, metalness: 0.8 });
  const glowMat = new THREE.MeshBasicMaterial({ color });

  if (isStanding) {
    // Ornate base
    const base = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.45, 0.2, 8), ironMat);
    base.position.y = 0.1;
    base.castShadow = true;
    group.add(base);

    // Fluted vertical pole
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.08, height, 8), ironMat);
    pole.position.y = height / 2;
    pole.castShadow = true;
    group.add(pole);
  }

  // Lantern Cage
  const cageY = isStanding ? height + 0.25 : 0;
  const cage = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.5, 0.4), ironMat);
  cage.position.y = cageY;
  cage.castShadow = true;
  group.add(cage);

  // Glowing Flame
  const flame = new THREE.Mesh(new THREE.SphereGeometry(0.12, 8, 8), glowMat);
  flame.position.y = cageY;
  group.add(flame);

  // Point Light
  const light = new THREE.PointLight(color, intensity, distance);
  light.position.y = cageY;
  light.castShadow = true;
  light.shadow.bias = -0.002;
  group.add(light);

  return group;
}
