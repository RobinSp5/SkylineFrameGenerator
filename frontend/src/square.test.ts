import { describe, expect, it } from "vitest";
import { localToLngLat, squareCorners, squareGeoJSON } from "./square";

const base = { lat: 50, lon: 8, sideM: 1000, rotationDeg: 0 };

describe("localToLngLat", () => {
  it("moves north by side/2 for y", () => {
    const [lon, lat] = localToLngLat(0, 500, 50, 8);
    expect(lon).toBeCloseTo(8, 9);
    expect(lat).toBeCloseTo(50 + 500 / 110574, 6);
  });
  it("shrinks longitude step at higher latitude", () => {
    const [lon50] = localToLngLat(500, 0, 50, 8);
    const [lon0] = localToLngLat(500, 0, 0, 8);
    expect(lon50 - 8).toBeGreaterThan(lon0 - 8);
  });
});

describe("squareCorners", () => {
  it("returns SW, SE, NE, NW without rotation", () => {
    const [sw, se, ne, nw] = squareCorners(base);
    expect(sw[0]).toBeLessThan(8); expect(sw[1]).toBeLessThan(50);
    expect(se[0]).toBeGreaterThan(8); expect(se[1]).toBeLessThan(50);
    expect(ne[0]).toBeGreaterThan(8); expect(ne[1]).toBeGreaterThan(50);
    expect(nw[0]).toBeLessThan(8); expect(nw[1]).toBeGreaterThan(50);
  });
  it("rotates clockwise: the NE corner moves to due east at 45°", () => {
    const [, , ne] = squareCorners({ ...base, rotationDeg: 45 });
    expect(ne[1]).toBeCloseTo(50, 6);
    expect(ne[0]).toBeGreaterThan(8);
  });
});

describe("squareGeoJSON", () => {
  it("is a closed polygon feature", () => {
    const f = squareGeoJSON(base);
    expect(f.geometry.type).toBe("Polygon");
    const ring = f.geometry.coordinates[0];
    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual(ring[4]);
  });
});
