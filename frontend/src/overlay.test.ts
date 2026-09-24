// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JobState } from "./api";
import { setupOverlay } from "./overlay";
import PAGE from "../index.html?raw";

const BODY = PAGE.indexOf("<body>") + "<body>".length;
const APP = PAGE.slice(BODY, PAGE.indexOf("<script", BODY)).trim();

const $ = (id: string) => document.getElementById(id)!;
const job = (over: Partial<JobState>): JobState => ({ id: "j", status: "running", stage: "", message: "", stats: {}, ...over });
const labels = () => [...$("gen-steps").querySelectorAll("li")].map((li) => [li.dataset.state, li.querySelector(".gen-step-label")!.textContent]);

describe("generation overlay", () => {
  let clock = 0;
  beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML = APP;
    clock = 0;
  });
  afterEach(() => vi.useRealTimers());

  const open = (spec = { terrain: false, trees: true }) => {
    const overlay = setupOverlay($("app"), () => clock);
    overlay.start(spec, "Eppstein · 1500 m on 10 cm");
    return overlay;
  };

  it("opens on the card with the steps this run takes", () => {
    open();
    expect($("gen-overlay").hidden).toBe(false);
    expect($("gen-title").textContent).toBe("Generating model");
    expect($("gen-subtitle").textContent).toBe("Eppstein · 1500 m on 10 cm");
    expect(document.activeElement).toBe($("gen-card"));
    expect(labels().map(([, label]) => label)).toEqual([
      "Finding the place name",
      "Downloading map data",
      "Cleaning up the footprints",
      "Placing trees",
      "Building the 3D solids",
      "Writing the print files",
    ]);
    expect($("gen-eta").textContent).toBe("Estimating…");
  });

  it("marks done, current and pending steps and shows the backend's detail", () => {
    const overlay = open();
    overlay.update(job({ stage: "mesh", message: "Building solids for 2148 buildings in 143 blocks", progress: 0.6, progress_next: 0.8, elapsed_s: 12 }));
    expect(labels().map(([state]) => state)).toEqual(["done", "done", "done", "done", "current", "todo"]);
    const current = $("gen-steps").querySelector("[aria-current=step]")!;
    expect(current.querySelector(".gen-step-detail")!.textContent).toBe("Building solids for 2148 buildings in 143 blocks");
    expect($("gen-count").textContent).toBe("Step 5 of 6");
    expect($("gen-announce").textContent).toBe("Step 5 of 6: Building the 3D solids");
    expect($("gen-percent").textContent).toBe("60");
    expect($("gen-bar").getAttribute("aria-valuenow")).toBe("60");
    expect($("gen-elapsed").textContent).toBe("0:12");
    // 60 % in 12 s: about 8 s to go.
    expect($("gen-eta").textContent).toBe("About 10 s left");
  });

  it("keeps counting between polls", () => {
    const overlay = open();
    overlay.update(job({ stage: "prepare", progress: 0.2, progress_next: 0.5, elapsed_s: 4 }));
    clock += 3000;
    vi.advanceTimersByTime(3000);
    expect($("gen-elapsed").textContent).toBe("0:07");
    expect(Number($("gen-percent").textContent)).toBeGreaterThan(20);
  });

  it("says it is waiting while the job is queued", () => {
    const overlay = open();
    overlay.update(job({ status: "queued", elapsed_s: 0 }));
    vi.advanceTimersByTime(500);
    expect($("gen-eta").textContent).toBe("Waiting for the server");
  });

  it("closes when the run is done and hands the focus on", () => {
    const overlay = open();
    const target = $("dl-3mf");
    target.hidden = false;
    overlay.finish(target);
    expect($("app").hasAttribute("data-generating")).toBe(false);
    vi.advanceTimersByTime(300);
    expect($("gen-overlay").hidden).toBe(true);
  });

  it("shows a failure with a way to try again or go back to the settings", () => {
    const overlay = open();
    const retry = vi.fn();
    const closed = vi.fn();
    overlay.onRetry(retry);
    overlay.onClose(closed);
    overlay.fail("No buildings found in the selected area.");
    expect($("gen-title").textContent).toBe("Generation failed");
    expect($("gen-error").hidden).toBe(false);
    expect($("gen-running").hidden).toBe(true);
    expect($("gen-error-text").textContent).toBe("No buildings found in the selected area.");
    expect(document.activeElement).toBe($("gen-retry"));
    ($("gen-retry") as HTMLButtonElement).click();
    expect(retry).toHaveBeenCalledOnce();
    $("gen-overlay").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(closed).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(300);
    expect($("gen-overlay").hidden).toBe(true);
  });

  it("starts clean after a failure", () => {
    const overlay = open();
    overlay.fail("Overpass down");
    overlay.start({ terrain: true, trees: false }, "Frankfurt");
    expect($("gen-title").textContent).toBe("Generating model");
    expect($("gen-error").hidden).toBe(true);
    expect(labels().map(([, label]) => label)).toContain("Loading the terrain");
    expect(labels().map(([, label]) => label)).not.toContain("Placing trees");
  });
});
