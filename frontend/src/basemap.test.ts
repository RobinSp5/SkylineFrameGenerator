import { describe, expect, it } from "vitest";
import { darkPaint } from "./basemap";

describe("darkPaint", () => {
  it("gives water, woods, buildings and roads distinct colours", () => {
    const water = darkPaint({ id: "water", type: "fill" })["fill-color"];
    const wood = darkPaint({ id: "landcover_wood", type: "fill" })["fill-color"];
    const building = darkPaint({ id: "building", type: "fill" })["fill-color"];
    const minor = darkPaint({ id: "highway_minor", type: "line" })["line-color"];
    const major = darkPaint({ id: "highway_major_inner", type: "line" })["line-color"];
    const ground = darkPaint({ id: "background", type: "background" })["background-color"];
    expect(new Set([water, wood, building, minor, major, ground]).size).toBe(6);
  });

  it("keeps casings and rail dashes below the roads they outline", () => {
    expect(darkPaint({ id: "highway_major_casing", type: "line" })["line-color"]).not.toBe(
      darkPaint({ id: "highway_major_inner", type: "line" })["line-color"],
    );
    expect(darkPaint({ id: "railway_dashline", type: "line" })["line-color"]).toBe(
      darkPaint({ id: "background", type: "background" })["background-color"],
    );
  });

  it("lights up labels with a dark halo and leaves other layer types alone", () => {
    const town = darkPaint({ id: "label_town", type: "symbol" });
    expect(town["text-color"]).toBe("#d4d4d8");
    expect(town["text-halo-color"]).toBe("#17171a");
    expect(darkPaint({ id: "hillshade", type: "hillshade" })).toEqual({});
  });
});
