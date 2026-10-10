/**
 * StoneSage Workstation Ace Editor Controller
 * Lightweight server-hosted editor with 1:1 modern web IDE capabilities:
 * - Multi-file tabbed editor with dirty indicators
 * - Ace Editor syntax highlighting, line numbers, folding, bracket matching
 * - File tree right-click context menu (New File, New Folder, Rename, Delete)
 * - IDE status bar with cursor position, encoding, and syntax mode switcher
 * - Integrated terminal toggle
 */

import { State } from './state.js';

let aceEditor = null;
const openTabs = new Map(); // path -> { path, name, session, isDirty, originalContent }
let activeTabPath = null;

// Extension to Ace mode mapping
const MODE_MAP = {
  py: 'ace/mode/python',
  js: 'ace/mode/javascript',
  mjs: 'ace/mode/javascript',
  cjs: 'ace/mode/javascript',
  ts: 'ace/mode/javascript',
  json: 'ace/mode/json',
  html: 'ace/mode/html',
  htm: 'ace/mode/html',
  css: 'ace/mode/css',
  scss: 'ace/mode/css',
  md: 'ace/mode/markdown',
  markdown: 'ace/mode/markdown',
  sh: 'ace/mode/sh',
  bash: 'ace/mode/sh',
  zsh: 'ace/mode/sh',
  yaml: 'ace/mode/yaml',
  yml: 'ace/mode/yaml',
  txt: 'ace/mode/text',
  conf: 'ace/mode/sh',
  env: 'ace/mode/sh',
  log: 'ace/mode/text'
};

export function initWorkstationAce() {
  const container = document.getElementById('ace-editor');
  if (!container || !window.ace) {
    console.warn('[WorkstationAce] Container #ace-editor or window.ace not found.');
    return;
  }

  // Configure Ace paths
  try {
    ace.config.set('basePath', '/vendor/ace');
    ace.config.set('modePath', '/vendor/ace');
    ace.config.set('themePath', '/vendor/ace');
  } catch (e) {
    console.warn('[WorkstationAce] Error setting Ace paths:', e);
  }

  // Initialize Ace Editor
  aceEditor = ace.edit('ace-editor');
  aceEditor.setTheme('ace/theme/tomorrow_night');
  aceEditor.setFontSize(13);
  aceEditor.setShowPrintMargin(false);
  aceEditor.setHighlightActiveLine(true);
  aceEditor.setDisplayIndentGuides(true);
  aceEditor.session.setTabSize(4);
  aceEditor.session.setUseSoftTabs(true);
  aceEditor.session.setUseWrapMode(false);

  // Set initial blank session
  const defaultSession = new ace.EditSession('// Welcome to StoneSage Workstation IDE\n// Select a file from the Project Files tree on the left to start editing.\n', 'ace/mode/javascript');
  aceEditor.setSession(defaultSession);

  // Custom keybindings
  aceEditor.commands.addCommand({
    name: 'saveWorkstationFile',
    bindKey: { win: 'Ctrl-S', mac: 'Command-S' },
    exec: function() {
      window.saveWorkstationFile();
    }
  });

  aceEditor.commands.addCommand({
    name: 'closeActiveTab',
    bindKey: { win: 'Ctrl-W', mac: 'Command-W' },
    exec: function() {
      if (activeTabPath) closeTab(activeTabPath);
    }
  });

  // Track cursor movement for status bar
  aceEditor.selection.on('changeCursor', updateStatusBarPosition);

  // Wire theme change listener
  window.addEventListener('stonesage:theme-changed', (e) => {
    const theme = e.detail?.theme;
    if (theme === 'win98') {
      aceEditor.setTheme('ace/theme/github');
    } else {
      aceEditor.setTheme('ace/theme/tomorrow_night');
    }
  });

  // Wire global workstation API
  window.openFileInAce = openFileInAce;
  window.closeWorkstationTab = closeTab;
  window.saveWorkstationFile = saveActiveFile;
  window.reloadWorkstationFile = reloadActiveFile;
  window.toggleWorkstationDiff = toggleDiff;
  window.setAceLanguageMode = setLanguageMode;

  // Initialize Tree Context Menu
  initTreeContextMenu();

  console.log('⚡ [WorkstationAce] Lightweight Ace Editor initialized successfully.');
}

/**
 * Switch Workstation View Mode (Code Editor vs 3D Studio)
 */
export function setWorkstationViewMode(mode) {
  const wsViewport = document.getElementById('workstation-viewport');
  const ws3dPane = document.getElementById('workstation-3d-pane');
  const codeBtn = document.getElementById('ws-mode-btn-code');
  const d3Btn = document.getElementById('ws-mode-btn-3d');

  if (mode === '3d') {
    if (wsViewport) wsViewport.style.display = 'none';
    if (ws3dPane) ws3dPane.style.display = 'flex';
    if (codeBtn) codeBtn.classList.remove('active');
    if (d3Btn) d3Btn.classList.add('active');

    // Trigger 3D initialization and resize
    if (typeof window.initThreeViewport === 'function') {
      window.initThreeViewport();
    }
    if (typeof window.onViewportResize === 'function') {
      setTimeout(window.onViewportResize, 100);
    }
    if (typeof window.refresh3DGallery === 'function') {
      window.refresh3DGallery();
    }
  } else {
    if (wsViewport) wsViewport.style.display = 'flex';
    if (ws3dPane) ws3dPane.style.display = 'none';
    if (codeBtn) codeBtn.classList.add('active');
    if (d3Btn) d3Btn.classList.remove('active');
    if (aceEditor) aceEditor.resize();
  }
}
window.setWorkstationViewMode = setWorkstationViewMode;

/**
 * Open file in Ace Editor
 */
export async function openFileInAce(filePath) {
  if (!filePath) return;

  // Intercept 3D Models and auto-load into Workstation 3D Studio
  const ext = filePath.split('.').pop().toLowerCase();
  if (['glb', 'gltf', 'obj'].includes(ext)) {
    setWorkstationViewMode('3d');
    const fileUrl = `/api/workspace/file?path=${encodeURIComponent(filePath)}&raw=1`;
    const fileName = filePath.split('/').pop() || filePath;
    if (typeof window.inspectModel === 'function') {
      window.inspectModel(fileUrl, fileName);
    } else if (typeof window.loadModelIntoViewport === 'function') {
      window.loadModelIntoViewport(fileUrl, fileName);
    }
    const statusState = document.getElementById('ws-status-state');
    if (statusState) statusState.textContent = `3D View: ${fileName}`;
    return;
  }

  // If currently in 3D mode, switch back to code editor when opening a code file
  setWorkstationViewMode('code');

  if (!aceEditor) initWorkstationAce();

  State.activeFile = filePath;
  const titleEl = document.getElementById('workstation-active-file-title');
  const mobileIndicator = document.getElementById('ws-mobile-file-indicator');
  if (titleEl) titleEl.textContent = filePath;
  if (mobileIndicator) mobileIndicator.textContent = filePath.split('/').pop() || filePath;

  // Highlight active tree element
  document.querySelectorAll('.tree-file').forEach(el => {
    if (el.getAttribute('data-path') === filePath) {
      el.style.backgroundColor = 'var(--term-surface-dim)';
      el.style.color = 'var(--term-text-bright)';
    } else {
      el.style.backgroundColor = '';
      el.style.color = '';
    }
  });

  // Check if already open in tabs
  if (openTabs.has(filePath)) {
    activateTab(filePath);
    return;
  }

  // Ensure code editor is shown, not terminal
  const editorContainer = document.getElementById('workstation-editor-container');
  const termScreen = document.getElementById('workstation-term-screen');
  const toggleBtn = document.getElementById('ws-toggle-view-btn');
  if (editorContainer) editorContainer.style.display = 'flex';
  if (termScreen) termScreen.style.display = 'none';
  if (toggleBtn) toggleBtn.textContent = '[TERMINAL EMBED]';

  // Fetch file content
  try {
    const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(filePath)}`);
    const data = await res.json();
    if (data.ok) {
      const content = data.content || '';
      const ext = filePath.split('.').pop().toLowerCase();
      const mode = MODE_MAP[ext] || 'ace/mode/text';

      const session = new ace.EditSession(content, mode);
      session.setTabSize(4);
      session.setUseSoftTabs(true);
      session.setUseWrapMode(false);

      // Track dirty state
      const tabData = {
        path: filePath,
        name: filePath.split('/').pop() || filePath,
        session: session,
        isDirty: false,
        originalContent: content
      };

      session.on('change', () => {
        const cur = session.getValue();
        const dirty = cur !== tabData.originalContent;
        if (tabData.isDirty !== dirty) {
          tabData.isDirty = dirty;
          renderTabs();
          updateStatusBarState();
        }
        // Mirror to legacy hidden textarea for backward compatibility
        const legacyTa = document.getElementById('workstation-editor-textarea');
        if (legacyTa) legacyTa.value = cur;
      });

      openTabs.set(filePath, tabData);
      activateTab(filePath);
      renderTabs();

      // Switch to editor view automatically on mobile
      if (window.innerWidth <= 768 && typeof window.setWorkstationMobileMode === 'function') {
        window.setWorkstationMobileMode('editor');
      }
    } else {
      alert('Could not open file: ' + (data.error || 'Unknown error'));
    }
  } catch (e) {
    console.error('[WorkstationAce] Error fetching file:', e);
  }
}

/**
 * Activate existing tab
 */
function activateTab(filePath) {
  const tabData = openTabs.get(filePath);
  if (!tabData) return;

  activeTabPath = filePath;
  State.activeFile = filePath;
  aceEditor.setSession(tabData.session);
  aceEditor.focus();

  // Update title & status
  const titleEl = document.getElementById('workstation-active-file-title');
  if (titleEl) titleEl.textContent = filePath;

  renderTabs();
  updateStatusBarPosition();
  updateStatusBarMode();
  updateStatusBarState();

  // Update file stats
  const statsEl = document.getElementById('workstation-file-stats');
  if (statsEl) {
    const lines = tabData.session.getLength();
    const bytes = new Blob([tabData.session.getValue()]).size;
    statsEl.textContent = `(${lines} lines, ${formatByteCount(bytes)})`;
  }
}

/**
 * Close tab
 */
export function closeTab(filePath, event) {
  if (event) event.stopPropagation();
  const tabData = openTabs.get(filePath);
  if (!tabData) return;

  if (tabData.isDirty) {
    const ok = confirm(`Save changes to ${tabData.name} before closing?`);
    if (ok) {
      saveActiveFile();
    }
  }

  openTabs.delete(filePath);

  if (activeTabPath === filePath) {
    const remaining = Array.from(openTabs.keys());
    if (remaining.length > 0) {
      activateTab(remaining[remaining.length - 1]);
    } else {
      activeTabPath = null;
      State.activeFile = null;
      const blankSession = new ace.EditSession('', 'ace/mode/text');
      aceEditor.setSession(blankSession);
      const titleEl = document.getElementById('workstation-active-file-title');
      if (titleEl) titleEl.textContent = '(Click any file in tree to open)';
      const statsEl = document.getElementById('workstation-file-stats');
      if (statsEl) statsEl.textContent = '';
    }
  }

  renderTabs();
}

/**
 * Render tab bar
 */
function renderTabs() {
  const bar = document.getElementById('ws-tabs-bar');
  if (!bar) return;

  bar.innerHTML = '';
  openTabs.forEach((tabData, path) => {
    const tabEl = document.createElement('div');
    tabEl.className = `ws-tab ${path === activeTabPath ? 'active' : ''} ${tabData.isDirty ? 'dirty' : ''}`;
    tabEl.title = path;

    const nameSpan = document.createElement('span');
    nameSpan.className = 'ws-tab-name';
    nameSpan.textContent = tabData.name + (tabData.isDirty ? ' ●' : '');
    tabEl.appendChild(nameSpan);

    const closeBtn = document.createElement('button');
    closeBtn.className = 'ws-tab-close';
    closeBtn.innerHTML = '&times;';
    closeBtn.title = 'Close Tab (Ctrl+W)';
    closeBtn.onclick = (e) => closeTab(path, e);
    tabEl.appendChild(closeBtn);

    tabEl.onclick = () => activateTab(path);
    bar.appendChild(tabEl);
  });
}

/**
 * Save active file to disk
 */
export async function saveActiveFile() {
  if (!activeTabPath || !openTabs.has(activeTabPath)) {
    alert('No active file selected to save.');
    return;
  }

  const tabData = openTabs.get(activeTabPath);
  const content = tabData.session.getValue();
  const saveBtn = document.getElementById('ws-save-btn');
  const statsEl = document.getElementById('workstation-file-stats');

  if (saveBtn) saveBtn.textContent = 'SAVING...';

  try {
    const res = await fetch('/api/workspace/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: activeTabPath, content: content })
    });
    const data = await res.json();
    if (data.ok) {
      tabData.originalContent = content;
      tabData.isDirty = false;
      renderTabs();
      updateStatusBarState();

      if (saveBtn) {
        saveBtn.textContent = '✓ SAVED!';
        setTimeout(() => { saveBtn.textContent = '[💾 SAVE FILE]'; }, 1600);
      }
      if (statsEl) {
        const lines = tabData.session.getLength();
        statsEl.textContent = `(${lines} lines, ${formatByteCount(data.size || content.length)})`;
      }
    } else {
      alert('Failed to save file: ' + (data.error || 'Unknown error'));
      if (saveBtn) saveBtn.textContent = '[💾 SAVE FILE]';
    }
  } catch (e) {
    alert('Save request failed: ' + e);
    if (saveBtn) saveBtn.textContent = '[💾 SAVE FILE]';
  }
}

/**
 * Reload active file
 */
export async function reloadActiveFile() {
  if (activeTabPath) {
    openTabs.delete(activeTabPath);
    await openFileInAce(activeTabPath);
  }
}

/**
 * Git diff view
 */
export async function toggleDiff() {
  if (!activeTabPath || !openTabs.has(activeTabPath)) return;
  const tabData = openTabs.get(activeTabPath);
  const content = tabData.session.getValue();

  try {
    const res = await fetch('/api/workspace/diff', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: activeTabPath, content: content })
    });
    const data = await res.json();
    if (data.ok && data.diff) {
      alert(data.diff || 'No differences found against saved file.');
    } else {
      alert(data.message || 'Clean: File matches disk.');
    }
  } catch (e) {
    console.warn('Diff failed:', e);
  }
}

/**
 * Status Bar Updaters
 */
function updateStatusBarPosition() {
  const posEl = document.getElementById('ws-status-pos');
  if (!posEl || !aceEditor) return;
  const cursor = aceEditor.getCursorPosition();
  posEl.textContent = `Ln ${cursor.row + 1}, Col ${cursor.column + 1}`;
}

function updateStatusBarMode() {
  const modeSelect = document.getElementById('ws-mode-select');
  if (!modeSelect || !aceEditor) return;
  const modeId = aceEditor.session.getMode().$id || '';
  const simpleMode = modeId.split('/').pop() || 'text';
  modeSelect.value = simpleMode;
}

function updateStatusBarState() {
  const stateEl = document.getElementById('ws-status-state');
  if (!stateEl) return;
  if (activeTabPath && openTabs.has(activeTabPath)) {
    const isDirty = openTabs.get(activeTabPath).isDirty;
    if (isDirty) {
      stateEl.textContent = 'Unsaved ●';
      stateEl.style.color = 'var(--term-accent-gold)';
    } else {
      stateEl.textContent = 'Saved';
      stateEl.style.color = 'var(--term-success)';
    }
  } else {
    stateEl.textContent = 'Ready';
    stateEl.style.color = 'var(--term-text-muted)';
  }
}

export function setLanguageMode(mode) {
  if (!aceEditor) return;
  aceEditor.session.setMode(`ace/mode/${mode}`);
}

/**
 * File Explorer Tree Context Menu
 */
function initTreeContextMenu() {
  const treeContainer = document.getElementById('workstation-file-tree');
  if (!treeContainer) return;

  // Context Menu element
  let menu = document.getElementById('ws-tree-context-menu');
  if (!menu) {
    menu = document.createElement('div');
    menu.id = 'ws-tree-context-menu';
    menu.className = 'ws-context-menu';
    menu.style.display = 'none';
    menu.innerHTML = `
      <div class="ws-context-item" data-action="new_file">📄 New File</div>
      <div class="ws-context-item" data-action="new_folder">📁 New Folder</div>
      <div class="ws-context-divider"></div>
      <div class="ws-context-item" data-action="rename">✏️ Rename</div>
      <div class="ws-context-item ws-context-delete" data-action="delete">🗑️ Delete</div>
    `;
    document.body.appendChild(menu);
  }

  let contextTargetPath = null;
  let contextTargetType = 'directory';

  treeContainer.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const item = e.target.closest('.tree-item');
    if (item) {
      contextTargetPath = item.getAttribute('data-path') || '';
      contextTargetType = item.getAttribute('data-type') || 'file';
    } else {
      contextTargetPath = '';
      contextTargetType = 'directory';
    }

    menu.style.top = `${e.clientY}px`;
    menu.style.left = `${e.clientX}px`;
    menu.style.display = 'block';
  });

  // Close context menu on global click
  document.addEventListener('click', () => {
    if (menu) menu.style.display = 'none';
  });

  // Handle menu actions
  menu.addEventListener('click', async (e) => {
    const action = e.target.getAttribute('data-action');
    if (!action) return;

    if (action === 'new_file') {
      const fileName = prompt('Enter new file name (relative to workspace):', '');
      if (fileName) {
        const fullPath = contextTargetType === 'directory' && contextTargetPath
          ? `${contextTargetPath}/${fileName}`
          : fileName;
        await fetch('/api/workspace/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: fullPath, content: '' })
        });
        if (typeof window.loadWorkspaceFiles === 'function') window.loadWorkspaceFiles();
        openFileInAce(fullPath);
      }
    } else if (action === 'new_folder') {
      const folderName = prompt('Enter new directory name:', '');
      if (folderName) {
        const fullPath = contextTargetType === 'directory' && contextTargetPath
          ? `${contextTargetPath}/${folderName}`
          : folderName;
        await fetch('/api/workspace/mkdir', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: fullPath })
        });
        if (typeof window.loadWorkspaceFiles === 'function') window.loadWorkspaceFiles();
      }
    } else if (action === 'rename') {
      if (!contextTargetPath) return;
      const newName = prompt(`Rename ${contextTargetPath} to:`, contextTargetPath);
      if (newName && newName !== contextTargetPath) {
        await fetch('/api/workspace/rename', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ old_path: contextTargetPath, new_path: newName })
        });
        if (openTabs.has(contextTargetPath)) {
          closeTab(contextTargetPath);
          openFileInAce(newName);
        }
        if (typeof window.loadWorkspaceFiles === 'function') window.loadWorkspaceFiles();
      }
    } else if (action === 'delete') {
      if (!contextTargetPath) return;
      const ok = confirm(`Permanently delete ${contextTargetPath}?`);
      if (ok) {
        await fetch('/api/workspace/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: contextTargetPath })
        });
        if (openTabs.has(contextTargetPath)) {
          closeTab(contextTargetPath);
        }
        if (typeof window.loadWorkspaceFiles === 'function') window.loadWorkspaceFiles();
      }
    }
  });
}

function formatByteCount(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// Hook handleTreeItemClick to route directly to openFileInAce
window.handleTreeItemClick = function(filePath, type) {
  if (type === 'directory') return;
  openFileInAce(filePath);
};
