const $ = id => document.getElementById(id);
const names = { face_absent:'Лицо не видно', second_face:'В кадре несколько лиц', phone_present:'Обнаружен телефон', phone_raised:'Телефон поднят возле лица', gaze_down:'Взгляд вниз', gaze_left:'Взгляд влево', gaze_right:'Взгляд вправо', head_down:'Наклон головы вниз', head_left:'Поворот головы влево', head_right:'Поворот головы вправо', focus_lost:'Потеря фокуса', shortcut_blocked:'Клавиша заблокирована', kiosk_started:'Защита включена', kiosk_stopped:'Защита снята', navigation_blocked:'Переход заблокирован', exam_submitted:'Ответы отправлены', session_started:'Сессия начата', session_stopped:'Сессия остановлена', calibration_started:'Калибровка начата', calibration_completed:'Калибровка завершена' };
let status = null, guards = null, exam = null, busy = false, connected = false, pendingAction = null, backendError = '', framePending = false;
const startedAt=Date.now();
function notify(text, error=false) { $('notice').textContent=text; $('notice').classList.toggle('error',error); }
async function request(command,payload) { return window.proctor.request(command,payload); }
async function action(command,payload,success) {
  if(busy) return;
  busy=true; renderControls();
  try { const value=await request(command,payload); if(success) notify(success); return value; }
  catch(error) { notify(error.message,true); throw error; }
  finally { busy=false; renderControls(); }
}
function duration(seconds) { const n=Math.max(0,Math.floor(seconds||0)); return `${Math.floor(n/60).toString().padStart(2,'0')}:${(n%60).toString().padStart(2,'0')}`; }
function renderControls() {
  const running=status?.phase==='running', active=!!guards?.active, ready=running && status?.calibrated && !!guards?.supported;
  $('start-camera').disabled=busy||running||['starting','stopping'].includes(status?.phase)||!connected;
  $('camera').disabled=running||active||busy;
  $('calibrate').disabled=busy||!running||active||status?.calibrating;
  $('teacher-pin').disabled=active||busy;
  $('start-exam').disabled=busy||!ready||active;
  $('questions').disabled=!active||!!exam?.submitted;
  $('submit').disabled=busy||!active||!!exam?.submitted;
  $('finish').disabled=busy||!active;
  $('stop-camera').disabled=busy||!running;
  $('exam-state').textContent=guards?.aborted?'Экзамен прерван':exam?.submitted?'Ответы приняты':active?'Защищенный экзамен':'Еще не начат';
}
function drawQuestions(value) {
  exam=value; $('questions').replaceChildren();
  for(const q of value.questions||[]) {
    const field=document.createElement('fieldset'); field.className='question';
    const legend=document.createElement('legend');legend.textContent=q.text;field.append(legend);
    for(const [key,text] of Object.entries(q.options||{})) {
      const label=document.createElement('label');label.className='option';
      const radio=document.createElement('input');radio.type='radio';radio.name=q.id;radio.value=key;radio.checked=value.answers?.[q.id]===key;
      const span=document.createElement('span');span.textContent=`${key}. ${text}`;label.append(radio,span);field.append(label);
    }
    $('questions').append(field);
  }
  renderControls();
}
function renderGuards(value) {
  guards=value;$('guard-state').textContent=value.active?'Активна: проверенный перехват':'Выключена';
  $('guard-detail').textContent=value.detail||value.method;
  if(!value.supported&&!value.active) $('guard-detail').textContent=value.detail;
  if(value.aborted){$('exam-state').textContent='Экзамен прерван';$('result').hidden=false;$('result').textContent='Экзамен прерван. Снятие защиты отмечено в журнале; результат требует проверки преподавателем.';}
  renderControls();
}
function renderStatus(value) {
  status=value;
  const phase={idle:'Камера выключена',starting:'Загрузка моделей и камеры',running:'Наблюдение работает',stopping:'Остановка наблюдения',stopped:'Наблюдение остановлено',error:'Ошибка наблюдения'};
  $('phase').textContent=phase[value.phase]||value.phase;$('elapsed').textContent=duration(value.elapsed_s);
  $('source').textContent=value.phase==='running'?(value.source==='local_video'?'Локальное видео':'Веб-камера'):'Камера выключена';
  if(value.error&&value.error!==backendError){backendError=value.error;notify(value.error,true);}else if(!value.error)backendError='';
  const cv=value.phase==='running'?value.cv:null;
  $('fps').textContent=cv?Number(value.fps||0).toFixed(1):'Нет данных';
  $('frame-age').textContent=value.frame_age_s==null?'Нет данных':`${Number(value.frame_age_s).toFixed(1)} с`;
  $('face-count').textContent=cv?String(cv.face_count??cv.faces?.length??0):'Нет данных';
  $('phone-confidence').textContent=cv?.phones?.length?`${Math.round(Math.max(...cv.phones.map(p=>p.confidence))*100)}%`:'Нет детекций';
  $('calibration').textContent=value.calibrated?'Выполнена':value.calibrating?`${value.calibration_samples||0} / 5 кадров`:'Не выполнена';
  $('cv-time').textContent=cv?`${Math.round(cv.inference_ms||0)} мс`:'Нет данных';
  $('calibration-help').textContent=value.calibrated?'Калибровка завершена. Проверьте защиту ОС и задайте PIN перед началом экзамена.':value.calibrating?'Смотрите в центр экрана. Калибровка принимает кадры только с одним лицом.':'После включения смотрите в центр экрана и нажмите «Калибровать взгляд». Требуются 5 кадров с одним лицом.';
  $('signals').replaceChildren();
  const signals=Object.entries(cv?.signals||{}).filter(([,enabled])=>enabled);
  for(const [name] of signals){const li=document.createElement('li');li.className='alert';li.textContent=names[name]||name;$('signals').append(li);}
  if(!signals.length){const li=document.createElement('li');li.className='empty';li.textContent=cv?'Текущих сигналов нет. Это не подтверждение отсутствия нарушений.':'Ожидание кадров.';$('signals').append(li);}
  if(value.phase!=='running'){ $('frame').hidden=true;$('frame-empty').hidden=false;$('frame-empty').textContent=value.phase==='starting'?'Загрузка локальных моделей. Ожидание первого кадра.':value.phase==='error'?`Кадр недоступен: ${value.error||'ошибка камеры'}`:'Камера выключена. Включите наблюдение для обработки кадров.';}
  renderJournal(value);renderControls();
}
function renderJournal(value) {
  const entries=value.recent_events||[];
  const all=entries.length?entries:(guards?.journal||[]);
  $('event-count').textContent=`${all.length} событий`;$('journal').replaceChildren();
  for(const item of all.slice(-40).reverse()) {
    const li=document.createElement('li'),time=document.createElement('time');
    time.textContent=item.elapsed_s!=null?`${duration(item.elapsed_s)} · ${new Date(item.at).toLocaleTimeString('ru-RU')}`:item.at?new Date(item.at).toLocaleTimeString('ru-RU'):'';
    li.append(time);const text=document.createElement('span');const kind=item.event?.type||item.kind||item.type;const details=item.event?.detail||item.message||'';text.textContent=`${names[kind]||kind||'Событие'}${details?`: ${details}`:''}${item.event?.duration_s!=null?` (${Number(item.event.duration_s).toFixed(1)} с)`:''}`;li.append(text);$('journal').append(li);
  }
  if(!all.length){const li=document.createElement('li');li.className='empty';li.textContent=value.session_id?'Событий пока нет. Сессия обрабатывает кадры локально.':'Сессия не начата. События появятся после включения наблюдения.';$('journal').append(li);}
}
async function poll() {
  try {
    const value=await request('status');
    if(!connected){connected=true;notify('Локальный сервер готов. Камера включается кнопкой наблюдения.');drawQuestions(await request('exam'));renderGuards(await request('guard-status'));}
    renderStatus(value);
  }catch(error){connected=false;const loading=Date.now()-startedAt<10000;$('phase').textContent=loading?'Сервер запускается':'Сервер недоступен';notify(loading?'Локальный сервер запускается. Камера выключена.':`Локальный сервер недоступен: ${error.message}. Проверьте установку Python и моделей.`,!loading);renderControls();}
}
async function pollFrame() {
  if(status?.phase!=='running'||framePending) return;
  const sessionID=status.session_id;
  framePending=true;
  try{const frame=await request('frame');if(status?.phase!=='running'||status.session_id!==sessionID)return;$('frame').src=frame;$('frame').hidden=false;$('frame-empty').hidden=true;}
  catch(error){$('frame').hidden=true;$('frame-empty').hidden=false;$('frame-empty').textContent=`Кадр недоступен: ${error.message}`;}
  finally{framePending=false;}
}
function protectedAction(command) {
  if(!guards?.active){action(command,undefined,command==='stop'?'Наблюдение остановлено.':undefined).catch(()=>{});return;}
  pendingAction=command;$('finish-pin').value='';$('pin-error').textContent='';$('pin-action').textContent=command==='exit'?'Введите PIN, чтобы снять защиту и закрыть приложение.':'Введите PIN преподавателя. Защита будет снята.';$('pin-dialog').showModal();$('finish-pin').focus();
}
$('start-camera').onclick=()=>action('start',{camera_index:Number($('camera').value)},'Наблюдение запускается. Камера и модели готовятся локально.').then(async()=>{drawQuestions(await request('exam'));$('result').hidden=true;await poll();}).catch(()=>{});
$('calibrate').onclick=()=>action('calibrate',undefined,'Калибровка началась. Смотрите в центр экрана.').then(poll).catch(()=>{});
$('start-exam').onclick=()=>action('guard-start',{pin:$('teacher-pin').value},'Защищенный экзамен начат.').then(value=>{if(value){$('teacher-pin').value='';renderGuards(value);$('preparation').hidden=true;}}).catch(()=>{});
$('finish').onclick=()=>protectedAction('guard-stop');
$('stop-camera').onclick=()=>protectedAction('stop');
$('exit').onclick=()=>protectedAction('exit');
$('pin-confirm').onclick=async()=>{
  try{const value=await action(pendingAction,{pin:$('finish-pin').value},'Действие преподавателя выполнено.');$('pin-dialog').close();$('preparation').hidden=false;if(pendingAction==='guard-stop')renderGuards(value);else renderGuards(await request('guard-status'));await poll();}
  catch(error){$('pin-error').textContent=error.message;}
};
$('question-form').onsubmit=async event=>{
  event.preventDefault();const answers=Object.fromEntries(new FormData(event.currentTarget));
  if(Object.keys(answers).length!==(exam?.questions?.length||0)){notify('Ответьте на каждый вопрос перед отправкой.',true);return;}
  try{await action('answers',{answers});const result=await action('submit');exam.submitted=true;$('result').hidden=false;$('result').textContent=`Ответы сохранены. Результат: ${result.correct??result.score??'?'} из ${result.total??exam.questions.length}. Сигналы прокторинга рассматривает преподаватель.`;renderControls();await poll();}catch{}
};
window.proctor.onGuard(value=>{renderGuards(value);if(!value.active){$('preparation').hidden=false;if(value.detail?.includes('Аварийный'))notify(value.detail,true);}});
poll();setInterval(poll,700);setInterval(pollFrame,300);
