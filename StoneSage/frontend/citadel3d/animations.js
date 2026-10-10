/**
 * Aevum-3D: Procedural Skeletal Animations
 * Zero external asset downloads - Pure hierarchical Three.js transforms
 */

const THREE = window.THREE;

export const ANIM_STATES = {
  IDLE: 'idle',
  WALKING: 'walking',
  TYPING: 'typing',
  CELEBRATING: 'celebrating',
  DIZZY: 'dizzy',
  MEGAPHONE: 'megaphone',
  MATING: 'mating'
};

export function setAnimState(agent, state) {
  if (!agent || !agent.userData) return;
  agent.userData.animState = state;
  agent.userData.animTimer = 0.0;
}

export function getAnimState(agent) {
  return agent?.userData?.animState || ANIM_STATES.IDLE;
}

export function triggerAgentEvent(agent, eventType) {
  if (!agent || !agent.userData) return;
  const ev = (eventType || '').toUpperCase();

  switch (ev) {
    case 'TASK_START':
    case 'TYPING':
      setAnimState(agent, ANIM_STATES.TYPING);
      break;
    case 'TASK_COMPLETE':
    case 'CELEBRATE':
    case 'CELEBRATING':
      setAnimState(agent, ANIM_STATES.CELEBRATING);
      break;
    case 'STUCK_LOOP':
    case 'DIZZY':
      setAnimState(agent, ANIM_STATES.DIZZY);
      break;
    case 'ALERT':
    case 'MEGAPHONE':
    case 'BROADCAST':
      setAnimState(agent, ANIM_STATES.MEGAPHONE);
      break;
    case 'AGENT_MATING':
    case 'MATING':
      setAnimState(agent, ANIM_STATES.MATING);
      break;
    case 'WALKING':
      setAnimState(agent, ANIM_STATES.WALKING);
      break;
    case 'IDLE':
    default:
      setAnimState(agent, ANIM_STATES.IDLE);
      break;
  }
}

export function updateAgentAnimation(agent, delta, time) {
  if (!agent || !agent.userData) return;

  const state = agent.userData.animState || ANIM_STATES.IDLE;
  agent.userData.animTimer = (agent.userData.animTimer || 0) + delta;
  const localTime = time + (agent.userData.animTimerOffset || 0);

  const torso = agent.userData.torso;
  const head = agent.userData.head;
  const leftArm = agent.userData.leftArm;
  const rightArm = agent.userData.rightArm;
  const leftLeg = agent.userData.leftLeg;
  const rightLeg = agent.userData.rightLeg;
  const isSitting = agent.userData.isSitting || false;

  // 1. Base Idle / Breathing baseline
  if (state === ANIM_STATES.IDLE) {
    if (head) {
      head.rotation.y = Math.sin(localTime * 0.8) * 0.15;
      head.rotation.x = Math.sin(localTime * 1.1) * 0.05;
    }
    if (torso) {
      torso.position.y = (isSitting ? 0.85 : 1.3) + Math.sin(localTime * 2.0) * 0.015;
    }
    if (!isSitting) {
      if (leftArm) leftArm.rotation.x = Math.sin(localTime * 1.2) * 0.05;
      if (rightArm) rightArm.rotation.x = -Math.sin(localTime * 1.2) * 0.05;
    }
    removeDizzyStars(agent);
  }

  // 2. Typing on Keyboard / Desk
  else if (state === ANIM_STATES.TYPING) {
    if (head) {
      head.rotation.x = -0.22 + Math.sin(localTime * 2.0) * 0.03;
      head.rotation.y = Math.sin(localTime * 0.7) * 0.08;
    }
    const tapSpeed = 16.0;
    if (leftArm) {
      leftArm.rotation.x = -Math.PI / 3 + Math.sin(localTime * tapSpeed) * 0.12;
      leftArm.rotation.z = -0.15;
    }
    if (rightArm) {
      rightArm.rotation.x = -Math.PI / 3 + Math.cos(localTime * tapSpeed) * 0.12;
      rightArm.rotation.z = 0.15;
    }
    if (torso) {
      torso.position.y = (isSitting ? 0.85 : 1.3) + Math.abs(Math.sin(localTime * tapSpeed * 0.5)) * 0.01;
    }
    removeDizzyStars(agent);
  }

  // 3. Walking / Patrolling
  else if (state === ANIM_STATES.WALKING) {
    const walkSpeed = 7.0;
    if (leftArm) leftArm.rotation.x = Math.sin(localTime * walkSpeed) * 0.6;
    if (rightArm) rightArm.rotation.x = -Math.sin(localTime * walkSpeed) * 0.6;
    if (leftLeg) leftLeg.rotation.x = -Math.sin(localTime * walkSpeed) * 0.5;
    if (rightLeg) rightLeg.rotation.x = Math.sin(localTime * walkSpeed) * 0.5;
    if (torso) {
      torso.position.y = 1.3 + Math.abs(Math.sin(localTime * walkSpeed)) * 0.08;
    }
    removeDizzyStars(agent);
  }

  // 4. Celebrating / Task Done Hop
  else if (state === ANIM_STATES.CELEBRATING) {
    const hopSpeed = 8.0;
    const hop = Math.abs(Math.sin(localTime * hopSpeed)) * 0.35;
    agent.position.y = (agent.userData.baseY || 0) + hop;

    if (leftArm) {
      leftArm.rotation.x = -2.6;
      leftArm.rotation.z = -0.6 + Math.sin(localTime * hopSpeed) * 0.2;
    }
    if (rightArm) {
      rightArm.rotation.x = -2.6;
      rightArm.rotation.z = 0.6 - Math.sin(localTime * hopSpeed) * 0.2;
    }
    if (head) {
      head.rotation.x = -0.3 + Math.sin(localTime * hopSpeed) * 0.1;
      head.rotation.y = Math.sin(localTime * 3.0) * 0.2;
    }
    removeDizzyStars(agent);

    // Auto-return to idle after 4 seconds
    if (agent.userData.animTimer > 4.0) {
      agent.position.y = agent.userData.baseY || 0;
      setAnimState(agent, ANIM_STATES.IDLE);
    }
  }

  // 5. Dizzy / Stuck Loop (Circling stars overhead)
  else if (state === ANIM_STATES.DIZZY) {
    if (torso) {
      torso.rotation.x = Math.sin(localTime * 3.0) * 0.15;
      torso.rotation.z = Math.cos(localTime * 2.5) * 0.15;
    }
    if (head) {
      head.rotation.x = Math.sin(localTime * 4.0) * 0.25;
      head.rotation.y = Math.cos(localTime * 4.0) * 0.25;
    }
    if (leftArm) leftArm.rotation.x = Math.sin(localTime * 2.0) * 0.3;
    if (rightArm) rightArm.rotation.x = -Math.cos(localTime * 2.0) * 0.3;

    updateDizzyStars(agent, delta, localTime);
  }

  // 6. Megaphone / Cluster Alert
  else if (state === ANIM_STATES.MEGAPHONE) {
    if (rightArm) {
      rightArm.rotation.x = -1.8;
      rightArm.rotation.y = -0.3;
      rightArm.rotation.z = 0.3;
    }
    if (head) {
      head.rotation.x = -0.15 + Math.sin(localTime * 6.0) * 0.08;
      head.rotation.y = Math.sin(localTime * 1.5) * 0.3;
    }
    if (torso) {
      torso.position.y = 1.3 + Math.sin(localTime * 6.0) * 0.03;
    }
    removeDizzyStars(agent);
  }

  // 7. Agent Mating Event
  else if (state === ANIM_STATES.MATING) {
    agent.rotation.y += delta * 2.0;
    agent.position.y = (agent.userData.baseY || 0) + Math.sin(localTime * 3.0) * 0.4 + 0.2;
    if (leftArm) {
      leftArm.rotation.x = -1.5;
      leftArm.rotation.z = -0.8;
    }
    if (rightArm) {
      rightArm.rotation.x = -1.5;
      rightArm.rotation.z = 0.8;
    }
    removeDizzyStars(agent);
  }
}

function updateDizzyStars(agent, delta, time) {
  let starsGroup = agent.getObjectByName('dizzyStarsGroup');
  if (!starsGroup) {
    starsGroup = new THREE.Group();
    starsGroup.name = 'dizzyStarsGroup';
    starsGroup.position.set(0, 2.5, 0); // Above head

    const starGeo = new THREE.OctahedronGeometry(0.12, 0);
    const starMat = new THREE.MeshBasicMaterial({ color: 0xfacc15 });

    for (let i = 0; i < 3; i++) {
      const star = new THREE.Mesh(starGeo, starMat);
      starsGroup.add(star);
    }
    agent.add(starsGroup);
  }

  // Orbit stars around head
  starsGroup.children.forEach((star, idx) => {
    const angle = time * 4.0 + (idx * (Math.PI * 2 / 3));
    const radius = 0.55;
    star.position.set(
      Math.cos(angle) * radius,
      Math.sin(time * 6.0 + idx) * 0.08,
      Math.sin(angle) * radius
    );
    star.rotation.y += delta * 5.0;
  });
}

function removeDizzyStars(agent) {
  const starsGroup = agent.getObjectByName('dizzyStarsGroup');
  if (starsGroup) {
    agent.remove(starsGroup);
    if (agent.userData.torso) {
      agent.userData.torso.rotation.set(0, 0, 0);
    }
  }
}