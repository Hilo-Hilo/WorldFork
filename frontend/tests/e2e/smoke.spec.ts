import { test, expect } from "@playwright/test";

/* End-to-end smoke for the wired UI.
 *
 * Prerequisites:
 *   - Backend running on http://127.0.0.1:8003 (Docker compose up).
 *   - Frontend dev server on the URL passed via WORLDFORK_FE_URL or
 *     defaulting to http://localhost:3003.
 *   - At least one Big Bang in the database with a final report rendered.
 *     The setup-skill onboarding run satisfies this.
 *
 * Cost: zero LLM calls. We never POST to /api/big-bangs in this suite —
 * we only read existing data and exercise UI navigation. The "creates a
 * new run" check is intentionally skipped here; run it manually when you
 * have credits to spend.
 */

test("home lists existing runs", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: /new scenario/i })).toBeVisible();

  // Wait for the runs list to populate (or empty-state to settle)
  await page.waitForFunction(
    () => !document.body.textContent?.includes("loading runs"),
    null,
    { timeout: 8000 },
  );
});

test("input page renders required form sections", async ({ page }) => {
  await page.goto("/input");
  await expect(page.getByRole("heading", { name: /configure a scenario run/i })).toBeVisible();
  await expect(page.getByText("Scenario source")).toBeVisible();
  await expect(page.getByText("Initializer prompt")).toBeVisible();
  await expect(page.getByText("Simulation config")).toBeVisible();
  await expect(page.getByText("use_initializer_agent")).toBeVisible();
  await expect(page.getByRole("button", { name: /run simulation/i })).toBeVisible();
});

test("input page can load the tiny demo scenario without submitting", async ({ page }) => {
  await page.goto("/input");
  await page.getByRole("button", { name: /use tiny demo/i }).click();
  await expect(page.getByText("demo-scenario.md")).toBeVisible();
  await expect(page.getByRole("button", { name: /run simulation/i })).toBeEnabled();
});

test("dashboard empty state when no run param", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: /no run selected/i })).toBeVisible();
});

test("dashboard with a real run id renders polled data", async ({ page, request }) => {
  // Pull the most recent run from the backend
  const resp = await request.get("/backend/api/agent/runs?verbosity=summary&limit=1");
  if (!resp.ok()) test.skip(true, "backend has no runs to dashboard");
  const body = await resp.json();
  const run = body?.data?.[0];
  test.skip(!run, "backend returned no runs");

  await page.goto(`/dashboard?run=${run.id}`);
  await expect(page.getByText(run.name)).toBeVisible({ timeout: 10000 });
  // The status pill in the top bar should not stay "…"
  await expect(page.locator(".dash-top .status")).not.toHaveText(/\.\.\./);
});

test("report empty state when no run param", async ({ page }) => {
  await page.goto("/report");
  await expect(page.getByRole("heading", { name: /no report/i })).toBeVisible();
});

test("report viewer renders markdown for an existing final report", async ({ page, request }) => {
  // Find a run that has a final report
  const runs = await request.get("/backend/api/agent/runs?verbosity=summary&limit=10");
  test.skip(!runs.ok(), "backend has no runs");
  const runIds: string[] = (await runs.json()).data?.map((r: { id: string }) => r.id) ?? [];

  let runWithReport: string | null = null;
  for (const id of runIds) {
    const reps = await request.get(`/backend/api/big-bangs/${id}/reports`);
    if (!reps.ok()) continue;
    const list = await reps.json();
    if (Array.isArray(list) && list.some((r: { report_type: string }) => r.report_type === "final_big_bang")) {
      runWithReport = id;
      break;
    }
  }
  test.skip(!runWithReport, "no run has a final report yet");

  await page.goto(`/report?run=${runWithReport}`);

  // The versions nav should populate, and the markdown column should have an h1.
  await expect(page.locator(".rpt-version").first()).toBeVisible({ timeout: 10000 });
  await expect(page.locator(".rpt-read h1").first()).toBeVisible({ timeout: 10000 });
});
