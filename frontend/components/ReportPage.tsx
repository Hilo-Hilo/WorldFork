"use client";

import { isValidElement, useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { api, ApiError, CLIENT_BASE } from "@/lib/api";
import type { Report, ReportStatus, ReportVersion } from "@/lib/types";

function explainExportError(err: unknown, format: "md" | "pdf"): { title: string; detail: string } {
  if (err instanceof ApiError) {
    let parsed: { detail?: string; error?: { message?: string } } | null = null;
    try {
      parsed = JSON.parse(err.body);
    } catch {
      /* not JSON */
    }
    const inner = parsed?.detail || parsed?.error?.message || err.body || "";
    if (err.status === 402 || inner.toLowerCase().includes("more credits")) {
      return {
        title: "LLM provider out of credits",
        detail: `Rendering .${format} hit OpenRouter HTTP 402. Add credits at https://openrouter.ai/settings/credits and retry.`,
      };
    }
    return { title: `Export .${format} failed (HTTP ${err.status})`, detail: inner.slice(0, 600) || err.message };
  }
  return { title: `Export .${format} failed`, detail: (err as Error)?.message || String(err) };
}

/* ----------------------------------------------------------------------------
 * Report Viewer — wired to the backend.
 *
 * Reads ?run=<big-bang-id> from URL and (optionally) ?version=<version-id>.
 * Fetches:
 *   GET /api/big-bangs/<id>/reports
 *   GET /api/reports/<report-id>/versions   (for each report)
 *   GET /api/report-versions/<version-id>/markdown (active selection)
 * "Export .pdf" calls POST /api/report-versions/<id>/render.
 * Falls back to a clear empty state if no reports have been generated yet.
 * -------------------------------------------------------------------------- */

function BrandMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
      <circle cx="3" cy="9" r="1.4" fill="currentColor" />
      <circle cx="14" cy="3.5" r="1.4" />
      <circle cx="14" cy="9" r="1.4" />
      <circle cx="14" cy="14.5" r="1.4" fill="currentColor" />
      <path d="M4.4 9h3.5c1.5 0 2.5-1 3-2 .5-1 1.4-2 3-2" />
      <path d="M4.4 9h8.2" />
      <path d="M4.4 9h3.5c1.5 0 2.5 1 3 2 .5 1 1.4 2 3 2" />
    </svg>
  );
}

function EmptyState({ runId, message }: { runId?: string; message: string }) {
  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 14,
        background: "var(--bg)",
        color: "var(--fg)",
      }}
    >
      <h1 style={{ fontSize: 22, fontWeight: 500, margin: 0 }}>No report.</h1>
      <p style={{ color: "var(--muted)", maxWidth: "44ch", textAlign: "center", margin: 0, fontSize: 13.5 }}>
        {message}
      </p>
      <div style={{ display: "flex", gap: 8 }}>
        <Link
          href="/"
          style={{
            border: "1px solid var(--border)",
            color: "var(--fg-2)",
            padding: "8px 14px",
            borderRadius: 6,
            fontSize: 13,
            textDecoration: "none",
          }}
        >
          Runs
        </Link>
        {runId && (
          <Link
            href={`/dashboard?run=${runId}`}
            style={{
              background: "var(--accent)",
              color: "var(--accent-fg)",
              padding: "8px 14px",
              borderRadius: 6,
              fontSize: 13,
              fontWeight: 600,
              textDecoration: "none",
            }}
          >
            Back to dashboard
          </Link>
        )}
      </div>
    </div>
  );
}

function ReportProgressState({ runId, status }: { runId: string; status?: ReportStatus }) {
  const stage = status?.stage || "waiting";
  const total = status?.multiverse_reports.total || 0;
  const completed = status?.multiverse_reports.completed || 0;
  const active = status?.active_llm_call || status?.active_job;
  return (
    <div className="rpt-progress-page">
      <div className="rpt-progress-panel">
        <div className="rpt-progress-kicker">report generation</div>
        <h1>{stageTitle(stage)}</h1>
        <p>{status?.message || "Waiting for report generation status..."}</p>
        <ReportStageRail stage={stage} />
        <div className="rpt-progress-grid">
          <div>
            <span className="k">single-universe reports</span>
            <span className="v">{completed}/{total || "..."}</span>
          </div>
          <div>
            <span className="k">final multiverse report</span>
            <span className="v">{status?.final_report.has_version ? "ready" : status?.final_report.status || "pending"}</span>
          </div>
          <div>
            <span className="k">run status</span>
            <span className="v">{status?.big_bang.status || "..."}</span>
          </div>
        </div>
        {active && (
          <div className="rpt-active-work">
            <span className="k">active work</span>
            <span className="v">{String(active.purpose || active.job_type || "backend work")}</span>
            <span className="s">{String(active.status || "")}</span>
          </div>
        )}
        <div className="rpt-progress-actions">
          <Link href={`/dashboard?run=${runId}`}>Back to dashboard</Link>
          <Link href="/">Runs</Link>
        </div>
      </div>
    </div>
  );
}

function ReportProgressBanner({ status }: { status?: ReportStatus }) {
  if (!status || status.stage === "ready") return null;
  return (
    <div className="rpt-progress-banner">
      <div>
        <span className="k">{stageTitle(status.stage)}</span>
        <span className="v">{status.message}</span>
      </div>
      <ReportStageRail stage={status.stage} compact />
    </div>
  );
}

function ReportStageRail({ stage, compact = false }: { stage: string; compact?: boolean }) {
  const stages = [
    ["simulation", "Simulation"],
    ["single_reports", "Single reports"],
    ["predicate_resolution", "Predicates"],
    ["final_report", "Final report"],
    ["ready", "Ready"],
  ];
  const activeIndex = stages.findIndex(([id]) => id === stage);
  return (
    <div className={`rpt-stage-rail ${compact ? "is-compact" : ""}`}>
      {stages.map(([id, label], index) => (
        <div
          key={id}
          className={`rpt-stage ${activeIndex >= 0 && index < activeIndex ? "is-done" : ""} ${index === activeIndex ? "is-active" : ""}`}
        >
          <span className="dot" />
          <span>{label}</span>
        </div>
      ))}
    </div>
  );
}

function stageTitle(stage: string): string {
  if (stage === "simulation") return "Simulation still running";
  if (stage === "single_reports") return "Generating single-universe reports";
  if (stage === "predicate_resolution") return "Resolving final question";
  if (stage === "final_report") return "Generating final multiverse report";
  if (stage === "ready") return "Final report ready";
  if (stage === "failed") return "Report generation failed";
  return "Waiting for reports";
}

function ViewerSummaryCard({
  summary,
  onCopySummary,
  onCopyLink,
  copied,
}: {
  summary: ViewerSummary | null;
  onCopySummary: () => void;
  onCopyLink: () => void;
  copied: string | null;
}) {
  if (!summary) return null;
  const isFinal = summary.scope === "final_multiverse";
  return (
    <section className={`rpt-summary-card ${isFinal ? "is-final" : "is-single"}`}>
      <div className="rpt-summary-head">
        <div>
          <span className="rpt-summary-scope">{isFinal ? "final multiverse" : "single universe"}</span>
          <h1>{isFinal ? `Most likely: ${summary.most_likely_result || "Unresolved"}` : `${summary.timeline_label || "Timeline"} branch report`}</h1>
          {summary.question && <p className="question">{summary.question}</p>}
        </div>
        <div className="rpt-summary-actions">
          <button onClick={onCopySummary} type="button">{copied === "summary" ? "Copied" : "Copy X summary"}</button>
          <button onClick={onCopyLink} type="button">{copied === "link" ? "Copied" : "Copy link"}</button>
        </div>
      </div>
      {isFinal ? (
        <>
          <div className="rpt-summary-stats">
            <div><span className="k">YES</span><span className="v">{percent(summary.p_yes)}</span></div>
            <div><span className="k">NO</span><span className="v">{percent(summary.p_no)}</span></div>
            <div><span className="k">confidence</span><span className="v">{confidenceLabel(summary.confidence)}</span></div>
            <div><span className="k">retained timelines</span><span className="v">{summary.retained_timeline_count ?? "-"}/{summary.total_timeline_count ?? "-"}</span></div>
          </div>
          {summary.top_timelines && summary.top_timelines.length > 0 && (
            <div className="rpt-summary-drivers">
              {summary.top_timelines.slice(0, 3).map((item, index) => (
                <div key={`${item.multiverse_id || index}`} className="driver">
                  <span className="rank">{index + 1}</span>
                  <span className="label">{String(item.ui_label || "timeline")}</span>
                  <span className="mass">{percent(item.effective_path_probability)}</span>
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <div className="rpt-summary-stats">
          <div><span className="k">outcome</span><span className="v">{summary.outcome || "unknown"}</span></div>
          <div><span className="k">path probability</span><span className="v">{percent(summary.path_probability)}</span></div>
          <div><span className="k">branch probability</span><span className="v">{percent(summary.branch_probability)}</span></div>
          <div><span className="k">latest tick</span><span className="v">{summary.latest_tick_index ?? "-"}</span></div>
        </div>
      )}
      {summary.scope_note && <p className="note">{summary.scope_note}</p>}
      {summary.branch_reason && <p className="note">Branch reason: {summary.branch_reason}</p>}
    </section>
  );
}

type FlatVersion = {
  reportId: string;
  reportType: Report["report_type"];
  version: ReportVersion;
};

type ViewerSummary = {
  scope: "single_universe" | "final_multiverse" | string;
  title?: string | null;
  question?: string | null;
  timeline_label?: string | null;
  outcome?: string | null;
  most_likely_result?: string | null;
  p_yes?: number | null;
  p_no?: number | null;
  confidence?: number | null;
  resolution_state?: string | null;
  path_probability?: number | null;
  branch_probability?: number | null;
  branch_reason?: string | null;
  latest_tick_index?: number | null;
  retained_timeline_count?: number | null;
  total_timeline_count?: number | null;
  top_timelines?: Array<Record<string, unknown>>;
  rationale?: string | null;
  scope_note?: string | null;
  share_text?: string | null;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function percent(value: unknown): string {
  return `${Math.round(asNumber(value) * 100)}%`;
}

function confidenceLabel(value: unknown): string {
  const n = asNumber(value);
  if (n >= 0.75) return "high";
  if (n >= 0.4) return "medium";
  return "low";
}

function shortText(value: unknown, limit = 160): string {
  const text = String(value || "").trim();
  if (text.length <= limit) return text;
  return text.slice(0, limit - 1).trimEnd() + "...";
}

function getViewerSummary(active: FlatVersion | null): ViewerSummary | null {
  if (!active) return null;
  const content = asRecord(active.version.content);
  const existing = asRecord(content.viewer_summary);
  if (existing.scope) return existing as ViewerSummary;
  return active.reportType === "final_big_bang"
    ? fallbackFinalSummary(content, active.version.title)
    : fallbackSingleSummary(content, active.version.title);
}

function fallbackFinalSummary(content: Record<string, unknown>, title: string): ViewerSummary {
  const forecast = asRecord(content.forecast_predictions);
  const primary = asRecord(forecast.primary);
  const pYes = asNumber(primary.p_yes, 0.5);
  const pNo = asNumber(primary.p_no, 1 - pYes);
  const confidence = asNumber(primary.confidence, 0);
  const topTimelines = deriveTopTimelines(content);
  const answer = confidence < 0.05 || Math.abs(pYes - pNo) < 0.02 ? "Unresolved" : pYes > pNo ? "Yes" : "No";
  const summary: ViewerSummary = {
    scope: "final_multiverse",
    title,
    question: typeof content.scenario_question === "string" ? content.scenario_question : null,
    most_likely_result: answer,
    p_yes: pYes,
    p_no: pNo,
    confidence,
    resolution_state: String(primary.resolution_state || ""),
    retained_timeline_count: topTimelines.filter((item) => item.include_in_final !== false).length,
    total_timeline_count: Array.isArray(content.multiverse_comparison) ? content.multiverse_comparison.length : topTimelines.length,
    top_timelines: topTimelines.slice(0, 4),
    rationale: typeof primary.rationale === "string" ? primary.rationale : null,
    scope_note: "This is the final multiverse report: probabilities come from retained timeline path mass.",
  };
  summary.share_text = finalShareText(summary);
  return summary;
}

function fallbackSingleSummary(content: Record<string, unknown>, title: string): ViewerSummary {
  const source = asRecord(content.source);
  const outcome = asRecord(content.outcome_distribution);
  const summary: ViewerSummary = {
    scope: "single_universe",
    title,
    question: typeof content.scenario_question === "string" ? content.scenario_question : null,
    timeline_label: String(source.ui_label || outcome.ui_label || "Timeline"),
    outcome: String(outcome.status || source.status || "unknown"),
    path_probability: asNumber(outcome.path_probability ?? source.path_probability),
    branch_probability: asNumber(outcome.branch_probability ?? source.branch_probability),
    branch_reason: typeof (outcome.branch_reason ?? source.branch_reason) === "string" ? String(outcome.branch_reason ?? source.branch_reason) : null,
    latest_tick_index: typeof source.source_tick_index === "number" ? source.source_tick_index : asNumber(outcome.latest_tick_index, 0),
    scope_note: "This is one branch report, not the global multiverse forecast.",
  };
  summary.share_text = singleShareText(summary);
  return summary;
}

function deriveTopTimelines(content: Record<string, unknown>): Array<Record<string, unknown>> {
  const comparison = Array.isArray(content.multiverse_comparison) ? content.multiverse_comparison.map(asRecord) : [];
  const comparisonById = new Map(comparison.map((item) => [String(item.multiverse_id || ""), item]));
  const adjudication = asRecord(content.timeline_adjudication);
  const entries = Array.isArray(adjudication.entries) ? adjudication.entries.map(asRecord) : [];
  const rows = entries.length
    ? entries.map((entry) => {
        const cmp = comparisonById.get(String(entry.multiverse_id || "")) || {};
        return {
          ...entry,
          ui_label: entry.ui_label || cmp.ui_label,
          branch_reason: cmp.branch_reason,
          effective_path_probability: asNumber(entry.effective_path_probability, asNumber(cmp.path_probability)),
        };
      })
    : comparison.map((item) => ({
        multiverse_id: item.multiverse_id,
        ui_label: item.ui_label,
        include_in_final: true,
        branch_reason: item.branch_reason,
        effective_path_probability: asNumber(item.path_probability),
      }));
  return rows
    .sort((a, b) => asNumber(b.effective_path_probability) - asNumber(a.effective_path_probability));
}

function finalShareText(summary: ViewerSummary): string {
  const question = summary.question ? `"${shortText(summary.question, 180)}"` : "the primary question";
  const top = summary.top_timelines?.[0];
  const driver = top ? `\nTop path-mass driver: ${top.ui_label || "timeline"} (${percent(top.effective_path_probability)}).` : "";
  return [
    `WorldFork final multiverse report for: ${question}`,
    "",
    `Most likely result: ${summary.most_likely_result || "Unresolved"}`,
    `YES ${percent(summary.p_yes)} / NO ${percent(summary.p_no)}, ${confidenceLabel(summary.confidence)} confidence.${driver}`,
  ].join("\n");
}

function singleShareText(summary: ViewerSummary): string {
  const reason = summary.branch_reason ? `\nKey branch: ${shortText(summary.branch_reason, 160)}` : "";
  return [
    `WorldFork single-universe report: ${summary.timeline_label || "timeline"}`,
    "",
    `Outcome in this branch: ${summary.outcome || "unknown"}`,
    `Path probability: ${percent(summary.path_probability)}.${reason}`,
  ].join("\n");
}

/** Walk a React children tree and concatenate the plain text. Needed so heading
 * IDs match the TOC slugs even when the heading contains inline markdown
 * (emphasis, code, links) — String(children) on a node tree returns
 * "[object Object]" which diverges from the TOC's text-based slugs. */
function extractText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (isValidElement(node)) {
    const props = node.props as { children?: ReactNode };
    return extractText(props.children);
  }
  return "";
}

/** Strip the most common inline markdown syntax so a TOC label parsed from
 * raw markdown matches the rendered text content that extractText() returns
 * on the <h2>. Keeps the slug derivation symmetric on both sides. */
function stripInlineMarkdown(text: string): string {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/_([^_]+)_/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .trim();
}

function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

export default function ReportPage({
  runId,
  initialVersionId,
}: {
  runId?: string;
  initialVersionId?: string;
}) {
  if (!runId) return <EmptyState message="No run selected. Open this page from a dashboard or runs list." />;

  return <ReportPageWired runId={runId} initialVersionId={initialVersionId} />;
}

function ReportPageWired({
  runId,
  initialVersionId,
}: {
  runId: string;
  initialVersionId?: string;
}) {
  const [activeVersionId, setActiveVersionId] = useState<string | null>(initialVersionId ?? null);

  useEffect(() => {
    setActiveVersionId(initialVersionId ?? null);
  }, [initialVersionId, runId]);

  const reportsQ = useQuery({
    queryKey: ["reports", runId],
    queryFn: () => api.listReports(runId),
    refetchInterval: 4000,
  });

  const reportStatusQ = useQuery({
    queryKey: ["reportStatus", runId],
    queryFn: () => api.getReportStatus(runId),
    refetchInterval: 3000,
  });
  const reportStatus = reportStatusQ.data;

  const reports = reportsQ.data || [];

  // Fetch versions for each report in parallel
  const versionQueries = useQueries({
    queries: reports.map((r) => ({
      queryKey: ["reportVersions", r.id],
      queryFn: () => api.listReportVersions(r.id),
      staleTime: 30000,
      refetchInterval: 5000,
    })),
  });

  const flatVersions: FlatVersion[] = useMemo(() => {
    const out: FlatVersion[] = [];
    reports.forEach((r, idx) => {
      const vs = versionQueries[idx]?.data || [];
      vs.forEach((v) => out.push({ reportId: r.id, reportType: r.report_type, version: v }));
    });
    out.sort((a, b) => {
      // Final reports first, then by created_at desc
      if (a.reportType === "final_big_bang" && b.reportType !== "final_big_bang") return -1;
      if (b.reportType === "final_big_bang" && a.reportType !== "final_big_bang") return 1;
      return b.version.created_at.localeCompare(a.version.created_at);
    });
    return out;
  }, [reports, versionQueries]);

  // Auto-select the first (most relevant) version, but do not hold on to a stale
  // query-param or user selection after the available version set changes.
  const activeId =
    activeVersionId && flatVersions.some((v) => v.version.id === activeVersionId)
      ? activeVersionId
      : flatVersions[0]?.version.id;
  const active = flatVersions.find((v) => v.version.id === activeId) || null;
  const viewerSummary = getViewerSummary(active);
  const [copied, setCopied] = useState<string | null>(null);

  const markdownQ = useQuery({
    queryKey: ["reportMarkdown", activeId],
    queryFn: () => api.getReportMarkdown(activeId!),
    enabled: !!activeId,
    refetchInterval: false,
    staleTime: 60_000,
  });

  const triggerDownload = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const exportPdf = useMutation({
    mutationFn: () => api.renderReport(activeId!, "pdf"),
    onSuccess: ({ blob, filename }) => triggerDownload(blob, filename),
  });

  const exportMd = useMutation({
    mutationFn: () => api.renderReport(activeId!, "md"),
    onSuccess: ({ blob, filename }) => triggerDownload(blob, filename),
  });

  const copyText = async (kind: "summary" | "link", text: string) => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard API unavailable");
      await navigator.clipboard.writeText(text);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "true");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    setCopied(kind);
    window.setTimeout(() => setCopied(null), 1800);
  };

  const reportLink =
    typeof window === "undefined"
      ? `/report?run=${runId}${activeId ? `&version=${activeId}` : ""}`
      : `${window.location.origin}/report?run=${runId}${activeId ? `&version=${activeId}` : ""}`;

  // Build TOC by parsing markdown headings — must be called before any early returns
  const md = (markdownQ.data as string) || "";
  const tocItems = useMemo(() => {
    const items: { id: string; label: string }[] = [];
    md.split("\n").forEach((line) => {
      const m = /^##\s+(.+)/.exec(line);
      if (m) {
        // Strip inline markdown so the slug matches what extractText() will
        // produce on the rendered <h2>. Without this, `## **Bold** title`
        // would slug to "bold-title" here but "object-object-title" in the
        // h2 renderer (or some other divergence), breaking deep-link nav.
        const plain = stripInlineMarkdown(m[1].trim());
        items.push({ id: slugify(plain), label: plain });
      }
    });
    return items;
  }, [md]);

  if (reportsQ.isLoading) {
    return (
      <ReportProgressState runId={runId} status={reportStatus} />
    );
  }

  // Surface fetch failures explicitly so an outage doesn't masquerade as
  // "no reports yet" — that messaging tells the user to re-run the
  // simulation, which is the wrong remediation when the backend is down.
  if (reportsQ.isError) {
    const reason = (reportsQ.error as Error | undefined)?.message
      ?? "unknown error";
    return (
      <EmptyState
        runId={runId}
        message={`Could not load reports for this run: ${reason}. Check the backend (/readyz) and reload.`}
      />
    );
  }

  if (!reportsQ.isLoading && reports.length === 0) {
    return <ReportProgressState runId={runId} status={reportStatus} />;
  }

  if (!active && flatVersions.length === 0) {
    return <ReportProgressState runId={runId} status={reportStatus} />;
  }

  const exportErr = exportPdf.error
    ? { e: exportPdf.error, fmt: "pdf" as const, reset: () => exportPdf.reset() }
    : exportMd.error
      ? { e: exportMd.error, fmt: "md" as const, reset: () => exportMd.reset() }
      : null;

  return (
    <div className="rpt" data-screen-label="03 Report viewer">
      {/* TOP */}
      <header className="rpt-top">
        <div className="left">
          <span style={{ color: "var(--accent)", display: "inline-flex" }}>
            <BrandMark />
          </span>
          <Link href="/" style={{ color: "inherit", textDecoration: "none", fontWeight: 600 }}>
            worldfork
          </Link>
          <span className="sep">/</span>
          <Link href={`/dashboard?run=${runId}`} className="name" style={{ color: "var(--fg)", textDecoration: "none" }}>
            run {runId.slice(0, 8)}…
          </Link>
          <span className="sep">/</span>
          <span className="meta">reports</span>
        </div>
        <div className="center">
          {active && (
            <>
              <span>
                report&nbsp;<b>{active.reportType === "final_big_bang" ? "final" : "mv"}.v{active.version.version}</b>
              </span>
              <span>
                rendered&nbsp;<b>{new Date(active.version.updated_at).toLocaleTimeString()}</b>
              </span>
              <span>
                model&nbsp;<b>{active.version.model || "—"}</b>
              </span>
            </>
          )}
        </div>
        <div className="right">
          <button className="dash-btn" onClick={() => exportMd.mutate()} disabled={!active || exportMd.isPending}>
            {exportMd.isPending ? "…" : "Export .md"}
          </button>
          <button className="dash-btn is-primary" onClick={() => exportPdf.mutate()} disabled={!active || exportPdf.isPending}>
            {exportPdf.isPending ? "rendering…" : "Export .pdf"}
          </button>
        </div>
      </header>

      <div className="rpt-notices">
        <ReportProgressBanner status={reportStatus} />

        {exportErr && (() => {
          const { title, detail } = explainExportError(exportErr.e, exportErr.fmt);
          return (
            <div
              data-testid="rpt-error-banner"
              style={{
                margin: "10px 14px",
                padding: "10px 14px",
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderLeft: "2px solid var(--danger)",
                borderRadius: 4,
                display: "flex",
                gap: 12,
                alignItems: "flex-start",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "var(--danger)",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 700, marginBottom: 3 }}>{title}</div>
                <div style={{ color: "var(--fg-2)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{detail}</div>
              </div>
              <button
                onClick={exportErr.reset}
                style={{
                  background: "transparent",
                  border: "1px solid var(--border)",
                  color: "var(--muted)",
                  padding: "2px 8px",
                  borderRadius: 3,
                  cursor: "pointer",
                  fontSize: 11,
                  fontFamily: "var(--font-mono)",
                }}
              >
                dismiss
              </button>
            </div>
          );
        })()}
      </div>

      {/* BODY */}
      <div className="rpt-body">
        {/* LEFT NAV */}
        <aside className="rpt-nav">
          <div className="rpt-nav-section">
            <h3>Versions</h3>
            {flatVersions.map((fv) => {
              const isFinal = fv.reportType === "final_big_bang";
              const isActive = fv.version.id === activeId;
              return (
                <div
                  key={fv.version.id}
                  className={`rpt-version ${isActive ? "is-active" : ""} ${isFinal ? "final" : ""}`}
                  onClick={() => setActiveVersionId(fv.version.id)}
                >
                  <div>
                    <div className="name">{fv.version.title}</div>
                    <div className="meta">
                      <span>{new Date(fv.version.created_at).toLocaleString()}</span>
                      <span className="sep">·</span>
                      <span>{isFinal ? "final" : "multiverse"}</span>
                    </div>
                  </div>
                  <span className="ver">v{fv.version.version}</span>
                </div>
              );
            })}
          </div>
          <div className="rpt-toc">
            <h3>Contents</h3>
            <ol>
              {tocItems.length === 0 && (
                <li style={{ borderLeft: "none", color: "var(--muted-2)" }}>(no sections)</li>
              )}
              {tocItems.map((t) => (
                <li
                  key={t.id}
                  className="depth-1"
                  onClick={() => {
                    const el = document.getElementById(t.id);
                    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
                  }}
                >
                  {t.label}
                </li>
              ))}
            </ol>
          </div>
        </aside>

        {/* CENTER: read */}
        <article className="rpt-read">
          <div className="rpt-read-inner">
            <ViewerSummaryCard
              summary={viewerSummary}
              copied={copied}
              onCopySummary={() => copyText("summary", `${viewerSummary?.share_text || ""}\n\n${reportLink}`.trim())}
              onCopyLink={() => copyText("link", reportLink)}
            />
            {markdownQ.isLoading && (
              <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>loading…</div>
            )}
            {markdownQ.error && (
              <div
                style={{
                  border: "1px solid var(--border)",
                  borderLeft: "2px solid var(--danger)",
                  padding: "14px 16px",
                  borderRadius: 4,
                  color: "var(--danger)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                }}
              >
                {(markdownQ.error as Error).message}
              </div>
            )}
            {md && (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  h2: ({ children }) => {
                    // Walk the React node tree to extract plain text.
                    // String(children) returns "[object Object]" for any
                    // heading that contains inline markdown (emphasis, code,
                    // links), which would diverge from the TOC slugs (built
                    // from raw markdown text) and break in-page anchors.
                    const id = slugify(extractText(children));
                    return <h2 id={id}>{children}</h2>;
                  },
                }}
              >
                {md}
              </ReactMarkdown>
            )}
          </div>
        </article>

        {/* RIGHT: aside */}
        <aside className="rpt-aside">
          <div className="rpt-aside-section">
            <h4>
              Render <span className="hint">artifact</span>
            </h4>
            <div className="rpt-render-status">
              <span className="dot" />
              <span>
                {active?.version.markdown_artifact_id ? "markdown ready" : "no markdown artifact yet"}
                {active?.version.pdf_artifact_id ? "  ·  pdf ready" : ""}
              </span>
            </div>
            {(exportPdf.isSuccess || exportMd.isSuccess) && (
              <div
                style={{
                  marginTop: 10,
                  padding: "10px 12px",
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--accent)",
                }}
              >
                downloaded &nbsp;·&nbsp; check your browser download tray
              </div>
            )}
          </div>

          {active?.version.source_multiverse_ids && active.version.source_multiverse_ids.length > 0 && (
            <div className="rpt-aside-section">
              <h4>
                Source multiverses <span className="hint">{active.version.source_multiverse_ids.length}</span>
              </h4>
              <div className="outcome-list">
                {active.version.source_multiverse_ids.slice(0, 8).map((id) => (
                  <div key={id} className="outcome-item">
                    <span className="id">{String(id).slice(0, 6)}</span>
                    <span className="label">{String(id).slice(0, 24)}…</span>
                    <span className="pct">—</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="rpt-aside-section">
            <h4>Actions</h4>
            <div className="rpt-actions">
              <Link href={`/dashboard?run=${runId}`} className="rpt-action" style={{ textDecoration: "none" }}>
                Open in dashboard<span className="kbd">⌘D</span>
              </Link>
              <button className="rpt-action" onClick={() => exportPdf.mutate()} disabled={!active || exportPdf.isPending}>
                Render PDF<span className="kbd">⌘P</span>
              </button>
              <button
                className="rpt-action"
                onClick={() => copyText("summary", `${viewerSummary?.share_text || ""}\n\n${reportLink}`.trim())}
                disabled={!viewerSummary}
              >
                Copy X summary<span className="kbd">{copied === "summary" ? "done" : "⌘C"}</span>
              </button>
              <button
                className="rpt-action"
                onClick={() => copyText("link", reportLink)}
              >
                Copy report link<span className="kbd">{copied === "link" ? "done" : "link"}</span>
              </button>
            </div>
          </div>

          {/* tiny debug aid: link to the raw markdown via the proxy */}
          {active && (
            <div className="rpt-aside-section">
              <h4>Raw</h4>
              <a
                href={`${CLIENT_BASE}/api/report-versions/${active.version.id}/markdown`}
                target="_blank"
                rel="noreferrer"
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--muted)",
                  textDecoration: "underline",
                  textUnderlineOffset: 3,
                }}
              >
                /report-versions/{active.version.id.slice(0, 8)}…/markdown
              </a>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
