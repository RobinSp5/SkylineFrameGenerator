// @vitest-environment happy-dom
// Set per-file so vite.config.ts can stay on the fast `environment: "node"` default.
import { beforeEach, describe, expect, it } from "vitest";
import type { JobState } from "./api";
import { progressOf, setupControls, summarize } from "./controls";
import { PRESETS, presetFor } from "./presets";
import PAGE from "../index.html?raw";

// The real page markup, minus the module script: the tests break when index.html and the wiring drift.
// Searched from <body> on: the theme bootstrap script in <head> comes first in the file.
const BODY = PAGE.indexOf("<body>") + "<body>".length;
const APP = PAGE.slice(BODY, PAGE.indexOf("<script", BODY)).trim();

function field<T extends HTMLElement>(id: string): T {
  return document.getElementById(id) as unknown as T;
}

describe("presetFor", () => {
  it("recognises every preset and falls back to custom", () => {
    expect(presetFor(1500, 100)).toBe("skyline");
    expect(presetFor(800, 100)).toBe("detail");
    expect(presetFor(1500, 200)).toBe("gross");
    expect(presetFor(1234, 100)).toBe("custom");
    expect(presetFor(1500, 123)).toBe("custom");
  });

  it("matches the backend table", () => {
    expect(PRESETS).toEqual({
      skyline: { sideM: 1500, plateMm: 100 },
      detail: { sideM: 800, plateMm: 100 },
      gross: { sideM: 1500, plateMm: 200 },
    });
  });
});

describe("setupControls", () => {
  let root: HTMLElement;
  let updates: Array<Record<string, number>>;

  beforeEach(() => {
    document.body.innerHTML = APP;
    root = document.getElementById("app")!;
    updates = [];
  });

  const fire = (el: HTMLElement, type: string) => el.dispatchEvent(new Event(type, { bubbles: true }));
  const presetValue = () => root.querySelector<HTMLInputElement>("input[name=preset]:checked")?.value;
  /** Checks a preset radio the way a click would: the change event bubbles to the fieldset. */
  const pickPreset = (value: string) => {
    const radio = root.querySelector<HTMLInputElement>(`input[name=preset][value=${value}]`)!;
    radio.checked = true;
    fire(radio, "change");
  };

  it("starts on the preset that matches the initial fields", () => {
    setupControls(root);
    expect(presetValue()).toBe("skyline");
  });

  it("writes side and print size when a preset is picked and tells the map", () => {
    const controls = setupControls(root);
    controls.onSquareInput((p) => updates.push(p as Record<string, number>));

    pickPreset("gross");

    expect(field<HTMLInputElement>("side").value).toBe("1500");
    expect(field<HTMLInputElement>("size").value).toBe("20");
    expect(field<HTMLOutputElement>("side-out").value).toBe("1500 m");
    expect(field("size-value").textContent).toBe("20.0");
    expect(field("scale-out").textContent).toBe("20 × 20 cm plate · 1 : 7\u202F500");
    expect(controls.read().plate_size_mm).toBe(200);
    expect(updates).toEqual([{ sideM: 1500 }]);
  });

  it("switches to Detail including the side length", () => {
    const controls = setupControls(root);
    pickPreset("detail");
    expect(controls.read().side_m).toBe(800);
    expect(controls.read().plate_size_mm).toBe(100);
  });

  it("falls back to Custom when the side slider is moved by hand", () => {
    const controls = setupControls(root);
    controls.onSquareInput((p) => updates.push(p as Record<string, number>));

    const side = field<HTMLInputElement>("side");
    side.value = "1250";
    fire(side, "input");

    expect(presetValue()).toBe("custom");
    expect(updates).toEqual([{ sideM: 1250 }]);
  });

  it("maps the print size in centimetres to plate millimetres and falls back to Custom", () => {
    const controls = setupControls(root);
    const size = field<HTMLInputElement>("size");
    size.value = "14.5";
    fire(size, "input");
    expect(presetValue()).toBe("custom");
    expect(controls.read().plate_size_mm).toBe(145);
    expect(field("size-value").textContent).toBe("14.5");
    expect(field("file-preview").textContent).toBe("Skyline_1500m_14.5cm.3mf");
  });

  it("keeps the fields untouched when Custom is selected", () => {
    const controls = setupControls(root);
    controls.onSquareInput((p) => updates.push(p as Record<string, number>));
    pickPreset("custom");
    expect(controls.read().side_m).toBe(1500);
    expect(updates).toEqual([]);
  });

  it("does not leave the preset when only the rotation changes", () => {
    setupControls(root);
    const rotation = field<HTMLInputElement>("rotation");
    rotation.value = "30";
    fire(rotation, "input");
    expect(presetValue()).toBe("skyline");
  });

  it("re-derives the preset when the map reports a new square", () => {
    const controls = setupControls(root);
    controls.writeSquare({ lat: 50, lon: 8, sideM: 800, rotationDeg: 0 });
    expect(presetValue()).toBe("detail");
    expect(field("place-coords").textContent).toBe("50.0000 N\u00A0\u00A08.0000 E");
  });

  it("reads mode, height factor and plate thickness", () => {
    const controls = setupControls(root);
    root.querySelector<HTMLInputElement>("input[name=mode][value=full]")!.checked = true;
    field<HTMLInputElement>("thickness").value = "4";
    field<HTMLInputElement>("zfactor").value = "2";
    expect(controls.read()).toMatchObject({ mode: "full", plate_thickness_mm: 4, z_exaggeration: 2 });
  });

  it("shows the building height factor as a slider outside the collapsed plate section", () => {
    setupControls(root);
    const zfactor = field<HTMLInputElement>("zfactor");
    expect(zfactor.type).toBe("range");
    expect(zfactor.closest("details")).toBeNull();
    zfactor.value = "2.3";
    zfactor.dispatchEvent(new Event("input"));
    expect(field<HTMLOutputElement>("zfactor-out").value).toBe("2.3×");
  });

  it("optimizes for printing by default and sends the switch", () => {
    const controls = setupControls(root);
    expect(controls.read().print_optimized).toBe(true);
    field<HTMLInputElement>("optimize").checked = false;
    expect(controls.read().print_optimized).toBe(false);
  });

  it("names the place from the picked search result", () => {
    const controls = setupControls(root);
    expect(field("place-name").textContent).toBe("Selected area");
    controls.setPlace("Eppstein, Main-Taunus-Kreis, Hessen, 65817, Deutschland");
    expect(field("place-name").textContent).toBe("Eppstein");
    expect(field("place-region").textContent).toBe("Main-Taunus-Kreis, Hessen");
    expect(field("file-preview").textContent).toBe("Eppstein_1500m_10cm.3mf");
  });
});

describe("setupControls result", () => {
  let root: HTMLElement;
  beforeEach(() => {
    document.body.innerHTML = APP;
    root = document.getElementById("app")!;
  });

  const job = (extra: Partial<JobState> = {}): JobState => ({
    id: "job1",
    status: "done",
    stage: "export",
    message: "Ready",
    stats: { buildings: 2148, lod2_buildings: 702, terrain_source: "copernicus", terrain_relief_mm: 10.414 },
    ...extra,
  });

  it("shows stats, the job name and the file names from file_stem", () => {
    const controls = setupControls(root);
    controls.showResult(job({ name: "Eppstein", file_stem: "Eppstein_1500m_10cm" }), { ...controls.read(), terrain: true });
    expect(root.dataset.view).toBe("result");
    expect(field("config").hidden).toBe(true);
    expect(field("place-name").textContent).toBe("Eppstein");
    expect(field("result-meta").textContent).toBe("10 × 10 cm · 1500 m · terrain on");
    expect(field("stat-buildings").textContent).toBe("2\u202F148");
    expect(field("stat-lod2").textContent).toBe("702");
    expect(field("stat-relief").textContent).toBe("10.4");
    expect(field("stat-relief-wrap").hidden).toBe(false);
    expect(field("dl-3mf").getAttribute("download")).toBe("Eppstein_1500m_10cm.3mf");
    expect(field("dl-stl").getAttribute("download")).toBe("Eppstein_1500m_10cm.stl");
    expect(field("dl-sources").getAttribute("download")).toBe("Eppstein_1500m_10cm_SOURCES.txt");
    expect(field("dl-3mf-name").textContent).toBe("Eppstein_1500m_10cm.3mf");
  });

  it("keeps the server file names without file_stem and hides relief without terrain", () => {
    const controls = setupControls(root);
    controls.showResult(job({ stats: { buildings: 3 } }), controls.read());
    expect(field("dl-3mf").getAttribute("download")).toBe("");
    expect(field("dl-3mf-name").textContent).toBe("model.3mf");
    expect(field("stat-relief-wrap").hidden).toBe(true);
    expect(field("place-name").textContent).toBe("Selected area");
  });

  it("goes back to the settings and the map on Adjust settings", () => {
    const controls = setupControls(root);
    let adjusted = 0;
    controls.onAdjust(() => adjusted++);
    controls.showResult(job(), controls.read());
    controls.setStage("preview");
    expect(field("viewer").hidden).toBe(false);
    field<HTMLButtonElement>("adjust").click();
    expect(root.dataset.view).toBe("config");
    expect(field("viewer").hidden).toBe(true);
    expect(adjusted).toBe(1);
  });

  it("switches the stage with the toggle", () => {
    const controls = setupControls(root);
    controls.showResult(job(), controls.read());
    root.querySelector<HTMLButtonElement>("[data-stage=preview]")!.click();
    expect(field("viewer").hidden).toBe(false);
    expect(root.querySelector("[data-stage=preview]")!.getAttribute("aria-pressed")).toBe("true");
  });

  it("shows a short busy label with the percentage and restores it afterwards", () => {
    const controls = setupControls(root);
    controls.setBusy(true);
    expect(field<HTMLButtonElement>("generate").disabled).toBe(true);
    controls.setProgress({ status: "running", stage: "fetch", message: "Loading OpenStreetMap data" });
    expect(field("generate-label").textContent).toMatch(/^Generating… (\d+ %)?$/);
    expect(field("generate-label").textContent!.length).toBeLessThan(20);
    expect(field("progress").hidden).toBe(false);
    controls.setBusy(false);
    expect(field("generate-label").textContent).toBe("Generate model");
    expect(field("progress").hidden).toBe(true);
  });
});

describe("progressOf", () => {
  it("is indeterminate while queued and fills by stage", () => {
    expect(progressOf({ status: "queued", stage: "", message: "" })).toEqual({ label: "Waiting for the server", fraction: null });
    // fetch, terrain, prepare, trees, mesh, export: six stages, seven steps on the bar.
    expect(progressOf({ status: "running", stage: "fetch", message: "Loading" }).fraction).toBeCloseTo(1 / 7);
    expect(progressOf({ status: "running", stage: "trees", message: "Placing trees" }).fraction).toBeCloseTo(4 / 7);
    expect(progressOf({ status: "running", stage: "export", message: "Writing" }).fraction).toBeCloseTo(6 / 7);
    expect(progressOf({ status: "running", stage: "other", message: "" })).toEqual({ label: "other", fraction: null });
  });
});

describe("summarize", () => {
  it("names the LoD2 source when one was used", () => {
    expect(summarize({ buildings: 851, blocks: 164, roofs: 300, lod2_buildings: 612, lod2_source: "hessen" })).toBe(
      "Done: 851 buildings, 164 blocks, 300 roofs, 612 of which from LoD2 Hessen",
    );
  });

  it("stays on the OSM wording without a source", () => {
    expect(summarize({ buildings: 42, blocks: 3, roofs: 5, lod2_source: "" })).toBe(
      "Done: 42 buildings, 3 blocks, 5 roofs",
    );
  });

  it("says nothing about LoD2 when the source delivered no individual building", () => {
    expect(summarize({ buildings: 42, blocks: 3, roofs: 5, lod2_source: "hessen", lod2_buildings: 0 })).toBe(
      "Done: 42 buildings, 3 blocks, 5 roofs",
    );
  });

  it("survives an empty stats object", () => {
    expect(summarize({})).toBe("Done: 0 buildings, 0 blocks, 0 roofs");
  });
});

describe("summarize terrain", () => {
  it("reports the relief when terrain was used", () => {
    expect(summarize({ buildings: 42, blocks: 3, roofs: 5, terrain_source: "copernicus", terrain_relief_mm: 10.414 })).toBe(
      "Done: 42 buildings, 3 blocks, 5 roofs, terrain 10.4 mm",
    );
  });

  it("says so when terrain was asked for but not available", () => {
    expect(summarize({ buildings: 42, blocks: 3, roofs: 5, terrain_source: "", terrain_note: "Gelände nicht verfügbar" })).toBe(
      "Done: 42 buildings, 3 blocks, 5 roofs, terrain unavailable (flat plate)",
    );
  });
});

describe("summarize trees", () => {
  it("counts the printed trees before the terrain", () => {
    expect(summarize({ buildings: 42, blocks: 3, roofs: 5, trees: 1234, trees_source: "worldcover" })).toBe(
      "Done: 42 buildings, 3 blocks, 5 roofs, 1234 trees",
    );
    expect(
      summarize({ buildings: 42, blocks: 3, roofs: 5, trees: 7, terrain_source: "copernicus", terrain_relief_mm: 10.414 }),
    ).toBe("Done: 42 buildings, 3 blocks, 5 roofs, 7 trees, terrain 10.4 mm");
  });

  it("says nothing about trees when none were printed", () => {
    expect(summarize({ buildings: 42, blocks: 3, roofs: 5, trees: 0, trees_source: "" })).toBe(
      "Done: 42 buildings, 3 blocks, 5 roofs",
    );
  });

  it("says so when the tree cover was not available", () => {
    expect(summarize({ buildings: 42, blocks: 3, roofs: 5, trees: 12, trees_source: "", trees_note: "WorldCover nicht verfügbar" })).toBe(
      "Done: 42 buildings, 3 blocks, 5 roofs, 12 trees (OpenStreetMap only)",
    );
  });
});

describe("setupControls trees", () => {
  beforeEach(() => {
    document.body.innerHTML = APP;
  });

  it("is on by default and sends the checkbox state", () => {
    const controls = setupControls(document.getElementById("app")!);
    expect(controls.read().trees).toBe(true);
    field<HTMLInputElement>("trees").checked = false;
    expect(controls.read().trees).toBe(false);
  });

  it("names its sources", () => {
    setupControls(document.getElementById("app")!);
    const row = field<HTMLInputElement>("trees").closest("label")!;
    expect(row.textContent).toContain("Trees");
    expect(row.textContent).toContain("From ESA WorldCover and OpenStreetMap");
  });
});

describe("setupControls terrain", () => {
  beforeEach(() => {
    document.body.innerHTML = APP;
  });

  it("is on by default with its factor already enabled", () => {
    const controls = setupControls(document.getElementById("app")!);
    expect(controls.read().terrain).toBe(true);
    expect(controls.read().terrain_exaggeration).toBe(1);
    expect(field<HTMLInputElement>("terrainz").disabled).toBe(false);
    field<HTMLInputElement>("terrainz").value = "1.5";
    expect(controls.read()).toMatchObject({ terrain: true, terrain_exaggeration: 1.5 });
  });

  it("disables the factor with the checkbox", () => {
    const controls = setupControls(document.getElementById("app")!);
    const terrain = field<HTMLInputElement>("terrain");
    terrain.checked = false;
    terrain.dispatchEvent(new Event("change"));
    expect(field<HTMLInputElement>("terrainz").disabled).toBe(true);
    expect(controls.read().terrain).toBe(false);
  });
});

describe("setupControls lod2", () => {
  beforeEach(() => {
    document.body.innerHTML = APP;
  });

  it("sends the checkbox state", () => {
    const controls = setupControls(document.getElementById("app")!);
    expect(controls.read().lod2).toBe(true);
    field<HTMLInputElement>("lod2").checked = false;
    expect(controls.read().lod2).toBe(false);
  });

  it("offers SOURCES.txt next to the model files", () => {
    const controls = setupControls(document.getElementById("app")!);
    controls.showDownloads("job1");
    expect(field<HTMLAnchorElement>("dl-sources").getAttribute("href")).toBe("/api/jobs/job1/SOURCES.txt");
  });
});

describe("setupControls overture", () => {
  beforeEach(() => {
    document.body.innerHTML = APP;
  });

  it("is off by default and sends the checkbox state", () => {
    const controls = setupControls(document.getElementById("app")!);
    expect(field<HTMLInputElement>("overture").checked).toBe(false);
    expect(controls.read().overture).toBe(false);
    field<HTMLInputElement>("overture").checked = true;
    expect(controls.read().overture).toBe(true);
  });
});

describe("setupControls multicolor", () => {
  beforeEach(() => {
    document.body.innerHTML = APP;
  });

  it("is on by default", () => {
    const controls = setupControls(document.getElementById("app")!);
    expect(field<HTMLInputElement>("multicolor").checked).toBe(true);
    expect(controls.read().multicolor).toBe(true);
  });

  it("sends multicolor: false when switched off", () => {
    const controls = setupControls(document.getElementById("app")!);
    field<HTMLInputElement>("multicolor").checked = false;
    expect(controls.read().multicolor).toBe(false);
  });

  it("sits with the other switches and says what it does", () => {
    setupControls(document.getElementById("app")!);
    const input = field<HTMLInputElement>("multicolor");
    expect(input.getAttribute("role")).toBe("switch");
    const row = input.closest("label")!;
    expect(row.textContent).toContain("Colour print");
    expect(row.textContent).toContain("Buildings, roads, water and trees on separate filaments");
    expect(row.parentElement).toBe(field<HTMLInputElement>("trees").closest("label")!.parentElement);
  });
});

describe("progressOf with the backend fraction", () => {
  it("uses the weighted progress when the backend sends it", () => {
    expect(progressOf({ status: "running", stage: "mesh", message: "Building", progress: 0.62 }).fraction).toBe(0.62);
    expect(progressOf({ status: "running", stage: "name", message: "Looking up", progress: 0 }).fraction).toBe(0);
  });
});
