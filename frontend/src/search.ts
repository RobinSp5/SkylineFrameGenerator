// Debounced place search backed by the backend geocode endpoint.
import { geocode, type GeocodeHit } from "./api";

export function setupSearch(input: HTMLInputElement, list: HTMLElement, onPick: (hit: GeocodeHit) => void): void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  // Sequence guard: every state change bumps `latest`, so an in-flight request whose sequence is no
  // longer the latest one is ignored instead of rendering over newer state.
  let latest = 0;

  const render = (hits: GeocodeHit[]) => {
    list.replaceChildren();
    for (const hit of hits) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "search-hit";
      // textContent, never innerHTML: the name comes from an upstream geocoding service.
      button.textContent = hit.name;
      button.addEventListener("click", () => {
        // Picking ends the search: drop a pending debounce and supersede any in-flight request,
        // otherwise a query typed moments ago would re-open the dropdown.
        clearTimeout(timer);
        ++latest;
        list.replaceChildren();
        input.value = hit.name;
        onPick(hit);
      });
      list.append(button);
    }
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const seq = ++latest;
    const q = input.value.trim();
    if (q.length < 2) {
      list.replaceChildren();
      return;
    }
    timer = setTimeout(async () => {
      try {
        const hits = await geocode(q);
        if (seq !== latest) return;
        render(hits);
      } catch (err) {
        if (seq !== latest) return;
        list.replaceChildren();
        console.error("geocode failed", err);
      }
    }, 300);
  });
}
