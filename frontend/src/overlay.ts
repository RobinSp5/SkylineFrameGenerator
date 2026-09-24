// The generation overlay: a large card over the map while a job runs, with the current step, a
// determinate bar, the elapsed time and an estimate of the time left. Failures stay in it with a
// way to try again.
import type { JobState } from "./api";
import { Estimator, STEP_LABELS, formatClock, formatRemaining, isStep, stepsFor, type Step } from "./progress";

export interface Overlay {
  /** Opens the card for a run of the given steps; `subtitle` names the place and size. */
  start(spec: { terrain: boolean; trees: boolean }, subtitle: string): void;
  update(job: JobState): void;
  /** Closes the card; `focus` gets the keyboard focus afterwards. */
  finish(focus?: HTMLElement | null): void;
  fail(message: string): void;
  onRetry(cb: () => void): void;
  /** Fires when the user closes a failed run's card. */
  onClose(cb: () => void): void;
}

/** How often the elapsed time and the estimate move between two polls. */
const TICK_MS = 250;
/** Matches the fade-out in style.css (.gen). */
const FADE_MS = 220;

function el<T extends HTMLElement>(root: ParentNode, id: string): T {
  const node = root.querySelector<T>(`#${id}`);
  if (!node) throw new Error(`missing element #${id}`);
  return node;
}

export function setupOverlay(root: HTMLElement, now: () => number = () => performance.now()): Overlay {
  const overlay = el<HTMLElement>(root, "gen-overlay");
  const card = el<HTMLElement>(root, "gen-card");
  const title = el<HTMLElement>(root, "gen-title");
  const subtitle = el<HTMLElement>(root, "gen-subtitle");
  const percent = el<HTMLElement>(root, "gen-percent");
  const eta = el<HTMLElement>(root, "gen-eta");
  const bar = el<HTMLElement>(root, "gen-bar");
  const fill = el<HTMLElement>(root, "gen-fill");
  const live = el<HTMLElement>(root, "gen-live");
  const steps = el<HTMLOListElement>(root, "gen-steps");
  const announce = el<HTMLElement>(root, "gen-announce");
  const elapsed = el<HTMLElement>(root, "gen-elapsed");
  const count = el<HTMLElement>(root, "gen-count");
  const running = el<HTMLElement>(root, "gen-running");
  const error = el<HTMLElement>(root, "gen-error");
  const errorText = el<HTMLElement>(root, "gen-error-text");
  const retry = el<HTMLButtonElement>(root, "gen-retry");
  const close = el<HTMLButtonElement>(root, "gen-close");

  let order: Step[] = [];
  let estimator = new Estimator();
  let timer: ReturnType<typeof setInterval> | null = null;
  // Elapsed seconds at the last poll and the local clock then; the tick extrapolates from both.
  let polledS = 0;
  let polledAt = 0;
  let startedAt = 0;
  let lastShownS = 0;
  let current = "";
  let waiting = false;
  let retryListener: (() => void) | null = null;
  let closeListener: (() => void) | null = null;
  let hideTimer: ReturnType<typeof setTimeout> | null = null;

  const elapsedNow = () => {
    // Never backwards: a poll may report slightly less than the local extrapolation had reached.
    lastShownS = Math.max(lastShownS, polledS + (now() - polledAt) / 1000);
    return lastShownS;
  };

  const renderSteps = (stage: string, message: string) => {
    const at = isStep(stage) ? order.indexOf(stage) : -1;
    steps.replaceChildren(
      ...order.map((step, i) => {
        const li = document.createElement("li");
        li.dataset.state = at < 0 ? "todo" : i < at ? "done" : i === at ? "current" : "todo";
        const label = document.createElement("span");
        label.className = "gen-step-label";
        label.textContent = STEP_LABELS[step];
        li.append(label);
        if (i === at) {
          li.setAttribute("aria-current", "step");
          // The backend's own line, when it carries numbers ("Building solids for 2148 buildings
          // ..."); without any it only repeats the label in other words.
          if (/\d/.test(message)) {
            const detail = document.createElement("span");
            detail.className = "gen-step-detail mono";
            detail.textContent = message;
            li.append(detail);
          }
        }
        return li;
      }),
    );
    count.textContent = at < 0 ? "" : `Step ${at + 1} of ${order.length}`;
  };

  const tick = () => {
    const seconds = elapsedNow();
    const reading = estimator.read(seconds);
    const pct = Math.min(99, Math.floor(reading.fraction * 100));
    percent.textContent = String(pct);
    fill.style.transform = `scaleX(${reading.fraction})`;
    // The shimmer sits on the stretch of the bar the current step will fill.
    live.style.left = `${reading.stageStart * 100}%`;
    live.style.width = `${Math.max(0, reading.stageEnd - reading.stageStart) * 100}%`;
    bar.setAttribute("aria-valuenow", String(pct));
    eta.textContent = waiting ? "Waiting for the server" : formatRemaining(reading.remainingS);
    elapsed.textContent = formatClock(seconds);
  };

  const stopTimer = () => {
    if (timer !== null) clearInterval(timer);
    timer = null;
  };

  /** Fades the card out (style.css), then takes it out of the page. */
  const hide = () => {
    stopTimer();
    overlay.classList.remove("open");
    root.removeAttribute("data-generating");
    if (hideTimer !== null) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      overlay.hidden = true;
      hideTimer = null;
    }, FADE_MS);
  };

  retry.addEventListener("click", () => retryListener?.());
  close.addEventListener("click", () => {
    hide();
    closeListener?.();
  });
  overlay.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && overlay.dataset.state === "error") close.click();
  });

  return {
    start(spec, text) {
      if (hideTimer !== null) clearTimeout(hideTimer);
      hideTimer = null;
      order = stepsFor(spec);
      estimator = new Estimator();
      polledS = 0;
      lastShownS = 0;
      polledAt = startedAt = now();
      current = "";
      waiting = false;
      overlay.dataset.state = "running";
      title.textContent = "Generating model";
      subtitle.textContent = text;
      running.hidden = false;
      error.hidden = true;
      card.setAttribute("aria-busy", "true");
      announce.textContent = "Generating model";
      renderSteps("", "");
      overlay.hidden = false;
      root.setAttribute("data-generating", "");
      // Next frame, so the entrance transition runs from the hidden state.
      requestAnimationFrame(() => overlay.classList.add("open"));
      tick();
      stopTimer();
      timer = setInterval(tick, TICK_MS);
      // Screen reader and keyboard users land on the card; the generate button just went disabled.
      card.focus({ preventScroll: true });
    },
    update(job) {
      // Older backends send no elapsed time; the local clock since start() stands in.
      polledS = job.elapsed_s ?? (now() - startedAt) / 1000;
      polledAt = now();
      const at = isStep(job.stage) ? order.indexOf(job.stage) : -1;
      const fallback = at < 0 ? undefined : { start: at / order.length, end: (at + 1) / order.length };
      waiting = job.status === "queued";
      if (waiting) {
        tick();
        return;
      }
      estimator.observe(job, polledS, fallback);
      renderSteps(job.stage, job.message);
      if (job.stage !== current) {
        current = job.stage;
        if (at >= 0) announce.textContent = `Step ${at + 1} of ${order.length}: ${STEP_LABELS[job.stage as Step]}`;
      }
      tick();
    },
    finish(focus) {
      percent.textContent = "100";
      fill.style.transform = "scaleX(1)";
      hide();
      focus?.focus({ preventScroll: true });
    },
    fail(message) {
      stopTimer();
      overlay.dataset.state = "error";
      title.textContent = "Generation failed";
      errorText.textContent = message;
      running.hidden = true;
      error.hidden = false;
      card.setAttribute("aria-busy", "false");
      announce.textContent = `Generation failed: ${message}`;
      retry.focus({ preventScroll: true });
    },
    onRetry(cb) {
      retryListener = cb;
    },
    onClose(cb) {
      closeListener = cb;
    },
  };
}
