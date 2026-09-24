// Light / dark theme: follows the system unless the viewer picks one, and remembers the pick.
// The resolved theme lives on <html data-theme>, which every dark rule in style.css keys on;
// index.html sets it before first paint so a dark system never flashes the light UI.

export type ThemePreference = "system" | "light" | "dark";
export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "skyline-theme";
const ORDER: ThemePreference[] = ["system", "light", "dark"];
const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
const listeners: Array<(t: Theme) => void> = [];

/** The stored pick; storage can be blocked (private mode, sandboxed previews), so it never throws. */
export function preference(): ThemePreference {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* storage unavailable: fall back to the system */
  }
  return "system";
}

export function resolve(pref: ThemePreference, systemIsDark = systemDark.matches): Theme {
  return pref === "system" ? (systemIsDark ? "dark" : "light") : pref;
}

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function apply(): void {
  const theme = resolve(preference());
  const root = document.documentElement;
  if (root.dataset.theme === theme) return;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  listeners.forEach((cb) => cb(theme));
}

export function setPreference(pref: ThemePreference): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, pref);
  } catch {
    /* not remembered, but still applied for this visit */
  }
  document.documentElement.dataset.themePref = pref;
  apply();
}

/** The next preference in the toggle's cycle: system, light, dark. */
export function nextPreference(pref: ThemePreference): ThemePreference {
  return ORDER[(ORDER.indexOf(pref) + 1) % ORDER.length];
}

export function onThemeChange(cb: (t: Theme) => void): void {
  listeners.push(cb);
}

export function initTheme(): void {
  document.documentElement.dataset.themePref = preference();
  apply();
  systemDark.addEventListener("change", apply);
}
