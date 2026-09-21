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
}

export type JobStatus = "queued" | "running" | "done" | "error";

export interface JobState {
  id: string;
  status: JobStatus;
  stage: string;
  message: string;
  stats: Record<string, number>;
}

export interface GeocodeHit {
  name: string;
  lat: number;
  lon: number;
}

export type JobFile = "model.stl" | "model.3mf" | "preview.glb";

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

export function createJob(spec: FrameSpecInput): Promise<{ id: string }> {
  return request("/api/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(spec),
  });
}

export function getJob(id: string): Promise<JobState> {
  return request(`/api/jobs/${encodeURIComponent(id)}`);
}

export async function waitForJob(id: string, onUpdate?: (job: JobState) => void, intervalMs = 1000): Promise<JobState> {
  for (;;) {
    const job = await getJob(id);
    onUpdate?.(job);
    if (job.status === "done" || job.status === "error") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

export function jobFileUrl(id: string, name: JobFile): string {
  return `/api/jobs/${encodeURIComponent(id)}/${name}`;
}

export function geocode(q: string): Promise<GeocodeHit[]> {
  const params = new URLSearchParams({ q });
  return request(`/api/geocode?${params.toString()}`);
}
