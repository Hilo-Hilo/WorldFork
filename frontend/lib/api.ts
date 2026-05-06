/* WorldFork API client.
 *
 * Server-side: hits the backend directly via API_BASE_URL.
 * Client-side: hits a Next.js rewrite at /backend/* so the browser can talk
 *   to a backend that's bound to Windows-side localhost or a different host.
 *   See next.config.mjs for the rewrite rule.
 */

import type {
  AgentEnvelope,
  AgentLog,
  BigBang,
  CreateBigBangPayload,
  Job,
  Multiverse,
  ReadyzResult,
  Report,
  ReportVersion,
  RunSummary,
  Tick,
} from "./types";
import {
  DEMO_RUN_ID,
  demoAgentLogsEnvelope,
  demoBigBang,
  demoJobs,
  demoMultiverses,
  demoMultiverseTicks,
  demoRunsListEnvelope,
  isDemoRun,
} from "./demo";

const SERVER_BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  process.env.API_BASE_URL ||
  "http://127.0.0.1:8003";

/** Used by client components — proxied through Next.js to avoid mixed-host issues. */
export const CLIENT_BASE = "/backend";

/** Base URL appropriate for the current execution context. */
function base(): string {
  return typeof window === "undefined" ? SERVER_BASE : CLIENT_BASE;
}

class ApiError extends Error {
  status: number;
  body: string;
  constructor(message: string, status: number, body: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(base() + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(
      `${res.status} ${res.statusText} ${path}`,
      res.status,
      body,
    );
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

/* ===== endpoints ===== */

export const api = {
  // /readyz returns 503 on degraded state with a meaningful JSON body
  // ({ok:false, checks:{...}}). Parse it instead of throwing so the UI
  // can render "degraded" rather than a hard "unreachable" error.
  readyz: async (): Promise<ReadyzResult> => {
    const res = await fetch(base() + "/readyz", { cache: "no-store" });
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      return (await res.json()) as ReadyzResult;
    }
    if (!res.ok) {
      throw new ApiError(`${res.status} ${res.statusText} /readyz`, res.status, await res.text());
    }
    return { ok: true, checks: {} } as ReadyzResult;
  },

  listRuns: async (limit = 20) => {
    // The runs page shows the demo run alongside any real runs. We always merge,
    // so it's discoverable even after the user has created real scenarios.
    // Errors are no longer swallowed here — the runs page surfaces them as a
    // banner and renders demo-only as the fallback so backend outages don't
    // masquerade as "no runs yet".
    const real = await http<AgentEnvelope<RunSummary[]>>(
      `/api/agent/runs?verbosity=summary&limit=${limit}`,
    );
    const demo = demoRunsListEnvelope(limit);
    return {
      ...real,
      data: [...demo.data, ...real.data],
    };
  },

  getBigBang: (id: string) =>
    isDemoRun(id) ? Promise.resolve(demoBigBang()) : http<BigBang>(`/api/big-bangs/${id}`),

  createBigBang: (payload: CreateBigBangPayload) =>
    http<BigBang>("/api/big-bangs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  pauseBigBang: (id: string) =>
    isDemoRun(id) ? Promise.resolve(demoBigBang()) : http<BigBang>(`/api/big-bangs/${id}/pause`, { method: "POST" }),

  resumeBigBang: (id: string) =>
    isDemoRun(id) ? Promise.resolve(demoBigBang()) : http<BigBang>(`/api/big-bangs/${id}/resume`, { method: "POST" }),

  createRunUntilCompleteJob: (id: string, maxTotalTicks = 32) =>
    isDemoRun(id)
      ? Promise.resolve(demoJobs()[0])
      : http<Job>("/api/jobs", {
          method: "POST",
          body: JSON.stringify({
            job_type: "run_big_bang_until_complete",
            big_bang_id: id,
            payload: { big_bang_id: id, max_total_ticks: maxTotalTicks },
            // Stable per (run, max_ticks) so async double-clicks dedupe to
            // a single job. Backend (jobs/routes.py:108) returns the existing
            // record on key collision and re-enqueues if it has stalled.
            idempotency_key: `run-complete:${id}:${maxTotalTicks}`,
          }),
        }),

  listJobs: (bigBangId: string, limit = 20) =>
    isDemoRun(bigBangId)
      ? Promise.resolve(demoJobs())
      : http<Job[]>(`/api/jobs?big_bang_id=${encodeURIComponent(bigBangId)}&limit=${limit}`),

  listMultiverses: (bigBangId: string) =>
    isDemoRun(bigBangId)
      ? Promise.resolve(demoMultiverses())
      : http<Multiverse[]>(`/api/big-bangs/${bigBangId}/multiverses`),

  getMultiverse: (id: string) =>
    id.startsWith("demo-")
      ? Promise.resolve(demoMultiverses().find((m) => m.id === id) as Multiverse)
      : http<Multiverse>(`/api/multiverses/${id}`),

  listMultiverseTicks: (id: string) =>
    id.startsWith("demo-")
      ? Promise.resolve(demoMultiverseTicks(id))
      : http<{ ticks: Tick[] } | Tick[]>(`/api/multiverses/${id}/ticks`).then((r) =>
          Array.isArray(r) ? r : r.ticks,
        ),

  // Demo runs are intentionally backend-less; return an empty list so
  // ReportPage falls into the "No reports yet" branch rather than firing a
  // live request that would 404 in offline / no-backend demo sessions.
  listReports: (bigBangId: string) =>
    isDemoRun(bigBangId)
      ? Promise.resolve([] as Report[])
      : http<Report[]>(`/api/big-bangs/${bigBangId}/reports`),

  listReportVersions: (reportId: string) =>
    http<ReportVersion[]>(`/api/reports/${reportId}/versions`),

  getReportVersion: (versionId: string) =>
    http<ReportVersion>(`/api/report-versions/${versionId}`),

  getReportMarkdown: (versionId: string) =>
    http<string>(`/api/report-versions/${versionId}/markdown`),

  renderReport: async (versionId: string, format: "pdf" | "md" = "pdf"): Promise<{ blob: Blob; filename: string }> => {
    const apiFormat = format === "md" ? "markdown" : "pdf";
    const res = await fetch(`${base()}/api/report-versions/${versionId}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: apiFormat }),
      cache: "no-store",
    });
    if (!res.ok) {
      const body = await res.text();
      throw new ApiError(`${res.status} ${res.statusText} render`, res.status, body);
    }
    const blob = await res.blob();
    const cd = res.headers.get("content-disposition") || "";
    const m = /filename="([^"]+)"/.exec(cd);
    const filename = m?.[1] || `report-${versionId.slice(0, 8)}.${format}`;
    return { blob, filename };
  },

  // agentLogs is global (not run-scoped). When the dashboard is showing the
  // demo run it should see only demo log entries; otherwise it gets the real
  // global feed merged with the demo entries (so the demo always has signal in
  // the live-log strip). Detection uses the URL since this is client-side.
  agentLogs: async (limit = 30): Promise<AgentEnvelope<AgentLog[]>> => {
    const onDemoDashboard =
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).get("run") === DEMO_RUN_ID;
    if (onDemoDashboard) return demoAgentLogsEnvelope(limit);
    const real = await http<AgentEnvelope<AgentLog[]>>(
      `/api/agent/logs?verbosity=summary&limit=${limit}`,
    ).catch(() => ({ ok: true, data: [], meta: {} }) as AgentEnvelope<AgentLog[]>);
    return real;
  },
};

export { ApiError };
