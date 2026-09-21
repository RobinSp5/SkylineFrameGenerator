// Square selection geometry. Uses an equirectangular approximation that is accurate to a few
// metres for squares up to 5 km; the backend uses a proper azimuthal projection for the model.
import type { Feature, Polygon } from "geojson";

export interface SquareParams {
  lat: number;
  lon: number;
  sideM: number;
  rotationDeg: number; // clockwise from north
}

const M_PER_DEG_LAT = 110574;
const M_PER_DEG_LON_EQUATOR = 111320;

export function localToLngLat(x: number, y: number, lat: number, lon: number): [number, number] {
  const mPerDegLon = M_PER_DEG_LON_EQUATOR * Math.cos((lat * Math.PI) / 180);
  return [lon + x / mPerDegLon, lat + y / M_PER_DEG_LAT];
}

export function squareCorners(p: SquareParams): [number, number][] {
  const h = p.sideM / 2;
  const t = (p.rotationDeg * Math.PI) / 180;
  const c = Math.cos(t);
  const s = Math.sin(t);
  const local: [number, number][] = [
    [-h, -h],
    [h, -h],
    [h, h],
    [-h, h],
  ];
  // clockwise rotation by t
  return local.map(([x, y]) => localToLngLat(x * c + y * s, -x * s + y * c, p.lat, p.lon));
}

export function squareGeoJSON(p: SquareParams): Feature<Polygon> {
  const corners = squareCorners(p);
  return {
    type: "Feature",
    properties: {},
    geometry: { type: "Polygon", coordinates: [[...corners, corners[0]]] },
  };
}
