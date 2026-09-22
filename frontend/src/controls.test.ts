// @vitest-environment happy-dom
// Set per-file so vite.config.ts can stay on the fast `environment: "node"` default.
import { beforeEach, describe, expect, it } from "vitest";
import { setupControls } from "./controls";
import { PRESETS, presetFor } from "./presets";

const SIDEBAR = `
  <input id="search" />
  <div id="search-results"></div>
  <select id="preset">
    <option value="skyline">Skyline</option>
    <option value="detail">Detail</option>
    <option value="gross">Groß</option>
    <option value="custom">Eigene</option>
  </select>
  <output id="side-out"></output>
  <input id="side" type="range" min="200" max="5000" step="50" value="1500" />
  <output id="rotation-out"></output>
  <input id="rotation" type="range" min="-180" max="180" step="1" value="0" />
  <input id="plate" type="number" value="100" />
  <input id="thickness" type="number" value="3" />
  <input id="zfactor" type="number" value="1.5" />
  <select id="mode"><option value="simple">simple</option><option value="full">full</option></select>
  <button id="generate"></button>
  <p id="status"></p>
  <div id="downloads" hidden><a id="dl-stl"></a><a id="dl-3mf"></a></div>
`;

function field<T extends HTMLElement>(id: string): T {
  return document.getElementById(id) as unknown as T;
}

describe("presetFor", () => {
  it("recognises every preset and falls back to custom", () => {
    expect(presetFor(1500, 100)).toBe("skyline");
    expect(presetFor(800, 100)).toBe("detail");
    expect(presetFor(1500, 200)).toBe("gross");
    expect(presetFor(1234, 100)).toBe("custom");
    expect(presetFor(1500, 123)).toBe("custom");
  });

  it("matches the backend table", () => {
    expect(PRESETS).toEqual({
      skyline: { sideM: 1500, plateMm: 100 },
      detail: { sideM: 800, plateMm: 100 },
      gross: { sideM: 1500, plateMm: 200 },
    });
  });
});

describe("setupControls", () => {
  let root: HTMLElement;
  let updates: Array<Record<string, number>>;

  beforeEach(() => {
    document.body.innerHTML = `<div id="app">${SIDEBAR}</div>`;
    root = document.getElementById("app")!;
    updates = [];
  });

  const fire = (el: HTMLElement, type: string) => el.dispatchEvent(new Event(type));

  it("starts on the preset that matches the initial fields", () => {
    setupControls(root);
    expect(field<HTMLSelectElement>("preset").value).toBe("skyline");
  });

  it("writes side and plate when a preset is picked and tells the map", () => {
    const controls = setupControls(root);
    controls.onSquareInput((p) => updates.push(p as Record<string, number>));

    const preset = field<HTMLSelectElement>("preset");
    preset.value = "gross";
    fire(preset, "change");

    expect(field<HTMLInputElement>("side").value).toBe("1500");
    expect(field<HTMLInputElement>("plate").value).toBe("200");
    expect(field<HTMLOutputElement>("side-out").value).toBe("1500 m");
    expect(controls.read().plate_size_mm).toBe(200);
    expect(updates).toEqual([{ sideM: 1500 }]);
  });

  it("switches to Detail including the side length", () => {
    const controls = setupControls(root);
    const preset = field<HTMLSelectElement>("preset");
    preset.value = "detail";
    fire(preset, "change");
    expect(controls.read().side_m).toBe(800);
    expect(controls.read().plate_size_mm).toBe(100);
  });

  it("falls back to Eigene when the side slider is moved by hand", () => {
    const controls = setupControls(root);
    controls.onSquareInput((p) => updates.push(p as Record<string, number>));

    const side = field<HTMLInputElement>("side");
    side.value = "1250";
    fire(side, "input");

    expect(field<HTMLSelectElement>("preset").value).toBe("custom");
    expect(updates).toEqual([{ sideM: 1250 }]);
  });

  it("falls back to Eigene when the plate size is edited by hand", () => {
    setupControls(root);
    const plate = field<HTMLInputElement>("plate");
    plate.value = "140";
    fire(plate, "input");
    expect(field<HTMLSelectElement>("preset").value).toBe("custom");
  });

  it("keeps the fields untouched when Eigene is selected", () => {
    const controls = setupControls(root);
    controls.onSquareInput((p) => updates.push(p as Record<string, number>));
    const preset = field<HTMLSelectElement>("preset");
    preset.value = "custom";
    fire(preset, "change");
    expect(controls.read().side_m).toBe(1500);
    expect(updates).toEqual([]);
  });

  it("does not leave the preset when only the rotation changes", () => {
    setupControls(root);
    const rotation = field<HTMLInputElement>("rotation");
    rotation.value = "30";
    fire(rotation, "input");
    expect(field<HTMLSelectElement>("preset").value).toBe("skyline");
  });
});
