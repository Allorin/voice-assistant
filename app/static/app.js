/* Голосовой ежедневник — клиент.
   Голос → текст: Web Speech API браузера (Chrome, Edge, Safari) или, если его нет, запись + /api/stt.
   Текст → ответ: /api/chat. Ответ → голос: speechSynthesis браузера. */
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  log: $("log"), empty: $("empty"), input: $("input"), composer: $("composer"), mic: $("mic"),
  micLabel: $("micLabel"), hint: $("hint"), status: $("status"), speakToggle: $("speakToggle"),
  agenda: $("agenda"), agendaList: $("agendaList"), planToggle: $("planToggle"), planClose: $("planClose"),
  toast: $("toast"), suggestions: $("suggestions"), icsCopy: $("icsCopy"),
  settings: $("voiceSettings"), settingsOpen: $("settingsOpen"), settingsClose: $("settingsClose"),
  voiceSelect: $("voiceSelect"), voiceRate: $("voiceRate"), voiceVolume: $("voiceVolume"),
  rateValue: $("rateValue"), volumeValue: $("volumeValue"), bellEnabled: $("bellEnabled"),
  detailsEnabled: $("detailsEnabled"), voicePreview: $("voicePreview"), voiceStatus: $("voiceStatus"),
};

const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* приватный режим */ } },
};

const state = {
  sessionId: store.get("sessionId", null) || (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)),
  speak: store.get("speak", true),
  audioSettings: (() => {
    const saved = store.get("audioSettings", {}) || {};
    const number = (v, fallback, min, max) => typeof v === "number" && Number.isFinite(v)
      ? Math.max(min, Math.min(max, v)) : fallback;
    return { voice: typeof saved.voice === "string" ? saved.voice : "", rate: number(saved.rate, 1.05, 0.7, 1.4),
      volume: number(saved.volume, 1, 0, 1), bell: saved.bell !== false, details: saved.details !== false };
  })(),
  listening: false,
  busy: false,
  status: null,
  notified: new Set(store.get("notified", [])),
};
store.set("sessionId", state.sessionId);

/* ---------------------------------------------------------------- API */

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Ошибка ${res.status}`);
  return res.json();
}

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
  state.busy = true;
  els.input.value = "";
  addMessage("user", text);
  const typing = addMessage("assistant typing", "Думаю");
  try {
    const data = await api("/api/chat", { method: "POST", body: JSON.stringify({ text, session_id: state.sessionId,
      collect_details: state.audioSettings.details }) });
    typing.remove();
    const showItems = ["create", "update", "complete", "reopen", "search"].includes(data.intent) ? data.items : [];
    // если записи показаны карточками, из текста убираем их дубли-строки
    const shown = showItems.length
      ? data.reply.split("\n").filter((l) => l.trim() && !/^\s*•/.test(l) && !/^(Ранее|Дальше):$/.test(l.trim())).join("\n")
      : data.reply;
    addMessage("assistant", shown, { items: showItems, changed: data.changed, clarify: !!data.awaiting });
    // если человек говорил голосом и ассистент задал вопрос — сразу слушаем ответ
    const listenAfter = viaVoice && data.awaiting && canListen();
    say(data.speech, listenAfter ? () => startListening() : null);
    if (data.changed.length || ["create", "update", "delete", "complete", "reopen"].includes(data.intent)) {
      loadAgenda(data.changed);
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
  onError: (text) => { hint(text); els.voiceStatus.textContent = text; }, onVoicesChanged: updateVoices });
const audioHelp = "Настройки сохраняются в этом браузере. Для напоминаний держите страницу открытой.";

function updateVoices() {
  const voices = audio.voices();
  els.voiceSelect.textContent = "";
  els.voiceSelect.add(new Option("Автоматически · русский голос", ""));
  for (const voice of voices) {
    els.voiceSelect.add(new Option(`${voice.name} · ${voice.lang}`, voice.voiceURI));
  }
  if (voices.some((v) => v.voiceURI === state.audioSettings.voice)) els.voiceSelect.value = state.audioSettings.voice;
  els.voiceSelect.disabled = !voices.length;
  if (!audio.synth) els.voiceStatus.textContent = "В этом браузере нет синтеза речи. Будут доступны текстовые напоминания и колокольчик.";
  else if (!voices.length) els.voiceStatus.textContent = audio.synth.getVoices().length
    ? "Google русский, Microsoft Irina и Microsoft Pavel недоступны в этом браузере. Автоматический режим попробует озвучить текст по-русски."
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

function say(text, onEnd) {
  return audio.play(text, { onEnd: onEnd || (() => {}) });
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
let mediaRecorder = null;
let finalSent = false;

function canListen() {
  return !!SR || !!(state.status && state.status.stt_server && navigator.mediaDevices);
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
  if (SR) return startBrowserRecognition();
  if (state.status && state.status.stt_server) return startRecording();
  hint("Голосовой ввод работает в Chrome, Edge и Safari. Можно писать текстом.");
}

function stopListening() {
  if (recognition) recognition.stop();
  if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
}

function startBrowserRecognition() {
  recognition = new SR();
  recognition.lang = "ru-RU";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.maxAlternatives = 1;
  finalSent = false;
  recognition.onresult = (e) => {
    let interim = "", final = "";
    for (const r of e.results) (r.isFinal ? (final += r[0].transcript) : (interim += r[0].transcript));
    els.input.value = final || interim;
    if (final && !finalSent) { finalSent = true; send(final, true); }
  };
  recognition.onerror = (e) => {
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
    if (!finalSent && els.input.value.trim()) { finalSent = true; send(els.input.value, true); }
    else if (els.hint.textContent.startsWith("Слушаю")) hint("");
  };
  recognition.start();
  setListening(true);
}

async function startRecording() {
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { hint("Нет доступа к микрофону. Разрешите его в настройках браузера."); return; }
  const chunks = [];
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  mediaRecorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    setListening(false);
    hint("Распознаю…");
    const form = new FormData();
    form.append("file", new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" }), "speech.webm");
    try {
      const res = await fetch("/api/stt", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "ошибка распознавания");
      hint("");
      if (data.text) send(data.text, true); else hint("Ничего не распознано. Попробуйте ещё раз.");
    } catch (err) { hint(`Не получилось распознать речь: ${err.message}.`); }
  };
  mediaRecorder.start();
  setListening(true);
  hint("Слушаю… Нажмите ещё раз, когда закончите.");
  setTimeout(() => mediaRecorder && mediaRecorder.state === "recording" && mediaRecorder.stop(), 15000);
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
  let items;
  try { items = await api("/api/items/upcoming?days=7"); } catch { return; }
  const today = (state.status && state.status.now ? state.status.now : new Date().toISOString()).slice(0, 10);
  els.agendaList.textContent = "";
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "agenda-empty";
    p.textContent = "На неделю ничего не запланировано. Скажите, например: «Добавь встречу с командой завтра в 15:00».";
    els.agendaList.appendChild(p);
    return;
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
      loadAgenda([it.recurrence ? `${it.id}@${it.due_at}` : it.id]);
    }
    catch (err) { check.checked = !check.checked; showToast(err.message); }
  });
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = it.has_time ? it.due_at.slice(11, 16) : "день";
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
    try { await api(`/api/items/${it.id}`, { method: "DELETE" }); loadAgenda(); }
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
    try { await api(`/api/items/${it.id}${query}`, { method: "DELETE" }); loadAgenda(); }
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

async function copyCalendarLink() {
  const path = (state.status && state.status.calendar_path) || "/calendar/demo.ics";
  const url = location.origin + path;
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  try { await navigator.clipboard.writeText(url); } catch { /* нет доступа к буферу — покажем ссылку */ }
  showToast(local
    ? `Ссылка: ${url}. Подписка заработает, когда сайт будет доступен из интернета. Пока можно скачать .ics и импортировать.`
    : `Ссылка скопирована: ${url}. В Google Календаре: «Другие календари» → «Добавить по URL».`);
}

/* ---------------------------------------------------------------- старт */

async function loadStatus() {
  try {
    state.status = await api("/api/status");
    const nlu = state.status.llm_model ? `разбор запросов: ${state.status.llm_model} + правила` : "разбор запросов: правила";
    const voice = SR ? "голос: браузер" : state.status.stt_server ? "голос: сервер" : "голос: недоступен в этом браузере";
    els.status.textContent = `${nlu}, ${voice}`;
  } catch {
    els.status.textContent = "Сервер недоступен";
  }
  if (!canListen()) {
    els.mic.disabled = true;
    els.mic.title = "Голосовой ввод работает в Chrome, Edge и Safari";
  }
}

els.composer.addEventListener("submit", (e) => { e.preventDefault(); send(els.input.value); });
els.mic.addEventListener("click", () => (state.listening ? stopListening() : startListening()));
els.speakToggle.addEventListener("click", () => { setSpeak(!state.speak); if (state.speak) audio.unlock(); });
els.settingsOpen.addEventListener("click", () => { updateVoices(); syncAudioSettings(); els.settings.showModal(); });
els.settingsClose.addEventListener("click", () => els.settings.close());
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
els.icsCopy.addEventListener("click", copyCalendarLink);
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
