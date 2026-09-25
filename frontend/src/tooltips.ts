/** Tap-to-toggle for the ".info" explanation bubbles in index.html.
 *
 * Hover and keyboard focus already reveal a bubble through CSS alone (:hover/:focus-visible on
 * .info). A touch device has neither, so a tap toggles the "open" class instead; tapping anywhere
 * else, or pressing Escape, closes whichever bubble was open.
 */
export function setupTooltips(root: ParentNode = document): void {
  const infos = Array.from(root.querySelectorAll<HTMLElement>(".info"));
  if (infos.length === 0) return;

  const closeAll = () => {
    for (const info of infos) info.classList.remove("open");
  };

  for (const info of infos) {
    info.addEventListener("click", (event) => {
      event.stopPropagation();
      const wasOpen = info.classList.contains("open");
      closeAll();
      info.classList.toggle("open", !wasOpen);
    });
  }
  document.addEventListener("click", closeAll);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAll();
  });
}
