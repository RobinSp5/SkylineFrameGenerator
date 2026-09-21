// @vitest-environment happy-dom
// Set per-file so vite.config.ts can stay on the fast `environment: "node"` default.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GeocodeHit } from "./api";
import { setupSearch } from "./search";

vi.mock("./api", () => ({ geocode: vi.fn() }));
const { geocode } = await import("./api");
const geocodeMock = vi.mocked(geocode);

/** A geocode call whose promise we resolve by hand, to model a slow request. */
function deferred() {
  let resolve!: (hits: GeocodeHit[]) => void;
  const promise = new Promise<GeocodeHit[]>((r) => (resolve = r));
  return { promise, resolve };
}

const HIT: GeocodeHit = { name: "Berlin, Deutschland", lat: 52.52, lon: 13.405 };

describe("setupSearch", () => {
  let input: HTMLInputElement;
  let list: HTMLElement;
  let picked: GeocodeHit[];

  beforeEach(() => {
    vi.useFakeTimers();
    geocodeMock.mockReset();
    document.body.replaceChildren();
    input = document.createElement("input");
    list = document.createElement("div");
    document.body.append(input, list);
    picked = [];
    setupSearch(input, list, (hit) => picked.push(hit));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const type = (value: string) => {
    input.value = value;
    input.dispatchEvent(new Event("input"));
  };

  it("ignores a stale response that resolves after the input was cleared", async () => {
    const slow = deferred();
    geocodeMock.mockReturnValueOnce(slow.promise);

    type("ber");
    await vi.advanceTimersByTimeAsync(300);
    expect(geocodeMock).toHaveBeenCalledWith("ber");

    // User clears the field while the request is still in flight.
    type("");
    expect(list.children).toHaveLength(0);

    slow.resolve([HIT]);
    await vi.advanceTimersByTimeAsync(300);

    expect(list.children).toHaveLength(0);
    expect(geocodeMock).toHaveBeenCalledTimes(1);
  });

  it("cancels the pending debounce when a hit is picked", async () => {
    geocodeMock.mockResolvedValueOnce([HIT]);

    type("berli");
    await vi.advanceTimersByTimeAsync(300);
    expect(list.children).toHaveLength(1);

    // Keep typing, then click the still-visible hit before the new debounce fires.
    type("berlin");
    const button = list.children[0] as HTMLButtonElement;
    button.click();

    await vi.advanceTimersByTimeAsync(300);

    expect(geocodeMock).toHaveBeenCalledTimes(1);
    expect(list.children).toHaveLength(0);
    expect(picked).toEqual([HIT]);
    expect(input.value).toBe(HIT.name);
  });

  it("does not query for fewer than two characters", async () => {
    type("b");
    await vi.advanceTimersByTimeAsync(300);
    expect(geocodeMock).not.toHaveBeenCalled();
  });

  it("renders hit names as text, not markup", async () => {
    geocodeMock.mockResolvedValueOnce([{ name: "<img src=x onerror=alert(1)>", lat: 1, lon: 2 }]);

    type("xss");
    await vi.advanceTimersByTimeAsync(300);

    const button = list.children[0] as HTMLButtonElement;
    expect(button.tagName).toBe("BUTTON");
    expect(button.querySelector("img")).toBeNull();
    expect(button.textContent).toBe("<img src=x onerror=alert(1)>");
  });
});
