/**
 * StoneSage Multimodal Chat & Vision Controller (v3.0)
 * Handles drag-and-drop/paste images, token streaming, reasoning trace accordions, and inline nudging.
 */

import { State, saveChatHistory, safeStorage } from './state.js';
import { engineLabel } from './profile.js';
import { wavEnvelope, envelopeAt, attachMic, createListenerCore, looksLikeEcho } from './convo_audio.js';

let autoScrollChat = true;

export function initChat() {
  refreshBargeButton();
  const promptInput = document.getElementById('chat-prompt-input');
  const sendBtn = document.getElementById('chat-send-btn');
  const stopBtn = document.getElementById('chat-stop-btn');
  const fileInput = document.getElementById('chat-file-input');
  const attachBtn = document.getElementById('chat-attach-btn');
  const clearBtn = document.getElementById('chat-clear-btn');
  const stream = document.getElementById('chat-stream');

  // Decouple streaming response from auto-scrolling: detect if user scrolled up
  if (stream) {
    stream.addEventListener('scroll', () => {
      const distanceFromBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight;
      autoScrollChat = distanceFromBottom <= 80;
      const floatBtn = document.getElementById('chat-scroll-bottom-btn');
      if (floatBtn) {
        floatBtn.style.display = (!autoScrollChat && State.chat.isGenerating) ? 'flex' : 'none';
      }
    });
  }

  // 1. Enter key submission (Shift+Enter for newline)
  if (promptInput) {
    promptInput.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.keyCode === 13) && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.isComposing) {
        e.preventDefault();
        submitPrompt();
      }
    });

    // 2. Multimodal Clipboard Paste Handler (Ctrl+V with images)
    promptInput.addEventListener('paste', handleClipboardPaste);

    // 3. Drag-and-Drop Image Handlers
    promptInput.addEventListener('dragover', (e) => {
      e.preventDefault();
      promptInput.classList.add('drag-over');
    });

    promptInput.addEventListener('dragleave', () => {
      promptInput.classList.remove('drag-over');
    });

    promptInput.addEventListener('drop', (e) => {
      e.preventDefault();
      promptInput.classList.remove('drag-over');
      if (e.dataTransfer && e.dataTransfer.files) {
        handleFiles(e.dataTransfer.files);
      }
    });
  }

  // 4. Buttons
  if (sendBtn) sendBtn.addEventListener('click', submitPrompt);
  if (stopBtn) stopBtn.addEventListener('click', stopGeneration);
  if (clearBtn) clearBtn.addEventListener('click', clearHistory);
  if (attachBtn && fileInput) {
    attachBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      if (fileInput.files) handleFiles(fileInput.files);
    });
  }

  // Thread selector & cross-device sync listeners
  const threadSelect = document.getElementById('chat-thread-select');
  const newThreadBtn = document.getElementById('chat-new-thread-btn');
  const deleteThreadBtn = document.getElementById('chat-delete-thread-btn');
  if (threadSelect) {
    threadSelect.addEventListener('change', (e) => switchSession(e.target.value));
  }
  if (newThreadBtn) {
    newThreadBtn.addEventListener('click', promptCreateNewThread);
  }
  if (deleteThreadBtn) {
    deleteThreadBtn.addEventListener('click', deleteCurrentSession);
  }

  // Realtime Parallel Streams Toggle
  const parallelStreamBtn = document.getElementById('chat-parallel-stream-btn');
  if (parallelStreamBtn) {
    if (State.chat.parallelStreams === undefined) {
      State.chat.parallelStreams = safeStorage.getItem('stonesage_parallel_streams') === 'true';
    }
    updateParallelStreamsBtn(parallelStreamBtn);
    parallelStreamBtn.addEventListener('click', toggleParallelStreamsMode);
  }

  // Agent badge click opens Agent Directory
  const agentBadge = document.getElementById('chat-agent-badge');
  if (agentBadge) {
    agentBadge.style.cursor = 'pointer';
    agentBadge.title = 'Click to open Agent Directory & select AI persona';
    agentBadge.addEventListener('click', () => {
      if (window.openAgentDirectoryModal) window.openAgentDirectoryModal();
    });
  }

  // Pantheon Agent Selector listener
  const agentSelect = document.getElementById('chat-agent-select');
  if (agentSelect) {
    const syncModelForAgent = (agentVal) => {
      const topbarModel = document.getElementById('topbar-model-select');
      if (agentVal === 'courage-computer' || agentVal === 'home-agent') {
        State.activeModel = 'coordinator';
        if (topbarModel) topbarModel.value = 'coordinator';
      } else if (agentVal === 'coder-agent') {
        State.activeModel = 'coordinator';
        if (topbarModel) topbarModel.value = 'coordinator';
      }
    };

    if (agentSelect.value) {
      const initialText = agentSelect.options[agentSelect.selectedIndex]?.text || agentSelect.value;
      State.activeAgent = { id: agentSelect.value, name: initialText };
      syncModelForAgent(agentSelect.value);
      if (agentBadge) {
        agentBadge.textContent = `AGENT: ${initialText.toUpperCase()}`;
      }
    }
    agentSelect.addEventListener('change', (e) => {
      const selText = agentSelect.options[agentSelect.selectedIndex]?.text || e.target.value;
      State.activeAgent = { id: e.target.value, name: selText };
      syncModelForAgent(e.target.value);
      if (agentBadge) {
        agentBadge.textContent = `AGENT: ${selText.toUpperCase()}`;
      }
    });
  }

  // Initial load: fetch cross-device sessions from server
  loadSessionsFromServer();

  // Visibility change handler: re-acquire wake lock when phone wakes up during generation
  document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState === 'hidden' && State.chat.isGenerating) State.chat.hiddenDuringStream = true;
    if (document.visibilityState === 'visible' && State.chat.isGenerating) {
      // A phone lock often leaves the fetch hanging with no error at all, so the reply never arrives. If nothing came
      // in for a few seconds, drop the dead stream and poll the server's copy of the turn instead (it kept going).
      if (State.chat.hiddenDuringStream && performance.now() - (State.chat.lastChunkAt || 0) > 3000
          && State.chat.abortController) {
        State.chat.resumeOnAbort = true;
        State.chat.abortController.abort();
      }
      // Stream may have been broken by phone lock — try to re-acquire wake lock
      try {
        if ('wakeLock' in navigator) {
          await navigator.wakeLock.request('screen');
          console.log('[Chat] Wake lock re-acquired on visibility restore');
        }
      } catch (e) {}
    }
  });
}

/**
 * Clipboard Paste Handler for Multimodal Images
 */
function handleClipboardPaste(e) {
  const items = e.clipboardData ? e.clipboardData.items : [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.type.indexOf('image') !== -1) {
      const file = item.getAsFile();
      if (file) {
        processImageFile(file);
      }
    }
  }
}

/**
 * Drag & Drop / File Input Handler
 */
function handleFiles(fileList) {
  for (let i = 0; i < fileList.length; i++) {
    const file = fileList[i];
    if (file.type.startsWith('image/')) {
      processImageFile(file);
    }
  }
}

function processImageFile(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    const dataUrl = e.target.result;
    const imgObj = {
      id: `img_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`,
      name: file.name,
      type: file.type,
      size: file.size,
      data: dataUrl
    };
    State.chat.stagedImages.push(imgObj);
    renderStagedImages();
  };
  reader.readAsDataURL(file);
}

function renderStagedImages() {
  const tray = document.getElementById('staged-images-tray');
  if (!tray) return;

  if (State.chat.stagedImages.length === 0) {
    tray.style.display = 'none';
    tray.innerHTML = '';
    return;
  }

  tray.style.display = 'flex';
  tray.innerHTML = State.chat.stagedImages.map(img => `
    <div class="staged-img-pill" id="pill-${img.id}">
      <img src="${img.data}" class="staged-img-thumb" alt="${img.name}">
      <span class="staged-img-name">${img.name}</span>
      <button type="button" class="staged-img-remove" onclick="removeStagedImage('${img.id}')">&times;</button>
    </div>
  `).join('');
}

export function removeStagedImage(imgId) {
  State.chat.stagedImages = State.chat.stagedImages.filter(i => i.id !== imgId);
  renderStagedImages();
};

/**
 * Recovers an in-flight server generation if the browser stream was interrupted by phone lock
 */
async function recoverBackgroundSession(assistantMsg, msgElement) {
  const syncInd = document.getElementById('chat-sync-indicator');
  if (syncInd) syncInd.textContent = '🔄 RECONNECTING...';
  const done = async () => {
    // The turn finished on the server while the stream was gone. If the live state didn't carry the text, take the
    // reply the server saved to this chat session (it always does, even when the client went away).
    if (!(assistantMsg.content || '').trim()) {
      const saved = await savedReply(assistantMsg.timestamp || Date.now());
      if (saved) assistantMsg.content = saved;
    }
    assistantMsg.streamError = null;
    updateAssistantDom(msgElement, assistantMsg, false);
    saveChatHistory();
    return !!(assistantMsg.content || '').trim();
  };

  for (let attempt = 0; attempt < 120; attempt++) {           // up to 2 min: a long story, or a slow phone wake
    await new Promise(r => setTimeout(r, 1000));
    try {
      const res = await fetch('/api/harness/active-session');
      if (!res.ok) continue;
      const data = await res.json();
      if (!data.ok) break;

      if (data.output && data.output.length >= (assistantMsg.content || '').length) {
        assistantMsg.content = data.output;
      }
      if (data.reasoning && data.reasoning.length >= (assistantMsg.reasoning || '').length) {
        assistantMsg.reasoning = data.reasoning;
      }
      if (data.tokens_count) {
        assistantMsg.metrics.total_tokens = data.tokens_count;
      }
      if (data.tps) {
        assistantMsg.metrics.tps = data.tps;
      }
      updateAssistantDom(msgElement, assistantMsg, data.status === 'generating' || data.status === 'tool_calling');
      saveChatHistory();

      if (data.status === 'completed' || data.status === 'idle') {
        return await done();
      } else if (data.status === 'error') {
        assistantMsg.streamError = data.error || 'Server error';
        updateAssistantDom(msgElement, assistantMsg, false);
        saveChatHistory();
        return false;
      }
    } catch (_) {}
  }
  return await done();                                         // gave up polling: the saved reply is the last resort
}

/** The reply the server saved for this turn in the active chat session, or ''. Only a message saved after the turn
 *  began counts: the server marks the turn finished a moment before it saves it, and in that gap the newest saved
 *  reply is the PREVIOUS turn's. So it retries a few times rather than show the wrong answer. */
async function savedReply(sinceMs) {
  const sessionId = State.activeSessionId;
  if (!sessionId) return '';
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      const res = await fetch(`/api/chat/sessions/messages?session_id=${encodeURIComponent(sessionId)}`);
      if (res.ok) {
        const data = await res.json();
        const last = (data.messages || []).filter((m) => m.role === 'assistant').pop();
        // timestamps are server seconds; allow 5 s of clock skew between phone and server
        if (last && last.content && (!last.timestamp || last.timestamp * 1000 >= sinceMs - 5000)) return last.content;
      }
    } catch (_) {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  return '';
}

/**
 * Submit Prompt & Stream Token Response
 */
export async function submitPrompt() {
  const input = document.getElementById('chat-prompt-input');
  if (!input || State.chat.isGenerating) return;

  const promptText = input.value.trim();
  const images = [...State.chat.stagedImages];

  if (!promptText && images.length === 0) return;

  // Clear input & staged images
  input.value = '';
  State.chat.stagedImages = [];
  renderStagedImages();

  // Reset auto-scroll to follow latest user turn
  autoScrollChat = true;
  const floatBtn = document.getElementById('chat-scroll-bottom-btn');
  if (floatBtn) floatBtn.style.display = 'none';

  // 1. Log user message to state
  const userMsgId = `usr_${Date.now()}`;
  const userMsg = {
    id: userMsgId,
    role: 'user',
    content: promptText,
    images: images,
    timestamp: Date.now()
  };
  State.chat.messages.push(userMsg);
  saveChatHistory();
  appendMessageToDom(userMsg);

  // 2. Prepare assistant placeholder in state & DOM
  const curSelect = document.getElementById('chat-agent-select');
  const activeAgentId = (curSelect && curSelect.value) || (State.activeAgent ? State.activeAgent.id : (State.activeProjectAgent ? State.activeProjectAgent.agent_id : 'coordinator'));
  const activeAgentName = (curSelect && curSelect.selectedIndex >= 0 && curSelect.options[curSelect.selectedIndex]?.text) || (State.activeAgent ? State.activeAgent.name : null);

  const assistantMsgId = `asst_${Date.now()}`;
  const assistantMsg = {
    id: assistantMsgId,
    role: 'assistant',
    agent_id: activeAgentId,
    agent_name: activeAgentName,
    content: '',
    reasoning: '',
    metrics: { ttft_ms: 0, tps: 0, total_tokens: 0, duration_s: 0 },
    timestamp: Date.now(),
    node: State.activeNode
  };
  State.chat.messages.push(assistantMsg);
  saveChatHistory();

  const msgElement = appendMessageToDom(assistantMsg, true);

  // 3. UI state toggle
  State.chat.isGenerating = true;
  toggleGeneratingUi(true);

  // Acquire screen wake lock to prevent phone from sleeping during stream
  let wakeLock = null;
  try {
    if ('wakeLock' in navigator) {
      wakeLock = await navigator.wakeLock.request('screen');
      console.log('[Chat] Screen wake lock acquired — phone will stay awake during generation');
    }
  } catch (e) {
    console.log('[Chat] Wake lock unavailable:', e.message);
  }

  State.chat.abortController = new AbortController();
  const speech = window._convoMode ? createConvoSpeech() : null;   // speaks sentences as they stream in
  window._convoSpeech = speech;                                    // barge-in stops it through this
  State.chat.lastChunkAt = performance.now();
  State.chat.hiddenDuringStream = document.visibilityState === 'hidden';
  const startTime = performance.now();
  let firstTokenTime = null;
  let tokenCount = 0;

  try {
    // Set syncing indicator
    const syncInd = document.getElementById('chat-sync-indicator');
    if (syncInd) syncInd.textContent = '🔄 SYNCING...';

    // Dispatch request to cluster chat API
    const response = await fetch('/api/cluster/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target: State.activeModel || 'coordinator',
        session_id: State.activeSessionId || 'default',
        workspace_path: State.activeWorkspace ? State.activeWorkspace.path : '',
        agent_id: (document.getElementById('chat-agent-select') && document.getElementById('chat-agent-select').value) || (State.activeAgent ? State.activeAgent.id : (State.activeProjectAgent ? State.activeProjectAgent.agent_id : 'coordinator')),
        agent_system_prompt: State.activeAgent ? State.activeAgent.system_prompt : null,
        messages: State.chat.messages.slice(0, -1).map(m => {
          let cleanContent = m.content || '';
          cleanContent = cleanContent
            .replace(/\[ERROR:\s*[^\]]+\]/g, '')
            .replace(/>\s*\[!CAUTION\]\s*\n>\s*\*\*\[BACKEND ERROR\]\*\*:[^\n]*\n*/g, '')
            .replace(/\[STREAM INTERRUPTED BY OPERATOR\]/g, '')
            .replace(/^>\s*🧠\s*\*\*Tiered Memory Active\*\*:[^\n]*\n*/gm, '')
            .trim();
          return {
            role: m.role,
            content: cleanContent,
            images: m.images ? m.images.map(i => i.data) : []
          };
        }).filter(m => (m.content && m.content.length > 0) || (m.images && m.images.length > 0)),
        images: images.map(i => i.data),
        params: {
          temperature: 0.70,
          min_p: 0.06
        }
      }),
      signal: State.chat.abortController.signal
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let reasoningBuffer = '';
    let contentBuffer = '';
    let reasoningTokens = 0;
    let contentTokens = 0;
    let inThinkBlock = false;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      State.chat.lastChunkAt = performance.now();

      if (!firstTokenTime) {
        firstTokenTime = performance.now();
        assistantMsg.metrics.ttft_ms = Math.round(firstTokenTime - startTime);
      }

      const chunk = decoder.decode(value, { stream: true });
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;

        try {
          const parsed = JSON.parse(line.slice(6));
          if (parsed.error) {
            const errDetail = parsed.error.message || JSON.stringify(parsed.error);
            contentBuffer += `\n\n> [!CAUTION]\n> **[BACKEND ERROR]**: ${errDetail}\n\n`;
            assistantMsg.content = contentBuffer;
            updateAssistantDom(msgElement, assistantMsg, true);
            saveChatHistory();
            continue;
          }

          // Universal tool call event from cluster harness
          if (parsed.type === 'tool_call') {
            assistantMsg.executingTool = parsed.name || 'tool';
            // a camera look takes 3-14 s: say so instead of going quiet (both lines are in the server's audio cache)
            if (speech && SLOW_TOOL_FILLER[parsed.name]) speech.filler(SLOW_TOOL_FILLER[parsed.name]);
            assistantMsg.toolStatus = parsed.status || '';  // e.g. "Looking at the driveway front door camera…"
            const argsStr = typeof parsed.arguments === 'object' ? JSON.stringify(parsed.arguments, null, 2) : (parsed.arguments || '{}');
            contentBuffer += `\n\n:::TOOL_CALL:::${parsed.name}:::${argsStr}:::END_TOOL_CALL:::\n\n`;
            assistantMsg.content = contentBuffer;
            updateAssistantDom(msgElement, assistantMsg, true);
            saveChatHistory();
            continue;
          }

          // Courage wants a yes/no before acting (inferred actions, unlocks)
          if (parsed.type === 'approval_required') {
            contentBuffer += `\n\n:::APPROVAL:::${parsed.summary || parsed.name}:::END_APPROVAL:::\n\n`;
            assistantMsg.content = contentBuffer;
            updateAssistantDom(msgElement, assistantMsg, true);
            saveChatHistory();
            continue;
          }

          // Universal tool result event from cluster harness
          if (parsed.type === 'tool_result') {
            assistantMsg.executingTool = null;
            const resStr = typeof parsed.result === 'object' ? JSON.stringify(parsed.result, null, 2) : (parsed.result || '{}');
            contentBuffer += `\n\n:::TOOL_RESULT:::${parsed.name}:::${resStr}:::END_TOOL_RESULT:::\n\n`;
            assistantMsg.content = contentBuffer;
            updateAssistantDom(msgElement, assistantMsg, true);
            saveChatHistory();
            continue;
          }

          const delta = parsed.choices?.[0]?.delta || {};

          // Extract reasoning / chain-of-thought trace
          if (delta.reasoning_content) {
            reasoningBuffer += delta.reasoning_content;
            reasoningTokens++;
            tokenCount++;
          }

          // Extract standard response content, checking for inline <think> tags
          if (delta.content) {
            const txt = delta.content;
            tokenCount++;

            if (txt.includes('<think>')) {
              inThinkBlock = true;
            }

            if (inThinkBlock) {
              if (txt.includes('</think>')) {
                inThinkBlock = false;
                const parts = txt.split('</think>');
                reasoningBuffer += parts[0].replace('<think>', '');
                contentBuffer += parts[1] || '';
                if (speech) speech.push(parts[1] || '');
                reasoningTokens += parts[0].length ? 1 : 0;
                contentTokens += parts[1] ? 1 : 0;
              } else {
                reasoningBuffer += txt.replace('<think>', '');
                reasoningTokens++;
              }
            } else {
              contentBuffer += txt;
              if (speech) speech.push(txt);
              contentTokens++;
            }
          }

          // Calculate live tps
          const elapsedSec = Math.max((performance.now() - (firstTokenTime || startTime)) / 1000, 0.05);
          const currentTps = Math.round(tokenCount / elapsedSec);

          assistantMsg.reasoning = reasoningBuffer;
          assistantMsg.content = contentBuffer;
          assistantMsg.metrics.total_tokens = tokenCount;
          assistantMsg.metrics.reasoning_tokens = reasoningTokens;
          assistantMsg.metrics.content_tokens = contentTokens;
          assistantMsg.metrics.tps = currentTps;
          assistantMsg.metrics.duration_s = Math.round(elapsedSec * 10) / 10;
          // Courage sends its answer in one chunk; the server reports the real generated tokens and speed at the end
          if (parsed.usage && parsed.usage.completion_tokens) {
            assistantMsg.metrics.total_tokens = parsed.usage.completion_tokens;
            assistantMsg.metrics.content_tokens = parsed.usage.completion_tokens;
            assistantMsg.metrics.reasoning_tokens = 0;
            assistantMsg.metrics.tps = Math.round(parsed.usage.tps || 0);
          }

          // Incrementally update DOM & durable local storage!
          updateAssistantDom(msgElement, assistantMsg, true);
          saveChatHistory();
        } catch (_) {}
      }
    }

    if (!contentBuffer.trim() && !reasoningBuffer.trim() && State.chat.hiddenDuringStream
        && await recoverBackgroundSession(assistantMsg, msgElement)) {
      // the stream closed empty while the screen was off; the server's copy of the turn filled it in
    } else if (!contentBuffer.trim() && !reasoningBuffer.trim()) {
      assistantMsg.content = '> [!WARNING]\n> **[NO RESPONSE RECEIVED]**: The inference engine did not emit tokens. Check endpoint status in Fleet.';
    }

    // Finalize message state
    updateAssistantDom(msgElement, assistantMsg, false);
    saveChatHistory();

  } catch (err) {
    if (err.name === 'AbortError' && State.chat.resumeOnAbort) {
      // we dropped a stream the phone lock had killed (not the Stop button): take the reply from the server
      const ok = await recoverBackgroundSession(assistantMsg, msgElement);
      if (!ok) {
        assistantMsg.streamError = 'Stream lost while the screen was off';
        updateAssistantDom(msgElement, assistantMsg, false);
        saveChatHistory();
      }
    } else if (err.name === 'AbortError') {
      assistantMsg.interrupted = true;
      updateAssistantDom(msgElement, assistantMsg, false);
      saveChatHistory();
    } else {
      console.warn('[Chat] Stream interrupted, recovering background server session:', err);
      const ok = await recoverBackgroundSession(assistantMsg, msgElement);
      if (!ok) {
        assistantMsg.streamError = err.message || 'Stream interrupted';
        updateAssistantDom(msgElement, assistantMsg, false);
        saveChatHistory();
      }
    }
  } finally {
    State.chat.isGenerating = false;
    State.chat.abortController = null;
    State.chat.resumeOnAbort = false;
    State.chat.hiddenDuringStream = false;
    toggleGeneratingUi(false);
    const syncInd = document.getElementById('chat-sync-indicator');
    if (syncInd) syncInd.textContent = '✔ SYNCED';

    // Release screen wake lock
    if (wakeLock) {
      try { await wakeLock.release(); wakeLock = null; } catch (e) {}
      console.log('[Chat] Screen wake lock released');
    }

    // Conversation mode: finish speaking the response (most of a long one is already playing), then auto-record again
    if (window._convoMode && assistantMsg.content && !assistantMsg.interrupted && !assistantMsg.streamError) {
      if (!(speech && speech.end())) convoTtsAndContinue(assistantMsg.content);
    } else if (speech) {
      speech.cancel();
    }
  }
}

export function stopGeneration() {
  if (State.chat.abortController) {
    State.chat.abortController.abort();
  }
}

export async function clearHistory() {
  if (!confirm('Clear all conversation history and reasoning traces?')) return;
  State.chat.messages = [];
  saveChatHistory();
  renderChatHistory();
  if (State.activeSessionId) {
    try {
      await fetch('/api/chat/sessions/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: State.activeSessionId })
      });
    } catch (err) {
      console.warn('Could not clear server session messages:', err);
    }
  }
}

window.clearHistory = clearHistory;

/**
 * DOM Rendering Helpers
 */
function renderChatHistory() {
  const stream = document.getElementById('chat-stream');
  if (!stream) return;
  stream.innerHTML = '';

  State.chat.messages.forEach(msg => {
    appendMessageToDom(msg, false);
  });

  stream.scrollTop = stream.scrollHeight;
}

function renderAssistantBodyHtml(msg, isStreaming, isReasoning, rTokens, cTokens) {
  const statusText = isReasoning ? '⏳ THINKING...' : '✔ COMPLETED';
  const statusColor = isReasoning ? 'var(--term-accent-gold)' : 'var(--term-accent-green)';
  const hasReasoning = Boolean(msg.reasoning);
  const isOpen = msg.reasoningOpen !== false;

  if (State.chat.parallelStreams && hasReasoning) {
    return `
      <div class="chat-parallel-streams" id="parallel-grid-${msg.id}">
        <div class="stream-pane stream-pane-reasoning" id="reasoning-pane-${msg.id}" style="${isOpen ? '' : 'display:none;'}">
          <div class="stream-pane-header">
            <span>🧠 REASONING STREAM (${rTokens} tok)</span>
            <span class="stream-live-tag ${isReasoning ? 'pulse' : ''}" style="color:${statusColor};">${statusText}</span>
          </div>
          <pre class="reasoning-stream-content">${escapeHtml(msg.reasoning)}</pre>
        </div>
        <div class="stream-pane stream-pane-response" id="response-pane-${msg.id}" style="${!isOpen ? 'grid-column: 1 / -1;' : ''}">
          <div class="stream-pane-header">
            <span>⚡ RESPONSE STREAM (${cTokens} tok)</span>
            <span class="stream-live-tag ${isStreaming && !isReasoning ? 'pulse' : ''}" style="color:var(--term-accent-green);">${isStreaming && !isReasoning ? '⚡ STREAMING...' : ''}</span>
          </div>
          <div class="response-stream-content">${formatChatContent(msg.content)}</div>
        </div>
      </div>
    `;
  }

  // Classic Accordion View
  let reasoningHtml = '';
  if (hasReasoning) {
    reasoningHtml = `
      <details class="reasoning-trace" id="reasoning-details-${msg.id}" ${isOpen ? 'open' : ''} ontoggle="window.syncDetailsToggle('${msg.id}', this.open)">
        <summary style="cursor:pointer; user-select:none; font-size:0.75rem; color:${statusColor}; font-weight:bold;">
          [🧠 REASONING TRACE (${rTokens} tokens • ${statusText})]
        </summary>
        <pre class="reasoning-content" style="margin-top:6px; font-size:0.78rem; line-height:1.45; white-space:pre-wrap; max-height:320px; overflow-y:auto; background:var(--term-bg); border:1px solid var(--term-border-dim); padding:8px; border-radius:3px; color:var(--term-accent-gold);">${escapeHtml(msg.reasoning)}</pre>
      </details>
    `;
  }

  let errorBanner = '';
  if (msg.streamError) {
    errorBanner = `
      <div class="chat-stream-error" style="margin-top:6px; padding:6px 10px; background:rgba(255,50,50,0.12); border-left:3px solid var(--term-alert, #ff4444); font-size:0.78rem; color:var(--term-alert, #ff4444); border-radius:2px;">
        ⚠️ <strong>Stream Interrupted</strong>: ${escapeHtml(msg.streamError)}
      </div>
    `;
  } else if (msg.interrupted) {
    errorBanner = `
      <div class="chat-stream-error" style="margin-top:6px; padding:6px 10px; background:rgba(255,200,50,0.10); border-left:3px solid var(--term-accent-gold, #ffb800); font-size:0.78rem; color:var(--term-accent-gold, #ffb800); border-radius:2px;">
        ⏹️ <strong>Generation stopped by operator</strong>
      </div>
    `;
  }

  let liveStatusBar = '';
  if (isStreaming) {
    if (msg.executingTool) {
      liveStatusBar = `
        <div class="chat-live-status-bar tool">
          <span class="pulse-dot">⚙️</span>
          <span>${msg.toolStatus ? `<strong>${escapeHtml(msg.toolStatus)}</strong> <code>${escapeHtml(msg.executingTool)}</code>` : `<strong>EXECUTING TOOL:</strong> <code>${escapeHtml(msg.executingTool)}</code> &mdash; waiting for hardware / process output...`}</span>
        </div>
      `;
    } else if (isReasoning) {
      liveStatusBar = `
        <div class="chat-live-status-bar reasoning">
          <span class="pulse-dot">⏳</span>
          <span><strong>THINKING / REASONING IN PROGRESS</strong>${rTokens > 1 ? ` &mdash; ${rTokens} tokens trace` : ''}...</span>
        </div>
      `;
    } else {
      liveStatusBar = `
        <div class="chat-live-status-bar response">
          <span class="pulse-dot">⚡</span>
          <span><strong>STREAMING RESPONSE</strong>${cTokens > 1 ? ` &mdash; ${cTokens} tokens (${msg.metrics?.tps || 0} tps)` : ''}...</span>
        </div>
      `;
    }
  }

  return `
    ${reasoningHtml}
    <div class="chat-body-content">${formatChatContent(msg.content)}</div>
    ${liveStatusBar}
    ${errorBanner}
  `;
}

function appendMessageToDom(msg, isStreaming = false) {
  const stream = document.getElementById('chat-stream');
  if (!stream) return null;

  const msgDiv = document.createElement('div');
  msgDiv.className = `chat-msg ${msg.role}`;
  msgDiv.id = `msg-${msg.id}`;

  const isUser = msg.role === 'user';
  let displayAgent = msg.agent_name;
  if (!displayAgent) {
    const sel = document.getElementById('chat-agent-select');
    if (sel && sel.selectedIndex >= 0 && sel.options[sel.selectedIndex]) {
      displayAgent = sel.options[sel.selectedIndex].text;
    } else if (State.activeAgent && State.activeAgent.name) {
      displayAgent = State.activeAgent.name;
    } else if (State.activeProjectAgent) {
      displayAgent = State.activeProjectAgent.name || State.activeProjectAgent.identity?.name;
    }
  }
  const roleLabel = isUser ? '👤 OPERATOR' : (displayAgent ? `${displayAgent.toUpperCase()}` : '🤖 HARNESS COORDINATOR');

  let attachmentsHtml = '';
  if (msg.images && msg.images.length > 0) {
    attachmentsHtml = `
      <div class="msg-attachments">
        ${msg.images.map(img => `<img src="${img.data}" class="msg-img-preview" alt="Attachment" onclick="window.open(this.src)">`).join('')}
      </div>
    `;
  }

  const rTokens = msg.metrics?.reasoning_tokens || msg.metrics?.total_tokens || 0;
  const cTokens = msg.metrics?.content_tokens || 0;

  const metricsHtml = !isUser ? `
    <span class="chat-msg-metrics">
      ${msg.metrics && msg.metrics.tps > 0 ? `
        <span>${msg.metrics.total_tokens} total tok</span>
        ${msg.metrics.reasoning_tokens ? `<span style="color:var(--term-accent-gold);">(${msg.metrics.reasoning_tokens} thought / ${msg.metrics.content_tokens || 0} resp)</span>` : ''}
        <span>•</span>
        <span>${msg.metrics.tps} tps</span>
        <span>•</span>
        <span>${msg.metrics.ttft_ms}ms TTFT</span>
      ` : ''}
    </span>
  ` : '';

  const branchBtn = `<button class="term-cmd-btn" style="padding: 1px 6px; font-size: 0.7rem; color: var(--term-accent-green, #00d97e);" onclick="branchChatAtMessage('${msg.id}')" title="Branch conversation into a new thread from this message">[🌿 BRANCH]</button>`;

  const hasReasoning = Boolean(msg.reasoning);
  const isOpen = msg.reasoningOpen !== false;
  const reasoningBtn = hasReasoning ? `
    <button class="term-cmd-btn reasoning-toggle-btn" id="btn-reasoning-${msg.id}" style="padding: 1px 6px; font-size: 0.7rem; color: var(--term-accent-gold);" onclick="toggleMessageReasoning('${msg.id}')" title="Expand or Collapse Realtime Reasoning Stream">[🧠 ${isOpen ? 'HIDE' : 'SHOW'} THOUGHTS]</button>
  ` : '';

  const messageActions = !isUser ? `
    <button class="term-cmd-btn" style="padding: 1px 6px; font-size: 0.7rem; color: #fbb829;" onclick="triggerInlineNudge('${msg.id}')" title="Breakout Nudge Directive">[⚡ NUDGE]</button>
    <button class="term-cmd-btn" style="padding: 1px 6px; font-size: 0.7rem; color: var(--term-accent-green, #16a34a);" onclick="storeMessageToObsidian('${msg.id}')" title="Store message to AI Obsidian Brain">[📥 STORE]</button>
    <button class="term-cmd-btn" style="padding: 1px 6px; font-size: 0.7rem; color: var(--term-accent-gold);" onclick="pinMessageAsInvariant('${msg.id}')" title="Pin finding to project INVARIANTS.md">[📌 INVARIANT]</button>
    <button class="term-cmd-btn" style="padding: 1px 6px; font-size: 0.7rem; color: var(--term-accent-blue);" onclick="addMessageToHandover('${msg.id}')" title="Add milestone to project HANDOVER.md">[📋 HANDOVER]</button>
    <button class="term-cmd-btn chat-tts-btn" style="padding: 1px 6px; font-size: 0.7rem; color: #e879f9;" onclick="readMessageAloud('${msg.id}')" title="Read aloud with the Computer voice (Kokoro TTS)">[🔊 READ]</button>
    ${branchBtn}
    ${reasoningBtn}
  ` : `
    <button class="term-cmd-btn" style="padding: 1px 6px; font-size: 0.7rem; color: var(--term-accent-green, #16a34a);" onclick="storeMessageToObsidian('${msg.id}')" title="Store prompt to AI Obsidian Brain">[📥 STORE]</button>
    ${branchBtn}
  `;

  msgDiv.innerHTML = `
    <div class="chat-msg-header">
      <span class="chat-msg-role">${roleLabel}</span>
      <div class="chat-msg-meta">
        ${metricsHtml}
        ${messageActions}
        <span class="chat-msg-time">${formatTime(msg.timestamp)}</span>
      </div>
    </div>
    ${attachmentsHtml}
    <div class="chat-msg-body">
      ${!isUser ? renderAssistantBodyHtml(msg, isStreaming, isStreaming && (!msg.content || msg.content.length === 0), rTokens, cTokens) : `<div class="chat-body-content">${formatChatContent(msg.content)}</div>`}
    </div>
  `;

  stream.appendChild(msgDiv);
  if (autoScrollChat || isUser) {
    stream.scrollTop = stream.scrollHeight;
  }
  return msgDiv;
}

function updateAssistantDom(msgElement, msg, isStreaming) {
  if (!msgElement) return;

  const bodyEl = msgElement.querySelector('.chat-msg-body');
  const metricsEl = msgElement.querySelector('.chat-msg-metrics');
  const metaEl = msgElement.querySelector('.chat-msg-meta');

  const rTokens = msg.metrics?.reasoning_tokens || 0;
  const cTokens = msg.metrics?.content_tokens || 0;
  const isReasoning = isStreaming && (!msg.content || msg.content.length === 0);

  // During streaming reasoning, default open so operator can watch in real time
  if (isReasoning && msg.reasoningOpen === undefined) {
    msg.reasoningOpen = true;
  }

  if (metricsEl && msg.metrics.tps > 0) {
    metricsEl.innerHTML = `
      <span>${msg.metrics.total_tokens} total tok</span>
      ${rTokens ? `<span style="color:var(--term-accent-gold);">(${rTokens} thought / ${cTokens} resp)</span>` : ''}
      <span>•</span>
      <span>${msg.metrics.tps} tps</span>
      <span>•</span>
      <span>${msg.metrics.ttft_ms}ms TTFT</span>
      ${isReasoning ? '<span style="color:var(--term-accent-gold); font-weight:bold;">• [⏳ THINKING...]</span>' : (isStreaming ? '<span style="color:var(--term-accent-green); font-weight:bold;">• [⚡ STREAMING ANSWER...]</span>' : '')}
    `;
  }

  // Ensure [🧠 SHOW/HIDE THOUGHTS] button exists and reflects state
  if (metaEl) {
    let rBtn = metaEl.querySelector(`#btn-reasoning-${msg.id}`);
    const hasReasoning = Boolean(msg.reasoning || isReasoning);
    const isOpen = msg.reasoningOpen !== false;
    if (hasReasoning) {
      if (!rBtn) {
        rBtn = document.createElement('button');
        rBtn.className = 'term-cmd-btn reasoning-toggle-btn';
        rBtn.id = `btn-reasoning-${msg.id}`;
        rBtn.style.padding = '1px 6px';
        rBtn.style.fontSize = '0.7rem';
        rBtn.style.color = 'var(--term-accent-gold)';
        rBtn.title = 'Expand or Collapse Realtime Reasoning Stream';
        rBtn.onclick = () => window.toggleMessageReasoning(msg.id);
        const timeEl = metaEl.querySelector('.chat-msg-time');
        if (timeEl) {
          metaEl.insertBefore(rBtn, timeEl);
        } else {
          metaEl.appendChild(rBtn);
        }
      }
      rBtn.textContent = `[🧠 ${isOpen ? 'HIDE' : 'SHOW'} THOUGHTS]`;
    }
  }

  if (bodyEl) {
    bodyEl.innerHTML = renderAssistantBodyHtml(msg, isStreaming, isReasoning, rTokens, cTokens);

    // Auto-scroll the reasoning stream container if user is watching in real time
    if (isReasoning) {
      const rPre = bodyEl.querySelector('.reasoning-stream-content, .reasoning-content');
      if (rPre) {
        rPre.scrollTop = rPre.scrollHeight;
      }
    }
  }

  // Decoupled scrolling: only auto-scroll if user has not scrolled up to read
  const stream = document.getElementById('chat-stream');
  if (stream && autoScrollChat) {
    stream.scrollTop = stream.scrollHeight;
  }
  const floatBtn = document.getElementById('chat-scroll-bottom-btn');
  if (floatBtn) {
    floatBtn.style.display = (!autoScrollChat && isStreaming) ? 'flex' : 'none';
  }
}

export function scrollToChatBottom() {

  const stream = document.getElementById('chat-stream');
  if (stream) {
    stream.scrollTop = stream.scrollHeight;
    autoScrollChat = true;
  }
  const floatBtn = document.getElementById('chat-scroll-bottom-btn');
  if (floatBtn) floatBtn.style.display = 'none';
};

/**
 * Realtime Reasoning Stream & Details Sync
 */
export function syncDetailsToggle(msgId, isOpen) {

  const msg = State.chat.messages.find(m => m.id === msgId);
  if (msg) {
    msg.reasoningOpen = isOpen;
    const btn = document.getElementById(`btn-reasoning-${msgId}`);
    if (btn) {
      btn.textContent = `[🧠 ${isOpen ? 'HIDE' : 'SHOW'} THOUGHTS]`;
    }
  }
};

export function toggleMessageReasoning(msgId) {

  const msg = State.chat.messages.find(m => m.id === msgId);
  if (!msg) return;
  const currentlyOpen = msg.reasoningOpen !== false;
  msg.reasoningOpen = !currentlyOpen;
  saveChatHistory();

  const msgDiv = document.getElementById(`msg-${msg.id}`);
  if (msgDiv) {
    const rTokens = msg.metrics?.reasoning_tokens || 0;
    const cTokens = msg.metrics?.content_tokens || 0;
    const isStreaming = State.chat.isGenerating && State.chat.messages[State.chat.messages.length - 1]?.id === msg.id;
    const isReasoning = isStreaming && (!msg.content || msg.content.length === 0);
    updateAssistantDom(msgDiv, msg, isStreaming);
  } else {
    renderChatHistory();
  }
};

/**
 * Realtime Parallel Streams Mode (Side-by-Side Dual Column View)
 */
export function toggleParallelStreamsMode() {

  State.chat.parallelStreams = !State.chat.parallelStreams;
  safeStorage.setItem('stonesage_parallel_streams', State.chat.parallelStreams ? 'true' : 'false');
  const btn = document.getElementById('chat-parallel-stream-btn');
  if (btn) {
    updateParallelStreamsBtn(btn);
  }
  renderChatHistory();
};

function updateParallelStreamsBtn(btn) {
  if (!btn) return;
  const isParallel = Boolean(State.chat.parallelStreams);
  btn.textContent = isParallel ? '[⚡ PARALLEL STREAMS: ON]' : '[⚡ PARALLEL STREAMS: OFF]';
  btn.style.color = isParallel ? 'var(--term-accent-green, #00d97e)' : 'var(--term-text-muted)';
  btn.style.borderColor = isParallel ? 'var(--term-accent-green, #00d97e)' : 'var(--term-border-dim)';
}

function toggleGeneratingUi(isGenerating) {
  const sendBtn = document.getElementById('chat-send-btn');
  const stopBtn = document.getElementById('chat-stop-btn');
  if (sendBtn) sendBtn.style.display = isGenerating ? 'none' : 'inline-block';
  if (stopBtn) stopBtn.style.display = isGenerating ? 'inline-block' : 'none';
}

/**
 * Inline Message /nudge trigger
 */
export function triggerInlineNudge(msgId) {

  const directive = prompt("Enter authoritative breakout directive for this agent:", "Stop circular reasoning loop immediately. State current milestone and proceed to next proof.");
  if (directive) {
    fetch('/api/harness/nudge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: 'coordinator', directive: directive })
    }).then(r => r.json()).then(res => {
      alert(`[NUDGE DISPATCHED]: ${res.directive || directive}`);
    }).catch(err => {
      alert(`Nudge error: ${err.message}`);
    });
  }
};

/**
 * 1-Click Action Chip: Pin finding to project .stonesage/INVARIANTS.md
 */
export async function pinMessageAsInvariant(msgId) {

  const msg = State.chat.messages.find(m => m.id === msgId);
  const rawText = msg ? msg.content.trim() : '';
  const initialText = rawText.length > 250 ? rawText.slice(0, 250) + '...' : rawText;
  const invariantText = prompt("Confirm or edit the architectural invariant to pin into project .stonesage/INVARIANTS.md:", initialText);
  if (!invariantText) return;

  const wsPath = State.activeWorkspace ? State.activeWorkspace.path : '';
  if (!wsPath) {
    alert("No active workspace selected. Please select a project workspace in [F2: WORKSTATION] first.");
    return;
  }

  try {
    const res = await fetch('/api/workspaces/agent/invariant', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        workspace_path: wsPath,
        invariant_text: invariantText
      })
    });
    const data = await res.json();
    if (data.ok) {
      alert(`[INVARIANT PINNED]: ${data.message || 'Successfully saved to .stonesage/INVARIANTS.md'}`);
    } else {
      alert(`Failed to pin invariant: ${data.error || 'Unknown error'}`);
    }
  } catch (err) {
    alert(`Error pinning invariant: ${err.message}`);
  }
};

/**
 * 1-Click Action Chip: Append milestone/handover note to project .stonesage/HANDOVER.md
 */
export async function addMessageToHandover(msgId) {

  const msg = State.chat.messages.find(m => m.id === msgId);
  const rawText = msg ? msg.content.trim() : '';
  const initialText = rawText.length > 250 ? rawText.slice(0, 250) + '...' : rawText;
  const handoverText = prompt("Confirm or edit milestone note to append to .stonesage/HANDOVER.md:", initialText);
  if (!handoverText) return;

  const wsPath = State.activeWorkspace ? State.activeWorkspace.path : '';
  if (!wsPath) {
    alert("No active workspace selected. Please select a project workspace in [F2: WORKSTATION] first.");
    return;
  }

  try {
    const res = await fetch('/api/workspaces/agent/handover', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        workspace_path: wsPath,
        handover_text: handoverText
      })
    });
    const data = await res.json();
    if (data.ok) {
      alert(`[HANDOVER UPDATED]: ${data.message || 'Successfully appended to .stonesage/HANDOVER.md'}`);
    } else {
      alert(`Failed to update handover: ${data.error || 'Unknown error'}`);
    }
  } catch (err) {
    alert(`Error updating handover: ${err.message}`);
  }
};

/**
 * 1-Click Action Chip: Quick store message content, link, or note to AI Obsidian Brain
 */
export function storeMessageToObsidian(msgId) {
  const msg = State.chat.messages.find(m => m.id === msgId);
  if (!msg) return;

  const content = (msg.content || '').trim();
  const urlMatch = content.match(/https?:\/\/[^\s\)\"\'<>]+/i);

  if (window.openQuickCaptureModal) {
    if (urlMatch) {
      window.openQuickCaptureModal({
        type: 'url',
        url: urlMatch[0],
        title: `Captured from Chat (${msg.role})`,
        text: content,
        database: 'ai_obsidian'
      });
    } else {
      window.openQuickCaptureModal({
        type: 'note',
        title: `Chat Note - ${new Date().toLocaleDateString()}`,
        text: content,
        database: 'ai_obsidian'
      });
    }
  } else {
    alert("Quick capture modal not available.");
  }
}

/**
 * 1-Click Action Chip: Branch conversation thread from this message forward
 */
export async function branchChatAtMessage(msgId) {

  const targetIndex = State.chat.messages.findIndex(m => m.id === msgId);
  if (targetIndex === -1) {
    alert("Target message not found in active session.");
    return;
  }
  const targetMsg = State.chat.messages[targetIndex];
  const preview = (targetMsg.content || '').trim().slice(0, 30);
  const defaultTitle = `Branch: ${preview ? (preview + '...') : (State.activeSessionId || 'Thread')}`;
  const branchTitle = prompt("Enter a title for the branched thread (all messages up to this point will be included):", defaultTitle);
  if (!branchTitle) return;

  const wsPath = State.activeWorkspace ? State.activeWorkspace.path : '';
  const slicedMessages = State.chat.messages.slice(0, targetIndex + 1);

  try {
    const res = await fetch('/api/chat/sessions/branch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_session_id: State.activeSessionId,
        target_index: targetIndex,
        title: branchTitle,
        workspace_path: wsPath,
        messages: slicedMessages.map(m => ({
          role: m.role,
          content: m.content || '',
          thought: m.reasoning || '',
          images: m.images || [],
          metrics: m.metrics || {},
          tool_calls: m.tool_calls || []
        }))
      })
    });

    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }

    const newSession = data.session;
    State.activeSessionId = newSession.session_id;
    safeStorage.setItem('stonesage_active_session_id', newSession.session_id);

    await loadSessionsFromServer();
    await fetchSessionMessages(newSession.session_id);

    const syncInd = document.getElementById('chat-sync-indicator');
    if (syncInd) syncInd.textContent = `✔ BRANCHED (${data.copied_count || slicedMessages.length} msgs)`;
  } catch (err) {
    console.error('Failed to branch session:', err);
    alert(`Could not branch thread: ${err.message}`);
  }
};

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
}

function formatChatContent(raw) {
  if (!raw) return '';

  let formatted = raw;

  // 1. Convert Qwen/ChatML tool calls: <|tool_call>call:NAME{...}<tool_call|>
  formatted = formatted.replace(/<\|?tool_call\|?>\s*call:([a-zA-Z0-9_\-:]+)\s*(\{[\s\S]*?\})\s*<\|?\/?tool_call\|?>?/gi, (match, toolName, rawArgs) => {
    let cleanArgs = rawArgs.replace(/<\|"\|>/g, '"').replace(/<\|'\|>/g, "'").trim();
    try {
      const parsed = JSON.parse(cleanArgs);
      cleanArgs = JSON.stringify(parsed, null, 2);
    } catch (_) {}
    return `\n\n:::TOOL_CALL:::${toolName}:::${cleanArgs}:::END_TOOL_CALL:::\n\n`;
  });

  // 2. Convert XML format: <tool_call>...</tool_call> or <function_call>...</function_call>
  formatted = formatted.replace(/<(?:tool_call|function_call)>([\s\S]*?)(?:<\/(?:tool_call|function_call)>|$)/gi, (match, inner) => {
    let toolName = 'Universal Tool';
    let payload = inner.trim();
    
    // Check if wrapped in specific sub-tag like <temperature_control>...</temperature_control>
    const subTagMatch = payload.match(/^<([a-zA-Z0-9_\-]+)>([\s\S]*?)(?:<\/\1>|$)/);
    if (subTagMatch) {
      toolName = subTagMatch[1];
      payload = subTagMatch[2].trim();
    }

    try {
      const parsed = JSON.parse(payload);
      if (parsed.name && parsed.arguments) {
        toolName = parsed.name;
        payload = typeof parsed.arguments === 'object' ? JSON.stringify(parsed.arguments, null, 2) : parsed.arguments;
      } else {
        payload = JSON.stringify(parsed, null, 2);
      }
    } catch (_) {}

    return `\n\n:::TOOL_CALL:::${toolName}:::${payload}:::END_TOOL_CALL:::\n\n`;
  });

  // 3. Catch standalone known home assistant tags if emitted directly
  formatted = formatted.replace(/<(temperature_control|light_control|switch_control|climate_control)>([\s\S]*?)(?:<\/\1>|$)/gi, (match, tag, inner) => {
    let payload = inner.trim();
    try {
      const parsed = JSON.parse(payload);
      payload = JSON.stringify(parsed, null, 2);
    } catch (_) {}
    return `\n\n:::TOOL_CALL:::${tag}:::${payload}:::END_TOOL_CALL:::\n\n`;
  });

  // 4. Catch inline call:name{...} if not already converted
  formatted = formatted.replace(/\bcall:([a-zA-Z0-9_\-]+)\s*(\{[\s\S]*?\})/gi, (match, toolName, rawArgs) => {
    let payload = rawArgs.trim();
    try {
      const parsed = JSON.parse(payload);
      payload = JSON.stringify(parsed, null, 2);
    } catch (_) {}
    return `\n\n:::TOOL_CALL:::${toolName}:::${payload}:::END_TOOL_CALL:::\n\n`;
  });

  // 5. Clean any residual chat template / tool tokens
  formatted = formatted.replace(/<\|?(?:tool_call|im_start|im_end|endoftext)\|?>?/gi, '');

  let escaped = escapeHtml(formatted);

  // 6. Render subagent & tool call cards
  escaped = escaped.replace(/:::TOOL_CALL:::(.*?):::([\s\S]*?):::END_TOOL_CALL:::/g, (match, name, payload) => {
    const isSubagent = name === 'delegate_worker' || name === 'spawn_subagent';
    if (isSubagent) {
      const label = name === 'delegate_worker' ? `WORKER (${escapeHtml(engineLabel('worker'))})` : 'BACKGROUND SUBAGENT';
      return `<div class="subagent-card">
        <div class="subagent-card-header">
          <span class="subagent-badge">🤖 SUBAGENT DISPATCH</span>
          <span class="subagent-name">${label}</span>
          <span class="subagent-status-tag pulse">⚡ DISPATCHED</span>
        </div>
        <details class="subagent-card-drawer">
          <summary>View Subagent Task Specification</summary>
          <pre class="subagent-payload">${payload}</pre>
        </details>
      </div>`;
    }
    // one-line chip: "🔧 camera_look · camera: living_room", arguments one click away
    const brief = payload.replace(/&quot;|[{}\n]/g, '').replace(/\s+/g, ' ').trim();
    return `<details class="tool-chip"><summary>🔧 ${name}${brief ? ' · ' + brief.slice(0, 80) : ''}</summary><pre>${payload}</pre></details>`;
  });

  // 7. Render subagent & tool result cards
  escaped = escaped.replace(/:::TOOL_RESULT:::(.*?):::([\s\S]*?):::END_TOOL_RESULT:::/g, (match, name, payload) => {
    const isSubagent = name === 'delegate_worker' || name === 'spawn_subagent' || name === 'get_subagent_task';
    if (isSubagent) {
      const label = name === 'delegate_worker' ? `WORKER (${escapeHtml(engineLabel('worker'))})` : 'BACKGROUND SUBAGENT';
      return `<div class="subagent-result-card">
        <div class="subagent-card-header">
          <span class="subagent-badge success">🤖 SUBAGENT OUTPUT</span>
          <span class="subagent-name">${label}</span>
          <span class="subagent-status-tag success">✔ COMPLETED</span>
        </div>
        <details class="subagent-card-drawer" open>
          <summary>View Subagent Verified Results</summary>
          <pre class="subagent-payload">${payload}</pre>
        </details>
      </div>`;
    }
    const failed = /&quot;ok&quot;:\s*false/.test(payload);
    return `<details class="tool-chip ${failed ? 'fail' : 'ok'}"><summary>${failed ? '✗' : '✓'} ${name} result</summary><pre>${payload}</pre></details>`;
  });

  // 7b. Courage approval: one tap sends "yes" / "no"
  escaped = escaped.replace(/:::APPROVAL:::([\s\S]*?):::END_APPROVAL:::/g, (match, summary) =>
    `<div class="approval-chip"><span>⏳ ${summary}?</span>` +
    `<button type="button" onclick="answerApproval('yes', this)">✓ Yes</button>` +
    `<button type="button" onclick="answerApproval('no', this)">✗ No</button></div>`);

  // 8. OpenClaw Invariant & Handover Tags
  escaped = escaped.replace(/&lt;invariant&gt;([\s\S]*?)&lt;\/invariant&gt;/gi, '<div class="openclaw-invariant-card"><span class="openclaw-badge">🔒 OPENCLAW INVARIANT</span><div class="openclaw-content">$1</div></div>');
  escaped = escaped.replace(/&lt;handover&gt;([\s\S]*?)&lt;\/handover&gt;/gi, '<div class="openclaw-handover-card"><span class="openclaw-badge">📋 OPENCLAW HANDOVER</span><div class="openclaw-content">$1</div></div>');
  escaped = escaped.replace(/&lt;action&gt;([\s\S]*?)&lt;\/action&gt;/gi, '<div class="openclaw-action-card"><span class="openclaw-badge">⚡ OPENCLAW ACTION</span><div class="openclaw-content">$1</div></div>');

  // Format blockquotes (> ...)
  escaped = escaped.replace(/^(&gt;|>)\s?(.*)$/gm, '<blockquote class="chat-blockquote">$2</blockquote>');
  // Format bold **text**
  escaped = escaped.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  // Format inline code `code`
  escaped = escaped.replace(/`([^`]+)`/g, '<code class="chat-inline-code">$1</code>');
  // Format linebreaks
  escaped = escaped.replace(/\n/g, '<br>');
  // Tool chips sit in a tight row, not between blank lines
  escaped = escaped.replace(/(?:<br>\s*)+(<details class="tool-chip|<div class="approval-chip)/g, '$1')
    .replace(/(<\/details>|<\/button><\/div>)(?:\s*<br>)+/g, '$1');
  // Clean adjacent blockquotes
  escaped = escaped.replace(/<\/blockquote><br><blockquote class="chat-blockquote">/g, '<br>');
  return escaped;
}

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/**
 * Cross-Device Chat Session Management
 */
export async function loadSessionsFromServer() {
  const threadSelect = document.getElementById('chat-thread-select');
  const wsThreadSelect = document.getElementById('workstation-thread-select');
  const syncInd = document.getElementById('chat-sync-indicator');

  try {
    const res = await fetch('/api/chat/sessions');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    State.chatSessions = data.sessions || [];

    const populateSelect = (selectEl) => {
      if (!selectEl) return;
      selectEl.innerHTML = '';
      if (State.chatSessions.length === 0) {
        selectEl.innerHTML = '<option value="default">Default Conversation</option>';
      } else {
        State.chatSessions.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.session_id;
          const wsName = s.workspace_path ? s.workspace_path.split('/').pop() : 'Root';
          opt.textContent = `[${wsName}] ${s.title || s.session_id}`;
          if (s.session_id === State.activeSessionId) opt.selected = true;
          selectEl.appendChild(opt);
        });
      }
    };

    populateSelect(threadSelect);
    populateSelect(wsThreadSelect);

    if (State.chatSessions.length > 0 && !State.chatSessions.some(s => s.session_id === State.activeSessionId)) {
      State.activeSessionId = State.chatSessions[0].session_id;
      if (threadSelect) threadSelect.value = State.activeSessionId;
      if (wsThreadSelect) wsThreadSelect.value = State.activeSessionId;
    }

    await fetchSessionMessages(State.activeSessionId);

  } catch (err) {
    console.warn('Could not load sessions from server, using local history:', err);
    if (syncInd) syncInd.textContent = '⚠️ LOCAL ONLY';
    renderChatHistory();
  }
}

export async function fetchSessionMessages(sessionId) {
  if (!sessionId) return;
  const syncInd = document.getElementById('chat-sync-indicator');
  const wsBadge = document.getElementById('chat-workspace-badge');

  try {
    if (syncInd) syncInd.textContent = '🔄 SYNCING...';
    const res = await fetch(`/api/chat/sessions/messages?session_id=${encodeURIComponent(sessionId)}`);
    if (res.ok) {
      const data = await res.json();
      if (data.messages && Array.isArray(data.messages)) {
        State.chat.messages = data.messages.map(m => ({
          id: `msg_${m.id || Math.random()}`,
          role: m.role,
          content: m.content || '',
          reasoning: m.thought || '',
          images: m.images || [],
          metrics: m.metrics || {},
          timestamp: m.timestamp ? m.timestamp * 1000 : Date.now()
        }));
        saveChatHistory();
        renderChatHistory();
      }
      if (data.session) {
        const ws = data.session.workspace_path;
        if (wsBadge) {
          const name = ws ? ws.split('/').pop() : 'Active';
          wsBadge.textContent = `📁 [WS: ${name}]`;
        }
        const agentBadge = document.getElementById('chat-agent-badge');
        if (agentBadge) {
          if (data.session.agent_id && data.session.agent_id !== 'coordinator') {
            const rawSlug = data.session.agent_id.replace(/^agent_/, '').split('_')[0];
            const cleanName = rawSlug.charAt(0).toUpperCase() + rawSlug.slice(1);
            agentBadge.textContent = `🤖 [AGENT: ${cleanName}]`;
            agentBadge.style.color = 'var(--term-accent-gold)';
            agentBadge.style.borderColor = 'var(--term-accent-gold)';
          } else if (State.activeProjectAgent) {
            const aName = State.activeProjectAgent.name || State.activeProjectAgent.identity?.name || 'Agent';
            agentBadge.textContent = `🤖 [AGENT: ${aName}]`;
            agentBadge.style.color = 'var(--term-accent-gold)';
            agentBadge.style.borderColor = 'var(--term-accent-gold)';
          } else {
            agentBadge.textContent = `🤖 [AGENT: (none)]`;
            agentBadge.style.color = 'var(--term-text-muted)';
            agentBadge.style.borderColor = 'var(--term-border-dim)';
          }
        }
      }
      if (syncInd) syncInd.textContent = '✔ SYNCED';
    }
  } catch (e) {
    console.warn('Failed to fetch session messages:', e);
    if (syncInd) syncInd.textContent = '⚠️ OFFLINE';
  }
}

export async function switchSession(sessionId) {
  if (!sessionId || sessionId === State.activeSessionId) return;
  State.activeSessionId = sessionId;
  safeStorage.setItem('stonesage_active_session_id', sessionId);
  const threadSelect = document.getElementById('chat-thread-select');
  const wsThreadSelect = document.getElementById('workstation-thread-select');
  if (threadSelect) threadSelect.value = sessionId;
  if (wsThreadSelect) wsThreadSelect.value = sessionId;
  await fetchSessionMessages(sessionId);
}

export async function createNewSession(title, wsPath = "") {
  try {
    const targetWs = wsPath || (State.activeWorkspace ? State.activeWorkspace.path : '');
    const res = await fetch('/api/chat/sessions/new', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: title || "New Conversation",
        workspace_path: targetWs
      })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    const session = data.session;
    State.activeSessionId = session.session_id;
    safeStorage.setItem('stonesage_active_session_id', session.session_id);
    State.chat.messages = [];
    saveChatHistory();
    renderChatHistory();

    await loadSessionsFromServer();
    await fetchSessionMessages(session.session_id);
    return session;
  } catch (err) {
    console.error('Failed to create new session:', err);
    alert(`Could not create thread: ${err.message}`);
  }
}
window.createNewSession = createNewSession;

export async function promptCreateNewThread() {
  const title = prompt("Enter a title for this conversation thread:", "New Task / Inquiry");
  if (!title) return;
  const wsPath = State.activeWorkspace ? State.activeWorkspace.path : '';
  await createNewSession(title, wsPath);
}


export async function deleteCurrentSession() {
  if (!State.activeSessionId) return;
  const currSession = State.chatSessions.find(s => s.session_id === State.activeSessionId);
  const title = currSession ? (currSession.title || currSession.session_id) : State.activeSessionId;
  if (!confirm(`Are you sure you want to permanently delete thread "${title}"?`)) return;

  try {
    const res = await fetch('/api/chat/sessions/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: State.activeSessionId })
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.error || `HTTP ${res.status}`);
    }

    State.chatSessions = State.chatSessions.filter(s => s.session_id !== State.activeSessionId);
    State.chat.messages = [];
    saveChatHistory();
    renderChatHistory();

    if (State.chatSessions.length > 0) {
      State.activeSessionId = State.chatSessions[0].session_id;
      safeStorage.setItem('stonesage_active_session_id', State.activeSessionId);
      await loadSessionsFromServer();
      await fetchSessionMessages(State.activeSessionId);
    } else {
      await createNewSession("Default Conversation");
    }
  } catch (err) {
    console.error('Failed to delete session:', err);
    alert(`Could not delete conversation: ${err.message}`);
  }
}



export function answerApproval(answer, btn) {
  const chip = btn && btn.closest('.approval-chip');
  if (chip) chip.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  const input = document.getElementById('chat-prompt-input');
  if (!input) return;
  input.value = answer;
  submitPrompt();
}

window.answerApproval = answerApproval;
window.removeStagedImage = removeStagedImage;
window.scrollToChatBottom = scrollToChatBottom;
window.syncDetailsToggle = syncDetailsToggle;
window.toggleMessageReasoning = toggleMessageReasoning;
window.toggleParallelStreamsMode = toggleParallelStreamsMode;
window.triggerInlineNudge = triggerInlineNudge;
window.pinMessageAsInvariant = pinMessageAsInvariant;
window.addMessageToHandover = addMessageToHandover;
window.branchChatAtMessage = branchChatAtMessage;
window.storeMessageToObsidian = storeMessageToObsidian;

// ─── TTS: Read Aloud with Courage Computer Voice ────────────────────────
let chatAudioEl = null;
let ttsAbortController = null;

function getChatAudioPlayer() {
  if (!chatAudioEl) {
    chatAudioEl = document.getElementById('chat-tts-audio');
    if (!chatAudioEl) {
      chatAudioEl = document.createElement('audio');
      chatAudioEl.id = 'chat-tts-audio';
      chatAudioEl.style.display = 'none';
      document.body.appendChild(chatAudioEl);
    }
  }
  return chatAudioEl;
}

async function readMessageAloud(msgId) {
  const msg = State.chat.messages.find(m => m.id === msgId);
  if (!msg || !msg.content) return;

  // Strip markdown formatting for cleaner speech
  let text = msg.content
    .replace(/```[\s\S]*?```/g, ' [code block omitted] ')
    .replace(/`[^`]+`/g, match => match.slice(1, -1))
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/#{1,6}\s/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/>\s?\[![A-Z]+\]\n?/g, '')
    .replace(/>\s/g, '')
    .replace(/\n{2,}/g, '. ')
    .replace(/\n/g, ' ')
    .trim();

  // Kokoro has a practical limit; truncate gracefully
  if (text.length > 800) {
    text = text.substring(0, 797) + '...';
  }

  if (!text) return;

  // Find and update the button
  const btn = document.querySelector(`#msg-${msgId} .chat-tts-btn`);
  if (btn) { btn.textContent = '[🔊 ...]'; btn.disabled = true; }

  // Stop any playing audio
  const audio = getChatAudioPlayer();
  audio.pause();
  audio.src = '';
  if (ttsAbortController) ttsAbortController.abort();
  ttsAbortController = new AbortController();

  try {
    const res = await fetch('/api/presence/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice: 'bm_george', speed: 1.06 }),
      signal: ttsAbortController.signal
    });
    const data = await res.json();
    if (data.ok && data.audio_base64) {
      audio.src = `data:audio/wav;base64,${data.audio_base64}`;
      audio.play().catch(e => console.log('TTS autoplay prevented:', e));
      if (btn) btn.textContent = '[🔊 ■]';
      audio.onended = () => { if (btn) { btn.textContent = '[🔊 READ]'; btn.disabled = false; } };
      // Click again to stop
      if (btn) {
        btn.disabled = false;
        btn.onclick = () => {
          audio.pause();
          audio.src = '';
          btn.textContent = '[🔊 READ]';
          btn.onclick = () => readMessageAloud(msgId);
        };
      }
      return;
    } else {
      console.warn('TTS failed:', data.error);
    }
  } catch (e) {
    if (e.name !== 'AbortError') console.warn('TTS error:', e);
  }

  if (btn) { btn.textContent = '[🔊 READ]'; btn.disabled = false; }
}

window.readMessageAloud = readMessageAloud;

// ─── STT: Push-to-Talk via MediaRecorder + Local Faster Whisper ─────────
let micStream = null;
let mediaRecorder = null;
let audioChunks = [];
let sttActive = false;

async function toggleChatMic() {
  if (sttActive) {
    stopChatMic();
    return;
  }

  const micBtn = document.getElementById('chat-mic-btn');

  // Request microphone access
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    console.warn('Mic access failed:', e);
    if (e.name === 'NotAllowedError') {
      alert('Microphone access denied. Allow microphone permissions for this site.');
    } else if (e.name === 'NotFoundError') {
      alert('No microphone found.');
    } else {
      alert(`Microphone error: ${e.message}`);
    }
    return;
  }

  audioChunks = [];

  // Pick a supported mime type
  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus'
    : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm'
    : MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4'
    : '';

  try {
    mediaRecorder = new MediaRecorder(micStream, mimeType ? { mimeType } : {});
  } catch (e) {
    console.warn('MediaRecorder init failed:', e);
    micStream.getTracks().forEach(t => t.stop());
    micStream = null;
    return;
  }

  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) audioChunks.push(e.data);
  };

  mediaRecorder.onstop = async () => {
    // Release mic immediately
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }

    if (audioChunks.length === 0) return;

    const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
    audioChunks = [];

    if (blob.size < 100) {
      console.warn('[Chat] Audio too short to transcribe');
      return;
    }

    // Show transcribing state
    if (micBtn) { micBtn.textContent = '🎤 ...'; micBtn.disabled = true; }

    // Convert blob to base64 for JSON transport (server's do_POST pre-parses JSON)
    const arrayBuf = await blob.arrayBuffer();
    const bytes = new Uint8Array(arrayBuf);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
    const audioBase64 = btoa(binary);

    try {
      const res = await fetch('/api/voice/transcribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ audio_base64: audioBase64, mime_type: blob.type || 'audio/webm' })
      });
      const data = await res.json();
      if (data.ok && data.text) {
        const promptInput = document.getElementById('chat-prompt-input');
        if (promptInput) {
          const existing = promptInput.value.trim();
          promptInput.value = existing ? existing + ' ' + data.text : data.text;
          promptInput.style.height = 'auto';
          promptInput.style.height = promptInput.scrollHeight + 'px';
          promptInput.focus();
        }
        console.log('[Chat] Whisper transcription:', data.text);

        // Conversation mode: auto-submit after transcription
        if (window._convoMode && data.text.trim()) {
          updateConvoStatus('💬 Sending to AI...');
          setTimeout(() => submitPrompt(), 150);
        }
      } else {
        console.warn('[Chat] Transcription failed:', data.error);
        // In convo mode, re-record on failure
        if (window._convoMode) {
          updateConvoStatus('⚠ Retry — speak again');
          setTimeout(() => convoStartRecording(), 1500);
        }
      }
    } catch (e) {
      console.warn('[Chat] Transcription request error:', e);
      if (window._convoMode) {
        updateConvoStatus('⚠ Error — retrying...');
        setTimeout(() => convoStartRecording(), 2000);
      }
    } finally {
      if (!window._convoMode && micBtn) { micBtn.textContent = '🎤 MIC'; micBtn.disabled = false; }
    }
  };

  // Start recording — collect chunks every 250ms
  mediaRecorder.start(250);
  sttActive = true;

  if (micBtn) {
    micBtn.textContent = '🔴 STOP';
    micBtn.style.background = 'var(--term-accent-red, #ef4444)';
    micBtn.style.color = '#fff';
  }
  console.log('[Chat] Recording started (local Whisper STT)...');
}

function stopChatMic() {
  sttActive = false;
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop(); // triggers onstop → transcribe
  }
  const micBtn = document.getElementById('chat-mic-btn');
  if (micBtn) {
    micBtn.textContent = '🎤 ...';
    micBtn.style.background = '';
    micBtn.style.color = '';
  }
}

window.toggleChatMic = toggleChatMic;
window.stopChatMic = stopChatMic;

// ─── Conversation Mode: Hands-Free Voice Loop ──────────────────────────
// Flow: Record → Whisper STT → Auto-submit → Stream LLM → TTS → Repeat
window._convoMode = false;
let convoAudioEl = null;

function getConvoAudioPlayer() {
  if (!convoAudioEl) {
    convoAudioEl = document.createElement('audio');
    convoAudioEl.id = 'chat-convo-audio';
    convoAudioEl.style.display = 'none';
    document.body.appendChild(convoAudioEl);
  }
  return convoAudioEl;
}

function updateConvoStatus(text) {
  const el = document.getElementById('convo-status');
  if (el) el.textContent = text;
}

function toggleConvoMode() {
  const convoBtn = document.getElementById('chat-convo-btn');
  const micBtn = document.getElementById('chat-mic-btn');
  const statusEl = document.getElementById('convo-status');

  if (window._convoMode) {
    // Stop conversation mode
    window._convoMode = false;
    sttActive = false;

    // Stop any active recording
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      mediaRecorder.stop();
    }
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }

    // Stop any playing TTS, and release the always-open barge-in microphone
    if (window._convoSpeech) window._convoSpeech.stop();
    closeConvoListener();
    const audio = getConvoAudioPlayer();
    audio.pause();
    audio.src = '';

    if (convoBtn) {
      convoBtn.textContent = '🗣️ CONVO';
      convoBtn.style.background = '';
      convoBtn.style.color = '';
    }
    if (micBtn) {
      micBtn.textContent = '🎤 MIC';
      micBtn.style.background = '';
      micBtn.style.color = '';
      micBtn.disabled = false;
    }
    if (statusEl) statusEl.textContent = '';

    // Remove pulsing border
    const chatView = document.getElementById('view-chat');
    if (chatView) chatView.style.boxShadow = '';

    console.log('[Chat] Conversation mode OFF');
    return;
  }

  // Start conversation mode
  window._convoMode = true;

  if (convoBtn) {
    convoBtn.textContent = '🗣️ STOP';
    convoBtn.style.background = '#7c3aed';
    convoBtn.style.color = '#fff';
  }

  // Add pulsing border to indicate active convo mode
  const chatView = document.getElementById('view-chat');
  if (chatView) chatView.style.boxShadow = '0 0 0 2px #7c3aed, 0 0 12px rgba(124, 58, 237, 0.3)';

  if (statusEl) statusEl.textContent = '🎙️ Listening...';

  console.log('[Chat] Conversation mode ON — speak to begin');
  convoStartRecording();
}

/**
 * A finished utterance (either capture path) -> words -> the chat. `info.barge` marks a take that began while Computer was
 * talking: if it turns out to be Computer's own words coming back through the mic it is thrown away, and after two of those
 * barge-in switches itself off for the session (the phone cannot tell its speaker from you).
 */
async function convoSubmitAudio(audioBase64, mimeType, info) {
  try {
    const res = await fetch('/api/voice/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio_base64: audioBase64, mime_type: mimeType })
    });
    const data = await res.json();
    if (data.ok && data.text && data.text.trim()) {
      if (info && info.barge && looksLikeEcho(window._convoLastSpoken, data.text)) {
        window._convoEchoStrikes = (window._convoEchoStrikes || 0) + 1;
        console.warn('[Convo] that was Computer hearing itself, ignored:', data.text);
        if (window._convoEchoStrikes >= 2) {
          window._bargeDisabled = true;
          updateConvoStatus('✋ interrupt off: hearing itself');
          refreshBargeButton();
        }
        setTimeout(() => convoStartRecording(), 500);
        return;
      }
      const promptInput = document.getElementById('chat-prompt-input');
      if (promptInput) {
        promptInput.value = data.text;
        promptInput.style.height = 'auto';
        promptInput.style.height = promptInput.scrollHeight + 'px';
      }
      console.log('[Convo] Transcribed:', data.text, data.engine ? `(${data.engine}, ${data.ms} ms)` : '');
      if (window._convoT) window._convoT.text = performance.now();
      updateConvoStatus('💬 Sending...');
      setTimeout(() => submitPrompt(), 150);
    } else {
      console.warn('[Convo] Empty or failed transcription');
      updateConvoStatus('🎙️ Listening...');
      setTimeout(() => convoStartRecording(), 1000);
    }
  } catch (e) {
    console.warn('[Convo] Transcription error:', e);
    updateConvoStatus('⚠ Retrying...');
    setTimeout(() => convoStartRecording(), 2000);
  }
}

// ─── Barge-in: talk over Computer to stop it (opt-in, see convo_audio.js) ─────────────────────────────────────────────
// Raw 16 kHz capture with a pre-roll replaces MediaRecorder while it is on, and the mic stays open for the whole conversation.
const BARGE_KEY = 'stonesage.convo.bargein';
let convoListener = null;      // createListenerCore(...)
let convoMic = null;           // attachMic(...) handle
let convoPcmStream = null;     // the open MediaStream
let busyTimer = null;

function bargeInWanted() {
  try { return localStorage.getItem(BARGE_KEY) === '1' && !window._bargeDisabled; } catch (_) { return false; }
}

function refreshBargeButton() {
  const b = document.getElementById('chat-barge-btn');
  if (!b) return;
  const on = bargeInWanted();
  b.style.background = on ? '#7c3aed' : 'transparent';
  b.style.color = on ? '#fff' : '#7c3aed';
  b.title = window._bargeDisabled ? 'Interrupt switched off: Computer kept hearing itself. Tap to try again.'
    : on ? 'Interrupt is ON: talk over Computer to stop it. Tap to turn off.'
    : 'Interrupt is OFF. Tap to let you talk over Computer (experimental).';
}

function toggleBargeIn() {
  const turningOn = !(bargeInWanted());
  try { localStorage.setItem(BARGE_KEY, turningOn ? '1' : '0'); } catch (_) {}
  window._bargeDisabled = false;
  window._convoEchoStrikes = 0;
  if (!turningOn) closeConvoListener();
  refreshBargeButton();
  if (window._convoMode) updateConvoStatus(turningOn ? '✋ interrupt on from the next turn' : '✋ interrupt off');
}

/** How loud Computer is at this instant, from the loudness envelope of the clip that is playing. */
function convoPlaybackLevel() {
  const env = window._convoClipEnv;
  const a = getConvoAudioPlayer();
  if (!env || a.paused || a.ended) return 0;
  return envelopeAt(env, a.currentTime * 1000);
}

function bytesToBase64(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

function base64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function closeConvoListener() {
  clearTimeout(busyTimer);
  if (convoMic) { convoMic.close(); convoMic = null; }
  if (convoPcmStream) { convoPcmStream.getTracks().forEach((t) => t.stop()); convoPcmStream = null; }
  convoListener = null;
}

async function ensureConvoListener() {
  if (convoListener) return true;
  try {
    convoPcmStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    convoListener = createListenerCore({
      makeEndpointer: () => createEndpointer(),
      getReference: () => convoPlaybackLevel(),
      onUtterance: (wavBytes, info) => {
        window._convoT = { end: performance.now(), barge: !!info.barge };
        updateConvoStatus('🔄 Transcribing...');
        convoSubmitAudio(bytesToBase64(wavBytes), 'audio/wav', info);
      },
      onBarge: () => {
        console.log('[Convo] interrupted by voice');
        const sp = window._convoSpeech;
        window._convoLastSpoken = sp ? sp.text() : '';
        if (sp) sp.stop();
        try { stopGeneration(); } catch (_) {}
        updateConvoStatus('🗣️ Hearing you...');
      },
      onState: (m) => {
        clearTimeout(busyTimer);
        if (m === 'busy') {                       // never stay deaf: a turn that produced no speech must not end listening
          busyTimer = setTimeout(() => { if (convoListener && window._convoMode && convoListener.mode === 'busy') convoListener.listen(); }, 45000);
        }
      },
    });
    convoMic = attachMic(convoPcmStream, convoListener);
    if (!convoMic) throw new Error('no WebAudio');
    return true;
  } catch (e) {
    console.warn('[Convo] barge-in capture unavailable, using the plain recorder:', e);
    closeConvoListener();
    if (convoPcmStream) { convoPcmStream.getTracks().forEach((t) => t.stop()); convoPcmStream = null; }
    return false;
  }
}

/** Speech state from the audio player: Computer starts talking (the gate watches for you) / is done. */
function convoSpeakingHook(on) {
  if (convoListener && window._convoMode) convoListener.speaking(on);
}

/**
 * End-of-speech detection for conversation mode: energy with an adaptive noise floor (no server VAD, no model to load).
 * push(rms, tMs) is fed one level reading every ~40 ms and answers:
 *   'waiting'  nobody has spoken yet         'speech'  someone is speaking
 *   'end'      spoke, then ~0.8 s of quiet: stop and transcribe
 *   'noise'    a blip shorter than minSpeechMs (a cough, a door): ignored, keeps listening
 *   'noSpeech' nothing but silence for noSpeechMs: drop the take and listen again
 * The floor is measured in the first 300 ms and clamped, so starting to talk at once cannot raise the bar out of reach;
 * it then drifts slowly with the room (a TV, a fan) while nobody speaks.
 */
function createEndpointer(opts) {
  const cfg = Object.assign({ calibrateMs: 300, onMs: 100, hangMs: 800, minSpeechMs: 250, noSpeechMs: 12000,
                              minOn: 0.02, minOff: 0.012, onFactor: 3.5, offFactor: 2, maxFloor: 0.02 }, opts || {});
  let floor = null, calib = [], t0 = null, state = 'waiting', aboveSince = null, belowSince = null, speechStart = null;
  return {
    push(rms, t) {
      if (t0 === null) t0 = t;
      if (floor === null) {
        calib.push(rms);
        if (t - t0 < cfg.calibrateMs) return 'waiting';
        calib.sort((a, b) => a - b);
        floor = Math.min(calib[Math.floor(calib.length * 0.25)], cfg.maxFloor);    // the quiet quarter: speech may start mid-window
      }
      const on = Math.max(cfg.minOn, floor * cfg.onFactor);
      const off = Math.max(cfg.minOff, floor * cfg.offFactor);
      if (state === 'waiting') {
        if (rms > on) {
          if (aboveSince === null) aboveSince = t;
          if (t - aboveSince >= cfg.onMs) { state = 'speech'; speechStart = aboveSince; belowSince = null; return 'speech'; }
          return 'waiting';
        }
        aboveSince = null;
        floor = Math.min(floor * 0.98 + rms * 0.02, cfg.maxFloor);
        return t - t0 >= cfg.noSpeechMs ? 'noSpeech' : 'waiting';
      }
      if (rms < off) {
        if (belowSince === null) belowSince = t;
        if (t - belowSince >= cfg.hangMs) {
          const spoke = belowSince - speechStart;
          state = 'waiting'; aboveSince = null; belowSince = null;
          return spoke >= cfg.minSpeechMs ? 'end' : 'noise';
        }
      } else {
        belowSince = null;                       // between the two thresholds still counts as speech: a pause, not the end
      }
      return 'speech';
    }
  };
}

/** Watches the microphone level and reports speech start / end / silence through the callbacks. Returns a stop function. */
function startEndpointing(stream, onSpeech, onFinish) {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return { stop() {}, active: false };
  const ctx = new AC();
  try { ctx.resume(); } catch (_) {}
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  const src = ctx.createMediaStreamSource(stream);
  src.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  const ep = createEndpointer();
  let done = false;
  const stop = () => {
    if (done) return;
    done = true;
    clearInterval(timer);
    try { src.disconnect(); ctx.close(); } catch (_) {}
  };
  const timer = setInterval(() => {
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const r = ep.push(Math.sqrt(sum / buf.length), performance.now());
    if (r === 'speech') onSpeech();
    else if (r === 'end') { stop(); onFinish(true); }
    else if (r === 'noSpeech') { stop(); onFinish(false); }
  }, 40);
  return { stop, active: true };
}

async function convoStartRecording() {
  if (!window._convoMode) return;
  window._convoT = null;                                   // latency timing is per turn
  if (bargeInWanted() && await ensureConvoListener()) {    // interrupt on: the always-open capture path (else the plain recorder)
    if (!window._convoMode) return;
    convoListener.listen();
    updateConvoStatus('🎙️ Listening... (talk over me any time)');
    return;
  }

  const micBtn = document.getElementById('chat-mic-btn');
  updateConvoStatus('🎙️ Listening...');

  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    console.warn('[Convo] Mic access failed, stopping convo mode:', e);
    window._convoMode = false;
    toggleConvoMode(); // reset UI
    alert(`Mic access failed: ${e.message}`);
    return;
  }

  audioChunks = [];

  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus'
    : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm'
    : MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4'
    : '';

  try {
    mediaRecorder = new MediaRecorder(micStream, mimeType ? { mimeType } : {});
  } catch (e) {
    console.warn('[Convo] MediaRecorder init failed:', e);
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
    return;
  }

  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) audioChunks.push(e.data);
  };

  // Hands-free: stop by itself after ~0.8 s of quiet (see createEndpointer). A tap on the mic still stops it, and a take in
  // which nobody spoke is dropped instead of sent to the transcriber (which invents words from silence).
  let heard = false;
  let dropTake = false;
  const endpointing = startEndpointing(micStream, () => {
    heard = true;
    updateConvoStatus('🗣️ Hearing you...');
  }, (spoke) => {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
      dropTake = !spoke;
      window._convoT = { end: performance.now() };
      mediaRecorder.stop();
    }
  });

  // Reuse the same onstop handler — it checks window._convoMode
  mediaRecorder.onstop = async () => {
    endpointing.stop();
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
    if (window._convoMode && endpointing.active && !heard && dropTake) {      // silence only: listen again, send nothing
      audioChunks = [];
      setTimeout(() => convoStartRecording(), 300);
      return;
    }
    if (audioChunks.length === 0) {
      if (window._convoMode) { setTimeout(() => convoStartRecording(), 500); }
      return;
    }
    if (!window._convoT) window._convoT = { end: performance.now() };            // stopped by a tap

    const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
    audioChunks = [];

    if (blob.size < 100) {
      if (window._convoMode) { setTimeout(() => convoStartRecording(), 500); }
      return;
    }

    updateConvoStatus('🔄 Transcribing...');

    const arrayBuf = await blob.arrayBuffer();
    const bytes = new Uint8Array(arrayBuf);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
    const audioBase64 = btoa(binary);

    await convoSubmitAudio(audioBase64, blob.type || 'audio/webm', null);
  };

  mediaRecorder.start(250);
  sttActive = true;

  if (micBtn) {
    micBtn.textContent = '🔴 REC';
    micBtn.style.background = 'var(--term-accent-red, #ef4444)';
    micBtn.style.color = '#fff';
  }

  // Auto-stop after 30 seconds of continuous recording to prevent huge uploads
  // (bound to THIS recorder: with hands-free takes a stale timer used to stop a newer recording in mid-sentence)
  const thisRecorder = mediaRecorder;
  setTimeout(() => {
    if (window._convoMode && thisRecorder.state === 'recording') {
      dropTake = !heard;
      thisRecorder.stop();
    }
  }, 30000);
}

// Tools that make the reply wait seconds. The lines must stay in courage/agent.py CANNED_LINES (that is what gets pre-made).
const FILLER_DELAY_MS = 1200;     // a wait shorter than this is not worth a filler
const SLOW_TOOL_FILLER = { camera_look: 'Let me have a look.', camera_scan: 'Let me have a look.', presence_now: 'One moment.',
                           think_harder: 'One moment.' };

/** Text fit for the speaker: no tool-call markers (their JSON used to be read out), no markdown. */
function speechText(s) {
  return (s || '')
    .replace(/:::(TOOL_CALL|TOOL_RESULT|APPROVAL):::[\s\S]*?:::END_(TOOL_CALL|TOOL_RESULT|APPROVAL):::/g, ' ')
    .replace(/```[\s\S]*?```/g, ' [code block omitted] ')
    .replace(/`[^`]+`/g, match => match.slice(1, -1))
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/#{1,6}\s/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/>\s?\[![A-Z]+\]\n?/g, '')
    .replace(/>\s/g, '')
    .trim()
    .replace(/([.!?…:;])?[ \t]*\n{2,}\s*/g, (m, p) => (p || '.') + ' ')   // a paragraph break is a pause, not ".."
    .replace(/\n/g, ' ')
    .trim();
}

/**
 * Conversation mode: speak a reply while it is still arriving (Computer streams a long reply sentence by sentence).
 * push() takes text as it comes; finished sentences go to the TTS, fetched ahead and played in order. end() speaks the
 * rest and listens again once the last clip ends; it returns false when nothing came through push() (e.g. a reply
 * recovered after a phone lock), so the caller can speak the whole reply the old way.
 */
function createConvoSpeech() {
  let buffer = '';
  let queued = 0;
  let playing = false;
  let ended = false;
  let cancelled = false;
  let resumed = false;
  let fillerSaid = false;
  let spoken = '';                                        // everything pushed so far (barge-in compares what it hears with this)
  const clips = [];                                       // promises of {src, env} (null = TTS failed), in order

  const fetchClip = async (text) => {
    try {
      const res = await fetch('/api/presence/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice: 'bm_george', speed: 1.06 })
      });
      const data = await res.json();
      if (!(data.ok && data.audio_base64)) return null;
      let env = null;                                     // how loud the clip is over time: barge-in tells it from you
      try { env = wavEnvelope(base64ToBytes(data.audio_base64)); } catch (_) {}
      return { src: `data:audio/wav;base64,${data.audio_base64}`, env };
    } catch (_) {
      return null;
    }
  };

  const resume = () => {
    if (resumed || cancelled || !window._convoMode) return;
    resumed = true;
    setTimeout(() => convoStartRecording(), 400);
  };

  const playNext = async () => {
    if (cancelled || !window._convoMode) { playing = false; return; }
    if (!clips.length) {
      playing = false;
      if (ended) resume();
      return;
    }
    playing = true;
    const clip = await clips.shift();
    if (cancelled || !window._convoMode) { playing = false; return; }
    if (!clip) { playNext(); return; }
    const audio = getConvoAudioPlayer();
    audio.onended = () => playNext();
    audio.onerror = () => playNext();
    audio.src = clip.src;
    window._convoClipEnv = clip.env;
    audio.play().then(() => {
      convoSpeakingHook(true);
      const t = window._convoT;
      if (t && t.end && !t.audio) {
        t.audio = performance.now();
        console.log(`[Convo] you stopped talking -> words ${Math.round((t.text || t.end) - t.end)} ms -> first sound ${Math.round(t.audio - t.end)} ms`);
      }
    }).catch((e) => { console.warn('[Convo] TTS autoplay prevented:', e); playNext(); });
  };

  const enqueue = (text) => {
    const clean = speechText(text);
    if (!clean) return;
    queued++;
    updateConvoStatus('🔊 Speaking...');
    clips.push(fetchClip(clean));
    if (!playing) playNext();
  };

  // Clips cut at sentence ends: the first is the first sentence alone (talking starts sooner), later ones gather
  // >= 120 chars. Kokoro on CPU needs ~40 ms per character, so a whole reply in one clip took 13 s before a sound.
  const takeClips = (final) => {
    const re = /[.!?…]["')\]*_]*\s+/g;
    let start = 0;
    let m;
    while ((m = re.exec(buffer))) {
      const cut = m.index + m[0].length;
      if (cut - start >= (queued ? 120 : 1)) {
        enqueue(buffer.slice(start, cut));
        start = cut;
      }
    }
    buffer = buffer.slice(start);
    if (final && buffer.trim()) enqueue(buffer);
    if (final) buffer = '';
  };

  return {
    /** A short cached line ("One moment.") over a slow tool call, so the wait is not silence. Once per turn, never after the
     *  real reply has started, and it does not count as the reply (the first real sentence still goes out on its own). */
    filler(text) {
      if (cancelled || ended || fillerSaid || queued) return;
      fillerSaid = true;
      const pending = fetchClip(text);                    // fetched now (cached: instant), spoken only if the wait is real
      setTimeout(() => {
        if (cancelled || ended || queued) return;         // the reply arrived in time: stay quiet
        clips.push(pending);
        if (!playing) playNext();
      }, FILLER_DELAY_MS);
    },
    push(text) {
      if (cancelled || ended || !text) return;
      buffer += text;
      spoken += text;
      takeClips(false);
    },
    /** What Computer was saying (for the echo check after a barge-in). */
    text() { return spoken; },
    /** Barge-in: cut Computer off now. Nothing queued is spoken, and listening is started by the caller, not by this queue. */
    stop() {
      cancelled = true;
      clips.length = 0;
      playing = false;
      try { const a = getConvoAudioPlayer(); a.pause(); a.removeAttribute('src'); } catch (_) {}
      window._convoClipEnv = null;
    },
    end() {
      ended = true;
      takeClips(true);
      if (!queued) return false;
      if (!playing && !clips.length) resume();
      return true;
    },
    cancel() { cancelled = true; }
  };
}

/** Speak a whole reply that did not arrive as a stream (e.g. recovered after a phone lock), clip by clip. */
function convoTtsAndContinue(responseText) {
  if (!window._convoMode) return;
  const speech = createConvoSpeech();
  speech.push(speechText(responseText));        // tool markers out first: a clip cut must never split one
  if (!speech.end()) setTimeout(() => convoStartRecording(), 500);   // nothing to say: straight back to listening
}

window.toggleConvoMode = toggleConvoMode;
window.toggleBargeIn = toggleBargeIn;
window.convoStartRecording = convoStartRecording;
window.convoTtsAndContinue = convoTtsAndContinue;
window.updateConvoStatus = updateConvoStatus;
