// The Auto iPhone Uploader window. It is its own program (not a browser tab), so Windows shows
// its sound, taskbar entry and Alt+Tab tile as "Auto iPhone Uploader". It starts the local server
// when it is not running, then shows the editor that server serves on 127.0.0.1.
"use strict";

const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const APP_NAME = "Auto iPhone Uploader";
const ORIGIN = "http://127.0.0.1:4748";

app.setName(APP_NAME);
app.setAppUserModelId(APP_NAME);

// The app folder is the nearest parent that holds launch_video_drop.py: the checkout when this
// runs from desktop\dist\..., the install folder when the installer put it there.
function appRoot() {
  let dir = path.dirname(app.isPackaged ? process.execPath : __dirname);
  for (let i = 0; i < 6; i += 1) {
    if (fs.existsSync(path.join(dir, "launch_video_drop.py"))) return dir;
    dir = path.dirname(dir);
  }
  if (!app.isPackaged && fs.existsSync(path.join(__dirname, "..", "launch_video_drop.py"))) return path.join(__dirname, "..");
  return null;
}

// The installer bundles its own Python beside the app; a checkout uses pythonw on PATH. The
// Microsoft Store alias in WindowsApps only opens the Store, so it never counts.
function findPython(root) {
  const bundled = path.join(root, "python", "pythonw.exe");
  if (fs.existsSync(bundled)) return bundled;
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (!dir || /WindowsApps/i.test(dir)) continue;
    const candidate = path.join(dir.replace(/"/g, ""), "pythonw.exe");
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function serverUp() {
  return new Promise((resolve) => {
    const request = http.get(`${ORIGIN}/api/runtime`, { timeout: 1500 }, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on("timeout", () => request.destroy());
    request.on("error", () => resolve(false));
  });
}

async function ensureServer() {
  if (await serverUp()) return;
  const root = appRoot();
  if (!root) throw new Error("launch_video_drop.py was not found next to this program. Reinstall Auto iPhone Uploader.");
  const python = findPython(root);
  if (!python) throw new Error('Python was not found. Install Python 3.11 or newer from python.org with "Add python.exe to PATH" ticked, then open Auto iPhone Uploader again.');
  // --no-browser: the launcher starts the server and the phone link, and opens no window of its own.
  const child = spawn(python, [path.join(root, "launch_video_drop.py"), "--no-browser"], {
    cwd: root, detached: true, stdio: "ignore", windowsHide: true,
  });
  child.unref();
  for (let i = 0; i < 80; i += 1) {
    if (await serverUp()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Auto iPhone Uploader did not start. See ${path.join(root, ".state", "server.log")}`);
}

let win = null;

function createWindow() {
  win = new BrowserWindow({
    width: 1440, height: 920, minWidth: 420, minHeight: 500,
    title: APP_NAME, backgroundColor: "#111113", autoHideMenuBar: true, show: false,
    icon: path.join(__dirname, "icon.ico"),
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.setMenuBarVisibility(false);
  // Only the local editor loads in this window; every other link opens in the default browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(ORIGIN + "/")) {
      event.preventDefault();
      if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    }
  });
  win.once("ready-to-show", () => win.show());
  win.on("closed", () => { win = null; });
  win.loadURL(ORIGIN + "/");
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.focus();
  });
  app.whenReady().then(async () => {
    try {
      await ensureServer();
    } catch (error) {
      dialog.showErrorBox(APP_NAME, String(error.message || error));
      app.quit();
      return;
    }
    createWindow();
  });
  // Closing the window ends this program only; the server and the phone link keep running.
  app.on("window-all-closed", () => app.quit());
}
