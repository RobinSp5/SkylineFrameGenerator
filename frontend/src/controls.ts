// Sidebar wiring: reads the form into a FrameSpecInput and reflects map state back into the inputs.
import type { FrameSpecInput } from "./api";
import { jobFileUrl } from "./api";
import { PRESETS, presetFor, type Preset, type PresetName } from "./presets";
import type { SquareParams } from "./square";

export interface Controls {
  read(): FrameSpecInput;
  writeSquare(p: SquareParams): void;
  setBusy(busy: boolean): void;
  setStatus(text: string, isError?: boolean): void;
  showDownloads(id: string | null): void;
  onGenerate(cb: () => void): void;
  onSquareInput(cb: (p: Partial<SquareParams>) => void): void;
  elements: { search: HTMLInputElement; searchResults: HTMLElement };
}

function el<T extends HTMLElement>(root: HTMLElement, id: string): T {
  const node = root.querySelector<T>(`#${id}`);
  if (!node) throw new Error(`missing element #${id}`);
  return node;
}

export function setupControls(root: HTMLElement): Controls {
  const preset = el<HTMLSelectElement>(root, "preset");
  const side = el<HTMLInputElement>(root, "side");
  const sideOut = el<HTMLOutputElement>(root, "side-out");
  const rotation = el<HTMLInputElement>(root, "rotation");
  const rotationOut = el<HTMLOutputElement>(root, "rotation-out");
  const plate = el<HTMLInputElement>(root, "plate");
  const thickness = el<HTMLInputElement>(root, "thickness");
  const zfactor = el<HTMLInputElement>(root, "zfactor");
  const mode = el<HTMLSelectElement>(root, "mode");
  const generate = el<HTMLButtonElement>(root, "generate");
  const status = el<HTMLParagraphElement>(root, "status");
  const downloads = el<HTMLDivElement>(root, "downloads");
  const dlStl = el<HTMLAnchorElement>(root, "dl-stl");
  const dl3mf = el<HTMLAnchorElement>(root, "dl-3mf");

  // The centre lives on the map, not in a form field; writeSquare keeps this copy in sync.
  let square: SquareParams = { lat: 50.1106, lon: 8.6821, sideM: Number(side.value), rotationDeg: Number(rotation.value) };
  // Set by onSquareInput; the preset needs to reach the map too, so every listener is
  // registered here and goes through this one callback.
  let squareListener: ((p: Partial<SquareParams>) => void) | null = null;

  const syncOutputs = () => {
    sideOut.value = `${side.value} m`;
    rotationOut.value = `${rotation.value}°`;
  };
  syncOutputs();
  preset.value = presetFor(Number(side.value), Number(plate.value));

  side.addEventListener("input", () => {
    syncOutputs();
    preset.value = "custom";
    squareListener?.({ sideM: Number(side.value) });
  });
  rotation.addEventListener("input", () => {
    syncOutputs();
    squareListener?.({ rotationDeg: Number(rotation.value) });
  });
  // Editing a preset field by hand means the scale is no longer one of the presets (spec §9).
  plate.addEventListener("input", () => {
    preset.value = "custom";
  });
  preset.addEventListener("change", () => {
    const chosen = PRESETS[preset.value as PresetName] as Preset | undefined;
    if (!chosen) return; // "Eigene" keeps whatever the fields say
    side.value = String(chosen.sideM);
    plate.value = String(chosen.plateMm);
    syncOutputs();
    squareListener?.({ sideM: chosen.sideM });
  });

  return {
    read: () => ({
      center_lat: square.lat,
      center_lon: square.lon,
      side_m: Number(side.value),
      rotation_deg: Number(rotation.value),
      plate_size_mm: Number(plate.value),
      plate_thickness_mm: Number(thickness.value),
      mode: mode.value as "simple" | "full",
      z_exaggeration: Number(zfactor.value),
    }),
    writeSquare(p) {
      square = { ...p };
      side.value = String(p.sideM);
      rotation.value = String(p.rotationDeg);
      syncOutputs();
      preset.value = presetFor(Number(side.value), Number(plate.value));
    },
    setBusy(busy) {
      generate.disabled = busy;
    },
    setStatus(text, isError = false) {
      status.textContent = text;
      status.classList.toggle("error", isError);
    },
    showDownloads(id) {
      downloads.hidden = id === null;
      if (id) {
        dlStl.href = jobFileUrl(id, "model.stl");
        dl3mf.href = jobFileUrl(id, "model.3mf");
      }
    },
    onGenerate(cb) {
      generate.addEventListener("click", cb);
    },
    onSquareInput(cb) {
      squareListener = cb;
    },
    elements: { search: el<HTMLInputElement>(root, "search"), searchResults: el<HTMLElement>(root, "search-results") },
  };
}
