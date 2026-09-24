import { afterEach, describe, expect, it, vi } from "vitest";
import { createJob, geocode, jobFileUrl, waitForJob } from "./api";

function mockFetch(responses: Array<{ status?: number; body: unknown }>) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    const next = responses.shift()!;
    return new Response(JSON.stringify(next.body), {
      status: next.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return calls;
}

afterEach(() => vi.restoreAllMocks());

describe("createJob", () => {
  it("posts the spec and returns the id", async () => {
    const calls = mockFetch([{ status: 202, body: { id: "abc" } }]);
    const res = await createJob({ center_lat: 50, center_lon: 8, side_m: 1000, rotation_deg: 0, plate_size_mm: 100, plate_thickness_mm: 3, mode: "simple", z_exaggeration: 1.5, lod2: true, terrain: false, terrain_exaggeration: 1, trees: true, print_optimized: true });
    expect(res.id).toBe("abc");
    expect(calls[0].url).toBe("/api/jobs");
    expect(calls[0].init?.method).toBe("POST");
  });
  it("passes the abort signal to fetch", async () => {
    const calls = mockFetch([{ status: 202, body: { id: "abc" } }]);
    const controller = new AbortController();
    await createJob({ center_lat: 50, center_lon: 8, side_m: 1000, rotation_deg: 0, plate_size_mm: 100, plate_thickness_mm: 3, mode: "simple", z_exaggeration: 1.5, lod2: true, terrain: false, terrain_exaggeration: 1, trees: true, print_optimized: true }, controller.signal);
    expect(calls[0].init?.signal).toBe(controller.signal);
  });
  it("throws with the server detail on error", async () => {
    mockFetch([{ status: 422, body: { detail: "bad spec" } }]);
    await expect(createJob({} as never)).rejects.toThrow(/bad spec|422/);
  });
});

describe("waitForJob", () => {
  it("polls until done and reports updates", async () => {
    mockFetch([
      { body: { id: "abc", status: "running", stage: "fetch", message: "", stats: {} } },
      { body: { id: "abc", status: "done", stage: "export", message: "Ready", stats: { buildings: 3 } } },
    ]);
    const seen: string[] = [];
    const final = await waitForJob("abc", (j) => seen.push(j.status), 1);
    expect(final.status).toBe("done");
    expect(seen).toEqual(["running", "done"]);
  });
});

describe("waitForJob resilience", () => {
  it("tolerates transient poll failures and still resolves", async () => {
    mockFetch([
      { status: 500, body: { detail: "boom" } },
      { status: 500, body: { detail: "boom" } },
      { body: { id: "abc", status: "done", stage: "export", message: "Ready", stats: { buildings: 42 } } },
    ]);
    const job = await waitForJob("abc", undefined, 1);
    expect(job.status).toBe("done");
    expect(job.stats.buildings).toBe(42);
  });

  it("rethrows once the failures exceed the tolerance", async () => {
    mockFetch(Array.from({ length: 4 }, () => ({ status: 500, body: { detail: "boom" } })));
    await expect(waitForJob("abc", undefined, 1)).rejects.toThrow("boom");
  });

  it("rejects promptly with the abort reason and passes the signal to fetch", async () => {
    const seen: Array<RequestInit | undefined> = [];
    globalThis.fetch = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      seen.push(init);
      return new Response(JSON.stringify({ id: "abc", status: "running", stage: "fetch", message: "", stats: {} }), {
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;

    const controller = new AbortController();
    const reason = new Error("Timed out");
    // Abort while the poll loop is between requests: the sleep must reject instead of waiting a minute.
    const promise = waitForJob("abc", () => controller.abort(reason), 60_000, { signal: controller.signal });
    await expect(promise).rejects.toThrow("Timed out");
    expect(seen[0]?.signal).toBe(controller.signal);
  });
});

describe("helpers", () => {
  it("builds file urls", () => {
    expect(jobFileUrl("abc", "model.stl")).toBe("/api/jobs/abc/model.stl");
  });
  it("geocode encodes the query", async () => {
    const calls = mockFetch([{ body: [{ name: "Frankfurt", lat: 50.1, lon: 8.6 }] }]);
    const hits = await geocode("Frankfurt am Main");
    expect(hits[0].lat).toBe(50.1);
    expect(calls[0].url).toBe("/api/geocode?q=Frankfurt+am+Main");
  });
});
