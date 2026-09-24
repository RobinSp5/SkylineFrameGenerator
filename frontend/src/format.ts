// Pure formatting helpers for the panel: scale, plate size, coordinates, place labels and the
// client-side file name guess. Kept DOM-free so they can be tested in the node environment.

/** Narrow no-break space: groups thousands like "15 000" without letting the number wrap. */
const GROUP = " ";

/** 15000 -> "15 000". Integers only; callers round first. */
export function groupDigits(n: number): string {
  return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, GROUP);
}

/** 10 -> "10", 10.5 -> "10.5": whole centimetres drop the decimal. */
export function formatCm(cm: number): string {
  return Number.isInteger(cm) ? String(cm) : cm.toFixed(1);
}

/** The map scale of a print, e.g. 1500 m on 100 mm -> "1 : 15 000". */
export function formatScale(sideM: number, plateMm: number): string {
  return `1 : ${groupDigits((sideM * 1000) / plateMm)}`;
}

/** e.g. 100 mm -> "10 × 10 cm plate". */
export function formatPlate(plateMm: number): string {
  const cm = formatCm(plateMm / 10);
  return `${cm} × ${cm} cm plate`;
}

/** e.g. "50.1413 N  8.3925 E"; the double gap is a no-break pair so it survives HTML whitespace. */
export function formatCoords(lat: number, lon: number): string {
  const ns = `${Math.abs(lat).toFixed(4)} ${lat >= 0 ? "N" : "S"}`;
  const ew = `${Math.abs(lon).toFixed(4)} ${lon >= 0 ? "E" : "W"}`;
  return `${ns}  ${ew}`;
}

export interface PlaceLabel {
  name: string;
  region: string;
}

/**
 * Splits a geocoder display name ("Eppstein, Main-Taunus-Kreis, Hessen, 65817, Deutschland") into
 * the place itself and a short region line ("Main-Taunus-Kreis, Hessen"): postcodes are dropped and
 * at most two regional levels are kept, which usually leaves out the country.
 */
export function splitPlace(displayName: string): PlaceLabel {
  const parts = displayName
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
  const [name = "", ...rest] = parts;
  const region = rest.filter((p) => !/^\d/.test(p)).slice(0, 2).join(", ");
  return { name, region };
}

/** ASCII file-name slug that keeps the place readable: "Frankfurt am Main" -> "Frankfurt_am_Main". */
export function slugify(label: string): string {
  const ascii = label
    .replace(/ä/g, "ae")
    .replace(/ö/g, "oe")
    .replace(/ü/g, "ue")
    .replace(/Ä/g, "Ae")
    .replace(/Ö/g, "Oe")
    .replace(/Ü/g, "Ue")
    .replace(/ß/g, "ss")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "");
  const slug = ascii.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40).replace(/_+$/, "");
  return slug || "Skyline";
}

/**
 * The file name the backend will most likely choose (its `file_stem`), shown before a run. Only a
 * preview: once a job reports `file_stem`, that value wins.
 */
export function guessFileStem(placeName: string | null, sideM: number, plateMm: number): string {
  return `${slugify(placeName ?? "")}_${Math.round(sideM)}m_${formatCm(plateMm / 10)}cm`;
}
