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
} from "./types";

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

  listRuns: (limit = 20) =>
    http<AgentEnvelope<RunSummary[]>>(
      `/api/agent/runs?verbosity=summary&limit=${limit}`,
    ),

  getBigBang: (id: string) => http<BigBang>(`/api/big-bangs/${id}`),

  createBigBang: (payload: CreateBigBangPayload) =>
    http<BigBang>("/api/big-bangs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  pauseBigBang: (id: string) =>
    http<BigBang>(`/api/big-bangs/${id}/pause`, { method: "POST" }),

  resumeBigBang: (id: string) =>
    http<BigBang>(`/api/big-bangs/${id}/resume`, { method: "POST" }),

  createRunUntilCompleteJob: (id: string, maxTotalTicks = 32) =>
    http<Job>("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        job_type: "run_big_bang_until_complete",
        big_bang_id: id,
        payload: { big_bang_id: id, max_total_ticks: maxTotalTicks },
        idempotency_key: `run-complete:${id}:${Date.now()}`,
      }),
    }),

  listJobs: (bigBangId: string, limit = 20) =>
    http<Job[]>(`/api/jobs?big_bang_id=${encodeURIComponent(bigBangId)}&limit=${limit}`),

  listMultiverses: (bigBangId: string) =>
    http<Multiverse[]>(`/api/big-bangs/${bigBangId}/multiverses`),

  getMultiverse: (id: string) => http<Multiverse>(`/api/multiverses/${id}`),

  listReports: (bigBangId: string) =>
    http<Report[]>(`/api/big-bangs/${bigBangId}/reports`),

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

  agentLogs: (limit = 30) =>
    http<AgentEnvelope<AgentLog[]>>(
      `/api/agent/logs?verbosity=summary&limit=${limit}`,
    ),
};

export { ApiError };
