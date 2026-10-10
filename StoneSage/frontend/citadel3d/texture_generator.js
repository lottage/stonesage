/**
 * Aevum-3D: Procedural High-Resolution Texture Synthesis Engine
 * Generates authentic architectural textures directly in-memory via HTML5 Canvas.
 * Zero external asset downloads - Instant offline execution with rich material realism.
 */

const THREE = window.THREE;

function drawRoundRect(ctx, x, y, w, h, r) {
  if (typeof ctx.roundRect === 'function') {
    ctx.roundRect(x, y, w, h, r);
  } else {
    ctx.rect(x, y, w, h);
  }
}

function drawEllipse(ctx, x, y, rx, ry) {
  if (typeof ctx.ellipse === 'function') {
    ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  } else {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(rx, ry);
    ctx.arc(0, 0, 1, 0, Math.PI * 2);
    ctx.restore();
  }
}

/**
 * Creates a herringbone / parquet wood floor texture.
 */
export function createHerringboneWoodTexture(baseColor = '#78350f', plankW = 28, plankL = 96) {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = baseColor;
  ctx.fillRect(0, 0, 512, 512);

  const shades = ['#5c280b', '#6b2f0d', '#78350f', '#853b11', '#92400e', '#532309'];

  // Draw herringbone pattern
  const unit = plankL;
  for (let y = -unit; y < 512 + unit; y += plankW * 2) {
    for (let x = -unit; x < 512 + unit; x += unit) {
      // Horizontal plank
      const c1 = shades[Math.floor(Math.random() * shades.length)];
      ctx.fillStyle = c1;
      ctx.fillRect(x, y, plankL - 2, plankW - 2);

      // Vertical plank
      const c2 = shades[Math.floor(Math.random() * shades.length)];
      ctx.fillStyle = c2;
      ctx.fillRect(x + plankL - plankW, y + plankW, plankW - 2, plankL - 2);

      // Subtle wood grain lines
      ctx.strokeStyle = 'rgba(0,0,0,0.15)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x + 4, y + plankW / 2);
      ctx.lineTo(x + plankL - 6, y + plankW / 2);
      ctx.stroke();
    }
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(4, 4);
  texture.minFilter = THREE.LinearFilter;
  return texture;
}

/**
 * Creates medieval stone flagstone / cobblestone paving texture with mortar joints.
 */
export function createFlagstoneTexture(stoneColor = '#57534e', mortarColor = '#292524') {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext('2d');

  // Mortar base
  ctx.fillStyle = mortarColor;
  ctx.fillRect(0, 0, 512, 512);

  const stoneShades = ['#44403c', '#57534e', '#615c56', '#524d47', '#3f3b36', '#6b655f'];

  // Grid of irregular stone slabs
  const cols = 8;
  const rows = 8;
  const cw = 512 / cols;
  const ch = 512 / rows;

  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const offsetX = (r % 2 === 0 ? 0 : cw / 2);
      const x = c * cw + offsetX;
      const y = r * ch;

      ctx.fillStyle = stoneShades[(r * cols + c) % stoneShades.length];
      const pad = 4;
      ctx.beginPath();
      drawRoundRect(ctx, x + pad, y + pad, cw - pad * 2, ch - pad * 2, 4);
      ctx.fill();

      // Surface stippling / grain
      ctx.fillStyle = 'rgba(255,255,255,0.06)';
      ctx.fillRect(x + pad + 6, y + pad + 6, (cw - pad * 2) / 2, (ch - pad * 2) / 2);
    }
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(4, 4);
  texture.minFilter = THREE.LinearFilter;
  return texture;
}

/**
 * Creates architectural modern ceramic / concrete floor tile with crisp grout.
 */
export function createModernTileTexture(tileColor = '#e2e8f0', groutColor = '#94a3b8', size = 64) {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = groutColor;
  ctx.fillRect(0, 0, 512, 512);

  for (let y = 0; y < 512; y += size) {
    for (let x = 0; x < 512; x += size) {
      ctx.fillStyle = tileColor;
      ctx.fillRect(x + 2, y + 2, size - 4, size - 4);

      // Subtle gloss gradient
      const grad = ctx.createLinearGradient(x + 2, y + 2, x + size, y + size);
      grad.addColorStop(0, 'rgba(255,255,255,0.12)');
      grad.addColorStop(1, 'rgba(0,0,0,0.06)');
      ctx.fillStyle = grad;
      ctx.fillRect(x + 2, y + 2, size - 4, size - 4);
    }
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(3, 3);
  texture.minFilter = THREE.LinearFilter;
  return texture;
}

/**
 * Creates snow terrain texture with pressed footprints and sleigh ruts.
 */
export function createSnowTrackTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext('2d');

  // Crisp white snow base
  ctx.fillStyle = '#f8fafc';
  ctx.fillRect(0, 0, 512, 512);

  // Soft shaded footpath trails
  ctx.strokeStyle = '#cbd5e1';
  ctx.lineWidth = 18;
  ctx.lineCap = 'round';

  // Cross trails
  ctx.beginPath();
  ctx.moveTo(256, 0);
  ctx.lineTo(256, 512);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(0, 256);
  ctx.lineTo(512, 256);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(50, 50);
  ctx.lineTo(462, 462);
  ctx.stroke();

  // Subtle footprint speckles
  ctx.fillStyle = '#94a3b8';
  for (let i = 0; i < 80; i++) {
    const rx = 240 + (Math.random() - 0.5) * 40;
    const ry = Math.random() * 512;
    ctx.beginPath();
    ctx.ellipse(rx, ry, 3, 5, 0, 0, Math.PI * 2);
    ctx.fill();
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(2, 2);
  texture.minFilter = THREE.LinearFilter;
  return texture;
}

/**
 * Creates industrial cyberpunk panel texture with vents and hazard stripes.
 */
export function createCyberPanelTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = '#18181b';
  ctx.fillRect(0, 0, 512, 512);

  // Metal panel seams
  ctx.strokeStyle = '#09090b';
  ctx.lineWidth = 6;
  ctx.strokeRect(3, 3, 506, 506);
  ctx.strokeRect(3, 3, 506, 250);

  // Ventilation louvers
  ctx.fillStyle = '#09090b';
  for (let i = 0; i < 8; i++) {
    ctx.fillRect(40, 40 + i * 22, 200, 10);
  }

  // Warning Chevrons (Yellow / Black or Magenta / Black)
  for (let c = 0; c < 10; c++) {
    ctx.fillStyle = c % 2 === 0 ? '#ec4899' : '#09090b';
    ctx.beginPath();
    ctx.moveTo(300 + c * 18, 350);
    ctx.lineTo(315 + c * 18, 350);
    ctx.lineTo(295 + c * 18, 480);
    ctx.lineTo(280 + c * 18, 480);
    ctx.closePath();
    ctx.fill();
  }

  // Cyan circuit trace
  ctx.strokeStyle = '#06b6d4';
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(40, 300);
  ctx.lineTo(180, 300);
  ctx.lineTo(220, 340);
  ctx.lineTo(220, 480);
  ctx.stroke();

  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(2, 2);
  texture.minFilter = THREE.LinearFilter;
  return texture;
}

/**
 * Creates high-detail Persian oriental area rug texture with borders & central medallion.
 */
export function createOrientalRugTexture(baseColor = '#7f1d1d', accentColor = '#fde68a') {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 768;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = baseColor;
  ctx.fillRect(0, 0, 512, 768);

  // Outer fringed border
  ctx.strokeStyle = '#fef3c7';
  ctx.lineWidth = 14;
  ctx.strokeRect(16, 16, 480, 736);

  ctx.strokeStyle = accentColor;
  ctx.lineWidth = 6;
  ctx.strokeRect(32, 32, 448, 704);

  // Intricate floral / geometric borders
  ctx.strokeStyle = '#065f46';
  ctx.lineWidth = 4;
  ctx.strokeRect(44, 44, 424, 680);

  // Center medallion
  ctx.fillStyle = accentColor;
  ctx.beginPath();
  drawEllipse(ctx, 256, 384, 110, 160);
  ctx.fill();

  ctx.fillStyle = baseColor;
  ctx.beginPath();
  drawEllipse(ctx, 256, 384, 80, 120);
  ctx.fill();

  ctx.fillStyle = '#065f46';
  ctx.beginPath();
  ctx.arc(256, 384, 40, 0, Math.PI * 2);
  ctx.fill();

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  return texture;
}

/**
 * Creates modern Scandinavian diamond flat-weave area rug.
 */
export function createGeometricRugTexture(baseColor = '#334155', accentColor = '#e2e8f0') {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 768;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = baseColor;
  ctx.fillRect(0, 0, 512, 768);

  ctx.strokeStyle = accentColor;
  ctx.lineWidth = 8;

  const step = 64;
  for (let y = 0; y < 768 + step; y += step) {
    for (let x = 0; x < 512 + step; x += step) {
      ctx.beginPath();
      ctx.moveTo(x, y - step / 2);
      ctx.lineTo(x + step / 2, y);
      ctx.lineTo(x, y + step / 2);
      ctx.lineTo(x - step / 2, y);
      ctx.closePath();
      ctx.stroke();
    }
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  return texture;
}
