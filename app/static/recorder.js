/* Запись голоса для серверного распознавания (Vosk): 16 кГц, моно, 16 бит.
   Нужна браузерам без своего распознавания речи (Firefox, Opera, Яндекс Браузер и др.).
   Звук отдаётся кусками по ходу записи (onChunk — для потокового распознавания с текстом на лету)
   и целиком в WAV после остановки (onStop — запасной путь). Запись останавливается сама после паузы. */
"use strict";

const STT_RATE = 16000;

// Усреднение соседних отсчётов: 48/44,1 кГц → 16 кГц. Если частота и так не выше 16 кГц — без изменений
function downsample(samples, fromRate, toRate = STT_RATE) {
  if (fromRate <= toRate) return { samples: Float32Array.from(samples), rate: fromRate };
  const ratio = fromRate / toRate;
  const out = new Float32Array(Math.floor(samples.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio), end = Math.min(samples.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += samples[j];
    out[i] = sum / Math.max(1, end - start);
  }
  return { samples: out, rate: toRate };
}

function encodeWav(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, s) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

function toPcm16(samples) {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

// Громкость для индикатора на кнопке микрофона: 0 — тишина, 1 — громкая речь (логарифмическая шкала, как слышит ухо)
function levelFromRms(value) {
  if (!(value > 0.004)) return 0;
  return Math.min(1, Math.log10(value / 0.004) / Math.log10(0.25 / 0.004));
}

function rms(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / Math.max(1, samples.length));
}

// Решает, когда остановить запись: по паузе после речи, по долгой тишине или по длительности
class SpeechEndDetector {
  constructor({ silenceMs = 1300, noSpeechMs = 7000, maxMs = 15000, calibrationMs = 250 } = {}) {
    Object.assign(this, { silenceMs, noSpeechMs, maxMs, calibrationMs });
    this.elapsed = 0; this.lastVoice = 0; this.heard = false; this.noise = []; this.threshold = 0.02;
  }

  // level — громкость фрагмента (rms), ms — его длительность. Возвращает причину остановки или null
  push(level, ms) {
    this.elapsed += ms;
    if (this.elapsed <= this.calibrationMs) {
      this.noise.push(level);
      const floor = this.noise.reduce((a, b) => a + b, 0) / this.noise.length;
      this.threshold = Math.min(0.08, Math.max(0.012, floor * 2.5));
    } else if (level > this.threshold) {
      this.heard = true; this.lastVoice = this.elapsed;
    }
    if (this.elapsed >= this.maxMs) return "max";
    if (this.heard && this.elapsed - this.lastVoice >= this.silenceMs) return "silence";
    if (!this.heard && this.elapsed >= this.noSpeechMs) return "no-speech";
    return null;
  }
}

class VoiceRecorder {
  constructor({ onStop, onChunk, onLevel, detector = {} } = {}) {
    this.onStop = onStop || (() => {});
    this.onChunk = onChunk || null;
    this.onLevel = onLevel || null;
    this.level = 0;
    this.detectorOptions = detector;
    this.active = false;
  }

  // частота звука, который получают onChunk и сервер
  get outputRate() {
    return this.context ? Math.min(this.context.sampleRate, STT_RATE) : STT_RATE;
  }

  static supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia
      && (window.AudioContext || window.webkitAudioContext));
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.context = new Ctx();
    if (this.context.state === "suspended") await this.context.resume().catch(() => {});
    this.chunks = [];
    this.detector = new SpeechEndDetector(this.detectorOptions);
    this.source = this.context.createMediaStreamSource(this.stream);
    // ScriptProcessor устарел, но работает во всех браузерах и не требует отдельного файла-модуля
    this.processor = this.context.createScriptProcessor(4096, 1, 1);
    this.processor.onaudioprocess = (e) => {
      if (!this.active) return;
      const data = new Float32Array(e.inputBuffer.getChannelData(0));
      this.chunks.push(data);
      if (this.onChunk) this.onChunk(toPcm16(downsample(data, this.context.sampleRate).samples));
      const loudness = rms(data);
      // быстрый подъём и плавный спад — индикатор не дёргается между слогами
      this.level = Math.max(levelFromRms(loudness), this.level * 0.8);
      if (this.onLevel) this.onLevel(this.level);
      const reason = this.detector.push(loudness, (data.length / this.context.sampleRate) * 1000);
      if (reason) this.stop(reason);
    };
    this.source.connect(this.processor);
    this.processor.connect(this.context.destination);  // без этого Chrome не присылает звук
    this.active = true;
  }

  stop(reason = "manual") {
    if (!this.active) return;
    this.active = false;
    const rate = this.context.sampleRate;
    try { this.source.disconnect(); this.processor.disconnect(); } catch { /* уже отключены */ }
    this.stream.getTracks().forEach((t) => t.stop());
    this.context.close().catch(() => {});
    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    const heard = this.detector.heard || reason === "manual";
    if (!heard || total < rate * 0.3) return this.onStop(null, "no-speech");
    const all = new Float32Array(total);
    let offset = 0;
    for (const c of this.chunks) { all.set(c, offset); offset += c.length; }
    const { samples, rate: outRate } = downsample(all, rate);
    this.onStop(new Blob([encodeWav(samples, outRate)], { type: "audio/wav" }), reason);
  }
}

window.VoiceRecorder = VoiceRecorder;
window.VoiceRecorderInternals = { downsample, encodeWav, toPcm16, rms, levelFromRms, SpeechEndDetector };
