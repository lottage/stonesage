/**
 * StoneSage Terminal State & Persistence Engine (v3.0)
 * Eliminates F1 tab reset bug, preserves reasoning traces, and manages accessible themes.
 */

export const State = {
  activeTab: 'chat',
  activeTheme: 'deu',
  activeNode: 'local',
  activeModel: 'coordinator',
  activeSessionId: 'default',
  activeWorkspace: null,
  activeFile: null,
  activeProjectAgent: null,
  workspaces: [],
  workspaceRoots: [],
  chatSessions: [],
  autonomyLevel: 'tiered',
  chat: {
    messages: [],
    isGenerating: false,
    stagedImages: [],
    abortController: null,
  },
  harness: {
    contextLength: 8192,
    slots: 2,
    arch: '9b',
    quant: 'q4_k_m',
    vram: 12.0,
    estimate: null,
  },
  terminal: {
    connected: false,
    socket: null,
  },
  ha: {
    all: [],
    filter: 'curated',
    searchQuery: '',
    customizations: {}
  }
};

export const safeStorage = {
  getItem(key, fallback = null) {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        const val = window.localStorage.getItem(key);
        return val !== null ? val : fallback;
      }
    } catch (_) {}
    return fallback;
  },
  setItem(key, value) {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        window.localStorage.setItem(key, value);
        return true;
      }
    } catch (_) {}
    return false;
  },
  removeItem(key) {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        window.localStorage.removeItem(key);
        return true;
      }
    } catch (_) {}
    return false;
  }
};

/**
 * Initialize Theme
 * Enforces Deuteranopia ('deu') as standard default.
 */
export function initTheme() {
  const saved = safeStorage.getItem('stonesage_theme', 'deu');
  setTheme(saved);
  const select = document.getElementById('topbar-theme-select');
  if (select) select.value = saved;
}

export function setTheme(themeName) {
  const validThemes = ['deu', 'trit', 'win98', 'crt-green', 'crt-amber'];
  const theme = validThemes.includes(themeName) ? themeName : 'deu';
  document.documentElement.setAttribute('data-theme', theme);
  document.body.setAttribute('data-theme', theme);
  State.activeTheme = theme;
  safeStorage.setItem('stonesage_theme', theme);
  window.dispatchEvent(new CustomEvent('stonesage:theme-changed', { detail: { theme } }));
}

/**
 * Tab Navigation with URL Hash & LocalStorage Persistence
 * Prevents tab reset on app switch or refresh!
 */
export function initNavigation() {
  // 1. Determine starting tab: URL hash takes priority, then localStorage, then 'chat'
  let initialTab = 'chat';
  if (window.location.hash) {
    const hash = window.location.hash.replace('#/', '').replace('#', '').trim();
    if (hash) initialTab = hash;
  } else {
    const saved = safeStorage.getItem('stonesage_active_tab');
    if (saved) initialTab = saved;
  }

  // 2. Attach click listeners to all tab buttons
  document.querySelectorAll('.nav-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;
      if (target) switchTab(target);
    });
  });

  // 3. Listen to browser hash changes & visibility changes (mobile app backgrounding/resuming)
  window.addEventListener('hashchange', () => {
    const hash = window.location.hash.replace('#/', '').replace('#', '').trim();
    if (hash && hash !== State.activeTab) {
      switchTab(hash, false);
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      // Re-assert active tab on mobile un-pause
      const hash = window.location.hash.replace('#/', '').replace('#', '').trim();
      if (hash && hash !== State.activeTab) {
        switchTab(hash, false);
      }
    }
  });

  // 4. Activate initial tab
  switchTab(initialTab, true);
}

export function switchTab(tabId, updateUrl = true) {
  // Map legacy tabs into Engine Studio layers
  const engineLayers = ['fleet', 'harness', 'sampling', 'console', 'agentdna', 'memory', 'trainer'];
  let targetTab = tabId;
  let targetLayer = null;

  if (tabId === 'gallery3d') {
    targetTab = 'workstation';
    if (typeof window.setWorkstationViewMode === 'function') {
      window.setWorkstationViewMode('3d');
    }
  } else if (engineLayers.includes(tabId)) {
    targetTab = 'engine';
    targetLayer = (tabId === 'harness' || tabId === 'sampling' ? 'console' : tabId);
  }

  const btn = document.querySelector(`.nav-tab-btn[data-tab="${targetTab}"]`);
  const pane = document.getElementById(`view-${targetTab}`);
  if (!pane) {
    // Fallback to chat if invalid tab
    if (targetTab !== 'chat') switchTab('chat', updateUrl);
    return;
  }

  // Update tabs
  document.querySelectorAll('.nav-tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.view-pane').forEach(p => p.classList.remove('active'));

  if (btn) btn.classList.add('active');
  pane.classList.add('active');

  State.activeTab = targetTab;

  // Persist to localStorage and URL hash
  safeStorage.setItem('stonesage_active_tab', targetTab);

  if (targetLayer && typeof window.switchEngineLayer === 'function') {
    window.switchEngineLayer(targetLayer);
  }

  if (updateUrl && window.location.hash !== `#/${targetTab}`) {
    history.replaceState(null, '', `#/${targetTab}`);
  }

  // Dispatch custom tab switch event for view controllers
  window.dispatchEvent(new CustomEvent('stonesage:tab-switched', { detail: { tab: targetTab, layer: targetLayer } }));
}

// Global window binding for tab switching
window.switchTab = switchTab;

/**
 * Chat History Persistence
 * Stores messages WITH complete reasoning traces permanently.
 */
export function loadChatHistory() {
  try {
    const raw = safeStorage.getItem('stonesage_chat_history_v3');
    if (raw) {
      State.chat.messages = JSON.parse(raw);
    }
  } catch (e) {
    console.warn('Failed to load chat history:', e);
    State.chat.messages = [];
  }
  return State.chat.messages;
}

export function saveChatHistory() {
  try {
    // Keep up to 100 turns in durable local storage
    const trimmed = State.chat.messages.slice(-100);
    safeStorage.setItem('stonesage_chat_history_v3', JSON.stringify(trimmed));
  } catch (e) {
    console.warn('Failed to save chat history:', e);
  }
}

export function clearChatHistory() {
  State.chat.messages = [];
  safeStorage.removeItem('stonesage_chat_history_v3');
}

export function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

