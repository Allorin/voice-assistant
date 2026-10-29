// Запуск: node --test tests/js/audio.test.cjs (без дополнительных пакетов).
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const RU_VOICES = [{ voiceURI: "ru", name: "Microsoft Irina - Russian (Russia)", lang: "ru-RU" },
  { voiceURI: "custom", name: "Microsoft Pavel - Russian (Russia)", lang: "ru-RU" }];

function setup({ noSpeech = false, voices = RU_VOICES, remote = null } = {}) {
  const events = [], utterances = [], timers = new Map(), players = [];
  let nextTimer = 0, enabled = true;
  const settings = { voice: "custom", rate: 0.85, volume: 0.7, bell: true };
  const synth = { getVoices: () => voices, addEventListener() {},
    speak: (u) => { utterances.push(u); events.push(`speech:${u.text}`); }, cancel: () => events.push("cancel") };
  class AudioContext {
    state = "running"; currentTime = 0; destination = {};
    resume() { this.state = "running"; return Promise.resolve(); }
    createGain() { return { gain: { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {}, disconnect() {} }; }
    createOscillator() { return { frequency: {}, connect() {}, disconnect() {}, start() { events.push("bell"); }, stop() {} }; }
  }
  class Audio {  // HTMLAudioElement для звука с сервера
    constructor(src) { this.src = src; players.push(this); }
    play() { events.push(`play:${this.src}`); return Promise.resolve(); }
    pause() { this.paused = true; }
  }
  const window = { AudioContext, Audio, speechSynthesis: noSpeech ? undefined : synth };
  const URL = { createObjectURL: () => `blob:${players.length + 1}`, revokeObjectURL: (u) => events.push(`revoke:${u}`) };
  const context = vm.createContext({ window, URL, Blob: class { constructor(parts) { this.parts = parts; } },
    SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
    setTimeout: (fn, ms) => { const id = ++nextTimer; timers.set(id, { fn, ms }); return id; },
    clearTimeout: (id) => timers.delete(id) });
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../../app/static/audio.js"), "utf8"), context);
  const audio = new window.PlannerAudio({ getSettings: () => settings, isEnabled: () => enabled,
    onError: (e) => events.push(`error:${e}`), remote });
  return { audio, settings, events, utterances, timers, players, setEnabled: (value) => { enabled = value; } };
}

async function flush() { await Promise.resolve(); await Promise.resolve(); }

test("two reminders play bell, then speech, without interrupting each other", async () => {
  const { audio, utterances, timers, events } = setup();
  await audio.unlock();
  const first = audio.play("Первая встреча", { bell: true });
  const second = audio.play("Вторая встреча", { bell: true });
  assert.equal(utterances.length, 0, "speech waits for the bell to finish");
  [...timers.values()].find((t) => t.ms === 1200).fn();
  await flush();
  assert.equal(utterances[0].text, "Первая встреча");
  assert.equal(utterances.length, 1);
  utterances[0].onend();
  await first;
  assert.equal(events.filter((e) => e === "bell").length, 12);
  [...timers.values()].find((t) => t.ms === 1200).fn();
  await flush();
  assert.equal(utterances[1].text, "Вторая встреча");
  utterances[1].onend();
  assert.equal(await second, true);
  assert.equal(events.includes("cancel"), false);
});

test("selected voice, volume and rate reach the speech engine", async () => {
  const { audio, utterances } = setup();
  const done = audio.play("Пример");
  assert.equal(utterances[0].voice.voiceURI, "custom");
  assert.equal(utterances[0].rate, 0.85);
  assert.equal(utterances[0].volume, 0.7);
  utterances[0].onend();
  await done;
});

test("muting during the bell cancels speech and queued playback", async () => {
  const { audio, utterances, timers, setEnabled } = setup();
  await audio.unlock();
  const first = audio.play("Не произносить", { bell: true });
  const second = audio.play("Тоже не произносить", { bell: true });
  setEnabled(false);
  audio.stop();
  assert.equal(await first, false);
  assert.equal(await second, false);
  assert.equal(utterances.length, 0);
  assert.equal(timers.size, 0);
});

test("muted playback still shows text and preserves dialog callbacks", async () => {
  const { audio, utterances, setEnabled } = setup();
  setEnabled(false);
  let shown = false, completed = false;
  await audio.play("Текст", { bell: true, onStart: () => { shown = true; }, onEnd: () => { completed = true; } });
  assert.ok(shown && completed);
  assert.equal(utterances.length, 0);
});

test("speech failure does not block the next reminder", async () => {
  const { audio, utterances, events } = setup();
  const first = audio.play("Ошибка");
  const second = audio.play("Следующий");
  utterances[0].onerror({ error: "not-allowed" });
  await first;
  assert.equal(utterances[1].text, "Следующий");
  utterances[1].onend();
  await second;
  assert.ok(events.some((e) => e.startsWith("error:")));
});

test("missing speech support and locked audio do not stall the queue", async () => {
  const { audio, events } = setup({ noSpeech: true });
  assert.equal(await audio.play("Только текст", { bell: true }), true);
  assert.equal(audio.running, false);
  assert.ok(events.some((e) => e.startsWith("error:")));
});

function fakeServer() {
  const calls = [];
  let release = null;
  return {
    calls,
    hold() { return new Promise((resolve) => { release = resolve; }); },
    finish() { release && release(); },
    remote: { available: () => true, synthesize: async (text, rate) => { calls.push({ text, rate }); return new ArrayBuffer(8); } },
  };
}

async function settle() { for (let i = 0; i < 10; i++) await Promise.resolve(); }

// пустой список голосов: браузер ждёт voiceschanged не дольше секунды
async function voicesTimeout(timers) {
  await settle();
  [...timers.values()].find((t) => t.ms === 1000).fn();
  await settle();
}

test("without a Russian browser voice the server speaks, at the chosen rate and volume", async () => {
  const server = fakeServer();
  const { audio, utterances, players, events } = setup({ voices: [{ voiceURI: "en", name: "English", lang: "en-US" }],
    remote: server.remote });
  const done = audio.play("Встреча добавлена");
  await settle();
  assert.deepEqual(server.calls, [{ text: "Встреча добавлена", rate: 0.85 }]);
  assert.equal(utterances.length, 0);
  assert.equal(players.length, 1);
  assert.equal(players[0].volume, 0.7);
  players[0].onended();
  assert.equal(await done, true);
  assert.ok(events.includes("revoke:blob:1"));
});

test("browser Russian voice is preferred unless the server voice is chosen", async () => {
  const server = fakeServer();
  const { audio, utterances, players, settings } = setup({ remote: server.remote });
  const first = audio.play("Браузером");
  await settle();
  assert.equal(utterances.length, 1);
  assert.equal(server.calls.length, 0);
  utterances[0].onend();
  await first;
  settings.voice = "server";
  const second = audio.play("Сервером");
  await settle();
  assert.equal(server.calls.length, 1);
  players[0].onended();
  await second;
});

test("stop while the server prepares audio cancels playback", async () => {
  const server = fakeServer();
  const gate = server.hold();
  server.remote.synthesize = async () => { await gate; return new ArrayBuffer(8); };
  const { audio, players, timers } = setup({ voices: [], remote: server.remote });
  const done = audio.play("Долгий ответ");
  await voicesTimeout(timers);
  audio.stop();
  server.finish();
  assert.equal(await done, false);
  assert.equal(players.length, 0);
});

test("server failure and missing Russian voice are reported, not spoken garbled", async () => {
  const { audio, utterances, events } = setup({ voices: [{ voiceURI: "en", name: "English", lang: "en-US" }] });
  assert.equal(await audio.play("Без голоса"), true);
  assert.equal(utterances.length, 0);
  assert.ok(events.some((e) => e.includes("нет русского голоса")));

  const broken = { available: () => true, synthesize: async () => { throw new Error("502"); } };
  const second = setup({ voices: [], remote: broken });
  const failed = second.audio.play("Ошибка сервера");
  await voicesTimeout(second.timers);
  assert.equal(await failed, true);
  assert.ok(second.events.some((e) => e.includes("Сервер не смог озвучить")));
});
