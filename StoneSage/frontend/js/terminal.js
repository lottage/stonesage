/**
 * StoneSage 1:1 PTY / SSH Interactive Terminal Client (v3.2)
 * Real-time bidirectional terminal over WebSocket powered by vendored xterm.js and FitAddon.
 * Full VT/ANSI support for nano, vim, btop, tmux, arrow keys, and interactive shells.
 */

import { State, safeStorage } from './state.js';

let termSocket = null;
let reconnectTimer = null;
let reconnectDelay = 1000;

let mainTerm = null;
let fitAddonMain = null;

let wsTerm = null;
let fitAddonWs = null;

let isMounting = false;
let rawScrollbackBuffer = '';
let terminalBufferFallback = '';

/**
 * Maps StoneSage UI themes into xterm.js theme configurations.
 */
export function getXtermTheme(themeName) {
  switch (themeName) {
    case 'win98':
      return {
        background: '#000000',
        foreground: '#c0c0c0',
        cursor: '#ffffff',
        cursorAccent: '#000000',
        selectionBackground: '#000080',
        black: '#000000',
        red: '#800000',
        green: '#008000',
        yellow: '#808000',
        blue: '#000080',
        magenta: '#800080',
        cyan: '#008080',
        white: '#c0c0c0',
        brightBlack: '#808080',
        brightRed: '#ff0000',
        brightGreen: '#00ff00',
        brightYellow: '#ffff00',
        brightBlue: '#0000ff',
        brightMagenta: '#ff00ff',
        brightCyan: '#00ffff',
        brightWhite: '#ffffff'
      };
    case 'crt-green':
      return {
        background: '#080d09',
        foreground: '#00ff66',
        cursor: '#00ff66',
        cursorAccent: '#080d09',
        selectionBackground: 'rgba(0, 255, 102, 0.35)',
        black: '#080d09',
        red: '#ff5555',
        green: '#00ff66',
        yellow: '#ffdd44',
        blue: '#00cc55',
        magenta: '#ff77aa',
        cyan: '#00ffaa',
        white: '#b3ffcc',
        brightBlack: '#0f1c12',
        brightRed: '#ff7777',
        brightGreen: '#33ff88',
        brightYellow: '#ffee77',
        brightBlue: '#22ff77',
        brightMagenta: '#ffaacc',
        brightCyan: '#66ffcc',
        brightWhite: '#ffffff'
      };
    case 'crt-amber':
      return {
        background: '#0d0903',
        foreground: '#ffb000',
        cursor: '#ffb000',
        cursorAccent: '#0d0903',
        selectionBackground: 'rgba(255, 176, 0, 0.35)',
        black: '#0d0903',
        red: '#ff6633',
        green: '#ffb000',
        yellow: '#ffcc33',
        blue: '#ff9900',
        magenta: '#ff8844',
        cyan: '#ffbb33',
        white: '#ffe6b3',
        brightBlack: '#1c1407',
        brightRed: '#ff8855',
        brightGreen: '#ffc433',
        brightYellow: '#ffdd66',
        brightBlue: '#ffaa22',
        brightMagenta: '#ffaa66',
        brightCyan: '#ffcc55',
        brightWhite: '#ffffff'
      };
    case 'trit':
      return {
        background: '#0c171c',
        foreground: '#e2e8f0',
        cursor: '#26d6d8',
        cursorAccent: '#0c171c',
        selectionBackground: '#009e9f',
        black: '#0c171c',
        red: '#d9383a',
        green: '#26d6d8',
        yellow: '#f6a5b8',
        blue: '#009e9f',
        magenta: '#e06c75',
        cyan: '#26d6d8',
        white: '#ffffff',
        brightBlack: '#1a323d',
        brightRed: '#e06c75',
        brightGreen: '#4ade80',
        brightYellow: '#fde047',
        brightBlue: '#38bdf8',
        brightMagenta: '#d8b4fe',
        brightCyan: '#5eead4',
        brightWhite: '#ffffff'
      };
    case 'deu':
    default:
      return {
        background: '#0c0f13',
        foreground: '#f8fafc',
        cursor: '#fbb829',
        cursorAccent: '#0c0f13',
        selectionBackground: '#2b7de9',
        black: '#12151a',
        red: '#f87171',
        green: '#4ade80',
        yellow: '#fbb829',
        blue: '#60a5fa',
        magenta: '#c084fc',
        cyan: '#2dd4bf',
        white: '#f8fafc',
        brightBlack: '#242d3d',
        brightRed: '#fca5a5',
        brightGreen: '#86efac',
        brightYellow: '#fde047',
        brightBlue: '#93c5fd',
        brightMagenta: '#d8b4fe',
        brightCyan: '#5eead4',
        brightWhite: '#ffffff'
      };
  }
}

/**
 * Asynchronously ensures xterm.js and FitAddon are loaded into window.
 */
export async function ensureXtermLoaded() {
  if (typeof window.Terminal !== 'undefined' && typeof window.FitAddon !== 'undefined') {
    return true;
  }

  // Inject script tags if missing
  if (!document.getElementById('xterm-core-script')) {
    const s = document.createElement('script');
    s.id = 'xterm-core-script';
    s.src = '/vendor/xterm/xterm.js?v=3.7.6';
    document.head.appendChild(s);
  }
  if (!document.getElementById('xterm-fit-script')) {
    const s = document.createElement('script');
    s.id = 'xterm-fit-script';
    s.src = '/vendor/xterm/xterm-addon-fit.js?v=3.7.6';
    document.head.appendChild(s);
  }
  if (!document.getElementById('xterm-css-link')) {
    const l = document.createElement('link');
    l.id = 'xterm-css-link';
    l.rel = 'stylesheet';
    l.href = '/vendor/xterm/xterm.css?v=3.7.6';
    document.head.appendChild(l);
  }

  // Poll for up to 3 seconds
  for (let i = 0; i < 60; i++) {
    if (typeof window.Terminal !== 'undefined' && typeof window.FitAddon !== 'undefined') {
      return true;
    }
    await new Promise(r => setTimeout(r, 50));
  }

  return typeof window.Terminal !== 'undefined';
}

/**
 * Helper to construct an xterm instance with FitAddon attached.
 */
function createTerminalInstance(containerEl) {
  if (typeof window.Terminal === 'undefined') {
    return null;
  }

  const isMobile = window.innerWidth <= 768;
  const term = new window.Terminal({
    fontFamily: "Consolas, 'JetBrains Mono', 'Fira Code', 'Courier New', monospace",
    fontSize: isMobile ? 12 : 14,
    lineHeight: 1.25,
    cursorBlink: true,
    convertEol: true,
    scrollback: 10000,
    theme: getXtermTheme(State.activeTheme || 'deu'),
    allowProposedApi: true
  });

  let fitAddon = null;
  if (window.FitAddon) {
    const FitClass = window.FitAddon.FitAddon || window.FitAddon;
    fitAddon = new FitClass();
    term.loadAddon(fitAddon);
  }

  // Clear container before opening xterm
  containerEl.innerHTML = '';
  term.open(containerEl);

  // Send keystrokes directly to PTY daemon over WebSocket
  term.onData(data => {
    sendTerminalInput(data);
  });

  // Allow app & browser shortcuts to pass through
  term.attachCustomKeyEventHandler(e => {
    // Let F1-F12 navigate StoneSage tabs or browser shortcuts
    if (e.key && e.key.startsWith('F') && e.key.length <= 3) {
      return false;
    }
    // Allow Ctrl+R (reload) and Ctrl+Shift+I / Ctrl+Shift+C (devtools)
    if (e.ctrlKey && (e.key === 'r' || e.key === 'R')) return false;
    if (e.ctrlKey && e.shiftKey && (e.key === 'I' || e.key === 'i' || e.key === 'C' || e.key === 'c')) return false;
    return true;
  });

  return { term, fitAddon };
}

/**
 * Lazily mounts xterm into DOM elements as soon as xterm scripts are ready.
 */
export async function mountTerminalIfReady() {
  if (isMounting) return Boolean(mainTerm);
  isMounting = true;

  try {
    const loaded = await ensureXtermLoaded();
    if (!loaded) return false;

    const termScreen = document.getElementById('terminal-screen');
    const wsTermScreen = document.getElementById('workstation-term-screen');

    if (termScreen && !mainTerm) {
      const res = createTerminalInstance(termScreen);
      if (res) {
        mainTerm = res.term;
        fitAddonMain = res.fitAddon;
        if (rawScrollbackBuffer) {
          mainTerm.write(rawScrollbackBuffer);
        }
        setTimeout(() => sendTerminalResize(), 50);
      }
    }

    if (wsTermScreen && !wsTerm) {
      const res = createTerminalInstance(wsTermScreen);
      if (res) {
        wsTerm = res.term;
        fitAddonWs = res.fitAddon;
        if (rawScrollbackBuffer) {
          wsTerm.write(rawScrollbackBuffer);
        }
      }
    }

    return Boolean(mainTerm);
  } finally {
    isMounting = false;
  }
}

/**
 * Initialize the Terminal subsystems.
 */
export function initTerminal() {
  const connectBtn = document.getElementById('term-connect-btn');
  const clearBtn = document.getElementById('term-clear-btn');

  // Attempt initial mount immediately
  mountTerminalIfReady();

  if (connectBtn) {
    connectBtn.addEventListener('click', () => {
      if (State.terminal.connected) {
        disconnectTerminal();
      } else {
        connectTerminal();
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      if (mainTerm) mainTerm.clear();
      if (wsTerm) wsTerm.clear();
      rawScrollbackBuffer = '';
      terminalBufferFallback = '';
      const termScreen = document.getElementById('terminal-screen');
      const wsTermScreen = document.getElementById('workstation-term-screen');
      if (termScreen && !mainTerm) termScreen.innerHTML = '';
      if (wsTermScreen && !wsTerm) wsTermScreen.innerHTML = '';
      window.sendTerminalCommand('clear');
    });
  }

  // Workstation Quick Tool Launchers
  const nanoBtn = document.getElementById('ws-tool-nano');
  const microBtn = document.getElementById('ws-tool-micro');
  const lsBtn = document.getElementById('ws-tool-ls');
  const gitBtn = document.getElementById('ws-tool-git');
  const testBtn = document.getElementById('ws-tool-test');
  const openNanoBtn = document.getElementById('ws-open-nano-btn');
  const toggleEditorBtn = document.getElementById('ws-toggle-editor-btn');
  const editorTextarea = document.getElementById('workstation-editor-textarea');

  if (nanoBtn) nanoBtn.addEventListener('click', () => openFileInTerminal(State.activeFile || '', 'nano'));
  if (microBtn) microBtn.addEventListener('click', () => openFileInTerminal(State.activeFile || '', 'micro'));
  if (openNanoBtn) openNanoBtn.addEventListener('click', () => openFileInTerminal(State.activeFile || '', 'nano'));

  if (lsBtn) {
    lsBtn.addEventListener('click', () => {
      const wsPath = State.activeWorkspace ? State.activeWorkspace.path : '';
      const cmd = wsPath ? `cd "${wsPath}" ; ls -la` : 'ls -la';
      window.sendTerminalCommand(cmd);
    });
  }

  if (gitBtn) {
    gitBtn.addEventListener('click', () => {
      const wsPath = State.activeWorkspace ? State.activeWorkspace.path : '';
      const cmd = wsPath ? `cd "${wsPath}" ; git status` : 'git status';
      window.sendTerminalCommand(cmd);
    });
  }

  if (testBtn) {
    testBtn.addEventListener('click', () => {
      const wsPath = State.activeWorkspace ? State.activeWorkspace.path : '';
      const cmd = wsPath ? `cd "${wsPath}" ; python -m unittest` : 'python -m unittest';
      window.sendTerminalCommand(cmd);
    });
  }

  if (toggleEditorBtn && editorTextarea) {
    const wsTermScreen = document.getElementById('workstation-term-screen');
    toggleEditorBtn.addEventListener('click', () => {
      if (editorTextarea.style.display === 'none' || !editorTextarea.style.display) {
        editorTextarea.style.display = 'block';
        if (wsTermScreen) wsTermScreen.style.display = 'none';
        toggleEditorBtn.textContent = '[SHOW TERMINAL]';
      } else {
        editorTextarea.style.display = 'none';
        if (wsTermScreen) wsTermScreen.style.display = 'block';
        toggleEditorBtn.textContent = '[SHOW RAW FILE]';
        mountTerminalIfReady().then(() => {
          setTimeout(() => {
            sendTerminalResize();
            if (!isMobileOrTouch()) {
              focusTerminal();
            }
          }, 50);
        });
      }
    });
  }

  // Listen to tab switches to mount, connect, and fit on demand
  window.addEventListener('stonesage:tab-switched', (e) => {
    if (e.detail.tab === 'terminal' || e.detail.tab === 'workstation') {
      mountTerminalIfReady();
      if (!State.terminal.connected) {
        connectTerminal();
      }
      setTimeout(() => {
        sendTerminalResize();
        if (!isMobileOrTouch()) {
          focusTerminal();
        }
      }, 60);
    }
  });

  // Listen to StoneSage theme changes
  window.addEventListener('stonesage:theme-changed', (e) => {
    const newTheme = getXtermTheme(e.detail.theme);
    if (mainTerm) mainTerm.options.theme = newTheme;
    if (wsTerm) wsTerm.options.theme = newTheme;
  });

  // Window resize calculation
  window.addEventListener('resize', () => {
    sendTerminalResize();
  });
}

/**
 * Get or initialize roaming terminal session ID.
 */
export function getTermSessionId() {
  let sid = safeStorage.getItem('stonesage_term_session_id');
  if (!sid) {
    sid = 'term_' + Math.random().toString(36).substring(2, 10);
    safeStorage.setItem('stonesage_term_session_id', sid);
  }
  return sid;
}

/**
 * Send raw input string to PTY over WebSocket.
 */
function sendTerminalInput(data) {
  if (termSocket && termSocket.readyState === WebSocket.OPEN) {
    termSocket.send(JSON.stringify({
      type: 'input',
      session_id: getTermSessionId(),
      data: data
    }));
  }
}

/**
 * Establish WebSocket connection to StoneSage PTY Daemon (:8088).
 */
export function connectTerminal() {
  if (termSocket && termSocket.readyState === WebSocket.OPEN) return;

  mountTerminalIfReady();

  const statusEl = document.getElementById('terminal-status-badge');
  if (statusEl) {
    statusEl.className = 'status-badge';
    statusEl.innerHTML = '<span class="status-dot"></span> CONNECTING...';
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  let wsHost = window.location.host;
  if (window.location.port === '8080' || window.location.port === '8888') {
    wsHost = `${window.location.hostname}:8088`;
  }
  const wsUrl = `${protocol}//${wsHost}/`;

  try {
    termSocket = new WebSocket(wsUrl);

    termSocket.onopen = () => {
      State.terminal.connected = true;
      reconnectDelay = 1000;
      if (statusEl) {
        statusEl.className = 'status-badge online';
        statusEl.innerHTML = '<span class="status-dot"></span> PTY: CONNECTED';
      }
      const connectBtn = document.getElementById('term-connect-btn');
      if (connectBtn) connectBtn.textContent = '[DISCONNECT]';

      const nodeSelect = document.getElementById('topbar-node-select');
      const nodeVal = nodeSelect ? nodeSelect.value : State.activeNode;
      // host comes from the configured harness instance (data-host set by fleet.js), never from code
      const opt = nodeSelect ? nodeSelect.selectedOptions[0] : null;
      let targetHost = (opt && opt.dataset.host) || null;
      if (targetHost === '127.0.0.1' || targetHost === 'localhost') targetHost = null;

      termSocket.send(JSON.stringify({
        type: 'terminal_init',
        session_id: getTermSessionId(),
        target_host: targetHost
      }));

      mountTerminalIfReady().then(() => {
        setTimeout(() => {
          sendTerminalResize();
          if (!isMobileOrTouch()) {
            focusTerminal();
          }
        }, 80);
      });
    };

    termSocket.onmessage = (event) => {
      let rawData = '';
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'scrollback' && msg.data) {
          rawData = msg.data;
          rawScrollbackBuffer = rawData;
          if (mainTerm) {
            mainTerm.clear();
            mainTerm.write(rawData);
          }
          if (wsTerm) {
            wsTerm.clear();
            wsTerm.write(rawData);
          }
        } else if (msg.type === 'output' && msg.data) {
          rawData = msg.data;
          rawScrollbackBuffer += rawData;
          if (rawScrollbackBuffer.length > 500000) {
            rawScrollbackBuffer = rawScrollbackBuffer.slice(-250000);
          }
          if (mainTerm) mainTerm.write(rawData);
          if (wsTerm) wsTerm.write(rawData);
        } else if (msg.type === 'ping') {
          termSocket.send(JSON.stringify({ type: 'pong' }));
          return;
        }
      } catch (_) {
        rawData = event.data;
        rawScrollbackBuffer += rawData;
        if (mainTerm) mainTerm.write(rawData);
        if (wsTerm) wsTerm.write(rawData);
      }

      // If xterm is not yet mounted, update sanitized fallback and trigger mounting
      if (!mainTerm && rawData) {
        appendTerminalOutputFallback(rawData);
        mountTerminalIfReady();
      }
    };

    termSocket.onclose = () => {
      State.terminal.connected = false;
      if (statusEl) {
        statusEl.className = 'status-badge offline';
        statusEl.innerHTML = '<span class="status-dot"></span> PTY: DISCONNECTED';
      }
      const connectBtn = document.getElementById('term-connect-btn');
      if (connectBtn) connectBtn.textContent = '[CONNECT SHELL]';

      // Roaming Auto-reconnect if terminal tab is active
      if (State.activeTab === 'terminal') {
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => {
          reconnectDelay = Math.min(reconnectDelay * 1.5, 10000);
          connectTerminal();
        }, reconnectDelay);
      }
    };

    termSocket.onerror = (err) => {
      console.warn('Terminal WebSocket error:', err);
    };

  } catch (e) {
    console.error('Failed to open terminal socket:', e);
  }
}

/**
 * Disconnect active terminal session.
 */
export function disconnectTerminal() {
  clearTimeout(reconnectTimer);
  if (termSocket) {
    termSocket.close();
    termSocket = null;
  }
  State.terminal.connected = false;
}

/**
 * Accurately calculate terminal geometry and send SIGWINCH resize request to backend.
 */
export function sendTerminalResize() {
  if (!termSocket || termSocket.readyState !== WebSocket.OPEN) return;

  const isWs = State.activeTab === 'workstation';
  const activeScreen = isWs 
    ? document.getElementById('workstation-term-screen') 
    : document.getElementById('terminal-screen');

  if (!activeScreen || activeScreen.offsetParent === null || activeScreen.clientWidth === 0) {
    return; // Container is currently hidden
  }

  if (isWs && fitAddonWs && wsTerm) {
    try {
      fitAddonWs.fit();
      termSocket.send(JSON.stringify({
        type: 'resize',
        session_id: getTermSessionId(),
        cols: wsTerm.cols,
        rows: wsTerm.rows
      }));
      return;
    } catch (e) {
      console.warn('fitAddonWs error:', e);
    }
  }

  if (!isWs && fitAddonMain && mainTerm) {
    try {
      fitAddonMain.fit();
      termSocket.send(JSON.stringify({
        type: 'resize',
        session_id: getTermSessionId(),
        cols: mainTerm.cols,
        rows: mainTerm.rows
      }));
      return;
    } catch (e) {
      console.warn('fitAddonMain error:', e);
    }
  }

  // Fallback dimension calculation
  const cols = Math.max(Math.floor(activeScreen.clientWidth / 8.5), 40);
  const rows = Math.max(Math.floor(activeScreen.clientHeight / 18), 10);
  termSocket.send(JSON.stringify({
    type: 'resize',
    session_id: getTermSessionId(),
    cols: cols,
    rows: rows
  }));
}
window.sendTerminalResize = sendTerminalResize;

/**
 * Detect mobile or touch-primary device to prevent intrusive virtual keyboard popups.
 */
export function isMobileOrTouch() {
  if (typeof window === 'undefined') return false;
  if (window.innerWidth <= 768) return true;
  if (window.matchMedia && window.matchMedia('(hover: none) and (pointer: coarse)').matches) return true;
  if (('ontouchstart' in window || (navigator && navigator.maxTouchPoints > 0)) && window.innerWidth <= 1024) return true;
  return false;
}
window.isMobileOrTouch = isMobileOrTouch;

/**
 * Focus the currently visible terminal screen.
 * @param {boolean} force - If true, force focus even on mobile/touch devices.
 */
export function focusTerminal(force = false) {
  // On mobile / touch screens, never programmatically focus terminal textarea
  // unless explicitly requested, because focusing an input forces the OS virtual keyboard to pop up.
  if (!force && isMobileOrTouch()) {
    return;
  }
  if (State.activeTab === 'workstation') {
    if (wsTerm) {
      wsTerm.focus();
    } else {
      const wsScreen = document.getElementById('workstation-term-screen');
      if (wsScreen) wsScreen.focus();
    }
  } else {
    if (mainTerm) {
      mainTerm.focus();
    } else {
      const screen = document.getElementById('terminal-screen');
      if (screen) screen.focus();
    }
  }
}
window.focusTerminal = focusTerminal;

/**
 * Open file in terminal editor (nano, micro, vim).
 */
export function openFileInTerminal(filePath, editor = 'nano') {
  const wsPath = State.activeWorkspace ? State.activeWorkspace.path : '';
  let cmd = '';

  if (wsPath) {
    if (filePath) {
      cmd = `cd "${wsPath}" ; ${editor} "${filePath}"`;
    } else {
      cmd = `cd "${wsPath}" ; ${editor}`;
    }
  } else if (filePath) {
    cmd = `${editor} "${filePath}"`;
  } else {
    cmd = editor;
  }

  // Ensure terminal view is visible if raw editor was toggled
  const wsTermScreen = document.getElementById('workstation-term-screen');
  const wsEditorContainer = document.getElementById('workstation-editor-container');
  const wsToggleBtn = document.getElementById('ws-toggle-view-btn');

  if (wsTermScreen && wsEditorContainer && wsEditorContainer.style.display !== 'none') {
    wsEditorContainer.style.display = 'none';
    wsTermScreen.style.display = 'block';
    if (wsToggleBtn) wsToggleBtn.textContent = '[CODE EDITOR]';
  }

  window.sendTerminalCommand(cmd);
}
window.openFileInTerminal = openFileInTerminal;

/**
 * Quick Action Commands.
 */
window.sendTerminalCommand = function(cmd, autoFocus = true) {
  if (!termSocket || termSocket.readyState !== WebSocket.OPEN) {
    connectTerminal();
    setTimeout(() => {
      sendTerminalInput(cmd + '\r');
      if (isMobileOrTouch()) {
        if (document.activeElement && typeof document.activeElement.blur === 'function') {
          document.activeElement.blur();
        }
      } else if (autoFocus) {
        focusTerminal();
      }
    }, 600);
    return;
  }
  sendTerminalInput(cmd + '\r');

  if (isMobileOrTouch()) {
    // Dismiss virtual keyboard if open and never auto-focus on mobile
    if (document.activeElement && typeof document.activeElement.blur === 'function') {
      document.activeElement.blur();
    }
  } else if (autoFocus) {
    focusTerminal();
  }
};

/**
 * Mobile Terminal Touch Keys Handler.
 * Supports bash history navigation, saving (^O), exiting (^X), search (^W), and control signals.
 */
window.sendTerminalKey = function(keyType) {
  if (!termSocket || termSocket.readyState !== WebSocket.OPEN) {
    connectTerminal();
    setTimeout(() => window.sendTerminalKey(keyType), 500);
    return;
  }

  const keyMap = {
    'ctrl_c': '\x03',
    'ctrl_d': '\x04',
    'ctrl_x': '\x18',  // nano exit
    'ctrl_o': '\x0f',  // nano writeout / save
    'ctrl_w': '\x17',  // nano search
    'ctrl_k': '\x0b',  // nano cut
    'ctrl_u': '\x15',  // nano paste
    'ctrl_g': '\x07',  // nano help
    'enter': '\r',
    'tab': '\t',
    'up': '\x1b[A',
    'down': '\x1b[B',
    'right': '\x1b[C',
    'left': '\x1b[D',
    'esc': '\x1b',
    'backspace': '\x7f'
  };

  const data = keyMap[keyType] || keyType;
  sendTerminalInput(data);

  if (isMobileOrTouch()) {
    // Ensure quick touch buttons don't leave soft keyboard or textareas focused
    if (document.activeElement && typeof document.activeElement.blur === 'function') {
      document.activeElement.blur();
    }
  }
};

/**
 * Mobile Terminal Form Submit Handler.
 */
window.handleMobileTerminalSubmit = function(e) {
  if (e) e.preventDefault();
  const input = document.getElementById('terminal-mobile-input');
  if (!input) return;
  const val = input.value;
  if (!val) {
    window.sendTerminalKey('enter');
    return;
  }

  if (!termSocket || termSocket.readyState !== WebSocket.OPEN) {
    connectTerminal();
    setTimeout(() => {
      sendTerminalInput(val + '\r');
    }, 600);
  } else {
    sendTerminalInput(val + '\r');
  }
  input.value = '';
  input.focus();
};

/**
 * Strips raw ANSI control codes so they don't appear as literal characters like [K[A[C on screen.
 */
function cleanAnsiForDisplay(text) {
  if (!text) return '';
  return text
    // Strip cursor movement, line clears, DEC private modes
    .replace(/\x1b\[\?[0-9;]*[a-zA-Z]/g, '')
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '')
    .replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, '')
    .replace(/\x1b[\(\)][0-9a-zA-Z]/g, '')
    .replace(/\x1b[=>]/g, '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n');
}

/**
 * Sanitized text-stream fallback if xterm.js is still mounting or loading.
 */
function appendTerminalOutputFallback(rawText) {
  const cleaned = cleanAnsiForDisplay(rawText);
  terminalBufferFallback += cleaned;
  if (terminalBufferFallback.length > 500000) {
    terminalBufferFallback = terminalBufferFallback.slice(-250000);
  }
  const screen = document.getElementById('terminal-screen');
  const wsScreen = document.getElementById('workstation-term-screen');
  if (screen && !mainTerm) {
    screen.textContent = terminalBufferFallback;
    screen.scrollTop = screen.scrollHeight;
  }
  if (wsScreen && !wsTerm) {
    wsScreen.textContent = terminalBufferFallback;
    wsScreen.scrollTop = wsScreen.scrollHeight;
  }
}
