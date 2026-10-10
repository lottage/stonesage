/**
 * StoneSage Terminal Harness Master Bootstrap (v3.0)
 * Uniform Terminal UX, Deuteranopia Standard, PWA Service Worker, and Zero-State-Loss Navigation.
 */

import { State, initTheme, setTheme, initNavigation, loadChatHistory, switchTab } from './state.js';
import { initChat } from './chat.js';
import { initTerminal } from './terminal.js';
import { initHarness } from './harness.js';
import { initFleet } from './fleet.js';
import { fetchPresenceState, fetchGardenState } from './presence_garden.js';
import { fetchAgentDnaList } from './agent_dna.js';
import { initWorkstationAce } from './workstation_ace.js';
import { initEngineStudio } from './engine_studio.js';
import { initEngineConsole } from './engine_console.js';
import { initProfile } from './profile.js';
import './model_loader.js';
import './boost.js';
import './courage_trace.js';
import './courage_memories.js';

// Early window modal helpers so click handlers are bound immediately
window.openModelModal = function() {
  const m = document.getElementById('modal-model-picker');
  if (m) m.classList.add('open');
};

window.closeModelModal = function() {
  const m = document.getElementById('modal-model-picker');
  if (m) m.classList.remove('open');
};

window.openClusterModal = function() {
  const m = document.getElementById('modal-cluster-health');
  if (m) m.classList.add('open');
};

window.closeClusterModal = function() {
  const m = document.getElementById('modal-cluster-health');
  if (m) m.classList.remove('open');
};

window.openSettingsDrawer = function() {
  const m = document.getElementById('modal-settings-drawer');
  if (m) m.classList.add('open');
};

window.closeSettingsDrawer = function() {
  const m = document.getElementById('modal-settings-drawer');
  if (m) m.classList.remove('open');
};

window.openQuickCaptureModal = function(prefill) {
  const m = document.getElementById('modal-quick-capture');
  if (m) {
    m.classList.add('open');
    m.style.display = 'flex';
  }
};

window.closeQuickCaptureModal = function() {
  const m = document.getElementById('modal-quick-capture');
  if (m) {
    m.classList.remove('open');
    m.style.display = 'none';
  }
};

function bootstrapApp() {
  console.log('⚡ Initializing StoneSage Terminal Harness v3.0...');

  // 1. Initialize Accessible Theme (Deuteranopia Standard)
  try {
    initTheme();
    const themeSelect = document.getElementById('topbar-theme-select');
    const drawerThemeSelect = document.getElementById('drawer-theme-select');
    if (themeSelect) {
      themeSelect.addEventListener('change', (e) => {
        setTheme(e.target.value);
        if (drawerThemeSelect) drawerThemeSelect.value = e.target.value;
      });
    }
    if (drawerThemeSelect) {
      drawerThemeSelect.value = State.activeTheme || 'deu';
      drawerThemeSelect.addEventListener('change', (e) => {
        setTheme(e.target.value);
        if (themeSelect) themeSelect.value = e.target.value;
      });
    }
  } catch (err) {
    console.error('Failed to initTheme:', err);
  }

  // Mobile Topbar Quick Buttons (Direct Event Listeners)
  try {
    const mobilePingBtn = document.getElementById('mobile-ping-btn');
    if (mobilePingBtn) {
      mobilePingBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        window.openClusterModal();
      });
    }

    const mobileModelBtn = document.getElementById('mobile-model-btn');
    if (mobileModelBtn) {
      mobileModelBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        window.openModelModal();
      });
    }

    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    if (mobileMenuBtn) {
      mobileMenuBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        window.openSettingsDrawer();
      });
    }
  } catch (err) {
    console.error('Failed to bind mobile topbar buttons:', err);
  }

  // Drawer Node Continuum sync
  try {
    const drawerNodeSelect = document.getElementById('drawer-node-select');
    if (drawerNodeSelect) {
      drawerNodeSelect.addEventListener('change', (e) => {
        if (window.switchActiveNode) window.switchActiveNode(e.target.value);
      });
    }
  } catch (err) {
    console.error('Failed drawer node select listener:', err);
  }

  // Drawer Autonomy sync
  try {
    const drawerAutonomy = document.getElementById('drawer-autonomy-select');
    if (drawerAutonomy) {
      drawerAutonomy.addEventListener('change', (e) => {
        State.autonomyLevel = e.target.value;
        const wsAutonomy = document.getElementById('workstation-autonomy-select');
        if (wsAutonomy) wsAutonomy.value = e.target.value;
      });
    }
  } catch (err) {
    console.error('Failed drawer autonomy select listener:', err);
  }

  // 1b. Live hardware/model profile (fills every data-profile label)
  try {
    initProfile();
  } catch (err) {
    console.error('Failed initProfile:', err);
  }

  // 2. Initialize Durable Tab Navigation
  try {
    initNavigation();
  } catch (err) {
    console.error('Failed initNavigation:', err);
  }

  // 3. Load Chat History & Initialize Multimodal Chat Studio
  try {
    loadChatHistory();
    initChat();
  } catch (err) {
    console.error('Failed initChat:', err);
  }

  // 4. Initialize Interactive PTY Terminal
  try {
    initTerminal();
  } catch (err) {
    console.error('Failed initTerminal:', err);
  }

  // 5. Initialize Continuous Capacity & Harness Studio
  try {
    initHarness();
  } catch (err) {
    console.error('Failed initHarness:', err);
  }

  // 6. Initialize Fleet Matrix & Storage Fabric
  try {
    initFleet();
  } catch (err) {
    console.error('Failed initFleet:', err);
  }

  // 6b. Initialize Real Presence, Courage Computer & Landscape Cockpit
  try {
    fetchPresenceState();
    fetchGardenState();
    fetchAgentDnaList();
  } catch (err) {
    console.error('Failed init presence/garden/agentdna:', err);
  }

  // 6c. Initialize Workstation Ace Editor, Engine Studio & Engine Console
  try {
    initWorkstationAce();
    initEngineStudio();
    initEngineConsole();
  } catch (err) {
    console.error('Failed init workstation ace, engine studio, or engine console:', err);
  }

  // 7. Global Keyboard Navigation (F1 - F6, Ctrl+K)
  try {
    initGlobalKeyboardShortcuts();
  } catch (err) {
    console.error('Failed initGlobalKeyboardShortcuts:', err);
  }

  // 8. Register PWA Service Worker for Offline / Mobile Standalone Operation
  try {
    registerServiceWorker();
  } catch (err) {
    console.error('Failed registerServiceWorker:', err);
  }
}

// Durable Bootstrap: runs whether DOM is still loading, interactive, or complete
let appBootstrapped = false;
function safeBootstrap() {
  if (appBootstrapped) return;
  appBootstrapped = true;
  bootstrapApp();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', safeBootstrap);
} else {
  safeBootstrap();
}

/**
 * Keyboard Shortcuts
 */
function initGlobalKeyboardShortcuts() {
  window.addEventListener('keydown', (e) => {
    // Function keys F1 - F6 primary tab switching (with legacy fallbacks)
    const tabMap = {
      'F1': 'chat',
      'F2': 'workstation',
      'F3': 'terminal',
      'F4': 'engine',
      'F5': 'ha',
      'F6': 'obsidian',
      'F7': 'obsidian',
      'F8': 'ha',
      'F9': 'trainer',
      'F10': 'agentdna'
    };

    if (tabMap[e.key]) {
      e.preventDefault();
      switchTab(tabMap[e.key]);
      return;
    }

    // Ctrl+K to quick-focus active prompt or shell
    if (e.ctrlKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (State.activeTab === 'chat') {
        const input = document.getElementById('chat-prompt-input');
        if (input) input.focus();
      } else if (State.activeTab === 'terminal') {
        const screen = document.getElementById('terminal-screen');
        if (screen) screen.focus();
      }
    }
  });
}

/**
 * Global Out-of-band Nudge Breakout Modal
 */
window.triggerGlobalNudge = function() {
  const directive = prompt(
    "⚡ [OUT-OF-BAND NUDGE DIRECTIVE]\nBreak agent loops or log starvation non-destructively:\nEnter directive:",
    "Stop polling empty resources. Summarize progress and proceed to next milestone."
  );

  if (directive) {
    fetch('/api/harness/nudge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: 'coordinator', directive: directive })
    })
    .then(r => r.json())
    .then(res => {
      alert(`✅ [NUDGE INJECTED]: ${res.directive || directive}`);
    })
    .catch(err => {
      alert(`Nudge error: ${err.message}`);
    });
  }
};

/**
 * Register Service Worker for PWA
 */
function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js?v=4.0.3')
      .then(reg => {
        console.log('PWA Service Worker registered:', reg.scope);
        reg.update().catch(() => {});
      })
      .catch(err => {
        console.warn('PWA Service Worker registration skipped:', err);
      });
  }
}
