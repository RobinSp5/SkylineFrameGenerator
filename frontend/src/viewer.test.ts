import { describe, expect, it } from "vitest";
import { NEUTRAL_COLOR, previewMaterialParams } from "./viewer";

describe("previewMaterialParams", () => {
  it("shows the GLB's part colours for a colour print", () => {
    expect(previewMaterialParams(true)).toMatchObject({ vertexColors: true });
    expect(previewMaterialParams(true).color).toBeUndefined();
  });

  it("renders a single-colour print in one neutral colour", () => {
    expect(previewMaterialParams(false)).toMatchObject({ vertexColors: false, color: NEUTRAL_COLOR });
  });
});
