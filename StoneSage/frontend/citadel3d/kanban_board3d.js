/**
 * Aevum-3D: Physical Kanban Deck
 * Suspended cable tracks, clothes-pin clips, and canvas-rendered task cards
 */

const THREE = window.THREE;

export class KanbanBoard3D {
  constructor(scene) {
    this.scene = scene;
    this.group = new THREE.Group();
    this.group.position.set(0, 3.5, 0); // Anchored on Level 3 floor
    this.scene.add(this.group);

    this.cards = new Map();
    this.interactiveObjects = [];

    // Column X offsets relative to group
    this.columnX = {
      'TO_DO': -4.5,
      'DOING': 1.5,
      'DONE': 7.5
    };

    this.cableY = 3.6; // Cable height above floor
    this.buildCableStructure();
    this.buildFinishedBasket();
  }

  buildCableStructure() {
    // 1. Two heavy industrial steel stanchions supporting the main cable track
    const stanchionGeo = new THREE.BoxGeometry(0.3, 4.2, 0.3);
    const stanchionMat = new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.8, roughness: 0.3 });

    const leftPost = new THREE.Mesh(stanchionGeo, stanchionMat);
    leftPost.position.set(-7.0, 2.1, -1.0);
    leftPost.castShadow = true;
    this.group.add(leftPost);

    const rightPost = new THREE.Mesh(stanchionGeo, stanchionMat);
    rightPost.position.set(10.0, 2.1, -1.0);
    rightPost.castShadow = true;
    this.group.add(rightPost);

    // 2. Suspended Steel Cable
    const cableLength = 17.2;
    const cableGeo = new THREE.CylinderGeometry(0.04, 0.04, cableLength, 8);
    const cableMat = new THREE.MeshStandardMaterial({ color: 0x94a3b8, metalness: 0.9, roughness: 0.2 });
    const cable = new THREE.Mesh(cableGeo, cableMat);
    cable.rotation.z = Math.PI / 2;
    cable.position.set(1.5, this.cableY, -1.0);
    cable.castShadow = true;
    this.group.add(cable);

    // 3. Overhead Column Signs
    const columns = [
      { key: 'TO_DO', label: '1. TO DO', x: this.columnX['TO_DO'], color: '#64748b' },
      { key: 'DOING', label: '2. DOING / REASONING', x: this.columnX['DOING'], color: '#0284c7' },
      { key: 'DONE', label: '3. DONE / VERIFIED', x: this.columnX['DONE'], color: '#16a34a' }
    ];

    columns.forEach(col => {
      const signMesh = this.createSignMesh(col.label, col.color);
      signMesh.position.set(col.x, this.cableY + 0.9, -1.0);
      this.group.add(signMesh);
    });
  }

  createSignMesh(text, bgColor) {
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 128;
    const ctx = canvas.getContext('2d');

    // 90s beveled sign background
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, 512, 128);
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 6;
    ctx.strokeRect(3, 3, 506, 122);

    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 36px "MS Sans Serif", Arial, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 256, 64);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;

    const signGeo = new THREE.BoxGeometry(3.2, 0.7, 0.1);
    const signMat = new THREE.MeshStandardMaterial({ map: texture, roughness: 0.4 });
    return new THREE.Mesh(signGeo, signMat);
  }

  buildFinishedBasket() {
    // Metal wire catch basket for DONE cards
    const basketGeo = new THREE.BoxGeometry(3.2, 0.6, 2.0);
    const basketMat = new THREE.MeshStandardMaterial({
      color: 0x475569,
      metalness: 0.7,
      roughness: 0.3,
      wireframe: false
    });
    const basket = new THREE.Mesh(basketGeo, basketMat);
    basket.position.set(this.columnX['DONE'], 0.3, -1.0);
    basket.receiveShadow = true;
    this.group.add(basket);

    const rimGeo = new THREE.BoxGeometry(3.3, 0.08, 2.1);
    const rimMat = new THREE.MeshStandardMaterial({ color: 0x22c55e, roughness: 0.3 });
    const rim = new THREE.Mesh(rimGeo, rimMat);
    rim.position.set(this.columnX['DONE'], 0.6, -1.0);
    this.group.add(rim);
  }

  createCardTexture(title, type, status) {
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 320;
    const ctx = canvas.getContext('2d');

    // Card background
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, 512, 320);

    // Top Header Banner by Type
    const typeColors = {
      code: '#22c55e',
      test: '#0ea5e9',
      architecture: '#f59e0b',
      hypothesis: '#ec4899',
      mating: '#a855f7'
    };
    const headerColor = typeColors[type] || '#64748b';
    ctx.fillStyle = headerColor;
    ctx.fillRect(0, 0, 512, 48);

    // Header Type Text
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 24px "Segoe UI", Arial, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(`[${type.toUpperCase()}] TASK CARD`, 16, 33);

    // Status Pill
    ctx.fillStyle = status === 'DONE' ? '#dcfce7' : status === 'DOING' ? '#fef08a' : '#f1f5f9';
    ctx.fillRect(360, 8, 136, 32);
    ctx.fillStyle = status === 'DONE' ? '#15803d' : status === 'DOING' ? '#854d0e' : '#475569';
    ctx.font = 'bold 18px Arial';
    ctx.textAlign = 'center';
    ctx.fillText(status, 428, 30);

    // Card Body Title (Word Wrap)
    ctx.fillStyle = '#0f172a';
    ctx.font = 'bold 30px "MS Sans Serif", Tahoma, sans-serif';
    ctx.textAlign = 'left';
    
    const words = title.split(' ');
    let line = '';
    let y = 100;
    for (let i = 0; i < words.length; i++) {
      const testLine = line + words[i] + ' ';
      if (ctx.measureText(testLine).width > 470 && i > 0) {
        ctx.fillText(line, 20, y);
        line = words[i] + ' ';
        y += 40;
      } else {
        line = testLine;
      }
    }
    ctx.fillText(line, 20, y);

    // Footer Invariant / Telemetry Note
    ctx.fillStyle = '#64748b';
    ctx.font = '18px monospace';
    ctx.fillText('Dual-GPU Verified | Cosine < 0.85', 20, 290);

    // Outer border
    ctx.strokeStyle = '#cbd5e1';
    ctx.lineWidth = 8;
    ctx.strokeRect(4, 4, 504, 312);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    return texture;
  }

  addTaskCard({ id, title, type = 'code', status = 'TO_DO', agentId = null }) {
    if (this.cards.has(id)) return;

    const cardGroup = new THREE.Group();
    cardGroup.userData.taskId = id;
    cardGroup.userData.isKanbanCard = true;
    cardGroup.userData.title = title;
    cardGroup.userData.type = type;
    cardGroup.userData.status = status;
    cardGroup.userData.agentId = agentId;

    // Physical card dimensions: width 2.2, height 1.4, thickness 0.04
    const texture = this.createCardTexture(title, type, status);
    const cardGeo = new THREE.BoxGeometry(2.2, 1.4, 0.04);
    const cardMat = new THREE.MeshStandardMaterial({
      map: texture,
      roughness: 0.5,
      metalness: 0.1
    });
    const cardMesh = new THREE.Mesh(cardGeo, cardMat);
    cardMesh.castShadow = true;
    cardGroup.add(cardMesh);
    cardGroup.userData.cardMesh = cardMesh;

    // Wooden clothes-pin clip at top
    const pinGeo = new THREE.BoxGeometry(0.12, 0.4, 0.12);
    const pinMat = new THREE.MeshStandardMaterial({ color: 0xb45309, roughness: 0.7 });
    const pin = new THREE.Mesh(pinGeo, pinMat);
    pin.position.set(0, 0.75, 0);
    cardGroup.add(pin);

    // Oversized invisible click/touch hitbox
    const hitGeo = new THREE.BoxGeometry(2.5, 1.8, 0.6);
    const hitMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false });
    const hitbox = new THREE.Mesh(hitGeo, hitMat);
    hitbox.userData.isKanbanCard = true;
    hitbox.userData.taskId = id;
    cardGroup.add(hitbox);
    this.interactiveObjects.push(hitbox);

    // Calculate initial slot
    const targetPos = this.calculateCardTargetPos(status, id);
    cardGroup.position.copy(targetPos);
    cardGroup.userData.targetPos = targetPos.clone();
    cardGroup.userData.currentPos = targetPos.clone();
    cardGroup.userData.targetRotZ = 0;
    cardGroup.userData.swingTimer = Math.random() * 5;

    this.group.add(cardGroup);
    this.cards.set(id, {
      id,
      title,
      type,
      status,
      mesh: cardGroup,
      texture
    });

    this.realignColumnCards();
  }

  calculateCardTargetPos(status, id) {
    const colX = this.columnX[status] || 0;
    let index = 0;
    for (const [cid, card] of this.cards) {
      if (card.status === status) {
        if (cid === id) break;
        index++;
      }
    }
    const y = (status === 'DONE') 
      ? 0.5 + index * 0.1 
      : this.cableY - 1.0 - (index * 1.55);
    const z = (status === 'DONE') ? -1.0 + (index * 0.05) : -1.0;
    return new THREE.Vector3(colX, y, z);
  }

  realignColumnCards() {
    const counts = { 'TO_DO': 0, 'DOING': 0, 'DONE': 0 };
    for (const [, card] of this.cards) {
      const st = card.status;
      const idx = counts[st]++;
      const colX = this.columnX[st];
      if (st === 'DONE') {
        card.mesh.userData.targetPos.set(colX, 0.4 + idx * 0.12, -1.0);
        card.mesh.userData.targetRotZ = (idx % 2 === 0 ? 0.05 : -0.05);
      } else {
        card.mesh.userData.targetPos.set(colX, this.cableY - 1.0 - (idx * 1.55), -1.0);
        card.mesh.userData.targetRotZ = 0;
      }
    }
  }

  moveCardToStatus(id, newStatus) {
    const card = this.cards.get(id);
    if (!card) return;

    card.status = newStatus;
    card.mesh.userData.status = newStatus;

    // Update texture with new status pill
    const newTexture = this.createCardTexture(card.title, card.type, newStatus);
    card.mesh.userData.cardMesh.material.map = newTexture;
    card.mesh.userData.cardMesh.material.needsUpdate = true;
    card.texture = newTexture;

    // Add little swing impulse
    card.mesh.userData.swingVelocity = (Math.random() - 0.5) * 0.8;

    this.realignColumnCards();
  }

  getCard(id) {
    return this.cards.get(id);
  }

  getInteractiveObjects() {
    return this.interactiveObjects;
  }

  update(delta, time) {
    for (const [, card] of this.cards) {
      const mesh = card.mesh;
      const target = mesh.userData.targetPos;

      // Smooth lerp to target position
      mesh.position.lerp(target, 6.0 * delta);

      // Subtle cable sway animation if hung on cable
      if (card.status !== 'DONE') {
        mesh.userData.swingTimer += delta;
        const sway = Math.sin(mesh.userData.swingTimer * 2.0) * 0.03;
        mesh.rotation.z = sway + (mesh.userData.swingVelocity || 0);
        if (mesh.userData.swingVelocity) {
          mesh.userData.swingVelocity *= Math.exp(-3.0 * delta);
        }
      } else {
        mesh.rotation.z = mesh.userData.targetRotZ || 0;
      }
    }
  }
}