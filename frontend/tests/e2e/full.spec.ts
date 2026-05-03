import { test, expect, ConsoleMessage, Page } from "@playwright/test";

/* Comprehensive end-to-end coverage of the WorldFork UI.
 *
 * Goals:
 *   - Walk every interactive element on every page.
 *   - Capture browser console errors and fail tests on hook-order errors,
 *     uncaught exceptions, or 404/500 responses from the backend proxy.
 *   - Reuse existing backend data (zero-LLM cost). Never POST to /api/big-bangs.
 */

type CapturedError = { type: string; text: string };

function attachErrorCapture(page: Page) {
  const errors: CapturedError[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error") {
      const text = msg.text();
      // Next.js dev server logs version-staleness as a console error — ignore.
      if (text.includes("version-staleness") || text.includes("is outdated")) return;
      // Favicon or other resource 404s are acceptable.
      if (text.includes("Failed to load resource") && text.includes("404")) return;
      // /readyz returns 503 when any sub-check fails (e.g. openai-codex), and
      // the browser logs this at the network layer. The UI handles the JSON
      // body and displays "degraded" — so this network-level log is noise.
      if (text.includes("Failed to load resource") && text.includes("503")) return;
      errors.push({ type: "console.error", text });
    }
  });
  page.on("pageerror", (err) => {
    errors.push({ type: "pageerror", text: err.message });
  });
  page.on("response", (res) => {
    const url = res.url();
    // Skip /readyz — 503 there is design intent (degraded state).
    if (url.endsWith("/readyz")) return;
    if (url.includes("/backend/api/") && res.status() >= 500) {
      errors.push({ type: "5xx", text: `${res.status()} ${url}` });
    }
  });
  return errors;
}

function assertNoErrors(errors: CapturedError[]) {
  if (errors.length === 0) return;
  // Filter out noisy benign errors.
  const real = errors.filter(
    (e) => !e.text.includes("React DevTools") && !e.text.toLowerCase().includes("hydration"),
  );
  expect(real, `unexpected console/page errors:\n${real.map((e) => `[${e.type}] ${e.text}`).join("\n")}`).toEqual([]);
}

/* ============================================================
 *  Home page
 * ============================================================ */

test("home: renders and polls runs list without console errors", async ({ page, request }) => {
  const errs = attachErrorCapture(page);

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  // The CTA at the top
  await expect(page.getByRole("link", { name: /new scenario/i }).first()).toBeVisible();

  // Wait for the runs list (or empty state)
  await page.waitForFunction(
    () => !document.body.textContent?.includes("loading runs"),
    null,
    { timeout: 8000 },
  );

  // Backend has runs in the DB, so the list should render at least one row.
  const runs = await request.get("/backend/api/agent/runs?verbosity=summary&limit=20");
  if (runs.ok()) {
    const body = await runs.json();
    if (body?.data?.length) {
      // Each run name appears as a link → /dashboard?run=
      await expect(page.getByText(body.data[0].name).first()).toBeVisible();
      const dashLinks = page.locator('a[href*="/dashboard?run="]');
      expect(await dashLinks.count()).toBeGreaterThan(0);
    }
  }

  // Footer mock links
  await expect(page.getByRole("link", { name: "/input" })).toBeVisible();
  await expect(page.getByRole("link", { name: /\/dashboard/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /\/report/ })).toBeVisible();

  assertNoErrors(errs);
});

test("home → input: clicking 'New scenario' navigates to /input", async ({ page }) => {
  const errs = attachErrorCapture(page);
  await page.goto("/");
  await page.getByRole("link", { name: /new scenario/i }).first().click();
  await expect(page).toHaveURL(/\/input$/);
  await expect(page.getByRole("heading", { name: /configure a scenario run/i })).toBeVisible();
  assertNoErrors(errs);
});

/* ============================================================
 *  Input page
 * ============================================================ */

test("input: all sections render and submit is disabled with no scenario", async ({ page }) => {
  const errs = attachErrorCapture(page);
  await page.goto("/input");
  await expect(page.getByRole("heading", { name: /configure a scenario run/i })).toBeVisible();
  await expect(page.getByText("Scenario source")).toBeVisible();
  await expect(page.getByText("Initializer prompt")).toBeVisible();
  await expect(page.getByText("Simulation config")).toBeVisible();
  await expect(page.getByText("use_initializer_agent")).toBeVisible();

  const cta = page.getByRole("button", { name: /run simulation/i });
  await expect(cta).toBeDisabled();

  assertNoErrors(errs);
});

test("input: tiny demo loads scenario and enables CTA", async ({ page }) => {
  const errs = attachErrorCapture(page);
  await page.goto("/input");
  await page.getByRole("button", { name: /use tiny demo/i }).click();
  await expect(page.getByText("demo-scenario.md")).toBeVisible();
  await expect(page.getByRole("button", { name: /run simulation/i })).toBeEnabled();
  // Char count meta updates
  await expect(page.getByText(/scenario.*chars/)).toBeVisible();
  assertNoErrors(errs);
});

test("input: paste tab accepts text and enables CTA", async ({ page }) => {
  const errs = attachErrorCapture(page);
  await page.goto("/input");
  await page.getByRole("tab", { name: /paste text/i }).click();
  const ta = page.locator("textarea.wf-paste");
  await expect(ta).toBeVisible();
  await ta.fill("# Test\n\nA tiny pasted scenario.");
  await expect(page.getByRole("button", { name: /run simulation/i })).toBeEnabled();
  assertNoErrors(errs);
});

test("input: initializer toggle flips and prompt textarea enables", async ({ page }) => {
  const errs = attachErrorCapture(page);
  await page.goto("/input");

  // Scope the toggle to the "use_initializer_agent" row's number-suffix to
  // avoid colliding with "Run simulation" text content.
  const toggleRow = page.locator(".wf-field", { hasText: "use_initializer_agent" });
  const toggle = toggleRow.locator(".wf-num-suffix button");
  await expect(toggle).toHaveText("off");

  const promptTa = page.locator("textarea.wf-textarea");
  await expect(promptTa).toBeDisabled();

  await toggle.click();
  await expect(toggle).toHaveText("on");
  await expect(promptTa).toBeEnabled();

  assertNoErrors(errs);
});

test("input: branch_policy accordion opens and number fields are editable", async ({ page }) => {
  const errs = attachErrorCapture(page);
  await page.goto("/input");
  const accordion = page.getByRole("button", { name: /branch_policy/i });
  await accordion.click();
  // Wait for accordion fields to appear
  await expect(page.getByText("max_active_multiverses")).toBeVisible();
  await expect(page.getByText(/max_branches_per_tick/)).toBeVisible();
  await expect(page.getByText(/score_threshold/)).toBeVisible();
  assertNoErrors(errs);
});

test("input: max_ticks number field is editable", async ({ page }) => {
  const errs = attachErrorCapture(page);
  await page.goto("/input");

  // Locate the max_ticks input by its label structure
  const numFields = page.locator(".wf-num");
  expect(await numFields.count()).toBeGreaterThanOrEqual(2);
  await numFields.first().fill("8");
  await expect(numFields.first()).toHaveValue("8");
  assertNoErrors(errs);
});

test("input: backend status indicator is visible in the topbar", async ({ page }) => {
  const errs = attachErrorCapture(page);
  await page.goto("/input");
  // The status pill says "checking" briefly then "healthy" or "degraded"
  const meta = page.locator(".wf-topbar-meta");
  await expect(meta).toBeVisible();
  await expect(meta).toContainText(/healthy|degraded|unreachable|checking/);
  assertNoErrors(errs);
});

/* ============================================================
 *  Dashboard
 * ============================================================ */

test("dashboard: empty state with no run param", async ({ page }) => {
  const errs = attachErrorCapture(page);
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: /no run selected/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Runs$/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /new scenario/i })).toBeVisible();
  assertNoErrors(errs);
});

test("dashboard: real run renders top bar, rail, and tree", async ({ page, request }) => {
  const errs = attachErrorCapture(page);
  const resp = await request.get("/backend/api/agent/runs?verbosity=summary&limit=10");
  test.skip(!resp.ok(), "backend has no runs");
  const body = await resp.json();
  // pick a run that actually has multiverses, otherwise tree is empty.
  let chosen: { id: string; name: string } | null = null;
  for (const r of body.data || []) {
    const mvs = await request.get(`/backend/api/big-bangs/${r.id}/multiverses`);
    if (!mvs.ok()) continue;
    const list = await mvs.json();
    if (Array.isArray(list) && list.length > 0) {
      chosen = r;
      break;
    }
  }
  test.skip(!chosen, "no run has multiverses");

  await page.goto(`/dashboard?run=${chosen!.id}`);
  await expect(page.getByText(chosen!.name)).toBeVisible({ timeout: 10000 });
  // Status pill resolves
  const status = page.locator(".dash-top .status");
  await expect(status).toBeVisible();
  await expect(status).not.toHaveText(/\.\.\./);

  // Multiverse rail populates
  await expect(page.locator(".rail-list .mv-item").first()).toBeVisible({ timeout: 8000 });
  // Tree SVG renders
  await expect(page.locator(".tree-svg")).toBeVisible();
  // At least one node in the tree
  await expect(page.locator(".tree-svg .node").first()).toBeVisible();

  // Top-bar action buttons present
  await expect(page.getByRole("button", { name: /Pause|Resume/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Run to completion/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Open report/i })).toBeVisible();

  // Bottom log strip
  await expect(page.locator(".dash-bottom .label")).toContainText(/live log/i);

  assertNoErrors(errs);
});

test("dashboard: clicking a multiverse rail item highlights selection", async ({ page, request }) => {
  const errs = attachErrorCapture(page);
  const resp = await request.get("/backend/api/agent/runs?verbosity=summary&limit=10");
  test.skip(!resp.ok(), "backend has no runs");
  const body = await resp.json();
  let chosen: string | null = null;
  for (const r of body.data || []) {
    const mvs = await request.get(`/backend/api/big-bangs/${r.id}/multiverses`);
    if (!mvs.ok()) continue;
    const list = await mvs.json();
    if (Array.isArray(list) && list.length > 0) {
      chosen = r.id;
      break;
    }
  }
  test.skip(!chosen, "no run has multiverses");

  await page.goto(`/dashboard?run=${chosen}`);
  await expect(page.locator(".rail-list .mv-item").first()).toBeVisible({ timeout: 8000 });
  await page.locator(".rail-list .mv-item").first().click();
  await expect(page.locator(".rail-list .mv-item.is-active").first()).toBeVisible();
  // Right detail panel populates
  await expect(page.getByText(/^Selected/i).first()).toBeVisible();

  assertNoErrors(errs);
});

/* ============================================================
 *  Report viewer
 * ============================================================ */

test("report: empty state with no run param", async ({ page }) => {
  const errs = attachErrorCapture(page);
  await page.goto("/report");
  await expect(page.getByRole("heading", { name: /no report/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Runs$/ })).toBeVisible();
  assertNoErrors(errs);
});

test("report: empty state with a runId that has no reports", async ({ page, request }) => {
  const errs = attachErrorCapture(page);
  const resp = await request.get("/backend/api/agent/runs?verbosity=summary&limit=20");
  test.skip(!resp.ok(), "backend has no runs");
  const body = await resp.json();

  let runWithoutReport: string | null = null;
  for (const r of body.data || []) {
    const reps = await request.get(`/backend/api/big-bangs/${r.id}/reports`);
    if (!reps.ok()) continue;
    const list = await reps.json();
    if (Array.isArray(list) && list.length === 0) {
      runWithoutReport = r.id;
      break;
    }
  }
  test.skip(!runWithoutReport, "all runs have reports");

  await page.goto(`/report?run=${runWithoutReport}`);
  await expect(page.getByText(/No reports yet/i)).toBeVisible({ timeout: 10000 });
  assertNoErrors(errs);
});

test("report: real final report renders versions, markdown, TOC", async ({ page, request }) => {
  const errs = attachErrorCapture(page);
  const runs = await request.get("/backend/api/agent/runs?verbosity=summary&limit=20");
  test.skip(!runs.ok(), "backend has no runs");
  const ids: string[] = (await runs.json()).data?.map((r: { id: string }) => r.id) ?? [];

  let runWithReport: string | null = null;
  for (const id of ids) {
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

  // Versions nav populates
  await expect(page.locator(".rpt-version").first()).toBeVisible({ timeout: 15000 });
  // Markdown column renders headings
  await expect(page.locator(".rpt-read h1").first()).toBeVisible({ timeout: 15000 });
  // TOC has entries (either real or "no sections" fallback li)
  await expect(page.locator(".rpt-toc ol li").first()).toBeVisible();
  // Export buttons present
  await expect(page.getByRole("button", { name: /Export \.md/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Export \.pdf/ })).toBeVisible();
  // Right aside actions
  await expect(page.getByRole("link", { name: /Open in dashboard/i })).toBeVisible();
  // Raw markdown link
  await expect(page.getByRole("link", { name: /\/markdown$/i })).toBeVisible();

  assertNoErrors(errs);
});

test("report: clicking a version selects it (when multiple versions exist)", async ({ page, request }) => {
  const errs = attachErrorCapture(page);
  const runs = await request.get("/backend/api/agent/runs?verbosity=summary&limit=20");
  test.skip(!runs.ok(), "backend has no runs");
  const ids: string[] = (await runs.json()).data?.map((r: { id: string }) => r.id) ?? [];

  let runWithMultiple: string | null = null;
  for (const id of ids) {
    const reps = await request.get(`/backend/api/big-bangs/${id}/reports`);
    if (!reps.ok()) continue;
    const reports = await reps.json();
    if (!Array.isArray(reports)) continue;
    let totalVersions = 0;
    for (const r of reports) {
      const vs = await request.get(`/backend/api/reports/${r.id}/versions`);
      if (vs.ok()) totalVersions += (await vs.json()).length || 0;
    }
    if (totalVersions >= 2) {
      runWithMultiple = id;
      break;
    }
  }
  test.skip(!runWithMultiple, "no run has multiple report versions");

  await page.goto(`/report?run=${runWithMultiple}`);
  const versions = page.locator(".rpt-version");
  await expect(versions.first()).toBeVisible({ timeout: 15000 });
  const count = await versions.count();
  if (count >= 2) {
    await versions.nth(1).click();
    await expect(versions.nth(1)).toHaveClass(/is-active/);
  }

  assertNoErrors(errs);
});

test("report: export markdown triggers a download", async ({ page, request }) => {
  const errs = attachErrorCapture(page);
  const runs = await request.get("/backend/api/agent/runs?verbosity=summary&limit=20");
  test.skip(!runs.ok(), "backend has no runs");
  const ids: string[] = (await runs.json()).data?.map((r: { id: string }) => r.id) ?? [];

  let runWithReport: string | null = null;
  for (const id of ids) {
    const reps = await request.get(`/backend/api/big-bangs/${id}/reports`);
    if (!reps.ok()) continue;
    const list = await reps.json();
    if (Array.isArray(list) && list.length > 0) {
      runWithReport = id;
      break;
    }
  }
  test.skip(!runWithReport, "no reports");

  await page.goto(`/report?run=${runWithReport}`);
  await expect(page.locator(".rpt-version").first()).toBeVisible({ timeout: 15000 });

  const downloadPromise = page.waitForEvent("download", { timeout: 15000 });
  await page.getByRole("button", { name: /Export \.md/ }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.md$/);

  assertNoErrors(errs);
});

/* ============================================================
 *  Cross-page navigation
 * ============================================================ */

test("dashboard → report navigation works", async ({ page, request }) => {
  const errs = attachErrorCapture(page);
  const resp = await request.get("/backend/api/agent/runs?verbosity=summary&limit=10");
  test.skip(!resp.ok(), "backend has no runs");
  const body = await resp.json();
  let chosen: string | null = null;
  for (const r of body.data || []) {
    const mvs = await request.get(`/backend/api/big-bangs/${r.id}/multiverses`);
    if (!mvs.ok()) continue;
    const list = await mvs.json();
    if (Array.isArray(list) && list.length > 0) {
      chosen = r.id;
      break;
    }
  }
  test.skip(!chosen, "no run has multiverses");

  await page.goto(`/dashboard?run=${chosen}`);
  await expect(page.locator(".dash-top")).toBeVisible({ timeout: 10000 });
  await page.getByRole("link", { name: /Open report/i }).click();
  await expect(page).toHaveURL(new RegExp(`/report\\?run=${chosen}$`));

  assertNoErrors(errs);
});
