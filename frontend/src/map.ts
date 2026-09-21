// MapLibre map with an OSM raster basemap and a draggable selection square.
import {
  Map as MapLibreMap,
  NavigationControl,
  type GeoJSONSource,
  type MapMouseEvent,
  type MapOptions,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { squareGeoJSON, type SquareParams } from "./square";

export interface MapController {
  setParams(p: SquareParams): void;
  getParams(): SquareParams;
  onChange(cb: (p: SquareParams) => void): void;
  flyTo(lat: number, lon: number): void;
}

// maplibre-gl v6 does not re-export `StyleSpecification`; derive it from the public MapOptions.
type StyleSpec = Exclude<MapOptions["style"], string | undefined>;

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
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

export function createMap(container: HTMLElement, initial: SquareParams): MapController {
  let params = { ...initial };
  const listeners: Array<(p: SquareParams) => void> = [];

  const map = new MapLibreMap({
    container,
    style: STYLE,
    center: [params.lon, params.lat],
    zoom: 13,
    attributionControl: {},
  });
  map.addControl(new NavigationControl({ showCompass: false }), "top-left");

  // Before the style has loaded the source does not exist yet; the `load` handler below seeds the
  // source from the then-current `params`, so an early redraw is a harmless no-op.
  const redraw = () => {
    const src = map.getSource("square") as GeoJSONSource | undefined;
    src?.setData(squareGeoJSON(params));
  };

  const emit = () => listeners.forEach((cb) => cb(params));

  map.on("load", () => {
    map.addSource("square", { type: "geojson", data: squareGeoJSON(params) });
    map.addLayer({
      id: "square-fill",
      type: "fill",
      source: "square",
      paint: { "fill-color": "#ff6a00", "fill-opacity": 0.15 },
    });
    map.addLayer({
      id: "square-line",
      type: "line",
      source: "square",
      paint: { "line-color": "#ff6a00", "line-width": 2 },
    });

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
      map.flyTo({ center: [lon, lat], zoom: 13 });
      emit();
    },
  };
}
