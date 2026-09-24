// MapLibre map with a muted OSM raster basemap and a draggable selection square.
import {
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  setWorkerUrl,
  type GeoJSONSource,
  type MapMouseEvent,
  type MapOptions,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// maplibre derives its worker URL from its own `import.meta.url`, which points at the bundled app
// chunk here, so the default URL 404s and every GeoJSON source hangs unparsed (the square never
// draws). Hand it the worker Vite bundles for us instead.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import type { Feature, Polygon } from "geojson";
import { localToLngLat, squareCorners, squareGeoJSON, type SquareParams } from "./square";

setWorkerUrl(maplibreWorkerUrl);

export interface MapPadding {
  top?: number;
  right?: number;
  bottom?: number;
  left?: number;
}

export interface MapController {
  setParams(p: SquareParams): void;
  getParams(): SquareParams;
  onChange(cb: (p: SquareParams) => void): void;
  flyTo(lat: number, lon: number): void;
  /** Screen area covered by floating UI; the map centres the square in what is left. */
  setPadding(p: MapPadding): void;
}

// maplibre-gl v6 does not re-export `StyleSpecification`; derive it from the public MapOptions.
type StyleSpec = Exclude<MapOptions["style"], string | undefined>;

/** Colours per theme; they mirror the CSS tokens in style.css, which a WebGL layer cannot read. */
const THEMES = {
  light: {
    accent: "#C2410C",
    mask: "rgba(237,237,234,0.62)",
    // Desaturated and lifted: the basemap is context, the square is the subject.
    raster: { "raster-saturation": -0.85, "raster-contrast": -0.1, "raster-brightness-min": 0.12, "raster-brightness-max": 1 },
  },
  dark: {
    accent: "#FB923C",
    mask: "rgba(17,17,19,0.55)",
    // brightness-min above brightness-max inverts the tiles into a dark map.
    raster: { "raster-saturation": -0.9, "raster-contrast": -0.1, "raster-brightness-min": 0.82, "raster-brightness-max": 0.06 },
  },
} as const;

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
const theme = () => (darkQuery.matches ? THEMES.dark : THEMES.light);

const STYLE: StyleSpec = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm", paint: { ...theme().raster } }],
};

/** Signed area of a lon/lat ring (shoelace); the sign gives the winding direction. */
function ringArea(ring: [number, number][]): number {
  let sum = 0;
  for (let i = 0; i < ring.length; i++) {
    const [x1, y1] = ring[i];
    const [x2, y2] = ring[(i + 1) % ring.length];
    sum += x1 * y2 - x2 * y1;
  }
  return sum / 2;
}

/** The whole world with the square cut out, to dim everything outside the print area. */
function maskGeoJSON(p: SquareParams): Feature<Polygon> {
  const world: [number, number][] = [
    [-180, -85],
    [180, -85],
    [180, 85],
    [-180, 85],
  ];
  const hole = squareCorners(p);
  // A hole must wind against its outer ring or maplibre reads it as a second filled polygon.
  if (Math.sign(ringArea(hole)) === Math.sign(ringArea(world))) hole.reverse();
  return {
    type: "Feature",
    properties: {},
    geometry: { type: "Polygon", coordinates: [[...world, world[0]], [...hole, hole[0]]] },
  };
}

/** Midpoint of the square's top edge, where the side-length label sits. */
function topEdgeCenter(p: SquareParams): [number, number] {
  const t = (p.rotationDeg * Math.PI) / 180;
  const h = p.sideM / 2;
  // local (0, h) rotated clockwise by t, matching squareCorners
  return localToLngLat(h * Math.sin(t), h * Math.cos(t), p.lat, p.lon);
}

export function createMap(container: HTMLElement, initial: SquareParams): MapController {
  let params = { ...initial };
  const listeners: Array<(p: SquareParams) => void> = [];

  const map = new MapLibreMap({
    container,
    style: STYLE,
    center: [params.lon, params.lat],
    zoom: 13,
    attributionControl: { compact: true },
  });
  map.addControl(new NavigationControl({ showCompass: false }), "bottom-left");

  const labelEl = document.createElement("div");
  labelEl.className = "square-label mono";
  const label = new Marker({ element: labelEl, anchor: "bottom", offset: [0, -10] })
    .setLngLat(topEdgeCenter(params))
    .addTo(map);

  // Before the style has loaded the sources do not exist yet; the `load` handler below seeds them
  // from the then-current `params`, so an early redraw only moves the label.
  const redraw = () => {
    (map.getSource("square") as GeoJSONSource | undefined)?.setData(squareGeoJSON(params));
    (map.getSource("mask") as GeoJSONSource | undefined)?.setData(maskGeoJSON(params));
    labelEl.textContent = `${params.sideM} m`;
    label.setLngLat(topEdgeCenter(params));
  };
  redraw();

  const emit = () => listeners.forEach((cb) => cb(params));

  /** Zoom at which the square spans about 60 % of the map area left free by the padding. */
  const framingZoom = () => {
    const { top = 0, right = 0, bottom = 0, left = 0 } = map.getPadding();
    const w = container.clientWidth - left - right;
    const h = container.clientHeight - top - bottom;
    const targetPx = Math.max(120, 0.6 * Math.min(w, h));
    const metresPerPx = params.sideM / targetPx;
    // Web Mercator with maplibre's 512 px tiles: metres per pixel = C * cos(lat) / (512 * 2^z).
    const zoom = Math.log2((40_075_016.686 * Math.cos((params.lat * Math.PI) / 180)) / (512 * metresPerPx));
    return Math.min(18, Math.max(3, zoom));
  };
  let framed = false;

  const applyTheme = () => {
    if (!map.getLayer("square-line")) return;
    const t = theme();
    for (const [key, value] of Object.entries(t.raster)) {
      map.setPaintProperty("osm", key as keyof typeof t.raster, value);
    }
    map.setPaintProperty("mask", "fill-color", t.mask);
    map.setPaintProperty("square-fill", "fill-color", t.accent);
    map.setPaintProperty("square-line", "line-color", t.accent);
  };
  darkQuery.addEventListener("change", applyTheme);

  map.on("load", () => {
    map.addSource("mask", { type: "geojson", data: maskGeoJSON(params) });
    map.addSource("square", { type: "geojson", data: squareGeoJSON(params) });
    map.addLayer({ id: "mask", type: "fill", source: "mask", paint: { "fill-color": theme().mask } });
    map.addLayer({
      id: "square-fill",
      type: "fill",
      source: "square",
      // Nearly clear, but still a hit target for dragging.
      paint: { "fill-color": theme().accent, "fill-opacity": 0.04 },
    });
    map.addLayer({
      id: "square-line",
      type: "line",
      source: "square",
      paint: { "line-color": theme().accent, "line-width": 2 },
    });
    applyTheme();

    map.on("mouseenter", "square-fill", () => (map.getCanvas().style.cursor = "move"));
    map.on("mouseleave", "square-fill", () => (map.getCanvas().style.cursor = ""));

    map.on("mousedown", "square-fill", (e: MapMouseEvent) => {
      e.preventDefault();
      map.dragPan.disable();
      const start = e.lngLat;
      const startCenter = { lat: params.lat, lon: params.lon };

      const onMove = (ev: MapMouseEvent) => {
        params = {
          ...params,
          lat: startCenter.lat + (ev.lngLat.lat - start.lat),
          lon: startCenter.lon + (ev.lngLat.lng - start.lng),
        };
        redraw();
      };
      const onUp = () => {
        map.off("mousemove", onMove);
        map.dragPan.enable();
        emit();
      };
      map.on("mousemove", onMove);
      // window, not map: releasing the button outside the canvas must still end the drag
      window.addEventListener("mouseup", onUp, { once: true });
    });
  });

  return {
    setParams(p) {
      params = { ...p };
      redraw();
    },
    getParams: () => ({ ...params }),
    onChange(cb) {
      listeners.push(cb);
    },
    flyTo(lat, lon) {
      params = { ...params, lat, lon };
      redraw();
      map.flyTo({ center: [lon, lat], zoom: framingZoom() });
      emit();
    },
    setPadding(p) {
      const next = { top: 0, right: 0, bottom: 0, left: 0, ...p };
      const prev = map.getPadding();
      const same = prev.top === next.top && prev.right === next.right && prev.bottom === next.bottom && prev.left === next.left;
      if (same && framed) return;
      // Keep the square in view when the free area changes; the first call also sets the zoom,
      // since only now is it known how much of the canvas the panel covers.
      map.setPadding(next);
      map.jumpTo({ center: [params.lon, params.lat], zoom: framed ? map.getZoom() : framingZoom() });
      framed = true;
    },
  };
}
