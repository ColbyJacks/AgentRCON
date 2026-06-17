const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn, execSync } = require('child_process');
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

function freePort8000() {
  try {
    console.log("Checking if port 8000 is in use...");
    if (process.platform === 'win32') {
      const output = execSync('netstat -ano').toString();
      const lines = output.split('\n');
      for (const line of lines) {
        if (line.includes(':8000') && line.includes('LISTENING')) {
          const parts = line.trim().split(/\s+/);
          const pid = parts[parts.length - 1];
          if (pid && pid !== '0') {
            console.log(`Port 8000 is held by PID ${pid}. Killing ghost process...`);
            execSync(`taskkill /pid ${pid} /f /t`);
          }
        }
      }
    }
  } catch (e) {
    // Ignore error if port is not in use
  }
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
  freePort8000();
  killPythonBackend();
  
  if (!config.serverDir || !fs.existsSync(config.serverDir)) {
    console.log("[*] No valid server directory configured. Skipping Python daemon startup.");
    return;
  }

  let pythonCmd;
  let args;
  
  const serverDirArg = config.serverDir;

  if (app.isPackaged) {
    // In packaged app, AgentRCON.exe is bundled in the app resources folder
    pythonCmd = path.join(process.resourcesPath, 'AgentRCON.exe');
    args = ['--no-cli', '--server-dir', serverDirArg];
  } else {
    // In development mode, run using python with script path
    pythonCmd = 'python';
    const scriptPath = path.join(__dirname, '..', 'AgentRCON.py');
    args = ['-u', scriptPath, '--no-cli', '--server-dir', serverDirArg];
  }
  
  console.log(`Spawning backend: ${pythonCmd} ${args.join(' ')}`);
  
  pythonProcess = spawn(pythonCmd, args, {
    cwd: config.serverDir,
    shell: false,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' }
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
