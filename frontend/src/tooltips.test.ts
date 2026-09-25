// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { setupTooltips } from "./tooltips";

function info(id: string): HTMLElement {
  const btn = document.createElement("button");
  btn.className = "info";
  btn.id = id;
  document.body.appendChild(btn);
  return btn;
}

describe("tooltips", () => {
  it("opens a bubble on click and closes it again on a second click", () => {
    const a = info("a");
    setupTooltips(document.body);
    a.click();
    expect(a.classList.contains("open")).toBe(true);
    a.click();
    expect(a.classList.contains("open")).toBe(false);
  });

  it("closes any other open bubble when a different one is opened", () => {
    const a = info("a");
    const b = info("b");
    setupTooltips(document.body);
    a.click();
    b.click();
    expect(a.classList.contains("open")).toBe(false);
    expect(b.classList.contains("open")).toBe(true);
  });

  it("closes on an outside click and on Escape", () => {
    const a = info("a");
    setupTooltips(document.body);
    a.click();
    expect(a.classList.contains("open")).toBe(true);
    document.body.click();
    expect(a.classList.contains("open")).toBe(false);

    a.click();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(a.classList.contains("open")).toBe(false);
  });

  it("does nothing when there are no .info elements", () => {
    expect(() => setupTooltips(document.body)).not.toThrow();
  });
});
