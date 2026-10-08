const { app, BrowserWindow, ipcMain, globalShortcut, Menu, dialog } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { blockedInput, supportedHost, validatePayload } = require('./security.cjs');

const rendererPath = path.join(__dirname, 'renderer', 'index.html');
const rendererURL = pathToFileURL(rendererPath).href;
const root = path.dirname(__dirname);
const token = crypto.randomBytes(32).toString('base64url');
const videoIndex = process.argv.indexOf('--video');
const videoPath = videoIndex >= 0 ? process.argv[videoIndex + 1] : null;
const smokeMode=process.argv.includes('--smoke')||process.argv.includes('--smoke-full');
if(smokeMode){
  if(process.env.PROCTOR_TEST_XVFB!=='1'||!process.env.DISPLAY||process.env.WAYLAND_DISPLAY||process.env.XDG_SESSION_TYPE!=='x11')throw new Error('Smoke test requires explicit PROCTOR_TEST_XVFB=1 and isolated Xvfb X11.');
  app.commandLine.appendSwitch('ozone-platform','x11');
  app.commandLine.appendSwitch('force-device-scale-factor','1');
}
let win, helper, backendProcess, active = false, quitting = false, teacherPINHash, aborted = false;
let securityQueue = Promise.resolve();
let guardStarting = false;
const journal = [];
let guardDetail = 'Защита выключена. Просмотр CV не блокирует рабочий стол.';
let shortcutReport = {};

function guardState() {
  const host=supportedHost(process.platform, process.env);
  return { active, aborted, ...host, detail: host.supported ? guardDetail : host.detail, shortcuts: shortcutReport, journal: journal.slice(-100) };
}
function record(kind, message) {
  journal.push({ at: new Date().toISOString(), kind, message });
  if(journal.length>100)journal.shift();
  if (win && !win.isDestroyed()) win.webContents.send('guard-state', guardState());
  const types = { focus_lost: 'focus_lost', blocked_key: 'shortcut_blocked', guard_start: 'kiosk_started', guard_stop: 'kiosk_stopped', guard_abort: 'fullscreen_left', window_blocked: 'navigation_blocked', navigation_blocked: 'navigation_blocked' };
  if (types[kind] && backendProcess) securityQueue = securityQueue.then(() => backend('security', { type: types[kind], detail: message.slice(0, 240) })).catch(() => {});
}
async function abortExam(reason) {
  aborted = true;
  record('guard_abort', `Экзамен прерван: ${reason}`);
  releaseGuards(`Экзамен прерван: ${reason}`);
  await securityQueue.catch(() => {});
  await backend('stop').catch(() => {});
}
function verifyPIN(payload) {
  const candidate = crypto.createHash('sha256').update(String(payload?.pin || '')).digest();
  if (!teacherPINHash || !crypto.timingSafeEqual(candidate, teacherPINHash)) throw new Error('Неверный PIN преподавателя. Аварийный выход: Ctrl+Alt+Shift+Q.');
}
function releaseGuards(reason = 'Защита снята') {
  active = false;
  if (helper) { const child = helper; helper = null; child.kill(); }
  globalShortcut.unregisterAll();
  if (win && !win.isDestroyed()) { win.setKiosk(false); win.setAlwaysOnTop(false); win.setClosable(true); }
  guardDetail = reason;
  record('guard_stop', reason);
  return guardState();
}
function pythonPath() {
  const local = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  return process.env.PROCTOR_PYTHON || (fs.existsSync(local) ? local : process.platform === 'win32' ? 'python' : 'python3');
}
async function startGuards() {
  if (active) return guardState();
  const host = supportedHost(process.platform, process.env);
  if (!host.supported) throw new Error(host.detail);
  shortcutReport = {};
  for (const key of ['Alt+Tab', 'PrintScreen', 'Control+C', 'Control+V']) {
    try { shortcutReport[key] = globalShortcut.register(key, () => record('blocked_key', key)); }
    catch { shortcutReport[key] = false; }
    globalShortcut.unregister(key);
  }
  const child = spawn(pythonPath(), [path.join(__dirname, 'keyboard_guard.py')], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  helper = child;
  await new Promise((resolve, reject) => {
    let buffer = '', errorText = '', ready = false;
    const timer = setTimeout(() => { child.kill(); reject(new Error('Перехват клавиш не подтвердился за 5 секунд')); }, 5000);
    child.stderr.on('data', data => { errorText = (errorText + data).slice(-2000); });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.stdout.on('data', data => {
      buffer += data;
      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index); buffer = buffer.slice(index + 1);
        let event; try { event = JSON.parse(line); } catch { continue; }
        if (event.type === 'ready') { ready = true; clearTimeout(timer); resolve(); }
        if (event.type === 'blocked') record('blocked_key', event.key);
        if (event.type === 'error') { clearTimeout(timer); reject(new Error(event.message)); }
      }
    });
    child.on('exit', () => {
      clearTimeout(timer);
      if (!ready) reject(new Error(errorText || 'Нативная защита завершилась без подтверждения'));
      if (helper === child && active) abortExam('Перехват клавиш остановился');
    });
  }).catch(error => { releaseGuards(error.message); throw error; });
  if (child.exitCode !== null) { releaseGuards('Перехват клавиш остановился'); throw new Error(guardDetail); }
  const current = await backend('status');
  if (current.phase !== 'running' || current.frame_age_s == null || current.frame_age_s > 5) { releaseGuards('Поток CV недоступен. Экзамен не начат.'); throw new Error(guardDetail); }
  if(child.exitCode!==null||helper!==child||win.isDestroyed()){releaseGuards('Нативная защита остановилась при запуске');throw new Error(guardDetail);}
  if (!globalShortcut.register('Control+Alt+Shift+Q', () => {
    abortExam('Аварийный выход Ctrl+Alt+Shift+Q');
  })) { releaseGuards('Аварийное сочетание занято другой программой'); throw new Error(guardDetail); }
  active = true;
  aborted = false;
  win.setClosable(false);
  win.setAlwaysOnTop(true);
  win.setKiosk(true);
  win.focus();
  guardDetail = `${host.method}: Alt+Tab, Win, PrtScn, Ctrl+C/V перехвачены. Kiosk активен. Аварийный выход: Ctrl+Alt+Shift+Q.`;
  record('guard_start', guardDetail);
  return guardState();
}

const routes = {
  health: ['GET', '/health'], status: ['GET', '/api/status'], frame: ['GET', '/api/frame.jpg'], exam: ['GET', '/api/exam'],
  start: ['POST', '/api/start'], stop: ['POST', '/api/stop'], calibrate: ['POST', '/api/calibrate'],
  answers: ['POST', '/api/exam/answers'], submit: ['POST', '/api/exam/submit']
};
routes.security = ['POST', '/api/security'];
async function backend(command, payload) {
  const [method, route] = routes[command];
  const response = await fetch(`http://127.0.0.1:8765${route}`, {
    method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: method === 'POST' ? JSON.stringify(payload || {}) : undefined,
    signal: AbortSignal.timeout(12000), redirect: 'error'
  });
  if (!response.ok) {
    let text = await response.text();
    try { text = JSON.parse(text).detail || text; } catch {}
    throw new Error(typeof text === 'string' ? text : JSON.stringify(text));
  }
  if (command === 'frame') return `data:image/jpeg;base64,${Buffer.from(await response.arrayBuffer()).toString('base64')}`;
  return response.json();
}

app.whenReady().then(async () => {
  const backendArgs = [path.join(root, 'app.py')];
  if (videoPath) {
    if (!fs.existsSync(videoPath) || !fs.statSync(videoPath).isFile()) { dialog.showErrorBox('Видео не найдено', 'Передайте существующий локальный файл через --video.'); app.quit(); return; }
    backendArgs.push('--video', path.resolve(videoPath));
  }
  backendProcess = spawn(pythonPath(), backendArgs, { cwd: root, env: { ...process.env, PROCTOR_TOKEN: token }, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  backendProcess.stdout.on('data', data => process.stdout.write(data));
  backendProcess.stderr.on('data', data => process.stderr.write(data));
  backendProcess.on('error', error => { guardDetail = `Сервер не запущен: ${error.message}`; record('backend_error', guardDetail); });
  backendProcess.on('exit', code => { if (!quitting) { if(active) abortExam(`Локальный сервер остановился (код ${code})`); else releaseGuards(`Локальный сервер остановился (код ${code}).`); } });
  Menu.setApplicationMenu(null);
  win = new BrowserWindow({ width: 1340, height: 930, minWidth: 620, minHeight: 620, title: 'Qostanai Local Proctor', backgroundColor: '#f4f6f7',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true, devTools: false }
  });
  win.webContents.setWindowOpenHandler(() => { record('window_blocked', 'Попытка открыть дополнительное окно'); return { action: 'deny' }; });
  win.webContents.on('will-navigate', event => { event.preventDefault(); record('navigation_blocked', 'Переход из экзамена запрещен'); });
  win.webContents.on('will-attach-webview', event => event.preventDefault());
  win.webContents.on('context-menu', event => event.preventDefault());
  win.webContents.on('before-input-event', (event, input) => {
    if (active && blockedInput(input)) { event.preventDefault(); record('blocked_key', input.key); }
  });
  win.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  win.webContents.session.setPermissionCheckHandler(() => false);
  win.on('blur', () => {
    if (active) { record('focus_lost', 'Окно экзамена потеряло фокус'); setTimeout(() => { if (active && !win.isDestroyed()) { win.show(); win.focus(); } }, 100); }
  });
  win.on('close', event => { if (active && !quitting) { event.preventDefault(); record('close_blocked', 'Для завершения используйте кнопку преподавателя или аварийное сочетание'); } });
  win.webContents.on('render-process-gone', () => active ? abortExam('Процесс интерфейса завершился') : releaseGuards('Процесс интерфейса завершился'));
  win.on('closed', () => { releaseGuards('Окно закрыто'); });
  ipcMain.handle('proctor-command', async (event, command, payload) => {
    if (event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame || event.senderFrame.url.split('#')[0] !== rendererURL) throw new Error('Недоверенный IPC отправитель');
    validatePayload(command, payload);
    if (command === 'guard-status') return guardState();
    if (command === 'guard-start') {
      if (active || guardStarting) throw new Error('Защита уже включена или включается');
      guardStarting = true;
      try {
      const pin = process.env.PROCTOR_EXIT_PIN || payload?.pin;
      if (!/^\d{6,12}$/.test(pin || '')) throw new Error('Задайте PIN преподавателя: от 6 до 12 цифр');
      const status = await backend('status');
      if (status.phase !== 'running' || !status.calibrated) throw new Error('Перед экзаменом запустите камеру и завершите калибровку');
      teacherPINHash = crypto.createHash('sha256').update(pin).digest();
      return await startGuards();
      } catch(error){releaseGuards(error.message);throw error;} finally { guardStarting = false; }
    }
    if(['guard-stop','stop','exit'].includes(command)&&guardStarting)throw new Error('Дождитесь завершения проверки клавиш перед остановкой');
    if (command === 'guard-stop') { if (active) verifyPIN(payload); return releaseGuards('Преподаватель снял защиту'); }
    if (command === 'exit') {
      if (active) verifyPIN(payload);
      releaseGuards('Приложение закрыто');
      await securityQueue.catch(() => {});
      await backend('stop').catch(() => {});
      quitting = true; app.quit(); return {};
    }
    if (command === 'stop') { if (active) verifyPIN(payload); releaseGuards('Наблюдение остановлено'); await securityQueue.catch(() => {}); }
    if (['start', 'calibrate'].includes(command) && (active || guardStarting)) throw new Error('Сначала завершите экзамен с PIN преподавателя');
    if (['answers', 'submit'].includes(command) && !active) throw new Error('Начните защищенный экзамен после калибровки');
    const result = await backend(command, payload);
    if(command==='start'){aborted=false;guardDetail='Защита выключена. Просмотр CV не блокирует рабочий стол.';journal.length=0;win.webContents.send('guard-state',guardState());}
    if (command === 'status' && active && (result.phase !== 'running' || result.frame_age_s == null || result.frame_age_s > 5)) await abortExam('Поток камеры остановился или кадр устарел');
    return result;
  });
  await win.loadFile(rendererPath);
  const watchdog = setInterval(async () => {
    if (!active) return;
    try { const current = await backend('status'); if (current.phase !== 'running' || current.frame_age_s == null || current.frame_age_s > 5) await abortExam('Поток камеры остановился или кадр устарел'); }
    catch { if(active) await abortExam('Локальный сервер недоступен'); }
  }, 1000);
  win.on('closed', () => clearInterval(watchdog));
  if (videoPath) {
    // ponytail: the trusted launcher selects the replay path; IPC never accepts filesystem paths.
    win.webContents.send('guard-state', { ...guardState(), replay: true });
  }
  if (smokeMode) {
    setTimeout(async () => {
      try {
        await require('./tests/electron-smoke.cjs')(win, root, backend, guardState, process.argv.includes('--smoke-full'));
      } catch (error) { console.error(error); process.exitCode = 1; }
      quitting = true; releaseGuards('Изолированная проверка завершена'); if(backendProcess)backendProcess.kill(); app.exit(process.exitCode||0);
    }, 1800);
  }
});
app.on('before-quit', () => { quitting = true; releaseGuards('Приложение завершено'); if (backendProcess) backendProcess.kill(); });
app.on('window-all-closed', () => app.quit());
process.on('SIGINT', () => { releaseGuards('Остановлено'); app.quit(); });
process.on('SIGTERM', () => { releaseGuards('Остановлено'); app.quit(); });
