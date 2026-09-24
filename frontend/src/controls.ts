// Panel wiring: reads the form into a FrameSpecInput, reflects map state back into the inputs and
// switches the panel between configuring a print and showing a finished run.
import type { FrameSpecInput, JobState } from "./api";
import { jobFileUrl } from "./api";
import { formatCm, formatCoords, formatPlate, formatScale, groupDigits, guessFileStem, splitPlace } from "./format";
import { PRESETS, presetFor, type Preset, type PresetName } from "./presets";
import type { SquareParams } from "./square";

export type Stage = "map" | "preview";

export interface Controls {
  read(): FrameSpecInput;
  writeSquare(p: SquareParams): void;
  /** The geocoder display name of the picked place, or null for "Selected area". */
  setPlace(displayName: string | null): void;
  setBusy(busy: boolean): void;
  /** Shows a running job's stage on the generate button. */
  setProgress(job: Pick<JobState, "status" | "stage" | "message" | "progress">): void;
  setStatus(text: string, isError?: boolean): void;
  showDownloads(id: string | null, job?: Pick<JobState, "file_stem">): void;
  /** Switches the panel to the result of a finished job generated from `spec`. */
  showResult(job: JobState, spec: FrameSpecInput): void;
  setStage(stage: Stage): void;
  onGenerate(cb: () => void): void;
  onSquareInput(cb: (p: Partial<SquareParams>) => void): void;
  /** Fires when the user leaves the result to change settings. */
  onAdjust(cb: () => void): void;
  elements: { search: HTMLInputElement; searchResults: HTMLElement };
}

function el<T extends HTMLElement>(root: HTMLElement, id: string): T {
  const node = root.querySelector<T>(`#${id}`);
  if (!node) throw new Error(`missing element #${id}`);
  return node;
}

/** The value of the checked radio in a segmented control. */
function radioValue(group: HTMLElement): string {
  return group.querySelector<HTMLInputElement>("input[type=radio]:checked")?.value ?? "";
}

function setRadio(group: HTMLElement, value: string): void {
  for (const input of group.querySelectorAll<HTMLInputElement>("input[type=radio]")) {
    input.checked = input.value === value;
  }
}

/** Backend pipeline stages in run order (backend skylineframe/pipeline.py); terrain and trees are optional. */
const STAGES = ["fetch", "terrain", "prepare", "trees", "mesh", "export"];

/** Button label and bar fill for a running job; fraction null means "no idea yet" (indeterminate). */
export function progressOf(
  job: Pick<JobState, "status" | "stage" | "message" | "progress">,
): { label: string; fraction: number | null } {
  if (job.status === "queued" || !job.stage) return { label: job.message || "Waiting for the server", fraction: null };
  // The backend's weighted fraction wins; the stage index is the fallback for an older backend.
  if (typeof job.progress === "number") return { label: job.message || job.stage, fraction: job.progress };
  const index = STAGES.indexOf(job.stage);
  const fraction = index < 0 ? null : (index + 1) / (STAGES.length + 1);
  return { label: job.message || job.stage, fraction };
}

export function setupControls(root: HTMLElement): Controls {
  const sidebar = el<HTMLElement>(root, "sidebar");
  const preset = el<HTMLFieldSetElement>(root, "preset");
  const size = el<HTMLInputElement>(root, "size");
  const sizeValue = el<HTMLSpanElement>(root, "size-value");
  const scaleOut = el<HTMLSpanElement>(root, "scale-out");
  const side = el<HTMLInputElement>(root, "side");
  const sideOut = el<HTMLOutputElement>(root, "side-out");
  const rotation = el<HTMLInputElement>(root, "rotation");
  const rotationOut = el<HTMLOutputElement>(root, "rotation-out");
  const thickness = el<HTMLInputElement>(root, "thickness");
  const zfactor = el<HTMLInputElement>(root, "zfactor");
  const moreSummary = el<HTMLSpanElement>(root, "more-summary");
  const mode = el<HTMLFieldSetElement>(root, "mode");
  const optimize = el<HTMLInputElement>(root, "optimize");
  const lod2 = el<HTMLInputElement>(root, "lod2");
  const terrain = el<HTMLInputElement>(root, "terrain");
  const terrainz = el<HTMLInputElement>(root, "terrainz");
  const terrainzOut = el<HTMLOutputElement>(root, "terrainz-out");
  const trees = el<HTMLInputElement>(root, "trees");
  const multicolor = el<HTMLInputElement>(root, "multicolor");
  const zfactorOut = el<HTMLOutputElement>(root, "zfactor-out");
  const generate = el<HTMLButtonElement>(root, "generate");
  const generateLabel = el<HTMLSpanElement>(root, "generate-label");
  const progress = el<HTMLDivElement>(root, "progress");
  const progressBar = el<HTMLSpanElement>(root, "progress-bar");
  const filePreview = el<HTMLParagraphElement>(root, "file-preview");
  const status = el<HTMLParagraphElement>(root, "status");
  const placeName = el<HTMLHeadingElement>(root, "place-name");
  const placeRegion = el<HTMLParagraphElement>(root, "place-region");
  const placeCoords = el<HTMLParagraphElement>(root, "place-coords");
  const resultMeta = el<HTMLParagraphElement>(root, "result-meta");
  const config = el<HTMLElement>(root, "config");
  const result = el<HTMLElement>(root, "result");
  const statBuildings = el<HTMLElement>(root, "stat-buildings");
  const statLod2 = el<HTMLElement>(root, "stat-lod2");
  const statRelief = el<HTMLElement>(root, "stat-relief");
  const statReliefWrap = el<HTMLElement>(root, "stat-relief-wrap");
  const downloads = el<HTMLDivElement>(root, "downloads");
  const dlStl = el<HTMLAnchorElement>(root, "dl-stl");
  const dl3mf = el<HTMLAnchorElement>(root, "dl-3mf");
  const dlSources = el<HTMLAnchorElement>(root, "dl-sources");
  const dlStlName = el<HTMLSpanElement>(root, "dl-stl-name");
  const dl3mfName = el<HTMLSpanElement>(root, "dl-3mf-name");
  const dlSourcesName = el<HTMLSpanElement>(root, "dl-sources-name");
  const adjust = el<HTMLButtonElement>(root, "adjust");
  const stageToggle = el<HTMLElement>(root, "stage-toggle");
  const viewer = el<HTMLElement>(root, "viewer");
  const sheetToggle = el<HTMLButtonElement>(root, "sheet-toggle");

  // The centre lives on the map, not in a form field; writeSquare keeps this copy in sync.
  let square: SquareParams = { lat: 50.1106, lon: 8.6821, sideM: Number(side.value), rotationDeg: Number(rotation.value) };
  // Set by onSquareInput; the preset needs to reach the map too, so every listener is
  // registered here and goes through this one callback.
  let squareListener: ((p: Partial<SquareParams>) => void) | null = null;
  let place: string | null = null;
  let adjustListener: (() => void) | null = null;

  const plateMm = () => Math.round(Number(size.value) * 10);

  const syncOutputs = () => {
    sideOut.value = `${side.value} m`;
    side.setAttribute("aria-valuetext", `${side.value} metres`);
    rotationOut.value = `${rotation.value}°`;
    rotation.setAttribute("aria-valuetext", `${rotation.value} degrees`);
    const cm = Number(size.value);
    sizeValue.textContent = cm.toFixed(1);
    size.setAttribute("aria-valuetext", `${formatCm(cm)} centimetres`);
    scaleOut.textContent = `${formatPlate(plateMm())} · ${formatScale(Number(side.value), plateMm())}`;
    terrainzOut.value = `${Number(terrainz.value).toFixed(1)}×`;
    zfactorOut.value = `${Number(zfactor.value).toFixed(1)}×`;
    moreSummary.textContent = `${thickness.value} mm`;
    filePreview.textContent = `${guessFileStem(place && splitPlace(place).name, Number(side.value), plateMm())}.3mf`;
  };
  const syncCoords = () => {
    placeCoords.textContent = formatCoords(square.lat, square.lon);
  };
  syncOutputs();
  syncCoords();
  setRadio(preset, presetFor(Number(side.value), plateMm()));

  side.addEventListener("input", () => {
    syncOutputs();
    setRadio(preset, "custom");
    squareListener?.({ sideM: Number(side.value) });
  });
  rotation.addEventListener("input", () => {
    syncOutputs();
    squareListener?.({ rotationDeg: Number(rotation.value) });
  });
  // Editing a preset field by hand means the scale is no longer one of the presets (spec §9).
  size.addEventListener("input", () => {
    syncOutputs();
    setRadio(preset, "custom");
  });
  for (const input of [thickness, zfactor, terrainz]) input.addEventListener("input", syncOutputs);
  // The factor only means something with terrain on; greying it out says so (backend spec 4b §2).
  const syncTerrain = () => {
    terrainz.disabled = !terrain.checked;
  };
  syncTerrain();
  terrain.addEventListener("change", syncTerrain);
  preset.addEventListener("change", () => {
    const chosen = PRESETS[radioValue(preset) as PresetName] as Preset | undefined;
    if (!chosen) return; // "Custom" keeps whatever the fields say
    side.value = String(chosen.sideM);
    size.value = String(chosen.plateMm / 10);
    syncOutputs();
    squareListener?.({ sideM: chosen.sideM });
  });

  const setView = (view: "config" | "result") => {
    root.dataset.view = view;
    config.hidden = view !== "config";
    result.hidden = view !== "result";
  };

  const setStage = (stage: Stage) => {
    root.dataset.stage = stage;
    viewer.hidden = stage !== "preview";
    for (const button of stageToggle.querySelectorAll<HTMLButtonElement>("button[data-stage]")) {
      button.setAttribute("aria-pressed", String(button.dataset.stage === stage));
    }
  };
  setView("config");
  setStage("map");
  stageToggle.addEventListener("click", (e) => {
    const button = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-stage]");
    if (button) setStage(button.dataset.stage as Stage);
  });

  adjust.addEventListener("click", () => {
    setView("config");
    setStage("map");
    placeName.textContent = place ? splitPlace(place).name : "Selected area";
    adjustListener?.();
  });

  // Phone layout only: the sheet can fold down to its header and footer to free the map.
  sheetToggle.addEventListener("click", () => {
    const expanded = sheetToggle.getAttribute("aria-expanded") !== "true";
    sheetToggle.setAttribute("aria-expanded", String(expanded));
    sidebar.classList.toggle("collapsed", !expanded);
  });

  const read = (): FrameSpecInput => ({
    center_lat: square.lat,
    center_lon: square.lon,
    side_m: Number(side.value),
    rotation_deg: Number(rotation.value),
    plate_size_mm: plateMm(),
    plate_thickness_mm: Number(thickness.value),
    mode: radioValue(mode) as "simple" | "full",
    z_exaggeration: Number(zfactor.value),
    lod2: lod2.checked,
    terrain: terrain.checked,
    terrain_exaggeration: Number(terrainz.value),
    trees: trees.checked,
    print_optimized: optimize.checked,
    // Only sent when on: the backend field is new and the API rejects unknown keys
    // (extra="forbid"), so an older backend keeps working as long as the switch stays off.

    multicolor: multicolor.checked,
  });

  const showDownloads = (id: string | null, job?: Pick<JobState, "file_stem">) => {
    downloads.hidden = id === null;
    if (!id) return;
    dlStl.href = jobFileUrl(id, "model.stl");
    dl3mf.href = jobFileUrl(id, "model.3mf");
    dlSources.href = jobFileUrl(id, "SOURCES.txt");
    const stem = job?.file_stem;
    // Without a stem the empty `download` attribute lets the server's own file name through.
    const names: Array<[HTMLAnchorElement, HTMLElement, string, string]> = [
      [dl3mf, dl3mfName, stem ? `${stem}.3mf` : "", "model.3mf"],
      [dlStl, dlStlName, stem ? `${stem}.stl` : "", "model.stl"],
      [dlSources, dlSourcesName, stem ? `${stem}_SOURCES.txt` : "", "SOURCES.txt"],
    ];
    for (const [link, label, name, fallback] of names) {
      link.setAttribute("download", name);
      label.textContent = name || fallback;
    }
  };

  return {
    read,
    writeSquare(p) {
      square = { ...p };
      side.value = String(p.sideM);
      rotation.value = String(p.rotationDeg);
      syncOutputs();
      syncCoords();
      setRadio(preset, presetFor(Number(side.value), plateMm()));
    },
    setPlace(displayName) {
      place = displayName;
      const parts = displayName ? splitPlace(displayName) : { name: "Selected area", region: "" };
      placeName.textContent = parts.name;
      placeRegion.textContent = parts.region;
      placeRegion.hidden = parts.region === "";
      syncOutputs();
    },
    setBusy(busy) {
      generate.disabled = busy;
      generate.setAttribute("aria-busy", String(busy));
      sidebar.toggleAttribute("data-busy", busy);
      progress.hidden = !busy;
      if (busy) {
        generateLabel.textContent = "Starting";
        progress.classList.add("indeterminate");
      } else {
        generateLabel.textContent = "Generate model";
      }
    },
    setProgress(job) {
      const { fraction } = progressOf(job);
      // The overlay carries the step in words; the button only says that it is busy and how far
      // along, short enough never to be cut off (the stage message was, at 40 characters).
      generateLabel.textContent = fraction === null ? "Generating…" : `Generating… ${Math.round(fraction * 100)} %`;
      // Not mirrored into the status live region: the generation overlay announces each step.
      status.textContent = "";
      status.classList.remove("error");
      progress.classList.toggle("indeterminate", fraction === null);
      progressBar.style.transform = `scaleX(${fraction ?? 0})`;
    },
    setStatus(text, isError = false) {
      status.textContent = text;
      status.classList.toggle("error", isError);
    },
    showDownloads,
    showResult(job, spec) {
      const stats = job.stats ?? {};
      const count = (key: string) => Number(stats[key] ?? 0);
      if (job.name) placeName.textContent = job.name;
      const cm = formatCm(spec.plate_size_mm / 10);
      resultMeta.textContent = `${cm} × ${cm} cm · ${spec.side_m} m${spec.terrain ? " · terrain on" : ""}`;
      statBuildings.textContent = groupDigits(count("buildings"));
      statLod2.textContent = groupDigits(count("lod2_buildings"));
      const relief = stats.terrain_relief_mm;
      statReliefWrap.hidden = relief === undefined || relief === "";
      statRelief.textContent = Number(relief ?? 0).toFixed(1);
      showDownloads(job.id, job);
      setView("result");
    },
    setStage,
    onGenerate(cb) {
      generate.addEventListener("click", cb);
    },
    onSquareInput(cb) {
      squareListener = cb;
    },
    onAdjust(cb) {
      adjustListener = cb;
    },
    elements: { search: el<HTMLInputElement>(root, "search"), searchResults: el<HTMLElement>(root, "search-results") },
  };
}

/** The status line of a finished run (spec §8), e.g. "Done: 851 buildings, 164 blocks, 300 roofs,
 * 612 of which from LoD2 Hessen". Exported so it can be tested without a DOM. */
export function summarize(stats: Record<string, number | string>): string {
  const count = (key: string) => Number(stats[key] ?? 0);
  let text = `Done: ${count("buildings")} buildings, ${count("blocks")} blocks, ${count("roofs")} roofs`;
  const source = typeof stats.lod2_source === "string" ? stats.lod2_source : "";
  const fromLod2 = count("lod2_buildings");
  if (source && fromLod2 > 0) {
    text += `, ${fromLod2} of which from LoD2 ${source.charAt(0).toUpperCase()}${source.slice(1)}`;
  }
  // Only the printed ones: trees=0 is also what a run with the switch off reports. trees_note is
  // German backend text as well, and means the tree cover could not be loaded.
  const printedTrees = count("trees");
  if (printedTrees > 0) {
    text += `, ${printedTrees} trees${stats.trees_note ? " (OpenStreetMap only)" : ""}`;
  }
  // terrain_note is German backend text for the CLI; the UI only needs to know it is there.
  if (stats.terrain_source) {
    text += `, terrain ${count("terrain_relief_mm").toFixed(1)} mm`;
  } else if (stats.terrain_note) {
    text += ", terrain unavailable (flat plate)";
  }
  return text;
}
