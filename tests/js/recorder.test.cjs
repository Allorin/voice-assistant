// Запуск: node --test tests/js/recorder.test.cjs (без дополнительных пакетов).
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function load({ rate = 48000 } = {}) {
  const log = [];
  let processor = null;
  class AudioContext {
    sampleRate = rate; state = "running"; destination = {};
    createMediaStreamSource() { return { connect() {}, disconnect() { log.push("source-off"); } }; }
    createScriptProcessor() { processor = { connect() {}, disconnect() {} }; return processor; }
    resume() { return Promise.resolve(); }
    close() { log.push("close"); return Promise.resolve(); }
  }
  const track = { stop: () => log.push("mic-off") };
  const navigator = { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [track] }) } };
  const window = { AudioContext };
  const context = vm.createContext({ window, navigator, Blob: class { constructor(parts, opts) {
    this.parts = parts; this.type = opts.type; } }, Float32Array, ArrayBuffer, DataView, Math });
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../../app/static/recorder.js"), "utf8"), context);
  // звук порциями по 4096 отсчётов: громкость level
  const feed = (level, chunks = 1) => {
    for (let i = 0; i < chunks; i++) {
      const data = new Float32Array(4096).fill(level);
      processor.onaudioprocess({ inputBuffer: { getChannelData: () => data } });
    }
  };
  return { window, internals: window.VoiceRecorderInternals, feed, log };
}

test("downsample 48 kHz to 16 kHz averages neighbours", () => {
  const { downsample } = load().internals;
  const res = downsample(new Float32Array([0, 0.3, 0.6, 1, 1, 1]), 48000);
  assert.equal(res.rate, 16000);
  assert.deepEqual(Array.from(res.samples).map((v) => Math.round(v * 10) / 10), [0.3, 1]);
  assert.equal(downsample(new Float32Array(10), 8000).rate, 8000, "lower rates are kept as is");
});

test("encodeWav writes a valid PCM header and clamps samples", () => {
  const { encodeWav } = load().internals;
  const view = new DataView(encodeWav(new Float32Array([0, 1, -1, 2]), 16000));
  const text = (o, n) => String.fromCharCode(...Array.from({ length: n }, (_, i) => view.getUint8(o + i)));
  assert.equal(text(0, 4), "RIFF"); assert.equal(text(8, 4), "WAVE"); assert.equal(text(36, 4), "data");
  assert.equal(view.getUint16(22, true), 1, "mono");
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint16(34, true), 16, "16 bit");
  assert.equal(view.getUint32(40, true), 8);
  assert.deepEqual([0, 1, 2, 3].map((i) => view.getInt16(44 + i * 2, true)), [0, 32767, -32768, 32767]);
});

test("detector stops after a pause that follows speech", () => {
  const { SpeechEndDetector } = load().internals;
  const d = new SpeechEndDetector({ silenceMs: 1000, calibrationMs: 200 });
  const run = (level, ms) => { let r = null; for (let t = 0; t < ms && !r; t += 100) r = d.push(level, 100); return r; };
  assert.equal(run(0.005, 200), null, "calibration on background noise");
  assert.equal(run(0.2, 1500), null, "speech keeps recording");
  assert.equal(run(0.005, 900), null, "short pause between words");
  assert.equal(run(0.2, 300), null);
  assert.equal(run(0.005, 1000), "silence");
});

test("detector gives up when nobody speaks and caps the length", () => {
  const { SpeechEndDetector } = load().internals;
  const quiet = new SpeechEndDetector({ noSpeechMs: 3000 });
  let reason = null;
  for (let t = 0; t < 5000 && !reason; t += 100) reason = quiet.push(0.003, 100);
  assert.equal(reason, "no-speech");
  const talk = new SpeechEndDetector({ maxMs: 2000 });
  reason = null;
  for (let t = 0; t < 5000 && !reason; t += 100) reason = talk.push(t < 250 ? 0.003 : 0.3, 100);
  assert.equal(reason, "max");
});

test("recorder sends WAV after speech and pause, and releases the microphone", async () => {
  const { window, feed, log } = load();
  const stops = [];
  const rec = new window.VoiceRecorder({ onStop: (blob, reason) => stops.push({ blob, reason }) });
  await rec.start();
  feed(0.004, 3);   // ~250 мс фонового шума
  feed(0.3, 12);    // ~1 с речи
  feed(0.004, 20);  // пауза ~1,7 с
  assert.equal(stops.length, 1);
  assert.equal(stops[0].reason, "silence");
  assert.equal(stops[0].blob.type, "audio/wav");
  const wav = new DataView(stops[0].blob.parts[0]);
  assert.equal(wav.getUint32(24, true), 16000);
  assert.ok(log.includes("mic-off") && log.includes("close"));
  feed(0.3, 2);  // после остановки звук больше не копится
  assert.equal(stops.length, 1);
});

test("recorder reports silence instead of sending an empty recording", async () => {
  const { window, feed } = load();
  const stops = [];
  const rec = new window.VoiceRecorder({ onStop: (blob, reason) => stops.push({ blob, reason }),
    detector: { noSpeechMs: 1000 } });
  await rec.start();
  feed(0.003, 15);
  assert.deepEqual(stops, [{ blob: null, reason: "no-speech" }]);
  rec.stop();  // повторная остановка ничего не делает
  assert.equal(stops.length, 1);
});

test("recorder streams 16 kHz PCM chunks while recording", async () => {
  const { window, feed } = load();
  const chunks = [];
  const rec = new window.VoiceRecorder({ onStop() {}, onChunk: (pcm) => chunks.push(pcm) });
  await rec.start();
  assert.equal(rec.outputRate, 16000);
  feed(0.5, 3);
  assert.equal(chunks.length, 3);
  assert.equal(chunks[0].constructor.name, "Int16Array");
  assert.equal(chunks[0].length, Math.floor(4096 / 3));
  assert.equal(chunks[0][0], 16383, "0.5 of full scale");
  rec.stop();
  feed(0.5, 1);
  assert.equal(chunks.length, 3, "nothing is streamed after stop");
});

test("toPcm16 clamps and converts samples", () => {
  const { toPcm16 } = load().internals;
  assert.deepEqual(Array.from(toPcm16(new Float32Array([0, 1, -1, 3, -3]))), [0, 32767, -32768, 32767, -32768]);
});
