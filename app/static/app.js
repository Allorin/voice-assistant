/* Голосовой ежедневник — клиент.
   Голос → текст: Web Speech API браузера (Chrome, Edge, Safari) или, если его нет или он не работает,
   запись в WAV (recorder.js) + офлайн-распознавание Vosk на сервере (/api/stt).
   Текст → ответ: /api/chat. Ответ → голос: speechSynthesis браузера. */
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  log: $("log"), empty: $("empty"), input: $("input"), composer: $("composer"), mic: $("mic"),
  micLabel: $("micLabel"), hint: $("hint"), status: $("status"), speakToggle: $("speakToggle"),
  agenda: $("agenda"), agendaList: $("agendaList"), planToggle: $("planToggle"), planClose: $("planClose"),
  toast: $("toast"), suggestions: $("suggestions"), icsCopy: $("icsCopy"),
  calendar: $("calendarDialog"), calendarClose: $("calendarClose"), calendarUrl: $("calendarUrl"),
  calendarCopy: $("calendarCopy"), calendarGoogle: $("calendarGoogle"), calendarWebcal: $("calendarWebcal"),
  calendarHelp: $("calendarHelp"),
  settings: $("voiceSettings"), settingsOpen: $("settingsOpen"), settingsClose: $("settingsClose"),
  voiceSelect: $("voiceSelect"), voiceRate: $("voiceRate"), voiceVolume: $("voiceVolume"),
  rateValue: $("rateValue"), volumeValue: $("volumeValue"), bellEnabled: $("bellEnabled"),
  detailsEnabled: $("detailsEnabled"), voicePreview: $("voicePreview"), voiceStatus: $("voiceStatus"),
  profileForm: $("profileForm"), profileName: $("profileName"), profileHelp: $("profileHelp"), icsDownload: $("icsDownload"),
  sttField: $("sttField"), sttMode: $("sttMode"), themeSelect: $("themeSelect"),
  month: $("monthDialog"), monthOpen: $("calendarOpen"), monthTitle: $("monthTitle"), monthWeeks: $("monthWeeks"),
  monthDay: $("monthDay"), monthPrev: $("monthPrev"), monthNext: $("monthNext"), monthToday: $("monthToday"),
  monthClose: $("monthClose"),
  access: $("accessDialog"), accessForm: $("accessForm"), accessCode: $("accessCode"), accessHelp: $("accessHelp"),
};

const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* приватный режим */ } },
};

const newId = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));

const state = {
  sessionId: store.get("sessionId", null) || newId(),
  profile: store.get("profile", ""),  // "" — профиль сервера по умолчанию
  accessCode: store.get("accessCode", ""),  // код доступа к стенду, если он задан на сервере
  speak: store.get("speak", true),
  audioSettings: (() => {
    const saved = store.get("audioSettings", {}) || {};
    const number = (v, fallback, min, max) => typeof v === "number" && Number.isFinite(v)
      ? Math.max(min, Math.min(max, v)) : fallback;
    return { voice: typeof saved.voice === "string" ? saved.voice : "", rate: number(saved.rate, 1.05, 0.7, 1.4),
      volume: number(saved.volume, 1, 0, 1), bell: saved.bell !== false, details: saved.details === true };  // уточнения — по желанию
  })(),
  listening: false,
  busy: false,
  status: null,
  notified: new Set(store.get("notified", [])),
};
store.set("sessionId", state.sessionId);

/* ---------------------------------------------------------------- API */

// Профиль уходит в заголовке; кириллицу заголовок не пропускает, поэтому кодируем
const userHeaders = () => ({
  ...(state.profile ? { "X-User-Id": encodeURIComponent(state.profile) } : {}),
  ...(state.accessCode ? { "X-Access-Code": encodeURIComponent(state.accessCode) } : {}),
});

class AccessError extends Error {}

async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...userHeaders() } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (res.status === 401 && body.access_code_required) {
      askAccessCode(state.accessCode ? "Код не подошёл. Введите код доступа ещё раз." : "");
      throw new AccessError(body.detail || "Нужен код доступа");
    }
    throw new Error(body.detail || `Ошибка ${res.status}`);
  }
  return res.json();
}

/* ---------------------------------------------------------------- код доступа */

function askAccessCode(message) {
  if (message) els.accessHelp.textContent = message;
  if (!els.access.open) {
    els.access.showModal();
    els.accessCode.focus();
  }
}

els.accessForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  state.accessCode = els.accessCode.value.trim();
  if (!state.accessCode) return;
  const res = await fetch("/api/status", { headers: userHeaders() }).catch(() => null);
  if (!res || !res.ok) {
    els.accessHelp.textContent = !res ? "Сервер недоступен, попробуйте ещё раз."
      : res.status === 429 ? "Слишком много неверных попыток. Подождите минуту." : "Код не подошёл, проверьте его.";
    return;
  }
  store.set("accessCode", state.accessCode);
  els.access.close();
  await loadStatus();
  loadAgenda();
  checkReminders();
});

/* ---------------------------------------------------------------- диалог */

function addMessage(role, text, opts = {}) {
  els.empty.hidden = true;
  const div = document.createElement("div");
  div.className = `msg ${role}` + (opts.clarify ? " clarify" : "");
  div.textContent = text;
  if (opts.items && opts.items.length) div.appendChild(renderItems(opts.items, opts.changed || []));
  els.log.appendChild(div);
  els.log.scrollTop = els.log.scrollHeight;
  return div;
}

function renderItems(items, changed) {
  const ul = document.createElement("ul");
  ul.className = "items";
  for (const it of items.slice(0, 5)) {
    const li = document.createElement("li");
    li.className = `item ${it.type}` + (it.status === "done" ? " done" : "") + (changed.includes(it.id) ? " flash" : "");
    li.innerHTML = `<span class="time"></span><span class="title"></span><span class="kind"></span>`;
    li.querySelector(".time").textContent = it.when;
    li.querySelector(".title").textContent = it.title;
    li.querySelector(".kind").textContent = it.recurrence_text && it.occurrence
      ? `${it.type_label}, ${it.recurrence_text}` : it.type_label;
    if (it.notes || it.remind_before_min) {
      const details = document.createElement("span");
      details.className = "details";
      details.textContent = [it.notes, it.remind_before_min ? `Напомнить за ${it.remind_before_min} мин.` : ""].filter(Boolean).join("\n");
      li.appendChild(details);
    }
    ul.appendChild(li);
  }
  return ul;
}

async function send(text, viaVoice = false) {
  text = text.trim();
  if (!text || state.busy) return;
  if (openMonthByCommand(text)) return;
  state.busy = true;
  els.input.value = "";
  addMessage("user", text);
  const typing = addMessage("assistant typing", "Думаю");
  try {
    // голос сервера: сервер начнёт озвучивать ответ сразу, не дожидаясь отдельного запроса страницы
    const ttsRate = state.speak && await audio.useServer() ? state.audioSettings.rate : null;
    const data = await api("/api/chat", { method: "POST", body: JSON.stringify({ text, session_id: state.sessionId,
      collect_details: state.audioSettings.details, tts_rate: ttsRate }) });
    typing.remove();
    const showItems = ["create", "update", "complete", "reopen", "search", "undo"].includes(data.intent) ? data.items : [];
    // если записи показаны карточками, из текста убираем их дубли-строки
    const shown = showItems.length
      ? data.reply.split("\n").filter((l) => l.trim() && !/^\s*•/.test(l) && !/^(Ранее|Дальше):$/.test(l.trim())).join("\n")
      : data.reply;
    const msg = addMessage("assistant", shown, { items: showItems, changed: data.changed, clarify: !!data.awaiting });
    offerMonth(msg, data);
    // если человек говорил голосом и ассистент задал вопрос — сразу слушаем ответ
    const listenAfter = viaVoice && data.awaiting && canListen();
    say(data.speech, listenAfter ? () => startListening() : null, data.speech_parts);
    if (data.changed.length || ["create", "update", "delete", "complete", "reopen", "undo"].includes(data.intent)) {
      refreshPlan(data.changed);
    }
  } catch (err) {
    typing.remove();
    addMessage("assistant", `Не получилось выполнить запрос: ${err.message}. Попробуйте ещё раз.`);
  } finally {
    state.busy = false;
  }
}

/* ---------------------------------------------------------------- озвучка */

const audio = new PlannerAudio({ getSettings: () => state.audioSettings, isEnabled: () => state.speak,
  onError: (text) => { hint(text); els.voiceStatus.textContent = text; }, onVoicesChanged: updateVoices,
  // озвучка на сервере (Piper), если в браузере нет русского голоса или он выбран в настройках
  remote: {
    available: () => !!(state.status && state.status.tts_server),
    synthesize: async (text, rate) => {
      const res = await fetch("/api/tts", { method: "POST", body: JSON.stringify({ text, rate }),
        headers: { "Content-Type": "application/json", ...userHeaders() } });
      if (!res.ok) throw new Error(`Ошибка ${res.status}`);
      return res.arrayBuffer();
    },
  },
});
const audioHelp = "Настройки сохраняются в этом браузере. Для напоминаний держите страницу открытой.";

function updateVoices() {
  const voices = audio.voices();
  const server = !!(state.status && state.status.tts_server);
  els.voiceSelect.textContent = "";
  els.voiceSelect.add(new Option("Автоматически · русский голос", ""));
  for (const voice of voices) {
    els.voiceSelect.add(new Option(`${voice.name} · ${voice.lang}`, voice.voiceURI));
  }
  if (server) els.voiceSelect.add(new Option("Ирина · голос сервера", PlannerAudio.SERVER_VOICE));
  const options = [...els.voiceSelect.options].map((o) => o.value);
  if (options.includes(state.audioSettings.voice)) els.voiceSelect.value = state.audioSettings.voice;
  els.voiceSelect.disabled = options.length < 2;
  const russian = audio.hasRussianVoice();
  if (server && !russian && !audio.synth) els.voiceStatus.textContent = "В этом браузере нет синтеза речи — ответы озвучивает сервер.";
  else if (server && !russian && audio.synth.getVoices().length) els.voiceStatus.textContent = "В браузере нет русского голоса — ответы озвучивает сервер.";
  else if (!audio.synth) els.voiceStatus.textContent = "В этом браузере нет синтеза речи. Будут доступны текстовые напоминания и колокольчик.";
  else if (!voices.length) els.voiceStatus.textContent = audio.synth.getVoices().length
    ? (russian ? "Google русский, Microsoft Irina и Microsoft Pavel недоступны в этом браузере. Автоматический режим озвучит текст другим русским голосом."
      : "В браузере нет русского голоса для озвучки. Ответы будут показаны текстом.")
    : server ? "Браузер не предоставил голоса — ответы озвучивает сервер."
      : "Браузер ещё не предоставил голоса. Попробуйте прослушать пример; выбор обновится автоматически.";
  else els.voiceStatus.textContent = audioHelp;
}

function syncAudioSettings() {
  els.voiceRate.value = state.audioSettings.rate;
  els.voiceVolume.value = Math.round(state.audioSettings.volume * 100);
  els.rateValue.textContent = `${state.audioSettings.rate.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}×`;
  els.volumeValue.textContent = `${Math.round(state.audioSettings.volume * 100)}%`;
  els.bellEnabled.checked = state.audioSettings.bell;
  els.detailsEnabled.checked = state.audioSettings.details;
}

function saveAudioSettings() {
  state.audioSettings = { voice: els.voiceSelect.value, rate: Number(els.voiceRate.value),
    volume: Number(els.voiceVolume.value) / 100, bell: els.bellEnabled.checked, details: els.detailsEnabled.checked };
  store.set("audioSettings", state.audioSettings);
  syncAudioSettings();
}

function say(text, onEnd, parts = null) {
  return audio.play(text, { onEnd: onEnd || (() => {}), parts });
}

function setSpeak(on) {
  state.speak = on;
  store.set("speak", on);
  els.speakToggle.setAttribute("aria-pressed", String(on));
  els.speakToggle.title = on ? "Озвучка включена" : "Озвучка выключена";
  if (!on) audio.stop();
}

/* ---------------------------------------------------------------- микрофон */

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let recorder = null;
let finalSent = false;
let switchToServer = false;
// Режим распознавания: auto — своё распознавание браузера (Chrome, Edge, Safari), а сервер (Vosk) — только
// если его нет или он отказал; browser; server. ?voice=server — для проверки
const STT_MODES = ["auto", "browser", "server"];
state.sttMode = (() => {
  const fromUrl = new URLSearchParams(location.search).get("voice");
  if (STT_MODES.includes(fromUrl)) store.set("sttMode", fromUrl);
  const saved = store.get("sttMode", "auto");
  return STT_MODES.includes(saved) ? saved : "auto";
})();

const serverStt = () => !!(state.status && state.status.stt_server && window.VoiceRecorder && VoiceRecorder.supported());
// отказ браузера запоминаем только до перезагрузки страницы: в Chrome случайный сбой сети не должен
// навсегда переключить на менее точное серверное распознавание
state.browserSttFailed = false;
const useServerStt = () => serverStt()
  && (!SR || state.sttMode === "server" || (state.sttMode === "auto" && state.browserSttFailed));

function canListen() {
  return !!SR || serverStt();
}

function setListening(on) {
  state.listening = on;
  els.mic.setAttribute("aria-pressed", String(on));
  els.micLabel.textContent = on ? "Остановить" : "Говорить";
  els.mic.title = on ? "Остановить запись" : "Говорить";
  if (on) hint("Слушаю… Говорите.");
}

function hint(text) { els.hint.textContent = text || ""; }

function startListening() {
  if (state.listening) return;
  audio.stop();
  if (useServerStt()) return startRecording();
  if (SR) return startBrowserRecognition();
  hint("Голосовой ввод работает в Chrome, Edge и Safari. Можно писать текстом.");
}

function stopListening() {
  if (recorder) return recorder.stop("manual");
  if (recognition) recognition.stop();
}

function setSttMode(mode) {
  state.sttMode = mode;
  store.set("sttMode", mode);
  if (els.sttMode) els.sttMode.value = mode;
  showStatus();
}

function startBrowserRecognition() {
  recognition = new SR();
  recognition.lang = "ru-RU";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.maxAlternatives = 1;
  finalSent = false;
  switchToServer = false;
  recognition.onresult = (e) => {
    let interim = "", final = "";
    for (const r of e.results) (r.isFinal ? (final += r[0].transcript) : (interim += r[0].transcript));
    els.input.value = final || interim;
    if (final && !finalSent) { finalSent = true; send(final, true); }
  };
  recognition.onerror = (e) => {
    // Opera, Яндекс Браузер, Brave и др. объявляют распознавание, но сервис Google им недоступен
    if (["network", "service-not-allowed", "language-not-supported"].includes(e.error) && serverStt()
        && state.sttMode === "auto") {
      switchToServer = true;
      return;
    }
    const messages = {
      "not-allowed": "Нет доступа к микрофону. Разрешите его в настройках браузера.",
      "no-speech": "Ничего не слышно. Нажмите на микрофон и говорите.",
      "network": "Распознавание речи недоступно без интернета. Можно писать текстом.",
      "audio-capture": "Микрофон не найден.",
    };
    hint(messages[e.error] || "Не получилось распознать речь. Попробуйте ещё раз.");
  };
  recognition.onend = () => {
    setListening(false);
    if (switchToServer) {
      state.browserSttFailed = true;
      showStatus();
      showToast("Распознавание речи браузера недоступно — речь распознаёт сервер.");
      return startRecording();
    }
    if (!finalSent && els.input.value.trim()) { finalSent = true; send(els.input.value, true); }
    else if (els.hint.textContent.startsWith("Слушаю")) hint("");
  };
  recognition.start();
  setListening(true);
}

// Потоковое распознавание: звук уходит на сервер по ходу речи, промежуточный текст сразу виден в поле ввода
function openSttStream(rate, onPartial) {
  let ws;
  try { ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/stt/stream`); }
  catch { return null; }
  let pending = [], pendingSize = 0, ended = false, failed = false, finalText = null, settle = null;
  const done = (text) => { if (settle) { settle(text); settle = null; } };
  const fail = () => { if (finalText === null) { failed = true; done(null); } };
  const flush = () => {
    if (!pendingSize || ws.readyState !== WebSocket.OPEN) return;
    const pcm = new Int16Array(pendingSize);
    let offset = 0;
    for (const p of pending) { pcm.set(p, offset); offset += p.length; }
    pending = []; pendingSize = 0;
    ws.send(pcm.buffer);
  };
  ws.onopen = () => {
    ws.send(JSON.stringify({ rate, code: state.accessCode || "" }));
    flush();
    if (ended) ws.send("end");
  };
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (typeof msg.partial === "string") onPartial(msg.partial);
    else if (typeof msg.text === "string") { finalText = msg.text; done(msg.text); }
    else if (msg.error) fail();
  };
  ws.onerror = fail;
  ws.onclose = fail;
  return {
    push(pcm) {  // копим ~0,25 с, чтобы не слать сотни мелких сообщений
      if (failed || ended) return;
      pending.push(pcm); pendingSize += pcm.length;
      if (pendingSize >= rate / 4) flush();
    },
    finish(timeoutMs = 8000) {
      return new Promise((resolve) => {
        if (finalText !== null || failed) return resolve(failed ? null : finalText);
        settle = resolve;
        ended = true;
        if (ws.readyState === WebSocket.OPEN) { flush(); ws.send("end"); }
        setTimeout(() => done(null), timeoutMs);
      });
    },
    close() { failed = true; try { ws.close(); } catch { /* уже закрыт */ } },
  };
}

async function startRecording() {
  let stream = null;
  recorder = new VoiceRecorder({
    onChunk: (pcm) => stream && stream.push(pcm),
    onLevel: (level) => els.mic.style.setProperty("--level", level.toFixed(2)),  // кольцо вокруг кнопки
    onStop: (wav) => { showLevel(false); recognizeOnServer(wav, stream); },
  });
  try {
    await recorder.start();
  } catch (err) {
    recorder = null;
    hint(err && err.name === "NotAllowedError" ? "Нет доступа к микрофону. Разрешите его в настройках браузера."
      : "Микрофон не найден или занят другой программой.");
    return;
  }
  if (state.status.stt_stream && window.WebSocket) {
    stream = openSttStream(recorder.outputRate, (text) => { if (state.listening) els.input.value = text; });
  }
  els.input.value = "";
  showLevel(true);
  setListening(true);
  hint("Слушаю… Говорите — после паузы запись остановится сама.");
}

// Индикатор громкости: видно, что микрофон слышит голос (при распознавании на сервере)
function showLevel(on) {
  els.mic.classList.toggle("metered", on);
  els.mic.style.setProperty("--level", "0");
}

async function recognizeOnServer(wav, stream) {
  recorder = null;
  setListening(false);
  if (!wav) {
    if (stream) stream.close();
    return hint("Ничего не слышно. Нажмите на микрофон и говорите.");
  }
  hint("Распознаю…");
  let text = null;
  if (stream) { text = await stream.finish(); stream.close(); }
  if (text === null) {  // потоковое распознавание недоступно — отправляем запись целиком
    const form = new FormData();
    form.append("file", wav, "speech.wav");
    try {
      const res = await fetch("/api/stt", { method: "POST", body: form, headers: userHeaders() });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `ошибка ${res.status}`);
      text = data.text || "";
    } catch (err) { return hint(`Не получилось распознать речь: ${err.message}.`); }
  }
  hint("");
  if (text) { els.input.value = text; send(text, true); }
  else { els.input.value = ""; hint("Ничего не распознано. Попробуйте ещё раз, чуть ближе к микрофону."); }
}

/* ---------------------------------------------------------------- план на неделю */

const WEEKDAYS = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];
const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

function dayTitle(dateStr, todayStr) {
  const d = new Date(dateStr + "T00:00:00");
  const t = new Date(todayStr + "T00:00:00");
  const diff = Math.round((d - t) / 86400000);
  const base = `${WEEKDAYS[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return diff === 0 ? `Сегодня, ${base}` : diff === 1 ? `Завтра, ${base}` : base[0].toUpperCase() + base.slice(1);
}

async function loadAgenda(changed = []) {
  let items, undated;
  try {
    [items, undated] = await Promise.all([api("/api/items/upcoming?days=7"), api("/api/items/undated")]);
  } catch { return; }
  const today = (state.status && state.status.now ? state.status.now : new Date().toISOString()).slice(0, 10);
  els.agendaList.textContent = "";
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "agenda-empty";
    p.textContent = "На неделю ничего не запланировано. Скажите, например: «Добавь встречу с командой завтра в 15:00».";
    els.agendaList.appendChild(p);
  }
  const groups = new Map();
  for (const it of items) {
    const key = it.due_at.slice(0, 10);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(it);
  }
  for (const [day, dayItems] of groups) {
    const sec = document.createElement("section");
    sec.className = "day" + (day === today ? " today" : "");
    const h = document.createElement("h3");
    h.textContent = dayTitle(day, today);
    sec.appendChild(h);
    const ul = document.createElement("ul");
    ul.className = "items";
    // у серии подсвечиваем только то повторение, которое меняли (ключ «id@дата»)
    for (const it of dayItems) {
      const flash = it.recurrence ? changed.includes(`${it.id}@${it.due_at}`) : changed.includes(it.id);
      ul.appendChild(agendaItem(it, flash));
    }
    sec.appendChild(ul);
    els.agendaList.appendChild(sec);
  }
  if (undated.length) {
    // задачи без срока («запиши купить корм коту») — отдельным блоком, иначе их не видно в плане
    const sec = document.createElement("section");
    sec.className = "day undated";
    const h = document.createElement("h3");
    h.textContent = "Без срока";
    const ul = document.createElement("ul");
    ul.className = "items";
    for (const it of undated) ul.appendChild(agendaItem(it, changed.includes(it.id)));
    sec.append(h, ul);
    els.agendaList.appendChild(sec);
  }
}

function checkLabel(it, done) {
  return done ? `Снять отметку: ${it.title}` : `Отметить выполненной: ${it.title}`;
}

function agendaItem(it, flash) {
  const li = document.createElement("li");
  li.className = `item ${it.type}` + (it.status === "done" ? " done" : "") + (flash ? " flash" : "");
  const check = document.createElement("input");
  check.type = "checkbox";
  check.className = "check";
  check.checked = it.status === "done";
  check.setAttribute("aria-label", checkLabel(it, check.checked));
  check.title = check.checked ? "Снять отметку" : "Отметить выполненной";
  check.addEventListener("change", async () => {
    // галочку можно и поставить, и снять; у серии отметка ставится на конкретную дату повторения
    const action = check.checked ? "complete" : "reopen";
    const on = it.recurrence ? `?on=${it.due_at.slice(0, 10)}` : "";
    try {
      await api(`/api/items/${it.id}/${action}${on}`, { method: "POST" });
      refreshPlan([it.recurrence ? `${it.id}@${it.due_at}` : it.id]);
    }
    catch (err) { check.checked = !check.checked; showToast(err.message); }
  });
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = !it.due_at ? "—" : it.has_time ? it.due_at.slice(11, 16) : "день";
  if (it.has_time && it.end_at) {
    const end = document.createElement("span");
    end.className = "time-end";
    end.textContent = "–" + it.end_at.slice(11, 16);
    if (it.end_at.slice(0, 10) !== it.due_at.slice(0, 10)) end.textContent += " +1";
    time.appendChild(end);
  }
  time.title = it.reminder_text || it.when;
  const title = document.createElement("span");
  title.className = "title";
  title.textContent = it.title;
  title.title = `${it.type_label}: ${it.title}` + (it.recurrence_text ? ` (${it.recurrence_text})` : "");
  if (it.recurrence) {
    const mark = document.createElement("span");
    mark.className = "repeat-mark";
    mark.textContent = "↻";
    mark.title = it.recurrence_text || "Повторяется";
    mark.setAttribute("aria-label", `Повторяется: ${it.recurrence_text || ""}`);
    title.append(" ", mark);
  }
  const del = document.createElement("button");
  del.type = "button";
  del.className = "del";
  del.textContent = "×";
  del.setAttribute("aria-label", `Удалить: ${it.title}`);
  del.addEventListener("click", async () => {
    if (it.recurrence) { showDeleteChoice(li, it, del); return; }
    try { await api(`/api/items/${it.id}`, { method: "DELETE" }); refreshPlan(); }
    catch (err) { showToast(err.message); }
  });
  li.append(check, time, title, del);
  if (it.notes || it.remind_before_min) {
    const details = document.createElement("details");
    details.className = "agenda-details";
    const summary = document.createElement("summary");
    summary.textContent = "Подробности";
    const content = document.createElement("p");
    content.textContent = [it.notes, it.remind_before_min ? `Напомнить за ${it.remind_before_min} мин.` : "Напомнить в назначенное время."].filter(Boolean).join("\n");
    details.append(summary, content);
    li.appendChild(details);
  }
  return li;
}

/* У повторяющейся записи «×» спрашивает, что удалить: только этот день или всю серию */
function showDeleteChoice(li, it, del) {
  const box = document.createElement("div");
  box.className = "del-choice";
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", `Удалить «${it.title}»`);
  const mk = (label, handler, cls = "") => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    if (cls) b.className = cls;
    b.addEventListener("click", handler);
    return b;
  };
  const remove = async (query) => {
    try { await api(`/api/items/${it.id}${query}`, { method: "DELETE" }); refreshPlan(); }
    catch (err) { showToast(err.message); }
  };
  const close = () => { box.remove(); del.hidden = false; li.classList.remove("choosing"); };
  box.append(
    mk("Только этот день", () => remove(`?on=${it.due_at.slice(0, 10)}`)),
    mk("Всю серию", () => remove("")),
    mk("Отмена", close, "ghost"),
  );
  del.hidden = true;
  li.classList.add("choosing");
  li.append(box);
  box.querySelector("button").focus();
}

/* ---------------------------------------------------------------- напоминания */

let toastTimer = null;
function showToast(text) {
  els.toast.textContent = text;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (els.toast.hidden = true), 8000);
}

let checkingReminders = false;
async function checkReminders() {
  if (checkingReminders) return;
  checkingReminders = true;
  let due;
  try { due = await api("/api/reminders/due?minutes=2"); } catch { return; }
  finally { checkingReminders = false; }
  for (const it of due) {
    const key = `${it.id}@${it.due_at}`;  // у серии каждое повторение напоминает отдельно
    if (state.notified.has(key)) continue;
    state.notified.add(key);
    const text = it.reminder_text || `Напоминаю: ${it.when} — ${it.title}.`;
    audio.play(text, { bell: true, onStart: () => showToast(text) });
  }
  store.set("notified", [...state.notified].slice(-200));
}

/* ---------------------------------------------------------------- календарь */

function openCalendarDialog() {
  const path = (state.status && state.status.calendar_path) || "/calendar/demo.ics";
  const url = location.origin + path;
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  els.calendarUrl.value = url;
  // Google принимает ссылку подписки в параметре cid; Apple и Outlook открывают схему webcal://
  els.calendarGoogle.href = `https://calendar.google.com/calendar/r?cid=${encodeURIComponent(url.replace(/^https?:/, "webcal:"))}`;
  els.calendarWebcal.href = url.replace(/^https?:/, "webcal:");
  els.calendarGoogle.hidden = els.calendarWebcal.hidden = local;
  if (local) {
    els.calendarHelp.textContent = "Подписка работает, когда сайт открыт из интернета (на стенде). "
      + "Локально можно скачать .ics и импортировать его в календарь.";
  }
  els.calendar.showModal();
  els.calendarUrl.select();
}

async function copyCalendarLink() {
  try {
    await navigator.clipboard.writeText(els.calendarUrl.value);
    els.calendarCopy.textContent = "Скопировано";
  } catch {
    els.calendarUrl.select();  // нет доступа к буферу — ссылка выделена, её можно скопировать вручную
    els.calendarCopy.textContent = "Ctrl+C";
  }
  setTimeout(() => (els.calendarCopy.textContent = "Скопировать"), 2000);
}

/* ---------------------------------------------------------------- месяц */

const MONTH_NAMES = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
  "Ноябрь", "Декабрь"];
const month = { first: null, selected: null, byDay: new Map() };

const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const todayIso = () => (state.status && state.status.now ? state.status.now.slice(0, 10) : isoDay(new Date()));
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};

// План на неделю и открытый календарь показывают одно и то же — после изменений обновляем оба
function refreshPlan(changed = []) {
  loadAgenda(changed);
  if (els.month.open) loadMonth();
}

function openMonth(day) {
  const key = day || todayIso();
  const d = new Date(key + "T00:00:00");
  month.first = new Date(d.getFullYear(), d.getMonth(), 1);
  month.selected = key;
  if (!els.month.open) els.month.showModal();
  loadMonth().then(() => focusDay(key));
}

function shiftMonth(delta) {
  const f = month.first;
  const next = new Date(f.getFullYear(), f.getMonth() + delta, 1);
  const lastDay = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
  const day = Math.min(Number(month.selected.slice(8, 10)), lastDay);
  month.first = next;
  month.selected = isoDay(new Date(next.getFullYear(), next.getMonth(), day));
  loadMonth();
}

async function loadMonth() {
  const first = month.first;
  const start = new Date(first.getFullYear(), first.getMonth(), 1 - ((first.getDay() + 6) % 7));  // с понедельника
  const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 42);
  els.monthTitle.textContent = `${MONTH_NAMES[first.getMonth()]} ${first.getFullYear()}`;
  let items = [];
  try { items = await api(`/api/items/range?start=${isoDay(start)}&end=${isoDay(end)}`); }
  catch (err) { showToast(err.message); }
  month.byDay = new Map();
  for (const it of items) {
    const key = it.due_at.slice(0, 10);
    if (!month.byDay.has(key)) month.byDay.set(key, []);
    month.byDay.get(key).push(it);
  }
  renderMonth(start);
  renderMonthDay();
}

function renderMonth(start) {
  const today = todayIso();
  els.monthWeeks.textContent = "";
  for (let i = 0; i < 42; i++) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    const key = isoDay(d);
    const dayItems = month.byDay.get(key) || [];
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "cell" + (d.getMonth() !== month.first.getMonth() ? " other" : "") + (key === today ? " today" : "");
    cell.dataset.date = key;
    cell.tabIndex = key === month.selected ? 0 : -1;
    cell.setAttribute("aria-selected", String(key === month.selected));
    const n = dayItems.length;
    cell.setAttribute("aria-label", `${dayTitle(key, today)}: ${n ? `${n} ${plural(n, "запись", "записи", "записей")}` : "записей нет"}`);
    const num = document.createElement("span");
    num.className = "num";
    num.textContent = d.getDate();
    cell.appendChild(num);
    for (const it of dayItems.slice(0, 3)) {
      const chip = document.createElement("span");
      chip.className = `chip ${it.type}` + (it.status === "done" ? " done" : "");
      if (it.has_time) {
        const time = document.createElement("b");
        time.textContent = it.due_at.slice(11, 16);
        chip.appendChild(time);
      }
      chip.append(it.title);
      cell.appendChild(chip);
    }
    if (n > 3) {
      const more = document.createElement("span");
      more.className = "more";
      more.textContent = `ещё ${n - 3}`;
      cell.appendChild(more);
    }
    if (n) {  // на телефоне вместо названий — точки по типам записей
      const dots = document.createElement("span");
      dots.className = "dots";
      for (const it of dayItems.slice(0, 4)) {
        const dot = document.createElement("i");
        dot.className = it.type;
        dots.appendChild(dot);
      }
      cell.appendChild(dots);
    }
    cell.addEventListener("click", () => selectDay(key));
    els.monthWeeks.appendChild(cell);
  }
}

function selectDay(key, focus = false) {
  const d = new Date(key + "T00:00:00");
  if (d.getMonth() !== month.first.getMonth() || d.getFullYear() !== month.first.getFullYear()) {
    month.first = new Date(d.getFullYear(), d.getMonth(), 1);
    month.selected = key;
    loadMonth().then(() => focus && focusDay(key));
    return;
  }
  month.selected = key;
  for (const cell of els.monthWeeks.children) {
    const on = cell.dataset.date === key;
    cell.setAttribute("aria-selected", String(on));
    cell.tabIndex = on ? 0 : -1;
  }
  renderMonthDay();
  if (focus) focusDay(key);
}

function focusDay(key) {
  const cell = els.monthWeeks.querySelector(`[data-date="${key}"]`);
  if (cell) cell.focus();
}

function renderMonthDay() {
  const key = month.selected;
  const items = month.byDay.get(key) || [];
  els.monthDay.textContent = "";
  const h = document.createElement("h3");
  h.textContent = dayTitle(key, todayIso());
  els.monthDay.appendChild(h);
  if (!items.length) {
    const d = new Date(key + "T00:00:00");
    const p = document.createElement("p");
    p.className = "agenda-empty";
    p.textContent = `Записей нет. Скажите, например: «Добавь встречу с командой ${d.getDate()} ${MONTHS[d.getMonth()]} в 15:00».`;
    els.monthDay.appendChild(p);
    return;
  }
  const ul = document.createElement("ul");
  ul.className = "items";
  for (const it of items) ul.appendChild(agendaItem(it, false));
  els.monthDay.appendChild(ul);
}

// стрелки — по дням и неделям, PageUp/PageDown — по месяцам
function monthKeys(e) {
  const steps = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
  if (e.key in steps) {
    e.preventDefault();
    const d = new Date(month.selected + "T00:00:00");
    d.setDate(d.getDate() + steps[e.key]);
    selectDay(isoDay(d), true);
  } else if (e.key === "PageUp" || e.key === "PageDown") {
    e.preventDefault();
    shiftMonth(e.key === "PageUp" ? -1 : 1);
  }
}

// «Открой календарь», «покажи календарь на ноябрь» — открываем месяц, не спрашивая сервер
const MONTH_STEMS = ["январ", "феврал", "март", "апрел", "ма[йя]", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"];
function openMonthByCommand(text) {
  if (!/^(?:открой|открыть|покажи|показать)\s+(?:мой\s+|весь\s+)?календар/i.test(text)) return false;
  const rest = text.replace(/^\S+\s+\S+/, "");  // без «покажи календарь»
  const idx = MONTH_STEMS.findIndex((stem) => new RegExp(`(?:^|[^а-яё])${stem}`, "i").test(rest));
  const now = new Date(todayIso() + "T00:00:00");
  let target = null;
  if (idx >= 0) {
    const year = idx < now.getMonth() ? now.getFullYear() + 1 : now.getFullYear();
    target = isoDay(new Date(year, idx, 1));
  }
  els.input.value = "";
  addMessage("user", text);
  const label = idx >= 0 ? ` на ${MONTH_NAMES[idx].toLowerCase()}` : "";
  addMessage("assistant", `Открываю календарь${label}.`);
  say(`Открываю календарь${label}.`);
  openMonth(target);
  return true;
}

// После списка за несколько дней — ссылка «Открыть в календаре»
function offerMonth(msg, data) {
  if (data.intent !== "list" || !data.items || !data.items.length) return;
  const days = new Set(data.items.filter((it) => it.due_at).map((it) => it.due_at.slice(0, 10)));
  if (days.size < 2) return;
  const first = [...days].sort()[0];
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "open-month";
  btn.textContent = "Открыть в календаре";
  btn.addEventListener("click", () => openMonth(first));
  msg.appendChild(document.createElement("br"));
  msg.appendChild(btn);
}

/* ---------------------------------------------------------------- профили */

function setProfile(id) {
  state.profile = id;
  store.set("profile", id);
  state.sessionId = newId();  // контекст диалога другого профиля не переносим
  store.set("sessionId", state.sessionId);
}

async function switchProfile(name) {
  const id = name.trim().replace(/\s+/g, "-");
  if (!/^[\p{L}\p{N}_.@-]{1,64}$/u.test(id)) {
    els.profileHelp.textContent = "Имя профиля: буквы, цифры и знаки . @ - _, до 64 символов.";
    return;
  }
  if (state.status && id === state.status.user) { els.settings.close(); return; }
  setProfile(id);
  for (const msg of els.log.querySelectorAll(".msg")) msg.remove();
  els.empty.hidden = false;
  await loadStatus();
  loadAgenda();
  checkReminders();
  els.settings.close();
  showToast(`Профиль «${id}». Записей: ${state.status ? state.status.items : 0}.`);
}

/* ---------------------------------------------------------------- старт */

function showStatus() {
  if (!state.status) return;
  const nlu = state.status.llm_model ? `разбор запросов: ${state.status.llm_model} + правила` : "разбор запросов: правила";
  const voice = useServerStt() ? "голос: сервер" : SR ? "голос: браузер" : "голос: недоступен в этом браузере";
  els.status.textContent = `профиль: ${state.status.user} · ${nlu}, ${voice}`;
  // выбор распознавания показываем, только если доступны оба варианта
  if (els.sttField) els.sttField.hidden = !(SR && serverStt());
}

async function loadStatus() {
  try {
    state.status = await api("/api/status");
    if (!state.profile && state.status.public_demo) {
      // публичный стенд: у каждого посетителя свой гостевой профиль с копией демо-записей
      setProfile(`гость-${Math.random().toString(36).slice(2, 6)}`);
      state.status = await api("/api/status");
    }
    showStatus();
    updateVoices();
    const cal = state.status.calendar_path;
    els.icsDownload.href = `${cal}${cal.includes("?") ? "&" : "?"}download=true`;
  } catch (err) {
    els.status.textContent = err instanceof AccessError ? "Нужен код доступа" : "Сервер недоступен";
  }
  els.mic.disabled = !canListen();
  if (!canListen()) els.mic.title = "Голосовой ввод работает в Chrome, Edge и Safari";
}

els.composer.addEventListener("submit", (e) => { e.preventDefault(); send(els.input.value); });
els.mic.addEventListener("click", () => (state.listening ? stopListening() : startListening()));
els.speakToggle.addEventListener("click", () => { setSpeak(!state.speak); if (state.speak) audio.unlock(); });
els.settingsOpen.addEventListener("click", () => {
  updateVoices(); syncAudioSettings();
  els.profileName.value = (state.status && state.status.user) || state.profile;
  els.settings.showModal();
});
els.profileForm.addEventListener("submit", (e) => { e.preventDefault(); switchProfile(els.profileName.value); });
els.settingsClose.addEventListener("click", () => els.settings.close());
els.sttMode.value = state.sttMode;
els.sttMode.addEventListener("change", () => setSttMode(els.sttMode.value));
for (const control of [els.voiceSelect, els.voiceRate, els.voiceVolume, els.bellEnabled, els.detailsEnabled]) {
  control.addEventListener(control.type === "range" ? "input" : "change", saveAudioSettings);
}
let previewing = false;
els.voicePreview.addEventListener("click", async () => {
  if (previewing) { audio.stop(); return; }
  previewing = true;
  els.voicePreview.textContent = "Остановить пример";
  const previewHint = state.audioSettings.bell ? "Сначала прозвучит колокольчик, затем пример напоминания." : "Сейчас прозвучит пример напоминания.";
  els.voiceStatus.textContent = previewHint;
  if (!state.listening) hint("");
  setSpeak(true);
  try {
    await audio.unlock();
    await audio.play("Напоминаю: сегодня в 13 часов 50 минут у вас встреча с коллегами. Подготовьте вопросы для обсуждения.", { bell: true });
  } finally {
    previewing = false;
    els.voicePreview.textContent = "Прослушать пример";
    if (els.voiceStatus.textContent === previewHint) els.voiceStatus.textContent = audioHelp;
  }
});
document.addEventListener("pointerdown", () => audio.unlock(), { passive: true });
document.addEventListener("keydown", () => audio.unlock());
els.icsCopy.addEventListener("click", openCalendarDialog);
els.monthOpen.addEventListener("click", () => openMonth());
els.monthPrev.addEventListener("click", () => shiftMonth(-1));
els.monthNext.addEventListener("click", () => shiftMonth(1));
els.monthToday.addEventListener("click", () => openMonth());
els.monthClose.addEventListener("click", () => els.month.close());
els.monthWeeks.addEventListener("keydown", monthKeys);
els.month.addEventListener("click", (e) => { if (e.target === els.month) els.month.close(); });  // щелчок по фону
// Оформление: тёмное по умолчанию, светлое или как в системе — запоминается в браузере
els.themeSelect.value = document.documentElement.dataset.theme || "dark";
els.themeSelect.addEventListener("change", () => {
  document.documentElement.dataset.theme = els.themeSelect.value;
  store.set("theme", els.themeSelect.value);
});
els.calendarCopy.addEventListener("click", copyCalendarLink);
els.calendarClose.addEventListener("click", () => els.calendar.close());
els.suggestions.addEventListener("click", (e) => { if (e.target.tagName === "BUTTON") send(e.target.textContent); });
els.planToggle.addEventListener("click", () => { els.agenda.classList.add("open"); els.planToggle.setAttribute("aria-expanded", "true"); });
els.planClose.addEventListener("click", () => { els.agenda.classList.remove("open"); els.planToggle.setAttribute("aria-expanded", "false"); });
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && e.ctrlKey) { e.preventDefault(); state.listening ? stopListening() : startListening(); }
});

setSpeak(state.speak);
syncAudioSettings();
updateVoices();
loadStatus().then(() => { loadAgenda(); checkReminders(); });
setInterval(checkReminders, 30000);
