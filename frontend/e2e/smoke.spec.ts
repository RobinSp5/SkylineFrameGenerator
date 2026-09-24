import { expect, test, type Page } from "@playwright/test";

/** Mocks one job that reports `running` once and then `done` with `extra` merged into the body. */
async function mockJob(page: Page, id: string, extra: Record<string, unknown> = {}) {
  let createdWith: Record<string, unknown> | null = null;
  await page.route("**/api/jobs", (route) => {
    createdWith = route.request().postDataJSON();
    return route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ id }) });
  });
  let polls = 0;
  await page.route(`**/api/jobs/${id}`, (route) => {
    polls += 1;
    const body =
      polls < 2
        ? { id, status: "running", stage: "fetch", message: "Loading OpenStreetMap data", stats: {} }
        : {
            id,
            status: "done",
            stage: "export",
            message: "Ready",
            stats: { buildings: 42, blocks: 3, roofs: 5, lod2_buildings: 12, lod2_source: "hessen" },
            ...extra,
          };
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.route(`**/api/jobs/${id}/preview.glb`, (route) => route.fulfill({ status: 404, body: "" }));
  return () => createdWith;
}

test("page loads, square is drawn, generation flow shows downloads", async ({ page }) => {
  const sent = await mockJob(page, "job1");

  await page.goto("/");
  // maplibre only inserts its canvas once WebGL came up, so this also proves the map initialised.
  await page.waitForFunction(() => document.querySelector("#map canvas") !== null);
  await expect(page.locator("#map canvas")).toBeVisible();
  await expect(page.locator("#map .maplibregl-canvas")).toBeVisible();
  await expect(page.locator("#brand")).toHaveText("Skyline Frame");
  await expect(page.locator(".square-label")).toHaveText("1500 m");

  // Presets write both fields; picking one must not need a page reload (spec §9).
  await page.getByRole("radio", { name: "Detail" }).check();
  await expect(page.locator("#side")).toHaveValue("800");
  await expect(page.locator("#size")).toHaveValue("10");
  await expect(page.locator("#scale-out")).toHaveText("10 × 10 cm plate · 1 : 8 000");
  await page.getByRole("radio", { name: "Skyline" }).check();
  await expect(page.locator("#side")).toHaveValue("1500");

  // The print-size slider drives the plate size and drops the preset to Custom.
  await page.locator("#size").fill("12.5");
  await expect(page.locator("#size-value")).toHaveText("12.5");
  await expect(page.getByRole("radio", { name: "Custom" })).toBeChecked();
  await expect(page.locator("#file-preview")).toHaveText("Skyline_1500m_12.5cm.3mf");

  await page.getByRole("radio", { name: "+ Streets & water" }).check();
  await page.click("#generate");

  await expect(page.locator("#status")).toContainText("42 buildings, 3 blocks, 5 roofs, 12 of which from LoD2 Hessen", {
    timeout: 10_000,
  });
  expect(sent()).toMatchObject({ mode: "full", plate_size_mm: 125, side_m: 1500, trees: true });
  await expect(page.locator("#downloads")).toBeVisible();
  await expect(page.locator("#stat-buildings")).toHaveText("42");
  await expect(page.locator("#stat-lod2")).toHaveText("12");
  await expect(page.locator("#dl-stl")).toHaveAttribute("href", "/api/jobs/job1/model.stl");
  await expect(page.locator("#dl-3mf")).toHaveAttribute("href", "/api/jobs/job1/model.3mf");
  await expect(page.locator("#dl-sources")).toHaveAttribute("href", "/api/jobs/job1/SOURCES.txt");
  await expect(page.locator("#generate-label")).toHaveText("Generate model");

  // Back to the settings, with the map in front again.
  await page.click("#adjust");
  await expect(page.locator("#config")).toBeVisible();
  await expect(page.locator("#viewer")).toBeHidden();
});

test("the job's name and file stem label the result and the downloads", async ({ page }) => {
  await mockJob(page, "job3", { name: "Eppstein", file_stem: "Eppstein_1500m_10cm" });
  await page.goto("/");
  await page.click("#generate");
  await expect(page.locator("#place-name")).toHaveText("Eppstein", { timeout: 10_000 });
  await expect(page.locator("#dl-3mf")).toHaveAttribute("download", "Eppstein_1500m_10cm.3mf");
  await expect(page.locator("#dl-stl")).toHaveAttribute("download", "Eppstein_1500m_10cm.stl");
  await expect(page.locator("#dl-sources")).toHaveAttribute("download", "Eppstein_1500m_10cm_SOURCES.txt");
  await expect(page.getByRole("group", { name: "View" })).toBeVisible();
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
  await expect(page.locator("#status")).toBeVisible();
  await expect(page.locator("#downloads")).toBeHidden();
  await expect(page.locator("#generate")).toBeEnabled();
});
