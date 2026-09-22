// Scale presets for the sidebar (spec §9). Keep in sync with backend/skylineframe/spec.py::PRESETS
// and with the CLI's --preset. Only side_m and plate_size_mm are preset values; everything else
// stays what the user set, and no new field is ever sent to the API (FrameSpec forbids extras).
export type PresetName = "skyline" | "detail" | "gross";

export interface Preset {
  sideM: number;
  plateMm: number;
}

export const PRESETS: Record<PresetName, Preset> = {
  skyline: { sideM: 1500, plateMm: 100 },
  detail: { sideM: 800, plateMm: 100 },
  gross: { sideM: 1500, plateMm: 200 },
};

/** The preset matching these values, or "custom" ("Eigene" in the UI). */
export function presetFor(sideM: number, plateMm: number): PresetName | "custom" {
  const names = Object.keys(PRESETS) as PresetName[];
  return names.find((name) => PRESETS[name].sideM === sideM && PRESETS[name].plateMm === plateMm) ?? "custom";
}
