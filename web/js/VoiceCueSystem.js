export class VoiceCueSystem {
  constructor() {
    this.isEnabled = false;
    this.synth = window.speechSynthesis || null;
    this.cues = [];
    this.lastSpokenStep = -1;
    this.voice = null;

    if (this.synth) {
      const loadVoices = () => {
        const voices = this.synth.getVoices();
        this.voice = voices.find((v) => v.lang.startsWith('en')) || voices[0] || null;
      };
      loadVoices();
      if (this.synth.onvoiceschanged !== undefined) {
        this.synth.onvoiceschanged = loadVoices;
      }
    }
  }

  loadCues(cueSheet = []) {
    this.cues = cueSheet || [];
    this.lastSpokenStep = -1;
  }

  toggle(enable) {
    this.isEnabled = enable;
    if (!this.isEnabled && this.synth) {
      this.synth.cancel();
    }
  }

  update(progress) {
    if (!this.isEnabled || !this.synth || !this.cues.length) return;

    const totalSteps = this.cues.length;
    const currentStepIdx = Math.min(totalSteps - 1, Math.floor(progress * totalSteps));

    if (currentStepIdx !== this.lastSpokenStep && currentStepIdx >= 0) {
      this.lastSpokenStep = currentStepIdx;
      const cue = this.cues[currentStepIdx];
      if (cue && cue.instruction) {
        this.speak(cue.instruction);
      }
    }
  }

  speak(text) {
    if (!this.synth) return;
    this.synth.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    if (this.voice) utterance.voice = this.voice;
    utterance.rate = 1.05;
    utterance.pitch = 1.0;
    this.synth.speak(utterance);
  }
}
