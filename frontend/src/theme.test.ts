// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { THEME_STORAGE_KEY, nextPreference, preference, resolve, setPreference } from "./theme";

describe("theme", () => {
  beforeEach(() => {
    // An in-memory store: Node 25 ships a global localStorage without methods, which shadows
    // happy-dom's (and theme.ts must survive a broken store anyway).
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
      clear: () => store.clear(),
    });
    delete document.documentElement.dataset.theme;
  });

  it("follows the system until the viewer picks", () => {
    expect(preference()).toBe("system");
    expect(resolve("system", true)).toBe("dark");
    expect(resolve("system", false)).toBe("light");
    expect(resolve("light", true)).toBe("light");
  });

  it("cycles system, light, dark", () => {
    expect(nextPreference("system")).toBe("light");
    expect(nextPreference("light")).toBe("dark");
    expect(nextPreference("dark")).toBe("system");
  });

  it("remembers the pick and sets data-theme on <html>", () => {
    setPreference("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.dataset.themePref).toBe("dark");
    setPreference("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
