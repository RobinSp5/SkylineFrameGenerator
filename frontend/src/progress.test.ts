import { describe, expect, it } from "vitest";
import { Estimator, STEP_LABELS, formatClock, formatRemaining, stepsFor } from "./progress";

describe("stepsFor", () => {
  it("skips terrain and trees when they are off", () => {
    expect(stepsFor({ terrain: false, trees: false })).toEqual(["name", "fetch", "prepare", "mesh", "export"]);
    expect(stepsFor({ terrain: true, trees: true })).toEqual(["name", "fetch", "terrain", "prepare", "trees", "mesh", "export"]);
  });

  it("has a plain label for every step, without dashes", () => {
    for (const label of Object.values(STEP_LABELS)) {
      expect(label).toMatch(/^[A-Z][A-Za-z0-9 ]+$/);
    }
  });
});

describe("formatClock", () => {
  it("counts minutes and seconds", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(7.9)).toBe("0:07");
    expect(formatClock(102)).toBe("1:42");
    expect(formatClock(-3)).toBe("0:00");
  });
});

describe("formatRemaining", () => {
  it("says it is estimating until there is a number", () => {
    expect(formatRemaining(null)).toBe("Estimating…");
  });

  it("rounds coarsely so the line does not tick every second", () => {
    expect(formatRemaining(2)).toBe("Almost done");
    expect(formatRemaining(6)).toBe("About 5 s left");
    expect(formatRemaining(21)).toBe("About 20 s left");
    expect(formatRemaining(23)).toBe("About 25 s left");
    expect(formatRemaining(62)).toBe("About 1 min left");
    expect(formatRemaining(100)).toBe("About 1.5 min left");
    expect(formatRemaining(250)).toBe("About 4 min left");
  });
});

/** Replays a run whose stages take `durations` seconds and records every reading, one per 0.25 s. */
function replay(spans: Array<[string, number, number, number]>) {
  const est = new Estimator();
  const out: Array<{ t: number; fraction: number; remaining: number | null }> = [];
  let t = 0;
  for (const [stage, start, end, seconds] of spans) {
    est.observe({ stage, progress: start, progress_next: end }, t);
    for (let i = 0; i < seconds * 4; i++) {
      const r = est.read(t);
      out.push({ t, fraction: r.fraction, remaining: r.remainingS });
      t += 0.25;
    }
  }
  return { out, total: t };
}

describe("Estimator", () => {
  it("estimates nothing at the very start", () => {
    const est = new Estimator();
    est.observe({ stage: "name", progress: 0, progress_next: 0.05 }, 0);
    expect(est.read(0.5).remainingS).toBeNull();
    expect(est.read(0.5).fraction).toBe(0);
  });

  it("predicts the rest of a run that goes as weighted", () => {
    // 20 s run: stage shares match the time actually spent.
    const { out } = replay([
      ["name", 0, 0.05, 1],
      ["fetch", 0.05, 0.15, 2],
      ["prepare", 0.15, 0.45, 6],
      ["mesh", 0.45, 0.7, 5],
      ["export", 0.7, 1, 6],
    ]);
    const at = (t: number) => out.find((r) => r.t >= t)!;
    expect(at(1).remaining).toBeNull(); // 5 % done after 1 s is too little to go on
    expect(at(5).remaining).toBeCloseTo(15, 0);
    expect(at(10).remaining).toBeCloseTo(10, 0);
    expect(at(17).remaining).toBeCloseTo(3, 0);
  });

  it("never goes negative, never moves backwards on the bar and never jumps", () => {
    // Export takes four times its weight: the estimate has to grow, but gently.
    const { out } = replay([
      ["name", 0, 0.05, 1],
      ["fetch", 0.05, 0.15, 2],
      ["prepare", 0.15, 0.45, 6],
      ["mesh", 0.45, 0.7, 5],
      ["export", 0.7, 1, 24],
    ]);
    for (let i = 1; i < out.length; i++) {
      expect(out[i].fraction).toBeGreaterThanOrEqual(out[i - 1].fraction);
      expect(out[i].fraction).toBeLessThan(1);
      const [a, b] = [out[i - 1].remaining, out[i].remaining];
      if (b !== null) expect(b).toBeGreaterThanOrEqual(0);
      // A quarter of a second may change the estimate by little more than a quarter second
      // counting down plus a slice of the difference; nothing like a jump of tens of seconds.
      if (a !== null && b !== null) expect(Math.abs(b - a)).toBeLessThan(2);
    }
  });

  it("smooths a stage change that suddenly halves the estimate", () => {
    const est = new Estimator();
    est.observe({ stage: "prepare", progress: 0.1, progress_next: 0.4 }, 10); // 100 s total
    expect(est.read(10).remainingS!).toBeCloseTo(90, 0);
    for (let t = 10.25; t <= 20; t += 0.25) est.read(t);
    const before = est.read(20).remainingS!;
    expect(before).toBeCloseTo(80, 0);
    est.observe({ stage: "mesh", progress: 0.4, progress_next: 0.7 }, 20); // 50 s total
    const after = est.read(20.25).remainingS!;
    expect(after).toBeLessThan(before);
    expect(after).toBeGreaterThan(60); // eased, not snapped to 30 s
  });

  it("keeps the span it had for a stage the backend does not weight", () => {
    const est = new Estimator();
    est.observe({ stage: "mesh", progress: 0.5, progress_next: 0.7 }, 10);
    est.observe({ stage: "polish" }, 12);
    const r = est.read(12);
    expect(r.stageStart).toBe(0.5);
    expect(r.stageEnd).toBe(0.7);
  });

  it("falls back to the step index an older backend implies", () => {
    const est = new Estimator();
    est.observe({ stage: "prepare" }, 4, { start: 2 / 5, end: 3 / 5 });
    const r = est.read(4);
    expect(r.fraction).toBeCloseTo(0.4);
    expect(r.remainingS).toBeCloseTo(6, 0);
  });
});
