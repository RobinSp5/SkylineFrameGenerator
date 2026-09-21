// Debounced place search backed by the backend geocode endpoint.
import { geocode, type GeocodeHit } from "./api";

export function setupSearch(input: HTMLInputElement, list: HTMLElement, onPick: (hit: GeocodeHit) => void): void {
  let timer: ReturnType<typeof setTimeout> | undefined;

  const render = (hits: GeocodeHit[]) => {
    list.replaceChildren();
    for (const hit of hits) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "search-hit";
      // textContent, never innerHTML: the name comes from an upstream geocoding service.
      button.textContent = hit.name;
      button.addEventListener("click", () => {
        list.replaceChildren();
        input.value = hit.name;
        onPick(hit);
      });
      list.append(button);
    }
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) {
      list.replaceChildren();
      return;
    }
    timer = setTimeout(async () => {
      try {
        render(await geocode(q));
      } catch (err) {
        list.replaceChildren();
        console.error("geocode failed", err);
      }
    }, 300);
  });
}
