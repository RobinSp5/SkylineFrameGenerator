// Entry point: wires the map, the sidebar, the job API and the 3D preview together.
import "./style.css";
import { createJob, waitForJob, jobFileUrl } from "./api";
import { setupControls, summarize } from "./controls";
import { createMap } from "./map";
import { setupSearch } from "./search";
import { createViewer } from "./viewer";

/** A generation run is abandoned after this long; the backend job itself keeps whatever it produced. */
const JOB_TIMEOUT_MS = 600_000;

const root = document.getElementById("app")!;
const controls = setupControls(root);
const map = createMap(document.getElementById("map")!, { lat: 50.1106, lon: 8.6821, sideM: 1500, rotationDeg: 0 });
let viewer: ReturnType<typeof createViewer> | null = null;
try {
  viewer = createViewer(document.getElementById("viewer")!);
} catch (err) {
  // No WebGL: the preview pane stays empty, but generating and downloading still work.
  console.error("3D preview unavailable (no WebGL?)", err);
}

controls.writeSquare(map.getParams());
map.onChange((p) => controls.writeSquare(p));
controls.onSquareInput((partial) => map.setParams({ ...map.getParams(), ...partial }));
setupSearch(controls.elements.search, controls.elements.searchResults, (hit) => map.flyTo(hit.lat, hit.lon));

controls.onGenerate(async () => {
  controls.setBusy(true);
  controls.showDownloads(null);
  // Drop the previous model up front: a failing run must not leave a stale preview beside the error.
  viewer?.clear();
  controls.setStatus("Job wird gestartet …");
  const abort = new AbortController();
  const timeout = setTimeout(() => abort.abort(new Error("Zeitüberschreitung")), JOB_TIMEOUT_MS);
  try {
    const { id } = await createJob(controls.read(), abort.signal);
    const job = await waitForJob(id, (j) => controls.setStatus(`${j.stage || j.status}: ${j.message}`), 1000, {
      signal: abort.signal,
    });
    if (job.status === "error") {
      controls.setStatus(job.message || "Generierung fehlgeschlagen", true);
      return;
    }
    const summary = summarize(job.stats ?? {});
    controls.setStatus(summary);
    controls.showDownloads(id);
    try {
      await viewer?.load(jobFileUrl(id, "preview.glb"));
    } catch (err) {
      console.error(err);
      controls.setStatus(`${summary} (Vorschau nicht verfügbar)`);
    }
  } catch (err) {
    controls.setStatus(err instanceof Error ? err.message : String(err), true);
  } finally {
    clearTimeout(timeout);
    controls.setBusy(false);
  }
});
