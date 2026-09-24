import { describe, expect, it } from "vitest";
import { formatCm, formatCoords, formatPlate, formatScale, groupDigits, guessFileStem, slugify, splitPlace } from "./format";

const NB = " ";

describe("scale and plate", () => {
  it("groups thousands with a narrow space", () => {
    expect(groupDigits(15000)).toBe(`15${NB}000`);
    expect(groupDigits(800)).toBe("800");
    expect(groupDigits(1234567)).toBe(`1${NB}234${NB}567`);
  });

  it("formats every preset scale", () => {
    expect(formatScale(1500, 100)).toBe(`1 : 15${NB}000`);
    expect(formatScale(800, 100)).toBe(`1 : 8${NB}000`);
    expect(formatScale(1500, 200)).toBe(`1 : 7${NB}500`);
  });

  it("rounds odd scales to a whole number", () => {
    expect(formatScale(1450, 105)).toBe(`1 : 13${NB}810`);
  });

  it("drops the decimal for whole centimetres only", () => {
    expect(formatCm(10)).toBe("10");
    expect(formatCm(10.5)).toBe("10.5");
    expect(formatPlate(100)).toBe("10 × 10 cm plate");
    expect(formatPlate(125)).toBe("12.5 × 12.5 cm plate");
  });
});

describe("coordinates", () => {
  it("uses hemisphere letters instead of signs", () => {
    expect(formatCoords(50.14131, 8.39249)).toBe("50.1413 N  8.3925 E");
    expect(formatCoords(-33.8688, -70.6693)).toBe("33.8688 S  70.6693 W");
  });
});

describe("splitPlace", () => {
  it("keeps two regional levels and drops the postcode", () => {
    expect(splitPlace("Eppstein, Main-Taunus-Kreis, Hessen, 65817, Deutschland")).toEqual({
      name: "Eppstein",
      region: "Main-Taunus-Kreis, Hessen",
    });
  });

  it("copes with short names", () => {
    expect(splitPlace("Frankfurt am Main, Hessen, Deutschland")).toEqual({ name: "Frankfurt am Main", region: "Hessen, Deutschland" });
    expect(splitPlace("Berlin")).toEqual({ name: "Berlin", region: "" });
  });
});

describe("file names", () => {
  it("slugifies to readable ASCII", () => {
    expect(slugify("Frankfurt am Main")).toBe("Frankfurt_am_Main");
    expect(slugify("München")).toBe("Muenchen");
    expect(slugify("Besançon")).toBe("Besancon");
    expect(slugify("Frankfurt (Oder)")).toBe("Frankfurt_Oder");
    expect(slugify("  ")).toBe("Skyline");
  });

  it("guesses the stem from place, side and plate", () => {
    expect(guessFileStem("Eppstein", 1500, 100)).toBe("Eppstein_1500m_10cm");
    expect(guessFileStem("Eppstein", 800, 125)).toBe("Eppstein_800m_12.5cm");
    expect(guessFileStem(null, 1500, 200)).toBe("Skyline_1500m_20cm");
  });
});
