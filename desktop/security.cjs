const COMMANDS = new Set(['health', 'status', 'frame', 'exam', 'start', 'stop', 'calibrate', 'answers', 'submit', 'guard-status', 'guard-start', 'guard-stop', 'exit']);

function blockedInput(input) {
  const key = String(input.key || '').toLowerCase();
  return (input.control && ['c', 'v', 'x', 't', 'n', 'w', 'r'].includes(key)) ||
    (input.alt && ['tab', 'f4'].includes(key)) || input.meta ||
    ['printscreen', 'f11', 'f12'].includes(key);
}

function supportedHost(platform, env) {
  if (platform === 'win32') return { supported: true, method: 'Windows WH_KEYBOARD_LL', detail: 'Проверка перехвата выполняется при запуске экзамена.' };
  if (platform === 'linux' && env.DISPLAY && !env.WAYLAND_DISPLAY && env.XDG_SESSION_TYPE !== 'wayland') {
    return { supported: true, method: 'X11 XGrabKey', detail: 'Проверка конфликтов сочетаний выполняется при запуске экзамена.' };
  }
  return { supported: false, method: 'Недоступно', detail: 'Строгий экзамен требует Windows или сеанс Linux X11. В Wayland доступен просмотр CV без изоляции ОС.' };
}

function validatePayload(command, payload) {
  if (!COMMANDS.has(command)) throw new Error('Недопустимая команда');
  if (command === 'start' && (!Number.isInteger(payload?.camera_index) || payload.camera_index < 0 || payload.camera_index > 8 || Object.keys(payload).some(key=>key!=='camera_index'))) throw new Error('Индекс камеры должен быть целым числом от 0 до 8');
  if (command === 'answers' && (!payload?.answers || typeof payload.answers !== 'object' || Array.isArray(payload.answers) || Object.entries(payload.answers).some(([k, v]) => !/^q[1-9]\d*$/.test(k) || !/^[A-D]$/.test(v)))) throw new Error('Некорректные ответы');
  return payload;
}

module.exports = { COMMANDS, blockedInput, supportedHost, validatePayload };
