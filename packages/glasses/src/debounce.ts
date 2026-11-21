/** Temporal debounce: a candidate must persist for N consecutive frames above a threshold before it counts. */
export class Debouncer {
  private streak = 0;
  private best = 0;
  constructor(readonly minFrames = 3, readonly minScore = 0.55) {}

  push(score: number): { stable: boolean; consecutive: number; best: number } {
    if (score >= this.minScore) {
      this.streak++;
      this.best = Math.max(this.best, score);
    } else {
      this.streak = 0;
      this.best = 0;
    }
    return { stable: this.streak >= this.minFrames, consecutive: this.streak, best: this.best };
  }

  reset(): void {
    this.streak = 0;
    this.best = 0;
  }
}
