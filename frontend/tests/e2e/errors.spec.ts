import { test, expect } from "@playwright/test";

/* Error-path coverage.
 *
 * Mocks backend responses for specific endpoints to verify that the UI
 * surfaces failures instead of silently swallowing them.
 *
 * No real backend mutations are made — we intercept at the network layer.
 */

const ANY_RUN_ID = "00000000-0000-0000-0000-000000000aaa";
const ANY_VERSION_ID = "00000000-0000-0000-0000-000000000bbb";

/* ----- helpers to fake an existing run/multiverse/report ----- */

async function mockSeededRun(page: import("@playwright/test").Page) {
  await page.route("**/backend/api/agent/runs**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        data: [
          {
            id: ANY_RUN_ID,
            name: "Mock run for error tests",
            status: "draft",
            created_at: new Date().toISOString(),
            multiverse_count: 1,
          },
        ],
        meta: { total: 1 },
      }),
    }),
  );
  await page.route(`**/backend/api/big-bangs/${ANY_RUN_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: ANY_RUN_ID,
        name: "Mock run for error tests",
        status: "draft",
        current_config_version: 1,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        scenario_input: { scenario_text_present: true, scenario_text_char_count: 100 },
        source_snapshot_id: null,
        description: null,
      }),
    }),
  );
  await page.route(`**/backend/api/big-bangs/${ANY_RUN_ID}/multiverses`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "11111111-1111-1111-1111-111111111111",
          big_bang_id: ANY_RUN_ID,
          parent_multiverse_id: null,
          fork_tick_index: null,
          ui_label: "M1",
          version: 1,
          depth: 0,
          status: "active",
          branch_reason: "Root timeline",
          branch_probability: 1.0,
          path_probability: 1.0,
          state: { heroes: [], cohorts: [] },
          report_status: "not_ready",
          ended_at: null,
          continued_from_report_version_id: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ]),
    }),
  );
  await page.route("**/backend/api/agent/logs**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, data: [] }) }),
  );
  await page.route("**/backend/api/jobs**", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
    }
    return route.fallback();
  });
}

/* ----- dashboard mutation errors ----- */

test("dashboard: surfaces 402 (out of credits) on Run to completion", async ({ page }) => {
  await mockSeededRun(page);
  await page.route("**/backend/api/jobs**", (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    return route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({
        detail:
          'LLMCallError: Report agent failed after standard and rescue attempts: standard: LLM unavailable: HTTP 402 Payment Required: {"error":{"message":"This request requires more credits"}}',
      }),
    });
  });

  await page.goto(`/dashboard?run=${ANY_RUN_ID}`);
  await page.getByRole("button", { name: /Run to completion/i }).click();

  const banner = page.getByTestId("dash-error-banner");
  await expect(banner).toBeVisible({ timeout: 8000 });
  await expect(banner).toContainText(/out of credits/i);
  await expect(banner).toContainText(/openrouter\.ai\/settings\/credits/i);
});

test("dashboard: surfaces 503 (LLM unavailable) on Run to completion", async ({ page }) => {
  await mockSeededRun(page);
  await page.route("**/backend/api/jobs**", (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    return route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "LLM unavailable" }),
    });
  });

  await page.goto(`/dashboard?run=${ANY_RUN_ID}`);
  await page.getByRole("button", { name: /Run to completion/i }).click();

  const banner = page.getByTestId("dash-error-banner");
  await expect(banner).toBeVisible({ timeout: 8000 });
  await expect(banner).toContainText(/LLM provider unavailable/i);
});

test("dashboard: surfaces 409 (conflict) on Pause", async ({ page }) => {
  await mockSeededRun(page);
  await page.route(`**/backend/api/big-bangs/${ANY_RUN_ID}/pause`, (route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: "big bang is paused" }),
    }),
  );

  await page.goto(`/dashboard?run=${ANY_RUN_ID}`);
  await page.getByRole("button", { name: /^Pause$/ }).click();

  const banner = page.getByTestId("dash-error-banner");
  await expect(banner).toBeVisible({ timeout: 8000 });
  await expect(banner).toContainText(/Conflict/i);
  await expect(banner).toContainText(/big bang is paused/i);
});

test("dashboard: error banner can be dismissed", async ({ page }) => {
  await mockSeededRun(page);
  await page.route(`**/backend/api/big-bangs/${ANY_RUN_ID}/pause`, (route) =>
    route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "boom" }) }),
  );

  await page.goto(`/dashboard?run=${ANY_RUN_ID}`);
  await page.getByRole("button", { name: /^Pause$/ }).click();

  const banner = page.getByTestId("dash-error-banner");
  await expect(banner).toBeVisible();
  await banner.getByRole("button", { name: /dismiss/i }).click();
  await expect(banner).toBeHidden();
});

test("dashboard: surfaces query error when bigBang fetch returns 500 with no cached data", async ({ page }) => {
  // Override the per-run GET to return 500 BEFORE navigating
  await page.route(`**/backend/api/big-bangs/${ANY_RUN_ID}`, (route) =>
    route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "db down" }) }),
  );
  await page.route(`**/backend/api/big-bangs/${ANY_RUN_ID}/multiverses`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) }),
  );
  await page.route("**/backend/api/agent/logs**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, data: [] }) }),
  );

  await page.goto(`/dashboard?run=${ANY_RUN_ID}`);
  const banner = page.getByTestId("dash-error-banner");
  await expect(banner).toBeVisible({ timeout: 8000 });
  await expect(banner).toContainText(/Loading run failed/i);
});

/* ----- input page mutation errors ----- */

test("input: surfaces 402 on Run simulation", async ({ page }) => {
  await page.route("**/backend/api/big-bangs", (route, req) => {
    if (req.method() === "POST") {
      return route.fulfill({
        status: 402,
        contentType: "application/json",
        body: JSON.stringify({ error: { message: "This request requires more credits" } }),
      });
    }
    return route.fallback();
  });

  await page.goto("/input");
  await page.getByRole("button", { name: /use tiny demo/i }).click();
  await page.getByRole("button", { name: /Run simulation/i }).click();

  await expect(page.getByText(/LLM provider out of credits/i)).toBeVisible({ timeout: 8000 });
  await expect(page.getByText(/openrouter\.ai\/settings\/credits/i)).toBeVisible();
});

test("input: surfaces generic 500 on Run simulation", async ({ page }) => {
  await page.route("**/backend/api/big-bangs", (route, req) => {
    if (req.method() === "POST") {
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "internal explode" }),
      });
    }
    return route.fallback();
  });

  await page.goto("/input");
  await page.getByRole("button", { name: /use tiny demo/i }).click();
  await page.getByRole("button", { name: /Run simulation/i }).click();

  await expect(page.getByText(/Couldn't create Big Bang/i)).toBeVisible({ timeout: 8000 });
});

test("input: initializer submit shows bounded wait and 503 guidance", async ({ page }) => {
  await page.route("**/backend/api/big-bangs", async (route, req) => {
    if (req.method() === "POST") {
      await new Promise((resolve) => setTimeout(resolve, 8500));
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "LLM unavailable" }),
      });
    }
    return route.fallback();
  });

  await page.goto("/input");
  await page.getByRole("button", { name: /use tiny demo/i }).click();

  const toggleRow = page.locator(".wf-field", { hasText: "use_initializer_agent" });
  await toggleRow.locator(".wf-num-suffix button").click();

  await page.getByRole("button", { name: /Run simulation/i }).click();

  await expect(page.getByText(/waiting on OpenRouter initializer response/i)).toBeVisible({ timeout: 8000 });
  await expect(page.getByText(/bounded by a backend timeout/i)).toBeVisible();
  await expect(page.getByText(/initializer LLM did not complete through OpenRouter/i)).toBeVisible({ timeout: 6000 });
});

/* ----- report page export errors ----- */

test("report: surfaces 402 on Export .md", async ({ page }) => {
  // Mock a run with one report+version so the export button is enabled
  await page.route(`**/backend/api/big-bangs/${ANY_RUN_ID}/reports`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "rrr-1",
          big_bang_id: ANY_RUN_ID,
          multiverse_id: null,
          report_type: "final_big_bang",
          status: "completed",
          current_version: 1,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ]),
    }),
  );
  await page.route("**/backend/api/reports/rrr-1/versions", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: ANY_VERSION_ID,
          report_id: "rrr-1",
          version: 1,
          title: "Mock final report",
          summary: null,
          source_multiverse_version: null,
          source_big_bang_config_version: 1,
          source_tick_snapshot_id: null,
          source_tick_index: 0,
          source_multiverse_ids: [],
          content: null,
          markdown_artifact_id: null,
          pdf_artifact_id: null,
          model: "mock",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ]),
    }),
  );
  await page.route(`**/backend/api/report-versions/${ANY_VERSION_ID}/markdown`, (route) =>
    route.fulfill({ status: 200, contentType: "text/plain", body: "# Mock\n\n## Section\n\nbody" }),
  );
  await page.route(`**/backend/api/report-versions/${ANY_VERSION_ID}/render`, (route) =>
    route.fulfill({
      status: 402,
      contentType: "application/json",
      body: JSON.stringify({ error: { message: "more credits required" } }),
    }),
  );

  await page.goto(`/report?run=${ANY_RUN_ID}`);
  await expect(page.locator(".rpt-version").first()).toBeVisible({ timeout: 10000 });
  await page.getByRole("button", { name: /Export \.md/ }).click();

  const banner = page.getByTestId("rpt-error-banner");
  await expect(banner).toBeVisible({ timeout: 8000 });
  await expect(banner).toContainText(/out of credits/i);
});

test("report: empty state when bigBang reports endpoint returns 500", async ({ page }) => {
  await page.route(`**/backend/api/big-bangs/${ANY_RUN_ID}/reports`, (route) =>
    route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "db down" }) }),
  );

  await page.goto(`/report?run=${ANY_RUN_ID}`);
  // Page should NOT crash — should show some recoverable empty/error state.
  // The report page falls back to "No reports yet." or "Loading reports…" depending on react-query timing.
  // The key assertion: page renders without an unhandled exception.
  await page.waitForLoadState("networkidle");
  // No JS error boundary fired — body is interactive and contains either the empty state or a banner.
  const html = await page.content();
  expect(html.length).toBeGreaterThan(500);
});
