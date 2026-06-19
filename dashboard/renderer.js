// DOM Element Selectors
const navItems = document.querySelectorAll('.nav-item');
const tabPanels = document.querySelectorAll('.tab-panel');

const serverStateBadge = document.getElementById('server-state-badge');
const stateDesc = document.getElementById('state-desc');
const cpuValue = document.getElementById('cpu-value');
const cpuProgress = document.getElementById('cpu-progress');
const ramValue = document.getElementById('ram-value');
const ramProgress = document.getElementById('ram-progress');
const uptimeValue = document.getElementById('uptime-value');
const playerCount = document.getElementById('player-count');
const playersListContainer = document.getElementById('players-list-container');

const btnStart = document.getElementById('btn-start');
const btnStop = document.getElementById('btn-stop');
const btnSleep = document.getElementById('btn-sleep');

const aiModelBadge = document.getElementById('ai-model-badge');
const aiModelDesc = document.getElementById('ai-model-desc');
const btnWarmupAi = document.getElementById('btn-warmup-ai');


const consoleLog = document.getElementById('console-log');
const consoleInputForm = document.getElementById('console-input-form');
const consoleInput = document.getElementById('console-input');

const memoriesPlayersList = document.getElementById('memories-players-list');
const memoryChatLog = document.getElementById('memory-chat-log');
const btnClearAllMemories = document.getElementById('btn-clear-all-memories');
const btnClearPlayerMemory = document.getElementById('btn-clear-player-memory');

const searchInput = document.getElementById('search-query');
const btnSearch = document.getElementById('btn-search');
const searchResultsContainer = document.getElementById('search-results-container');

// Settings & Setup selectors
const setupScreen = document.getElementById('setup-screen');
const btnSelectDir = document.getElementById('btn-select-dir');
const btnChangeDir = document.getElementById('btn-change-dir');
const settingsServerDirPath = document.getElementById('settings-server-dir-path');
const setupError = document.getElementById('setup-error');

// State Variables
let currentTab = 'status';
let selectedPlayerMemory = null;
let playerMemoriesData = {};
let lastConsoleLineCount = 0;
let appConfig = { serverDir: '' };

// Tab Navigation
navItems.forEach(item => {
  item.addEventListener('click', () => {
    const tab = item.dataset.tab;
    if (tab === currentTab) return;
    
    // Update active nav class
    navItems.forEach(i => i.classList.remove('active'));
    item.classList.add('active');
    
    // Update active tab panel
    tabPanels.forEach(p => p.classList.remove('active'));
    document.getElementById(`tab-${tab}`).classList.add('active');
    
    currentTab = tab;
    
    // Tab-specific trigger
    if (tab === 'memories') {
      loadPlayerMemories();
    } else if (tab === 'properties') {
      loadServerProperties();
    }
  });
});

// Update Dashboard Status
function updateDashboard() {
  if (!appConfig.serverDir) return;
  window.api.getStatus()
    .then(data => {
      // Update badge
      const state = data.status;
      serverStateBadge.textContent = state;
      serverStateBadge.className = `state-badge ${state.toLowerCase()}`;
      
      // Update badge description
      if (state === 'SLEEPING') {
        stateDesc.textContent = "Listening on port 25565 for incoming player connections.";
      } else if (state === 'STARTING') {
        stateDesc.textContent = "Java subprocess booting up homestead cozy mods...";
      } else if (state === 'RUNNING') {
        stateDesc.textContent = "Minecraft server online. AgentRCON daemon watching logs.";
      } else if (state === 'STOPPING') {
        stateDesc.textContent = "Safely saving worlds and stopping background threads.";
      }
      
      // Update control buttons active states
      btnStart.disabled = (state !== 'SLEEPING');
      btnStop.disabled = (state !== 'RUNNING' && state !== 'STARTING');
      btnSleep.disabled = (state !== 'RUNNING' && state !== 'STARTING');
      
      // Update telemetry
      cpuValue.textContent = data.cpu.toFixed(1);
      cpuProgress.style.width = `${Math.min(data.cpu, 100)}%`;
      
      ramValue.textContent = data.ram.toFixed(2);
      // RAM progress as percentage of 8GB
      const ramPercent = (data.ram / 8.0) * 100;
      ramProgress.style.width = `${Math.min(ramPercent, 100)}%`;
      
      uptimeValue.textContent = data.uptime;
      playerCount.textContent = data.player_count;
      
      // Update AI model status
      const modelStatus = data.model_status || 'idle';
      aiModelBadge.textContent = modelStatus.toUpperCase();
      
      if (modelStatus === 'ready') {
        aiModelBadge.className = 'state-badge running';
        aiModelDesc.textContent = "AI model loaded in memory and ready for instant responses.";
        btnWarmupAi.disabled = true;
        btnWarmupAi.textContent = "AI Initialized";
      } else if (modelStatus === 'loading') {
        aiModelBadge.className = 'state-badge starting';
        aiModelDesc.textContent = "Ollama is currently loading the model into memory...";
        btnWarmupAi.disabled = true;
        btnWarmupAi.textContent = "Initializing...";
      } else {
        aiModelBadge.className = 'state-badge sleeping';
        aiModelDesc.textContent = "Model is not loaded. First response will have a cold-start delay.";
        btnWarmupAi.disabled = false;
        btnWarmupAi.textContent = "Initialize AI";
      }
      
      // Update player list
      if (data.online_players.length > 0) {
        playersListContainer.className = "";
        playersListContainer.innerHTML = data.online_players.map(p => `
          <div class="player-tag">
            <span class="player-avatar">👤</span>
            <span class="player-name">${p}</span>
          </div>
        `).join('');
      } else {
        playersListContainer.className = "players-list-empty";
        playersListContainer.textContent = "No players currently online.";
      }
    })
    .catch(err => {
      console.error("Failed to fetch status:", err);
    });
}

// Update Console Logs
function updateConsoleLogs() {
  if (!appConfig.serverDir) return;
  window.api.getLogs()
    .then(data => {
      const logs = data.logs || [];
      if (logs.length === 0) {
        consoleLog.innerHTML = `<div class="console-line info">[System] No log entries yet. Start the server to view logs.</div>`;
        return;
      }
      
      // Only rebuild if log count has changed
      if (logs.length !== lastConsoleLineCount) {
        lastConsoleLineCount = logs.length;
        
        consoleLog.innerHTML = logs.map(line => {
          let lineClass = 'info';
          if (line.includes('[Server thread/WARN]')) {
            lineClass = 'warn';
          } else if (line.includes('[Server thread/ERROR]') || line.includes('Exception in thread')) {
            lineClass = 'error';
          } else if (line.includes('] [Rcon] AgentRCON:') || line.includes('say AgentRCON:')) {
            lineClass = 'agent';
          } else if (reMatchesChat(line)) {
            lineClass = 'chat';
          }
          
          return `<div class="console-line ${lineClass}">${escapeHtml(line)}</div>`;
        }).join('');
        
        // Auto-scroll to bottom
        consoleLog.scrollTop = consoleLog.scrollHeight;
      }
    })
    .catch(err => {
      console.error("Failed to fetch logs:", err);
    });
}

function reMatchesChat(line) {
  return /<[^>]+>/.test(line);
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Send Command via Console
consoleInputForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const cmd = consoleInput.value.trim();
  if (!cmd) return;
  
  // Inject visual command echo into log immediately
  const echoLine = document.createElement('div');
  echoLine.className = 'console-line';
  echoLine.style.color = 'hsl(280, 80%, 75%)';
  echoLine.textContent = `> ${cmd}`;
  consoleLog.appendChild(echoLine);
  consoleLog.scrollTop = consoleLog.scrollHeight;
  
  consoleInput.value = '';
  
  window.api.runCommand(cmd)
    .then(resp => {
      const responseLine = document.createElement('div');
      responseLine.className = 'console-line';
      responseLine.style.color = 'hsl(300, 70%, 75%)';
      responseLine.textContent = resp.output || resp.message || "Command executed.";
      consoleLog.appendChild(responseLine);
      consoleLog.scrollTop = consoleLog.scrollHeight;
    })
    .catch(err => {
      const errLine = document.createElement('div');
      errLine.className = 'console-line error';
      errLine.textContent = `Error: ${err.message || err}`;
      consoleLog.appendChild(errLine);
      consoleLog.scrollTop = consoleLog.scrollHeight;
    });
});

// Load Player Memories History
function loadPlayerMemories() {
  window.api.getHistory()
    .then(data => {
      playerMemoriesData = data || {};
      const players = Object.keys(playerMemoriesData);
      
      if (players.length === 0) {
        memoriesPlayersList.innerHTML = `<li class="players-list-empty">No players saved.</li>`;
        memoryChatLog.className = "memory-chat-log-empty";
        memoryChatLog.textContent = "No persistent players found in memories.";
        btnClearPlayerMemory.style.display = 'none';
        return;
      }
      
      memoriesPlayersList.innerHTML = players.map(p => `
        <li class="memory-player-item ${selectedPlayerMemory === p ? 'active' : ''}" data-player="${p}">
          👤 ${p}
        </li>
      `).join('');
      
      // Re-attach click listeners
      document.querySelectorAll('.memory-player-item').forEach(item => {
        item.addEventListener('click', () => {
          const p = item.dataset.player;
          selectedPlayerMemory = p;
          
          document.querySelectorAll('.memory-player-item').forEach(i => i.classList.remove('active'));
          item.classList.add('active');
          
          renderPlayerMemory(p);
        });
      });
      
      if (selectedPlayerMemory && playerMemoriesData[selectedPlayerMemory]) {
        renderPlayerMemory(selectedPlayerMemory);
      } else {
        btnClearPlayerMemory.style.display = 'none';
      }
    })
    .catch(err => {
      console.error("Failed to load memories:", err);
    });
}

function renderPlayerMemory(player) {
  const history = playerMemoriesData[player] || [];
  memoryChatLog.className = "memory-chat-log";
  btnClearPlayerMemory.style.display = 'block';
  
  if (history.length === 0) {
    memoryChatLog.innerHTML = `<div class="memory-chat-log-empty">Conversation log is empty.</div>`;
    return;
  }
  
  memoryChatLog.innerHTML = history.map(msg => {
    const role = msg.role;
    let content = msg.content;
    
    let parsedContentHtml = "";
    if (role === 'assistant') {
      const thoughtMatch = content.match(/<THOUGHT>\s*([\s\S]*?)\s*<\/THOUGHT>/i);
      const callMatch = content.match(/<CALL\s+name="([^"]+)"\s*>([\s\S]*?)<\/CALL>/i);
      const sayMatch = content.match(/<SAY>\s*([\s\S]*?)\s*<\/SAY>/i);
      
      if (thoughtMatch) {
        parsedContentHtml += `<div class="mem-thought"><strong>AI Thought:</strong> ${escapeHtml(thoughtMatch[1].trim())}</div>`;
      }
      if (callMatch) {
        parsedContentHtml += `<div class="mem-call"><strong>Tool Call:</strong> ${callMatch[1].trim()}(${escapeHtml(callMatch[2].trim())})</div>`;
      }
      
      let sayText = "";
      if (sayMatch) {
        sayText = sayMatch[1].trim();
      } else {
        sayText = content.replace(/<THOUGHT>[\s\S]*?<\/THOUGHT>/gi, '').replace(/<CALL[\s\S]*?<\/CALL>/gi, '').trim();
      }
      
      if (sayText) {
        parsedContentHtml += `<div class="mem-text">${escapeHtml(sayText)}</div>`;
      }
    } else {
      parsedContentHtml = `<div class="mem-text">${escapeHtml(content)}</div>`;
    }
    
    return `
      <div class="mem-msg ${role}">
        <span class="mem-msg-header">${role === 'user' ? player : 'AgentRCON'}</span>
        ${parsedContentHtml}
      </div>
    `;
  }).join('');
  
  memoryChatLog.scrollTop = memoryChatLog.scrollHeight;
}

// Mod Lang Search Scanner
btnSearch.addEventListener('click', runSearch);
searchInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runSearch();
});

function runSearch() {
  const q = searchInput.value.trim();
  if (!q) return;
  
  searchResultsContainer.className = "search-results-empty";
  searchResultsContainer.innerHTML = "Scanning mod archives for localization names... Please wait...";
  
  window.api.searchItem(q)
    .then(resp => {
      const resultsText = resp.results || "";
      if (!resultsText || resultsText.includes("No items or blocks matching")) {
        searchResultsContainer.className = "search-results-empty";
        searchResultsContainer.textContent = `No matches found for "${q}".`;
        return;
      }
      
      const lines = resultsText.split('\n');
      const results = [];
      
      lines.forEach(line => {
        const match = line.match(/- Name:\s*"([^"]+)"\s*->\s*ID:\s*([^\s]+)\s*\(Type:\s*([^)]+)\)/);
        if (match) {
          results.push({
            name: match[1],
            id: match[2],
            type: match[3]
          });
        }
      });
      
      if (results.length === 0) {
        searchResultsContainer.className = "";
        searchResultsContainer.innerHTML = `<pre style="font-family:'Fira Code', monospace; font-size: 13px; color: var(--text-muted); white-space: pre-wrap;">${escapeHtml(resultsText)}</pre>`;
        return;
      }
      
      searchResultsContainer.className = "";
      searchResultsContainer.innerHTML = `
        <div class="search-results-list">
          ${results.map(item => `
            <div class="search-result-item">
              <div class="result-info">
                <span class="result-type">${item.type}</span>
                <span class="result-name">${item.name}</span>
                <span class="result-id">${item.id}</span>
              </div>
              <div class="result-actions">
                <button class="btn btn-primary btn-small btn-copy" data-id="${item.id}">Copy ID</button>
                <button class="btn btn-success btn-small btn-give" data-id="${item.id}">Give ColbyJacks</button>
              </div>
            </div>
          `).join('')}
        </div>
      `;
      
      // Add action listeners
      document.querySelectorAll('.btn-copy').forEach(btn => {
        btn.addEventListener('click', () => {
          const id = btn.dataset.id;
          navigator.clipboard.writeText(id)
            .then(() => {
              btn.textContent = "Copied!";
              setTimeout(() => btn.textContent = "Copy ID", 1500);
            });
        });
      });
      
      document.querySelectorAll('.btn-give').forEach(btn => {
        btn.addEventListener('click', () => {
          const id = btn.dataset.id;
          btn.disabled = true;
          btn.textContent = "Giving...";
          
          window.api.runCommand(`give ColbyJacks ${id} 1`)
            .then(() => {
              btn.textContent = "Gave 1!";
              setTimeout(() => {
                btn.textContent = "Give ColbyJacks";
                btn.disabled = false;
              }, 1500);
            })
            .catch(() => {
              btn.textContent = "Failed";
              setTimeout(() => {
                btn.textContent = "Give ColbyJacks";
                btn.disabled = false;
              }, 1500);
            });
        });
      });
    })
    .catch(err => {
      searchResultsContainer.className = "search-results-empty";
      searchResultsContainer.textContent = `Search error: ${err.message || err}`;
    });
}

// Bind Control Actions
btnStart.addEventListener('click', () => {
  btnStart.disabled = true;
  window.api.controlServer('start')
    .then(r => console.log(r.message))
    .catch(e => console.error(e));
});

btnStop.addEventListener('click', () => {
  btnStop.disabled = true;
  window.api.controlServer('stop')
    .then(r => console.log(r.message))
    .catch(e => console.error(e));
});

btnSleep.addEventListener('click', () => {
  btnSleep.disabled = true;
  window.api.controlServer('sleep')
    .then(r => console.log(r.message))
    .catch(e => console.error(e));
});

btnWarmupAi.addEventListener('click', () => {
  btnWarmupAi.disabled = true;
  btnWarmupAi.textContent = "Initializing...";
  window.api.warmupAI()
    .then(r => console.log(r.message))
    .catch(e => console.error(e));
});

btnClearAllMemories.addEventListener('click', () => {
  if (confirm("Are you sure you want to clear all player memories? This cannot be undone.")) {
    window.api.clearHistory()
      .then(resp => {
        selectedPlayerMemory = null;
        loadPlayerMemories();
      })
      .catch(err => console.error("Failed to clear all memories:", err));
  }
});

btnClearPlayerMemory.addEventListener('click', () => {
  if (!selectedPlayerMemory) return;
  if (confirm(`Are you sure you want to clear history for player '${selectedPlayerMemory}'?`)) {
    window.api.clearHistory(selectedPlayerMemory)
      .then(resp => {
        selectedPlayerMemory = null;
        loadPlayerMemories();
      })
      .catch(err => console.error("Failed to clear player memory:", err));
  }
});


// Periodic Loops
updateDashboard();
updateConsoleLogs();
setInterval(updateDashboard, 1500);
setInterval(updateConsoleLogs, 1500);

// Setup & Settings Logic
function initApp() {
  window.api.getConfig()
    .then(cfg => {
      appConfig = cfg || { serverDir: '' };
      settingsServerDirPath.value = appConfig.serverDir || '';
      
      if (!appConfig.serverDir) {
        setupScreen.classList.add('active');
      } else {
        setupScreen.classList.remove('active');
        // Trigger immediate dashboard update since path is available
        updateDashboard();
        updateConsoleLogs();
      }
    })
    .catch(err => console.error("Failed to load config:", err));
}

function handleSelectDir() {
  setupError.textContent = '';
  window.api.selectServerDir()
    .then(dir => {
      if (dir) {
        appConfig.serverDir = dir;
        settingsServerDirPath.value = dir;
        
        window.api.saveConfig(appConfig)
          .then(res => {
            if (res.success) {
              setupScreen.classList.remove('active');
              // Trigger immediate updates
              updateDashboard();
              updateConsoleLogs();
            } else {
              setupError.textContent = "Failed to save directory.";
            }
          })
          .catch(err => {
            setupError.textContent = `Error saving config: ${err.message || err}`;
          });
      }
    })
    .catch(err => {
      setupError.textContent = `Error opening directory picker: ${err.message || err}`;
    });
}

btnSelectDir.addEventListener('click', handleSelectDir);
btnChangeDir.addEventListener('click', handleSelectDir);

// Initialize app on load
initApp();

// Server Properties Editor state & handlers
let serverPropertiesData = {};

function loadServerProperties() {
  const formContainer = document.getElementById('properties-form-container');
  formContainer.innerHTML = '<p class="properties-loading">Loading server properties...</p>';
  
  window.api.getProperties()
    .then(data => {
      serverPropertiesData = data.properties || {};
      renderProperties();
    })
    .catch(err => {
      formContainer.innerHTML = `<p class="properties-loading" style="color: var(--status-stopping);">Failed to load properties: ${err.message || err}</p>`;
    });
}

function renderProperties() {
  const formContainer = document.getElementById('properties-form-container');
  const searchVal = document.getElementById('properties-search').value.toLowerCase().trim();
  
  const keys = Object.keys(serverPropertiesData).sort();
  let html = '';
  
  let visibleCount = 0;
  for (const key of keys) {
    if (searchVal && !key.toLowerCase().includes(searchVal)) {
      continue;
    }
    visibleCount++;
    const val = serverPropertiesData[key];
    html += `
      <div class="properties-row">
        <div class="properties-key">${key}</div>
        <div class="properties-value-input">
          <input type="text" class="property-input-field" data-key="${key}" value="${val}">
        </div>
      </div>
    `;
  }
  
  if (visibleCount === 0) {
    if (keys.length === 0) {
      formContainer.innerHTML = '<p class="properties-loading">No server properties found.</p>';
    } else {
      formContainer.innerHTML = '<p class="properties-loading">No properties match search filter.</p>';
    }
  } else {
    formContainer.innerHTML = html;
    
    // Bind change listener to update our state cache
    document.querySelectorAll('.property-input-field').forEach(input => {
      input.addEventListener('input', (e) => {
        const k = e.target.dataset.key;
        serverPropertiesData[k] = e.target.value;
      });
    });
  }
}

function saveServerProperties() {
  const btnSave = document.getElementById('btn-save-properties');
  const originalText = btnSave.textContent;
  
  btnSave.disabled = true;
  btnSave.textContent = 'Saving...';
  
  window.api.saveProperties(serverPropertiesData)
    .then(res => {
      if (res.success) {
        btnSave.textContent = 'Saved!';
        btnSave.style.background = 'var(--status-running)';
        setTimeout(() => {
          btnSave.textContent = originalText;
          btnSave.style.background = '';
          btnSave.disabled = false;
        }, 2000);
      } else {
        alert("Failed to save properties: " + res.message);
        btnSave.textContent = originalText;
        btnSave.disabled = false;
      }
    })
    .catch(err => {
      alert("Error saving properties: " + (err.message || err));
      btnSave.textContent = originalText;
      btnSave.disabled = false;
    });
}

// Bind search and save events
document.getElementById('properties-search').addEventListener('input', renderProperties);
document.getElementById('btn-save-properties').addEventListener('click', saveServerProperties);
