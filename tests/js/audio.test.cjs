// Запуск: node --test tests/js/audio.test.cjs (без дополнительных пакетов).
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function setup({ noSpeech = false } = {}) {
  const events = [], utterances = [], timers = new Map();
  let nextTimer = 0, enabled = true;
  const settings = { voice: "custom", rate: 0.85, volume: 0.7, bell: true };
  const voices = [{ voiceURI: "ru", name: "Microsoft Irina - Russian (Russia)", lang: "ru-RU" },
    { voiceURI: "custom", name: "Microsoft Pavel - Russian (Russia)", lang: "ru-RU" }];
  const synth = { getVoices: () => voices, addEventListener() {},
    speak: (u) => { utterances.push(u); events.push(`speech:${u.text}`); }, cancel: () => events.push("cancel") };
  class AudioContext {
    state = "running"; currentTime = 0; destination = {};
    resume() { this.state = "running"; return Promise.resolve(); }
    createGain() { return { gain: { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {}, disconnect() {} }; }
    createOscillator() { return { frequency: {}, connect() {}, disconnect() {}, start() { events.push("bell"); }, stop() {} }; }
  }
  const window = { AudioContext, speechSynthesis: noSpeech ? undefined : synth };
  const context = vm.createContext({ window, SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
    setTimeout: (fn, ms) => { const id = ++nextTimer; timers.set(id, { fn, ms }); return id; },
    clearTimeout: (id) => timers.delete(id) });
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../../app/static/audio.js"), "utf8"), context);
  const audio = new window.PlannerAudio({ getSettings: () => settings, isEnabled: () => enabled,
    onError: (e) => events.push(`error:${e}`) });
  return { audio, settings, events, utterances, timers, setEnabled: (value) => { enabled = value; } };
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
