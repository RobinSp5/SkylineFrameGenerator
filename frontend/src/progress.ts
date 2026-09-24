// Progress of a running job in plain words, and an honest estimate of the time left.
import type { JobState } from "./api";

/** Every step a run can go through, in run order (backend app/jobs.py stage_weights). */
export const STEPS = ["name", "fetch", "terrain", "prepare", "trees", "mesh", "export"] as const;
export type Step = (typeof STEPS)[number];

export const STEP_LABELS: Record<Step, string> = {
  name: "Finding the place name",
  fetch: "Downloading map data",
  terrain: "Loading the terrain",
  prepare: "Cleaning up the footprints",
  trees: "Placing trees",
  mesh: "Building the 3D solids",
  export: "Writing the print files",
};

/** The steps this spec runs; terrain and trees are skipped when switched off. */
export function stepsFor(spec: { terrain: boolean; trees: boolean }): Step[] {
  return STEPS.filter((step) => (step !== "terrain" || spec.terrain) && (step !== "trees" || spec.trees));
}

export function isStep(stage: string): stage is Step {
  return (STEPS as readonly string[]).includes(stage);
}

/** "0:07", "1:42", "12:05". */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The remaining-time line. Rounded coarsely on purpose: a number that ticks every second
 * promises a precision the estimate does not have. */
export function formatRemaining(seconds: number | null): string {
  if (seconds === null) return "Estimating…";
  if (seconds < 5) return "Almost done";
  if (seconds < 60) return `About ${Math.max(5, Math.round(seconds / 5) * 5)} s left`;
  const minutes = Math.round(seconds / 30) / 2;
  return minutes <= 1 ? "About 1 min left" : `About ${minutes} min left`;
}

/** Below this share of the run, or this many seconds, the estimate is mostly noise. */
const MIN_FRACTION = 0.08;
const MIN_ELAPSED_S = 3;
/** Within a stage the bar never passes this share of it: the stage only ends when the server says so. */
const STAGE_CAP = 0.92;
/** Time constant (s) with which the shown estimate follows a new one. */
const SMOOTHING_S = 2.5;

export interface Reading {
  /** 0 to 1, including the interpolated progress inside the current stage. */
  fraction: number;
  /** Where the current stage starts and ends on the bar. */
  stageStart: number;
  stageEnd: number;
  /** Smoothed seconds left, or null while there is no meaningful estimate. */
  remainingS: number | null;
}

/**
 * Turns the polled job into a bar position and a remaining time, ticking between polls.
 *
 * The backend reports, per stage, the weighted share of the run done when the stage started
 * (`progress`) and where it ends (`progress_next`). At each stage change the run's total length is
 * estimated as elapsed / progress. Between changes the fraction advances through the stage at that
 * pace, but stops short of its end; a stage that overruns therefore makes the total grow instead of
 * the bar lying. remaining = elapsed / fraction - elapsed, then smoothed so it neither jumps nor
 * ever goes below zero.
 */
export class Estimator {
  private stage = "";
  private start = 0;
  private end = 0;
  private stageStartedS = 0;
  private totalS: number | null = null;
  private shownS: number | null = null;
  private lastTickS: number | null = null;

  /** Feed a polled job. `elapsedS` is the job's running time at that moment. */
  observe(job: Pick<JobState, "stage" | "progress" | "progress_next">, elapsedS: number, fallback?: { start: number; end: number }): void {
    let start = job.progress ?? fallback?.start ?? this.start;
    let end = job.progress_next ?? fallback?.end ?? this.end;
    // Never backwards: an unknown stage keeps the span it had.
    start = Math.max(this.start, Math.min(1, start));
    end = Math.max(start, Math.min(1, end));
    if (job.stage !== this.stage || start !== this.start) {
      this.stage = job.stage;
      this.stageStartedS = elapsedS;
      if (start >= MIN_FRACTION && elapsedS >= MIN_ELAPSED_S) this.totalS = elapsedS / start;
    }
    this.start = start;
    this.end = end;
  }

  /** Where things stand at `elapsedS` (extrapolated past the last poll by the caller). */
  read(elapsedS: number): Reading {
    const span = this.end - this.start;
    let fraction = this.start;
    if (this.totalS !== null && span > 0) {
      const expectedS = span * this.totalS;
      const within = Math.min(STAGE_CAP, Math.max(0, (elapsedS - this.stageStartedS) / expectedS));
      fraction = this.start + span * within;
    }
    let remaining: number | null = null;
    if (this.totalS !== null && fraction > 0) remaining = Math.max(0, elapsedS / fraction - elapsedS);

    if (remaining === null) {
      this.shownS = null;
    } else if (this.shownS === null || this.lastTickS === null) {
      this.shownS = remaining;
    } else {
      const dt = Math.max(0, elapsedS - this.lastTickS);
      // Count down with the clock, then ease towards the fresh estimate.
      const counted = Math.max(0, this.shownS - dt);
      this.shownS = Math.max(0, counted + (remaining - counted) * (1 - Math.exp(-dt / SMOOTHING_S)));
    }
    this.lastTickS = elapsedS;
    return { fraction, stageStart: this.start, stageEnd: this.end, remainingS: this.shownS };
  }
}
