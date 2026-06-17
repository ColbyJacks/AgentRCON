const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

let mainWindow;
let pythonProcess;
const configPath = path.join(app.getPath('userData'), 'config.json');
let config = { serverDir: '' };

function loadConfig() {
  if (fs.existsSync(configPath)) {
    try {
      config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
    } catch (e) {
      console.error("Failed to parse config.json:", e);
    }
  }
}

function saveConfig() {
  try {
    fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
  } catch (e) {
    console.error("Failed to write config.json:", e);
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1300,
    height: 850,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    },
    autoHideMenuBar: true,
    title: "AgentRCON Dashboard"
  });

  mainWindow.loadFile('index.html');

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function killPythonBackend() {
  if (pythonProcess) {
    console.log("Killing existing Python process tree...");
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/pid', pythonProcess.pid, '/f', '/t']);
      } else {
        pythonProcess.kill('SIGTERM');
      }
    } catch (e) {
      console.error("Failed to kill Python process:", e);
    }
    pythonProcess = null;
  }
}

function startPythonBackend() {
  killPythonBackend();
  
  if (!config.serverDir || !fs.existsSync(config.serverDir)) {
    console.log("[*] No valid server directory configured. Skipping Python daemon startup.");
    return;
  }

  const pythonCmd = 'python';
  const scriptPath = `"${path.join(__dirname, '..', 'AgentRCON.py')}"`;
  const serverDirArg = `"${config.serverDir}"`;
  
  console.log(`Spawning Python process: ${pythonCmd} ${scriptPath} --no-cli --server-dir ${serverDirArg}`);
  
  pythonProcess = spawn(pythonCmd, [scriptPath, '--no-cli', '--server-dir', serverDirArg], {
    cwd: path.join(__dirname, '..'),
    shell: true
  });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[Python STDOUT]: ${data}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[Python STDERR]: ${data}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`Python process exited with code ${code}`);
  });
}

// IPC Handlers
ipcMain.handle('select-server-dir', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: "Select Minecraft Server Folder"
  });
  if (!result.canceled && result.filePaths.length > 0) {
    return result.filePaths[0];
  }
  return null;
});

ipcMain.handle('get-config', () => {
  loadConfig();
  return config;
});

ipcMain.handle('save-config', (event, newConfig) => {
  config = newConfig;
  saveConfig();
  
  // Restart backend with new directory config
  startPythonBackend();
  return { success: true };
});

app.whenReady().then(() => {
  loadConfig();
  startPythonBackend();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('will-quit', () => {
  killPythonBackend();
});
