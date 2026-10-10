/**
 * Aevum-3D: Master Environment Manager
 * Coordinates real-time loading, lighting transitions, camera framing,
 * agent architectural placement, and Kanban positioning across the 5 micro-worlds.
 */

import { buildGreenhouseEnvironment } from './environments/env_greenhouse.js';
import { buildModernLoftEnvironment } from './environments/env_modern_loft.js';
import { buildGothicManorEnvironment } from './environments/env_gothic_manor.js';
import { buildArcticCampEnvironment } from './environments/env_arctic_camp.js';
import { buildCyberpunkDenEnvironment } from './environments/env_cyberpunk_den.js';

const THREE = window.THREE;

export const ENVIRONMENT_REGISTRY = {
  greenhouse: {
    id: 'greenhouse',
    name: 'Botanical Greenhouse Loft',
    icon: '🌿',
    builder: buildGreenhouseEnvironment,
    desc: 'Multi-tier sunroom loft, arched sunlit windows, ivy trellises & conservatory lounge'
  },
  modern_loft: {
    id: 'modern_loft',
    name: 'Modern Architect Loft',
    icon: '🏢',
    builder: buildModernLoftEnvironment,
    desc: 'White architectural cutaway, open-riser timber stairs, mezzanine & conversation pit'
  },
  gothic_manor: {
    id: 'gothic_manor',
    name: 'Grand Gothic Atrium',
    icon: '🏰',
    builder: buildGothicManorEnvironment,
    desc: 'Multi-story stone arcade galleries, library bookshelves, banquet table & reflection pool'
  },
  arctic_camp: {
    id: 'arctic_camp',
    name: 'Arctic Expedition Encampment',
    icon: '❄️',
    builder: buildArcticCampEnvironment,
    desc: 'Snowfield radial camp, roaring central stone firepit, expedition canvas tents & wagons'
  },
  cyberpunk_den: {
    id: 'cyberpunk_den',
    name: 'Cyberpunk Hacker Den',
    icon: '🌆',
    builder: buildCyberpunkDenEnvironment,
    desc: 'Vertical electronics lab, industrial catwalk, CRT banks, hanging conduit & neon glow'
  }
};

let currentEnvironmentInstance = null;

export function loadEnvironment(envKey, context) {
  const { scene, agents, kanbanBoard, camera, controls } = context;

  const envMeta = ENVIRONMENT_REGISTRY[envKey] || ENVIRONMENT_REGISTRY.greenhouse;

  // 1. Teardown existing environment
  if (currentEnvironmentInstance && currentEnvironmentInstance.group) {
    scene.remove(currentEnvironmentInstance.group);
    currentEnvironmentInstance.group.traverse((obj) => {
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        if (Array.isArray(obj.material)) {
          obj.material.forEach((m) => m.dispose());
        } else {
          obj.material.dispose();
        }
      }
    });
  }

  // 2. Build new environment
  const newEnv = envMeta.builder(scene);
  currentEnvironmentInstance = newEnv;
  localStorage.setItem('citadel_env_theme', envKey);

  // 3. Apply Scene Fog & Background
  if (scene && newEnv.config) {
    scene.background = new THREE.Color(newEnv.config.bg);
    scene.fog = new THREE.FogExp2(newEnv.config.fogColor, newEnv.config.fogDensity);
  }

  // 4. Camera & OrbitControls Framing
  if (camera && controls && newEnv.config) {
    const target = newEnv.config.cameraTarget || [0, 4, 0];
    controls.target.set(...target);
    if (newEnv.config.cameraPos) {
      camera.position.set(...newEnv.config.cameraPos);
    }
    camera.lookAt(controls.target);
    camera.updateProjectionMatrix();
    controls.update();
  }

  // 5. Reposition Agents to Architectural Hotspots
  if (agents && newEnv.agentSlots) {
    for (const [agentId, slot] of Object.entries(newEnv.agentSlots)) {
      const agent = agents.get(agentId);
      if (agent) {
        agent.position.set(...slot.pos);
        agent.rotation.y = slot.rotY || 0;
        agent.userData.baseY = slot.pos[1]; // Anchor for hops and animations
      }
    }
  }

  // 6. Reposition 3D Kanban Board
  if (kanbanBoard && newEnv.kanbanConfig) {
    kanbanBoard.group.position.set(...newEnv.kanbanConfig.pos);
    kanbanBoard.group.rotation.y = newEnv.kanbanConfig.rotY || 0;
  }

  // 7. Sync Dropdown if present
  const sel = document.getElementById('select-citadel-theme');
  if (sel && sel.value !== envKey) {
    sel.value = envKey;
  }

  console.log(`🏛️ Environment loaded: [${envMeta.name}]`);
  return newEnv;
}

export function getCurrentEnvironment() {
  return currentEnvironmentInstance;
}
