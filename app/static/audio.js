/* Озвучка и короткий синтезированный колокольчик.
   Текст озвучивает браузер (speechSynthesis). Если русского голоса в браузере нет (Chrome и Firefox на Linux
   и др.) или в настройках выбран голос сервера, звук берётся с сервера (remote.synthesize → WAV). */
"use strict";

const RUSSIAN = /^ru(?:[-_]|$)/i;

class PlannerAudio {
  static SERVER_VOICE = "server";

  constructor({ getSettings, isEnabled, onError = () => {}, onVoicesChanged = () => {}, remote = null }) {
    this.getSettings = getSettings;
    this.isEnabled = isEnabled;
    this.onError = onError;
    this.remote = remote;  // { available(): bool, synthesize(text, rate): Promise<ArrayBuffer> }
    this.synth = window.speechSynthesis;
    this.voicesLoading = null;
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

  hasRussianVoice() {
    return (this.synth?.getVoices() || []).some((v) => RUSSIAN.test(v.lang));
  }

  // Chrome отдаёт список голосов не сразу; на Linux без голосов он так и остаётся пустым
  voicesReady() {
    if (!this.synth || this.synth.getVoices().length) return Promise.resolve();
    this.voicesLoading ||= new Promise((resolve) => {
      const timer = setTimeout(resolve, 1000);
      this.synth.addEventListener?.("voiceschanged", () => { clearTimeout(timer); resolve(); }, { once: true });
    });
    return this.voicesLoading;
  }

  async useServer() {
    if (!this.remote || !this.remote.available()) return false;
    if (this.getSettings().voice === PlannerAudio.SERVER_VOICE || !this.synth) return true;
    await this.voicesReady();
    return !this.hasRussianVoice();
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
    if (this.remote?.available()) {
      return this.useServer().then((server) => (server ? this.speakServer(text) : this.speakLocal(text)));
    }
    return this.speakLocal(text);
  }

  speakLocal(text) {
    if (!this.synth) {
      this.onError("Озвучка недоступна в этом браузере. Напоминания будут показаны на экране.");
      return Promise.resolve();
    }
    if (this.synth.getVoices().length && !this.hasRussianVoice()) {
      // иначе английский голос прочитал бы русский текст неразборчиво
      this.onError("В браузере нет русского голоса для озвучки. Ответы показаны текстом.");
      return Promise.resolve();
    }
    return this.speakBrowser(text);
  }

  async speakServer(text) {
    const generation = this.generation;
    const settings = this.getSettings();
    let data;
    try {
      data = await this.remote.synthesize(text, settings.rate);
    } catch {
      this.onError("Сервер не смог озвучить ответ. Он показан текстом.");
      return;
    }
    if (!data || !data.byteLength || generation !== this.generation || !this.isEnabled()) return;
    const url = URL.createObjectURL(new Blob([data], { type: "audio/wav" }));
    const player = new window.Audio(url);
    player.volume = settings.volume;
    await new Promise((resolve) => {
      let timer;
      const done = () => {
        clearTimeout(timer);
        player.pause();
        URL.revokeObjectURL(url);
        if (this.finishSpeech === done) this.finishSpeech = null;
        resolve();
      };
      this.finishSpeech = done;
      player.onended = done;
      player.onerror = done;
      timer = setTimeout(done, 60000);
      Promise.resolve(player.play()).catch(() => {
        this.onError("Браузер не дал включить звук. Нажмите «Прослушать пример» в настройках озвучки.");
        done();
      });
    });
  }

  speakBrowser(text) {
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
