"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { api, CLIENT_BASE } from "@/lib/api";
import type { Report, ReportVersion } from "@/lib/types";

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

type FlatVersion = {
  reportId: string;
  reportType: Report["report_type"];
  version: ReportVersion;
};

export default function ReportPage({ runId }: { runId?: string }) {
  if (!runId) return <EmptyState message="No run selected. Open this page from a dashboard or runs list." />;

  return <ReportPageWired runId={runId} />;
}

function ReportPageWired({ runId }: { runId: string }) {
  const [activeVersionId, setActiveVersionId] = useState<string | null>(null);

  const reportsQ = useQuery({
    queryKey: ["reports", runId],
    queryFn: () => api.listReports(runId),
    refetchInterval: 4000,
  });

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

  // Auto-select the first (most relevant) version
  const activeId = activeVersionId ?? flatVersions[0]?.version.id;
  const active = flatVersions.find((v) => v.version.id === activeId) || null;

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

  if (reportsQ.isLoading) {
    return (
      <EmptyState runId={runId} message="Loading reports…" />
    );
  }

  if (!reportsQ.isLoading && reports.length === 0) {
    return (
      <EmptyState
        runId={runId}
        message='No reports yet. Drive the run to completion from the dashboard ("Run to completion") and a final report will be generated automatically.'
      />
    );
  }

  // Build TOC by parsing markdown headings
  const md = (markdownQ.data as string) || "";
  const tocItems = useMemo(() => {
    const items: { id: string; label: string }[] = [];
    md.split("\n").forEach((line) => {
      const m = /^##\s+(.+)/.exec(line);
      if (m) {
        const label = m[1].trim();
        const id = label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        items.push({ id, label });
      }
    });
    return items;
  }, [md]);

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
                    const text = String(children);
                    const id = text
                      .toLowerCase()
                      .replace(/[^a-z0-9]+/g, "-")
                      .replace(/^-|-$/g, "");
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
                onClick={() => navigator.clipboard.writeText(`worldfork run ${runId}`)}
              >
                Copy citation<span className="kbd">⌘C</span>
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
