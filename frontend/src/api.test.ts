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
    const res = await createJob({ center_lat: 50, center_lon: 8, side_m: 1000, rotation_deg: 0, plate_size_mm: 100, plate_thickness_mm: 3, mode: "simple", z_exaggeration: 1.5 });
    expect(res.id).toBe("abc");
    expect(calls[0].url).toBe("/api/jobs");
    expect(calls[0].init?.method).toBe("POST");
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
