// Sidebar wiring: reads the form into a FrameSpecInput and reflects map state back into the inputs.
import type { FrameSpecInput } from "./api";
import { jobFileUrl } from "./api";
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

  const syncOutputs = () => {
    sideOut.value = `${side.value} m`;
    rotationOut.value = `${rotation.value}°`;
  };
  syncOutputs();

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
      side.addEventListener("input", () => {
        syncOutputs();
        cb({ sideM: Number(side.value) });
      });
      rotation.addEventListener("input", () => {
        syncOutputs();
        cb({ rotationDeg: Number(rotation.value) });
      });
    },
    elements: { search: el<HTMLInputElement>(root, "search"), searchResults: el<HTMLElement>(root, "search-results") },
  };
}
