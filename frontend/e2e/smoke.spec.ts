import { expect, test } from "@playwright/test";

test("page loads, square is drawn, generation flow shows downloads", async ({ page }) => {
  await page.route("**/api/jobs", (route) =>
    route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ id: "job1" }) }),
  );
  let polls = 0;
  await page.route("**/api/jobs/job1", (route) => {
    polls += 1;
    const body =
      polls < 2
        ? { id: "job1", status: "running", stage: "fetch", message: "Loading", stats: {} }
        : {
            id: "job1",
            status: "done",
            stage: "export",
            message: "Ready",
            stats: { buildings: 42, blocks: 3, roofs: 5, lod2_buildings: 12, lod2_source: "hessen" },
          };
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.route("**/api/jobs/job1/preview.glb", (route) => route.fulfill({ status: 404, body: "" }));

  await page.goto("/");
  // maplibre only inserts its canvas once WebGL came up, so this also proves the map initialised.
  await page.waitForFunction(() => document.querySelector("#map canvas") !== null);
  await expect(page.locator("#map canvas")).toBeVisible();
  await expect(page.locator("#map .maplibregl-canvas")).toBeVisible();
  await expect(page.locator("#sidebar h1")).toHaveText("Skyline Frame");

  // Presets write both fields; picking one must not need a page reload (spec §9).
  await page.selectOption("#preset", "detail");
  await expect(page.locator("#side")).toHaveValue("800");
  await expect(page.locator("#plate")).toHaveValue("100");
  await page.selectOption("#preset", "skyline");
  await expect(page.locator("#side")).toHaveValue("1500");

  await page.selectOption("#mode", "full");
  await page.click("#generate");

  await expect(page.locator("#status")).toContainText("42 Gebäude, 3 Blöcke, 5 Dächer, 12 davon aus LoD2 Hessen", {
    timeout: 10_000,
  });
  await expect(page.locator("#downloads")).toBeVisible();
  await expect(page.locator("#dl-stl")).toHaveAttribute("href", "/api/jobs/job1/model.stl");
  await expect(page.locator("#dl-3mf")).toHaveAttribute("href", "/api/jobs/job1/model.3mf");
  await expect(page.locator("#dl-sources")).toHaveAttribute("href", "/api/jobs/job1/SOURCES.txt");
});

test("backend error is shown to the user", async ({ page }) => {
  await page.route("**/api/jobs", (route) =>
    route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ id: "job2" }) }),
  );
  await page.route("**/api/jobs/job2", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "job2", status: "error", stage: "fetch", message: "Overpass down", stats: {} }),
    }),
  );
  await page.goto("/");
  await page.click("#generate");
  await expect(page.locator("#status")).toHaveText("Overpass down");
  await expect(page.locator("#status")).toHaveClass(/error/);
  await expect(page.locator("#downloads")).toBeHidden();
});
