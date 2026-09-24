// Typed client for the backend API.

export interface FrameSpecInput {
  center_lat: number;
  center_lon: number;
  side_m: number;
  rotation_deg: number;
  plate_size_mm: number;
  plate_thickness_mm: number;
  mode: "simple" | "full";
  z_exaggeration: number;
  lod2: boolean;
  terrain: boolean;
  terrain_exaggeration: number;
}

export type JobStatus = "queued" | "running" | "done" | "error";

export interface JobState {
  id: string;
  status: JobStatus;
  stage: string;
  message: string;
  // lod2_source is a provider name, everything else is a number (backend spec §8).
  stats: Record<string, number | string>;
}

export interface GeocodeHit {
  name: string;
  lat: number;
  lon: number;
}

// SOURCES.txt names every source the model used; the README says it has to travel with a
// download, so it is offered next to the model files rather than only through the API.
export type JobFile = "model.stl" | "model.3mf" | "preview.glb" | "SOURCES.txt";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function createJob(spec: FrameSpecInput, signal?: AbortSignal): Promise<{ id: string }> {
  return request("/api/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(spec),
    signal,
  });
}

export function getJob(id: string, signal?: AbortSignal): Promise<JobState> {
  return request(`/api/jobs/${encodeURIComponent(id)}`, { signal });
}

export interface WaitForJobOptions {
  /** Cancels the polling; the returned promise rejects with the signal's reason. */
  signal?: AbortSignal;
  /** Consecutive poll failures tolerated before the error is rethrown. */
  maxConsecutiveFailures?: number;
}

/** Resolves after `ms`, or rejects with the abort reason if `signal` fires first. */
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * Polls the job until it is done or failed. A job runs for minutes, so a single network blip must
 * not kill the run: up to `maxConsecutiveFailures` failed polls in a row are retried, and any
 * successful poll resets the counter. The loop is unbounded in time by design — callers bound it
 * with `options.signal`.
 */
export async function waitForJob(
  id: string,
  onUpdate?: (job: JobState) => void,
  intervalMs = 1000,
  options: WaitForJobOptions = {},
): Promise<JobState> {
  const { signal, maxConsecutiveFailures = 3 } = options;
  let failures = 0;
  for (;;) {
    signal?.throwIfAborted();
    try {
      const job = await getJob(id, signal);
      failures = 0;
      onUpdate?.(job);
      if (job.status === "done" || job.status === "error") return job;
    } catch (err) {
      // An abort surfaces here as a fetch rejection; it is a cancellation, never a retryable blip.
      signal?.throwIfAborted();
      if (++failures > maxConsecutiveFailures) throw err;
    }
    await sleep(intervalMs, signal);
  }
}

export function jobFileUrl(id: string, name: JobFile): string {
  return `/api/jobs/${encodeURIComponent(id)}/${name}`;
}

export function geocode(q: string): Promise<GeocodeHit[]> {
  const params = new URLSearchParams({ q });
  return request(`/api/geocode?${params.toString()}`);
}
