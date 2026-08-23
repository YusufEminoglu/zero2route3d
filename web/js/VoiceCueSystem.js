export class VoiceCueSystem {
  constructor() {
    this.isEnabled = false;
    this.synth = (typeof window !== 'undefined' && 'speechSynthesis' in window) ? window.speechSynthesis : null;
    this.cues = [];
    this.lastSpokenStep = -1;
    this.voice = null;

    if (this.synth) {
      const loadVoices = () => {
        try {
          const voices = this.synth.getVoices() || [];
          this.voice = voices.find((v) => v.lang && v.lang.startsWith('en')) || voices[0] || null;
        } catch (_) {}
      };
      loadVoices();
      if ('onvoiceschanged' in this.synth) {
        this.synth.onvoiceschanged = loadVoices;
      }
    }
  }

  loadCues(cueSheet = []) {
    this.cues = Array.isArray(cueSheet) ? cueSheet : [];
    this.lastSpokenStep = -1;
  }

  toggle(enable) {
    this.isEnabled = enable !== undefined ? Boolean(enable) : !this.isEnabled;
    if (!this.isEnabled && this.synth) {
      try {
        this.synth.cancel();
      } catch (_) {}
    }
    return this.isEnabled;
  }

  get active() {
    return this.isEnabled;
  }

  update(progress) {
    if (!this.isEnabled || !this.synth || !this.cues.length) return;

    const totalSteps = this.cues.length;
    const currentStepIdx = Math.min(totalSteps - 1, Math.max(0, Math.floor(progress * totalSteps)));

    if (currentStepIdx !== this.lastSpokenStep && currentStepIdx >= 0) {
      this.lastSpokenStep = currentStepIdx;
      const cue = this.cues[currentStepIdx];
      if (cue && cue.instruction) {
        this.speak(cue.instruction);
      }
    }
  }

  speak(text) {
    if (!this.synth || !text) return;
    try {
      this.synth.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      if (this.voice) utterance.voice = this.voice;
      utterance.rate = 1.05;
      utterance.pitch = 1.0;
      this.synth.speak(utterance);
    } catch (_) {}
  }
}
