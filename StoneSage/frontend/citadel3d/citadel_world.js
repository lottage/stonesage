/**
 * Aevum-3D: Multi-Tier Agent Citadel & Physical Kanban Deck
 * Master World Engine & Telemetry Loop with High-Fidelity Architectural Environments
 */

import { createWorkerCharacter } from './procedural_models.js';
import { KanbanBoard3D } from './kanban_board3d.js';
import { updateAgentAnimation, triggerAgentEvent } from './animations.js';
import { AgentInspectorHUD } from './agent_inspector_hud.js';
import { loadEnvironment, getCurrentEnvironment, ENVIRONMENT_REGISTRY } from './environment_manager.js';

const THREE = window.THREE;

// --- SCENE STATE ---
let scene, camera, renderer, controls;
let kanbanBoard, hud;
const agents = new Map();
const interactiveObjects = [];
let clock;

// Active Environment Theme (Defaults to Greenhouse / Botanical Loft from Image 1)
let currentEnvKey = localStorage.getItem('citadel_env_theme') || 'greenhouse';
if (!ENVIRONMENT_REGISTRY[currentEnvKey]) currentEnvKey = 'greenhouse';

// Touch tracking
let pointerDownPos = { x: 0, y: 0 };
let pointerDownTime = 0;

// Particle systems
let celebrationParticles = [];
let matingParticles = [];

// Token Economy Metrics
const tokenMetrics = {
  cloudTokens: 21500,
  clusterTokens: 6008,
  savedDollars: 0.09
};

export function initCitadel() {
  try {
    const container = document.getElementById('webgl-canvas-container');
    const canvas = document.getElementById('three-canvas');
    if (!container || !canvas) {
      console.error('Citadel DOM containers missing');
      return;
    }

    if (!window.THREE) {
      showErrorBanner('Three.js failed to load. Please check network/vendor script.');
      return;
    }

    clock = new THREE.Clock();

    // 1. Scene
    scene = new THREE.Scene();

    // 2. Isometric Camera with robust dimension fallbacks
    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || (window.innerHeight - 32);
    const aspect = width / Math.max(height, 1);
    const d = 26;
    camera = new THREE.OrthographicCamera(-d * aspect, d * aspect, d, -d, 1, 1000);
    camera.position.set(38, 34, 38);
    camera.lookAt(0, 4, 0);

    // 3. Renderer with Soft Shadows
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    // 4. Orbit Controls with Touch Ergonomics
    if (THREE.OrbitControls) {
      controls = new THREE.OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.target.set(0, 4, 0);
      controls.maxPolarAngle = Math.PI / 2.05;
      controls.minZoom = 0.5;
      controls.maxZoom = 2.5;
    }

    // 5. Spawn Core Agents
    spawnInitialAgents();

    // 6. Setup Physical 3D Kanban Board
    kanbanBoard = new KanbanBoard3D(scene);
    interactiveObjects.push(...kanbanBoard.getInteractiveObjects());

    // Populate initial task cards
    kanbanBoard.addTaskCard({ id: 'task-1', title: 'Verify lock-free ring buffer', type: 'code', status: 'DOING', agentId: 'coord-alpha' });
    kanbanBoard.addTaskCard({ id: 'task-2', title: 'Unit tests for A-MEM cache', type: 'test', status: 'TO_DO', agentId: null });
    kanbanBoard.addTaskCard({ id: 'task-3', title: 'Hypothesis: Cosine threshold', type: 'hypothesis', status: 'TO_DO', agentId: null });
    kanbanBoard.addTaskCard({ id: 'task-4', title: 'Obsidian vault synchronization', type: 'architecture', status: 'DONE', agentId: 'worker-gamma' });

    // 7. Load Active Architectural Environment
    loadEnvironment(currentEnvKey, { scene, agents, kanbanBoard, camera, controls });

    // 8. Initialize Touch-Friendly HUD
    hud = new AgentInspectorHUD({
      onSendDirective: (agentId, directive) => handleSendDirective(agentId, directive),
      onNudge: (agentId) => handleAgentNudge(agentId)
    });

    // 9. Event Listeners (Touch, Resize, Buttons, Customizers)
    setupEventListeners(container);

    // Update Telemetry HUD Ticker with Token Metrics
    updateTickerMetrics();

    // 10. Expose Global Switcher
    window.setCitadelTheme = (key) => {
      currentEnvKey = key;
      try {
        localStorage.setItem('citadel_env_theme', key);
      } catch (e) {}
      const sel = document.getElementById('select-citadel-theme');
      if (sel) sel.value = key;
      loadEnvironment(key, { scene, agents, kanbanBoard, camera, controls });
    };

    // 11. Start Render Loop
    animate();
    console.log(`🏛️ Aevum-3D Citadel online [Active Environment: ${currentEnvKey}].`);
  } catch (err) {
    console.error('Failed to initialize Aevum Citadel:', err);
    showErrorBanner(err.message || String(err));
  }
}

function showErrorBanner(msg) {
  const container = document.getElementById('webgl-canvas-container');
  if (!container) return;
  const banner = document.createElement('div');
  banner.style.cssText = `
    position: absolute;
    top: 20px;
    left: 50%;
    transform: translateX(-50%);
    background: #fee2e2;
    color: #991b1b;
    border: 2px solid #ef4444;
    padding: 12px 18px;
    border-radius: 4px;
    font-family: monospace;
    font-size: 13px;
    z-index: 1000;
    max-width: 90%;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
  `;
  banner.innerHTML = `<strong>⚠️ 3D Citadel Render Error:</strong><br>${msg}`;
  container.appendChild(banner);
}

function updateTickerMetrics() {
  const statsEl = document.getElementById('ticker-stats');
  if (statsEl) {
    const offloadPct = Math.round((tokenMetrics.clusterTokens / (tokenMetrics.cloudTokens + tokenMetrics.clusterTokens)) * 100);
    statsEl.innerHTML = `Cloud: ${Math.round(tokenMetrics.cloudTokens / 100) / 10}k tok | Cluster: ${Math.round(tokenMetrics.clusterTokens / 100) / 10}k tok (${offloadPct}% local) | Saved: $${tokenMetrics.savedDollars.toFixed(2)}`;
  }
}

function spawnInitialAgents() {
  // Agent 1: Coordinator-Alpha
  const coord = createWorkerCharacter({
    archetype: 'engineer',
    agentId: 'coord-alpha',
    color: 0x1d4ed8,
    hasHardHat: false,
    hasClipboard: false,
    isSitting: false
  });
  coord.position.set(0, 0, 0);
  coord.userData.name = 'Coordinator-Alpha';
  coord.userData.role = 'Principal Systems Architect';
  coord.userData.node = 'VM 102 Vulkan0 (:8001 Q8_0)';
  coord.userData.animState = 'idle';
  scene.add(coord);
  agents.set('coord-alpha', coord);
  interactiveObjects.push(coord.userData.hitbox);

  // Agent 2: Worker-Beta
  const worker1 = createWorkerCharacter({
    archetype: 'engineer',
    agentId: 'worker-beta',
    color: 0x0284c7,
    hasHardHat: true,
    hardHatColor: 0xfacc15,
    hasClipboard: true,
    isSitting: false
  });
  worker1.position.set(0, 0, 0);
  worker1.userData.name = 'Worker-Beta';
  worker1.userData.role = 'High-Speed Test & Utility Solver';
  worker1.userData.node = 'VM 102 Vulkan1 (:8002 Q4_K_M)';
  worker1.userData.animState = 'idle';
  scene.add(worker1);
  agents.set('worker-beta', worker1);
  interactiveObjects.push(worker1.userData.hitbox);

  // Agent 3: Worker-Gamma
  const worker2 = createWorkerCharacter({
    archetype: 'engineer',
    agentId: 'worker-gamma',
    color: 0x0f766e,
    hasHardHat: true,
    hardHatColor: 0xffffff,
    hasMegaphone: false,
    isSitting: false
  });
  worker2.position.set(0, 0, 0);
  worker2.userData.name = 'Worker-Gamma';
  worker2.userData.role = 'Code Invariants & Lint Engine';
  worker2.userData.node = 'VM 102 Vulkan1 (:8002 Q4_K_M)';
  worker2.userData.animState = 'typing';
  scene.add(worker2);
  agents.set('worker-gamma', worker2);
  interactiveObjects.push(worker2.userData.hitbox);

  // Agent 4: Ally-Delta
  const allyAgent = createWorkerCharacter({
    archetype: 'engineer',
    agentId: 'ally-delta',
    color: 0x7e22ce,
    hasHardHat: false,
    hasMegaphone: true,
    isSitting: false
  });
  allyAgent.position.set(0, 0, 0);
  allyAgent.userData.name = 'Ally-Delta';
  allyAgent.userData.role = 'Claude-Hybrid Reasoning Core';
  allyAgent.userData.node = 'ROG Ally Extreme Z1 (:1234)';
  allyAgent.userData.animState = 'idle';
  scene.add(allyAgent);
  agents.set('ally-delta', allyAgent);
  interactiveObjects.push(allyAgent.userData.hitbox);
}

function setupEventListeners(container) {
  window.addEventListener('resize', () => {
    const w = container.clientWidth || window.innerWidth;
    const h = container.clientHeight || (window.innerHeight - 32);
    const aspect = w / Math.max(h, 1);
    const d = 26;
    camera.left = -d * aspect;
    camera.right = d * aspect;
    camera.top = d;
    camera.bottom = -d;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  });

  // Touch and Pointer Raycaster Selection
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();

  container.addEventListener('pointerdown', (e) => {
    pointerDownPos = { x: e.clientX, y: e.clientY };
    pointerDownTime = Date.now();
  });

  container.addEventListener('pointerup', (e) => {
    const dx = Math.abs(e.clientX - pointerDownPos.x);
    const dy = Math.abs(e.clientY - pointerDownPos.y);
    const dt = Date.now() - pointerDownTime;

    if (dx < 8 && dy < 8 && dt < 400) {
      const rect = container.getBoundingClientRect();
      pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(pointer, camera);
      const intersects = raycaster.intersectObjects(interactiveObjects, true);

      if (intersects.length > 0) {
        let hit = intersects[0].object;
        while (hit && !hit.userData.isAgentHitbox && !hit.userData.isKanbanCard && hit.parent) {
          hit = hit.parent;
        }

        if (hit && hit.userData.isAgentHitbox) {
          const agent = agents.get(hit.userData.agentId);
          if (agent) {
            focusAgent(agent);
          }
        } else if (hit && hit.userData.isKanbanCard) {
          const cardId = hit.userData.taskId;
          advanceKanbanCard(cardId);
        }
      }
    }
  });

  // Action Bar Buttons
  const btnRecenter = document.getElementById('btn-recenter');
  if (btnRecenter) {
    btnRecenter.addEventListener('click', () => {
      const activeEnv = getCurrentEnvironment();
      const target = activeEnv?.config?.cameraTarget || [0, 4, 0];
      const pos = activeEnv?.config?.cameraPos || [38, 34, 38];
      if (controls) controls.target.set(...target);
      camera.position.set(...pos);
      camera.zoom = 1.0;
      camera.updateProjectionMatrix();
    });
  }

  const btnNewAgent = document.getElementById('btn-new-agent');
  if (btnNewAgent) {
    btnNewAgent.addEventListener('click', () => {
      spawnNewAgentDialog();
    });
  }

  const btnMate = document.getElementById('btn-mate-agents');
  if (btnMate) {
    btnMate.addEventListener('click', () => {
      triggerMatingEvent();
    });
  }

  const btnAddTask = document.getElementById('btn-add-task');
  if (btnAddTask) {
    btnAddTask.addEventListener('click', () => {
      const title = prompt('Enter new task description:');
      if (title && title.trim()) {
        const id = 'task-' + (Date.now() % 10000);
        kanbanBoard.addTaskCard({ id, title: title.trim(), type: 'code', status: 'TO_DO' });
      }
    });
  }

  const btnTestAnims = document.getElementById('btn-test-animations');
  if (btnTestAnims) {
    btnTestAnims.addEventListener('click', () => {
      cycleTestAnimations();
    });
  }

  const btnVoice = document.getElementById('btn-voice-global');
  if (btnVoice) {
    btnVoice.addEventListener('click', () => {
      alert('Push-to-Talk active: Listening for cluster voice directives (Whisper :8200)...');
    });
  }

  // Environment Selector in Titlebar
  const selTheme = document.getElementById('select-citadel-theme');
  if (selTheme) {
    selTheme.value = currentEnvKey;
    selTheme.addEventListener('change', (e) => {
      window.setCitadelTheme(e.target.value);
    });
  }

  // Customizer Modal Trigger & Close
  const btnStyleStudio = document.getElementById('btn-open-style-studio');
  const styleModal = document.getElementById('citadel-style-modal');
  const styleBackdrop = document.getElementById('style-modal-backdrop');
  const styleCloseBtn = document.getElementById('style-modal-close-btn');

  if (btnStyleStudio && styleModal) {
    btnStyleStudio.addEventListener('click', () => {
      styleModal.classList.add('active');
      if (styleBackdrop) styleBackdrop.classList.add('active');
    });
  }

  if (styleCloseBtn && styleModal) {
    styleCloseBtn.addEventListener('click', () => {
      styleModal.classList.remove('active');
      if (styleBackdrop) styleBackdrop.classList.remove('active');
    });
  }

  if (styleBackdrop) {
    styleBackdrop.addEventListener('click', () => {
      if (styleModal) styleModal.classList.remove('active');
      styleBackdrop.classList.remove('active');
    });
  }
}

function focusAgent(agent) {
  if (controls) controls.target.copy(agent.position);
  hud.openInspector({
    id: agent.userData.agentId,
    name: agent.userData.name,
    role: agent.userData.role,
    node: agent.userData.node,
    status: (agent.userData.animState || 'idle').toUpperCase()
  });
}

function advanceKanbanCard(cardId) {
  const card = kanbanBoard.getCard(cardId);
  if (!card) return;

  if (card.status === 'TO_DO') {
    kanbanBoard.moveCardToStatus(cardId, 'DOING');
    const worker = agents.get('worker-beta');
    if (worker) triggerAgentEvent(worker, 'TASK_START');
  } else if (card.status === 'DOING') {
    kanbanBoard.moveCardToStatus(cardId, 'DONE');
    const worker = agents.get('worker-beta');
    if (worker) triggerAgentEvent(worker, 'TASK_COMPLETE');
    spawnCelebrationConfetti(card.mesh.position);

    tokenMetrics.clusterTokens += 450;
    tokenMetrics.savedDollars += 0.01;
    updateTickerMetrics();
  }
}

function spawnCelebrationConfetti(pos) {
  for (let i = 0; i < 35; i++) {
    const geo = new THREE.BoxGeometry(0.12, 0.12, 0.12);
    const mat = new THREE.MeshBasicMaterial({
      color: [0xfacc15, 0x22c55e, 0x38bdf8, 0xf43f5e][i % 4]
    });
    const p = new THREE.Mesh(geo, mat);
    p.position.copy(pos);
    p.userData.vel = new THREE.Vector3(
      (Math.random() - 0.5) * 6,
      Math.random() * 8 + 3,
      (Math.random() - 0.5) * 6
    );
    p.userData.life = 1.0;
    scene.add(p);
    celebrationParticles.push(p);
  }
}

function triggerMatingEvent() {
  const coord = agents.get('coord-alpha');
  const ally = agents.get('ally-delta');
  if (coord && ally) {
    triggerAgentEvent(coord, 'AGENT_MATING');
    triggerAgentEvent(ally, 'AGENT_MATING');

    const center = new THREE.Vector3(0, 1.5, 0);
    for (let i = 0; i < 50; i++) {
      const geo = new THREE.SphereGeometry(0.08, 6, 6);
      const mat = new THREE.MeshBasicMaterial({ color: 0xc084fc });
      const p = new THREE.Mesh(geo, mat);
      p.position.copy(center);
      p.userData.vel = new THREE.Vector3(
        (Math.random() - 0.5) * 5,
        Math.random() * 6 + 2,
        (Math.random() - 0.5) * 5
      );
      p.userData.life = 1.0;
      scene.add(p);
      matingParticles.push(p);
    }

    setTimeout(() => {
      spawnBlendedChildAgent();
    }, 2500);
  }
}

function spawnBlendedChildAgent() {
  const childId = 'child-' + (Date.now() % 1000);
  const child = createWorkerCharacter({
    archetype: 'engineer',
    agentId: childId,
    color: 0xec4899,
    hasHardHat: true,
    hardHatColor: 0xec4899,
    hasClipboard: true
  });
  child.position.set(0, 0.5, 0);
  child.userData.name = 'Ornith-Claude-Hybrid';
  child.userData.role = 'Synthetic Dual-Lineage Solver';
  child.userData.node = 'VM 102 + ROG Ally Swarm';
  child.userData.animState = 'celebrating';
  scene.add(child);
  agents.set(childId, child);
  interactiveObjects.push(child.userData.hitbox);
  triggerAgentEvent(child, 'TASK_COMPLETE');
  alert(`✨ Agent Mating Event Successful!\nNew Hybrid Archetype 'Ornith-Claude-Hybrid' crystallized into Citadel.`);
}

function cycleTestAnimations() {
  const beta = agents.get('worker-beta');
  if (!beta) return;
  const states = ['celebrating', 'dizzy', 'megaphone', 'walking', 'idle'];
  const next = states[(states.indexOf(beta.userData.animState) + 1) % states.length];
  beta.userData.animState = next;
  triggerAgentEvent(beta, next.toUpperCase());
}

function spawnNewAgentDialog() {
  const name = prompt('Enter Agent Name:', 'Worker-Delta');
  if (!name) return;
  const role = prompt('Enter Agent Role:', 'Algorithmic Test Specialist');
  const agentId = 'agent-' + (Date.now() % 1000);
  const newAgent = createWorkerCharacter({
    archetype: 'engineer',
    agentId: agentId,
    color: 0x10b981,
    hasHardHat: true,
    hardHatColor: 0xfacc15
  });
  newAgent.position.set(-4 + Math.random() * 8, 0, -4 + Math.random() * 8);
  newAgent.userData.name = name;
  newAgent.userData.role = role || 'Cluster Utility Worker';
  newAgent.userData.node = 'VM 102 Vulkan1 (:8002)';
  newAgent.userData.animState = 'celebrating';
  scene.add(newAgent);
  agents.set(agentId, newAgent);
  interactiveObjects.push(newAgent.userData.hitbox);
  triggerAgentEvent(newAgent, 'TASK_COMPLETE');
}

function handleSendDirective(agentId, directive) {
  const agent = agents.get(agentId);
  if (!agent) return;
  hud.appendReasoningToken(`\n[Directive Injected]: "${directive}"\nProcessing with local parameters (temperature=0.20, min_p=0.08)...\n`);
  triggerAgentEvent(agent, 'TYPING');

  const tid = 'task-' + (Date.now() % 10000);
  kanbanBoard.addTaskCard({ id: tid, title: directive, type: 'code', status: 'DOING', agentId });
}

function handleAgentNudge(agentId) {
  const agent = agents.get(agentId);
  if (!agent) return;
  hud.appendReasoningToken(`\n🚨 [AGENT-NUDGE ACTIVATED]: Out-of-band breakout sequence triggered. Invariant restored.\n`);
  triggerAgentEvent(agent, 'CELEBRATE');
}

// --- RENDER & ANIMATION LOOP ---
function animate() {
  requestAnimationFrame(animate);

  const delta = clock ? clock.getDelta() : 0.016;
  const time = clock ? clock.getElapsedTime() : Date.now() * 0.001;

  if (controls) controls.update();

  // Dynamic campfire flicker for Arctic Camp
  const activeEnv = getCurrentEnvironment();
  if (activeEnv && activeEnv.fireLight) {
    activeEnv.fireLight.intensity = 2.0 + Math.sin(time * 12.0) * 0.35 + (Math.random() - 0.5) * 0.25;
  }

  // 1. Update Skeletal Agent Animations
  agents.forEach((agent) => {
    updateAgentAnimation(agent, delta, time);
  });

  // 2. Update Physical 3D Kanban Board
  if (kanbanBoard) {
    kanbanBoard.update(delta, time);
  }

  // 3. Update Particle Systems
  celebrationParticles = celebrationParticles.filter((p) => {
    p.position.addScaledVector(p.userData.vel, delta);
    p.userData.vel.y -= 9.8 * delta;
    p.userData.life -= delta * 0.8;
    p.scale.setScalar(Math.max(0.01, p.userData.life));
    if (p.userData.life <= 0) {
      scene.remove(p);
      return false;
    }
    return true;
  });

  matingParticles = matingParticles.filter((p) => {
    p.position.addScaledVector(p.userData.vel, delta);
    p.userData.life -= delta * 0.7;
    p.scale.setScalar(Math.max(0.01, p.userData.life));
    if (p.userData.life <= 0) {
      scene.remove(p);
      return false;
    }
    return true;
  });

  if (renderer && scene && camera) {
    renderer.render(scene, camera);
  }
}

// Start on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initCitadel);
} else {
  initCitadel();
}
