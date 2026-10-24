/* Озвучка и короткий синтезированный колокольчик. Внешние сервисы и аудиофайлы не нужны. */
"use strict";

class PlannerAudio {
  constructor({ getSettings, isEnabled, onError = () => {}, onVoicesChanged = () => {} }) {
    this.getSettings = getSettings;
    this.isEnabled = isEnabled;
    this.onError = onError;
    this.synth = window.speechSynthesis;
    this.context = null;
    this.jobs = [];
    this.running = false;
    this.generation = 0;
    this.finishSpeech = null;
    this.finishBell = null;
    this.synth?.addEventListener?.("voiceschanged", onVoicesChanged);
  }

  voices() {
    const names = [/^Google\s+(?:русский|Russian)(?:\s|$)/i, /^Microsoft\s+Irina(?:\s|$)/i,
      /^Microsoft\s+Pavel(?:\s|$)/i];
    const rank = (voice) => names.findIndex((name) => name.test(voice.name));
    return (this.synth?.getVoices() || [])
      .filter((voice) => /^ru(?:-|$)/i.test(voice.lang) && rank(voice) >= 0)
      .sort((a, b) => rank(a) - rank(b));
  }

  voice() {
    const voices = this.voices();
    const selected = voices.find((v) => v.voiceURI === this.getSettings().voice);
    const russian = voices.filter((v) => /^ru(?:-|$)/i.test(v.lang));
    return selected || russian.find((v) => /google|милена|milena|yandex|natural/i.test(v.name)) || russian[0] || null;
  }

  async unlock() {
    if (!this.isEnabled()) return false;
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) return false;
      this.context ||= new Audio();
      if (this.context.state === "suspended") await this.context.resume();
      return this.context.state === "running";
    } catch { return false; }
  }

  play(text, { bell = false, onStart = () => {}, onEnd = () => {} } = {}) {
    return new Promise((resolve) => {
      this.jobs.push({ text, bell, onStart, onEnd, resolve });
      this.drain();
    });
  }

  async drain() {
    if (this.running) return;
    this.running = true;
    try {
      while (this.jobs.length) {
        const job = this.jobs.shift();
        const generation = this.generation;
        try {
          job.onStart();
          if (this.isEnabled()) {
            if (job.bell && this.getSettings().bell) await this.bell();
            if (generation === this.generation && this.isEnabled()) await this.speak(job.text);
          }
          if (generation === this.generation) job.onEnd();
          job.resolve(generation === this.generation);
        } catch {
          this.onError("Не удалось воспроизвести звук. Проверьте разрешения браузера и прослушайте пример в настройках.");
          job.resolve(false);
        }
      }
    } finally { this.running = false; }
  }

  speak(text) {
    if (!text) return Promise.resolve();
    if (!this.synth) {
      this.onError("Озвучка недоступна в этом браузере. Напоминания будут показаны на экране.");
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const settings = this.getSettings();
      const utterance = new SpeechSynthesisUtterance(text.replace(/[«»]/g, ""));
      utterance.lang = "ru-RU";
      const voice = this.voice();
      if (voice) { utterance.voice = voice; utterance.lang = voice.lang; }
      utterance.rate = settings.rate;
      utterance.volume = settings.volume;
      let timer;
      const done = () => {
        clearTimeout(timer);
        if (this.finishSpeech === done) this.finishSpeech = null;
        resolve();
      };
      this.finishSpeech = done;
      utterance.onend = done;
      utterance.onerror = (event) => {
        if (!["canceled", "interrupted"].includes(event.error)) {
          this.onError("Браузер не озвучил текст. Нажмите «Прослушать пример» в настройках и разрешите звук для сайта.");
        }
        done();
      };
      // Некоторые движки не присылают onend после сбоя; очередь должна продолжить работу.
      timer = setTimeout(() => { this.synth.cancel(); done(); }, Math.max(20000, text.length * 220 / settings.rate + 15000));
      try { this.synth.speak(utterance); }
      catch {
        this.onError("Браузер не озвучил текст. Попробуйте другой голос в настройках.");
        done();
      }
    });
  }

  async bell() {
    if (!this.getSettings().volume) return;
    if (!this.context || this.context.state !== "running") {
      this.onError("Чтобы включить колокольчик, нажмите «Прослушать пример» в настройках озвучки.");
      return;
    }
    const ctx = this.context;
    await new Promise((resolve) => {
      const nodes = [];
      const master = ctx.createGain();
      master.gain.value = this.getSettings().volume;
      master.connect(ctx.destination);
      const now = ctx.currentTime;
      for (const offset of [0, 0.24]) {
        // Затухающие обертоны создают мягкий металлический звон.
        for (const [frequency, level] of [[1318.5, 0.16], [2637, 0.07], [3692, 0.035]]) {
          const oscillator = ctx.createOscillator();
          const gain = ctx.createGain();
          oscillator.type = "sine";
          oscillator.frequency.value = frequency;
          gain.gain.setValueAtTime(0, now + offset);
          gain.gain.linearRampToValueAtTime(level, now + offset + 0.006);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.85);
          oscillator.connect(gain);
          gain.connect(master);
          oscillator.start(now + offset);
          oscillator.stop(now + offset + 0.9);
          nodes.push(oscillator, gain);
        }
      }
      let timer;
      const done = () => {
        clearTimeout(timer);
        for (const node of nodes) {
          if (node.stop) { try { node.stop(); } catch { /* уже остановлен */ } }
          node.disconnect();
        }
        master.disconnect();
        if (this.finishBell === done) this.finishBell = null;
        resolve();
      };
      this.finishBell = done;
      timer = setTimeout(done, 1200);
    });
  }

  stop() {
    this.generation++;
    for (const job of this.jobs.splice(0)) job.resolve(false);
    this.synth?.cancel();
    this.finishSpeech?.();
    this.finishBell?.();
  }
}

window.PlannerAudio = PlannerAudio;
