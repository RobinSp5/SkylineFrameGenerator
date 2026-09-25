// Entry point: wires the map, the panel, the job API and the 3D preview together.
import "./style.css";
import { createJob, waitForJob, jobFileUrl } from "./api";
import { setupControls, summarize } from "./controls";
import { formatCm, splitPlace } from "./format";
import { createMap } from "./map";
import { setupOverlay } from "./overlay";
import { initTheme, nextPreference, preference, setPreference, type ThemePreference } from "./theme";
import { setupSearch } from "./search";
import { setupTooltips } from "./tooltips";
import { createViewer } from "./viewer";

/** A generation run is abandoned after this long; the backend job itself keeps whatever it produced. */
const JOB_TIMEOUT_MS = 600_000;

const root = document.getElementById("app")!;
const controls = setupControls(root);
setupTooltips(root);
// Before the map: it picks its basemap style from the resolved theme.
initTheme();
const THEME_LABELS: Record<ThemePreference, string> = {
  system: "Theme: follow system",
  light: "Theme: light",
  dark: "Theme: dark",
};
const themeToggle = document.getElementById("theme-toggle")!;
const labelTheme = () => {
  const label = `${THEME_LABELS[preference()]}. Click to change.`;
  themeToggle.setAttribute("aria-label", label);
  themeToggle.title = THEME_LABELS[preference()];
};
labelTheme();
themeToggle.addEventListener("click", () => {
  setPreference(nextPreference(preference()));
  labelTheme();
});

const map = createMap(document.getElementById("map")!, { lat: 50.1106, lon: 8.6821, sideM: 1500, rotationDeg: 0 });
let viewer: ReturnType<typeof createViewer> | null = null;
try {
  viewer = createViewer(document.getElementById("viewer")!);
} catch (err) {
  // No WebGL: the preview pane stays empty, but generating and downloading still work.
  console.error("3D preview unavailable (no WebGL?)", err);
}

// The panel floats over the map; padding keeps the square centred in the part that stays visible.
const sidebar = document.getElementById("sidebar")!;
const syncPadding = () => {
  const phone = window.matchMedia("(max-width: 767px)").matches;
  const rect = sidebar.getBoundingClientRect();
  const covered = phone ? { bottom: Math.round(window.innerHeight - rect.top) } : { right: Math.round(window.innerWidth - rect.left) };
  // On a phone the search bar and, below it, the map attribution span the top edge too (style.css).
  map.setPadding(phone ? { ...covered, top: 100 } : covered);
  viewer?.setInsets(covered);
};
new ResizeObserver(syncPadding).observe(sidebar);
window.addEventListener("resize", syncPadding);
syncPadding();

controls.writeSquare(map.getParams());
map.onChange((p) => controls.writeSquare(p));
controls.onSquareInput((partial) => map.setParams({ ...map.getParams(), ...partial }));
setupSearch(controls.elements.search, controls.elements.searchResults, (hit) => {
  controls.setPlace(hit.name);
  // The panel header carries the region; the search field only needs the place itself.
  controls.elements.search.value = splitPlace(hit.name).name;
  map.flyTo(hit.lat, hit.lon);
});
controls.onAdjust(() => controls.setStatus(""));

const overlay = setupOverlay(root);
const generateButton = document.getElementById("generate") as HTMLButtonElement;
let generating = false;

async function generate(): Promise<void> {
  if (generating) return;
  generating = true;
  const spec = controls.read();
  controls.setBusy(true);
  controls.showDownloads(null);
  controls.setStatus("");
  const place = document.getElementById("place-name")?.textContent || "Selected area";
  overlay.start(spec, `${place} · ${spec.side_m} m on ${formatCm(spec.plate_size_mm / 10)} cm`);
  // Drop the previous model up front: a failing run must not leave a stale preview beside the error.
  viewer?.clear();
  const abort = new AbortController();
  const timeout = setTimeout(() => abort.abort(new Error("Timed out")), JOB_TIMEOUT_MS);
  const fail = (message: string) => {
    controls.setStatus(message, true);
    overlay.fail(message);
  };
  try {
    const { id } = await createJob(spec, abort.signal);
    const job = await waitForJob(
      id,
      (j) => {
        controls.setProgress(j);
        overlay.update(j);
      },
      1000,
      { signal: abort.signal },
    );
    if (job.status === "error") {
      fail(job.message || "Generation failed");
      return;
    }
    const summary = summarize(job.stats ?? {});
    controls.setStatus(summary);
    controls.showResult(job, spec);
    overlay.finish(document.getElementById("dl-3mf"));
    if (!viewer) return;
    try {
      await viewer.load(jobFileUrl(id, "preview.glb"), { multicolor: spec.multicolor === true });
      controls.setStage("preview");
    } catch (err) {
      console.error(err);
      controls.setStatus(`${summary} (preview unavailable)`);
    }
  } catch (err) {
    fail(err instanceof Error ? err.message : String(err));
  } finally {
    clearTimeout(timeout);
    controls.setBusy(false);
    generating = false;
  }
}

controls.onGenerate(generate);
overlay.onRetry(generate);
// Closing a failed run's card leaves the settings as they were, ready to change and run again.
overlay.onClose(() => generateButton.focus());
