// Basemap for both themes. OpenFreeMap's Positron has the right structure for this app: quiet
// land, clear streets, road numbers, town names. Its own "dark" style draws buildings #0a0a0a on a
// #0c0c0c ground, so in dark mode Positron is recoloured instead, layer by layer: water reads
// blue-grey, woods faintly green, buildings and roads as distinct steps of grey, labels light.
// OpenFreeMap: free, no key, no request limit, commercial use allowed; MapLibre shows its
// attribution (OpenFreeMap, OpenMapTiles, OpenStreetMap) from the style.

export const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";

/** Just the layer fields the recolouring looks at. */
export interface BasemapLayer {
  id: string;
  type: string;
}

const DARK = {
  ground: "#17171a",
  land: "#1c1c20",
  green: "#1b231e",
  water: "#1e2a35",
  building: "#2e2e34",
  buildingEdge: "#3b3b42",
  casing: "#232327",
  path: "#34343b",
  minor: "#404048",
  major: "#575760",
  motorway: "#6e6e78",
  rail: "#4a4a53",
  boundary: "#4a4a53",
  label: "#d4d4d8",
  roadLabel: "#a1a1aa",
  waterLabel: "#8fa6bd",
  halo: "#17171a",
} as const;

/** Paint overrides that turn one Positron layer dark; an empty object leaves the layer alone. */
export function darkPaint(layer: BasemapLayer): Record<string, string> {
  const id = layer.id;
  switch (layer.type) {
    case "background":
      return { "background-color": DARK.ground };
    case "fill":
      if (id.includes("water")) return { "fill-color": DARK.water };
      if (id.includes("building")) return { "fill-color": DARK.building, "fill-outline-color": DARK.buildingEdge };
      if (/park|wood|grass|landcover/.test(id)) return { "fill-color": DARK.green };
      return { "fill-color": DARK.land };
    case "line":
      if (id.includes("water")) return { "line-color": DARK.water };
      if (id.includes("casing")) return { "line-color": DARK.casing };
      if (id.includes("dashline")) return { "line-color": DARK.ground };
      if (id.includes("rail")) return { "line-color": DARK.rail };
      if (id.includes("boundary")) return { "line-color": DARK.boundary };
      if (id.includes("motorway")) return { "line-color": DARK.motorway };
      if (/major|runway|taxiway/.test(id)) return { "line-color": DARK.major };
      if (/path|pier/.test(id)) return { "line-color": DARK.path };
      return { "line-color": DARK.minor };
    case "symbol":
      if (id.includes("water")) return { "text-color": DARK.waterLabel, "text-halo-color": DARK.halo };
      if (id.includes("highway") || id.includes("road")) return { "text-color": DARK.roadLabel, "text-halo-color": DARK.halo };
      return { "text-color": DARK.label, "text-halo-color": DARK.halo };
    default:
      return {};
  }
}
