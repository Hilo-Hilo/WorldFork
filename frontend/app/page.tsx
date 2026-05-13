"use client";

import Link from "next/link";
import { type ReactNode, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DEMO_RUN_ID, demoRunsListEnvelope } from "@/lib/demo";
import type { RunSummary } from "@/lib/types";

const STATUS_TONE: Record<string, string> = {
  running: "var(--accent)",
  paused: "var(--warn)",
  draft: "var(--muted)",
  completed: "var(--muted-2)",
  terminated: "var(--muted-2)",
  archived: "var(--muted-2)",
  failed: "var(--danger)",
};

export default function Home() {
  const qc = useQueryClient();
  const [editingRunId, setEditingRunId] = useState<string | null>(null);
  const [nameDrafts, setNameDrafts] = useState<Record<string, string>>({});
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["runs", "summary"],
    queryFn: () => api.listRuns(20),
    refetchInterval: 3000,
  });

  const renameRun = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => api.updateBigBang(id, { name }),
    onSuccess: () => {
      setActionError(null);
      setEditingRunId(null);
      void qc.invalidateQueries({ queryKey: ["runs", "summary"] });
    },
    onError: (err) => setActionError((err as Error).message || "Rename failed"),
  });

  const deleteRun = useMutation({
    mutationFn: (id: string) => api.deleteBigBang(id),
    onSuccess: () => {
      setActionError(null);
      setConfirmingDelete(null);
      void qc.invalidateQueries({ queryKey: ["runs", "summary"] });
    },
    onError: (err) => setActionError((err as Error).message || "Delete failed"),
  });

  const startRename = (run: RunSummary) => {
    setActionError(null);
    setConfirmingDelete(null);
    setEditingRunId(run.id);
    setNameDrafts((current) => ({ ...current, [run.id]: current[run.id] ?? run.name }));
  };

  const saveRename = (run: RunSummary) => {
    const name = (nameDrafts[run.id] ?? run.name).trim();
    if (!name) {
      setActionError("Simulation name is required.");
      return;
    }
    renameRun.mutate({ id: run.id, name });
  };

  const runs = data?.data ?? (error ? demoRunsListEnvelope(20).data : []);
  const errMessage = error instanceof Error ? error.message : null;

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 28,
        fontFamily: "var(--font-sans)",
        color: "var(--fg)",
        padding: "72px 24px 80px",
        background:
          "radial-gradient(circle at 1px 1px, oklch(0.30 0.008 260) 1px, transparent 0) 0 0 / 24px 24px, var(--bg)",
      }}
    >
      <div style={{ textAlign: "center", maxWidth: "44ch" }}>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            letterSpacing: "0.10em",
            textTransform: "uppercase",
            color: "var(--muted)",
            marginBottom: 14,
          }}
        >
          worldfork / runs
        </div>
        <h1 style={{ fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em", margin: "0 0 12px" }}>
          {runs.length === 0 && !isLoading ? "No runs yet." : "Recent runs."}
        </h1>
        <p style={{ color: "var(--muted)", margin: 0 }}>
          {runs.length === 0 && !isLoading
            ? "Configure a scenario to spawn the first Big Bang."
            : "Live polled list of Big Bangs from the backend."}
        </p>
      </div>

      {errMessage && (
        <AlertBanner>
          backend unreachable - showing demo run only. <span style={{ color: "var(--muted)" }}>{errMessage}</span>
        </AlertBanner>
      )}

      {actionError && <AlertBanner>{actionError}</AlertBanner>}

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <Link
          href="/input"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 10,
            background: "var(--accent)",
            color: "var(--accent-fg)",
            padding: "11px 18px 11px 20px",
            borderRadius: 6,
            fontSize: 13.5,
            fontWeight: 600,
            textDecoration: "none",
          }}
        >
          New scenario
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>-&gt;</span>
        </Link>
        <Link
          href="/dashboard?run=demo"
          title="Render the dashboard against an animated synthetic run - no backend or LLM calls."
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            background: "transparent",
            color: "var(--fg-2)",
            border: "1px solid var(--border)",
            padding: "10px 14px",
            borderRadius: 6,
            fontSize: 13,
            fontWeight: 500,
            textDecoration: "none",
            fontFamily: "var(--font-sans)",
          }}
        >
          <span
            aria-hidden
            style={{
              display: "inline-block",
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "var(--accent)",
              boxShadow: "0 0 0 3px color-mix(in oklch, var(--accent) 25%, transparent)",
            }}
          />
          Demo run
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>no llm</span>
        </Link>
      </div>

      <div style={{ display: "grid", gap: 8, width: "100%", maxWidth: 860 }}>
        {error && (
          <div
            style={{
              padding: "14px 16px",
              border: "1px solid var(--border)",
              borderLeft: "2px solid var(--danger)",
              borderRadius: 4,
              background: "var(--surface)",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              color: "var(--danger)",
            }}
          >
            backend unreachable - {(error as Error).message}
          </div>
        )}
        {isLoading && (
          <div
            style={{
              padding: "14px 16px",
              border: "1px solid var(--border)",
              borderRadius: 4,
              background: "var(--surface)",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              color: "var(--muted)",
            }}
          >
            loading runs...
          </div>
        )}
        {runs.map((run) => (
          <RunRow
            key={run.id}
            run={run}
            draftName={nameDrafts[run.id] ?? run.name}
            isEditing={editingRunId === run.id}
            isConfirmingDelete={confirmingDelete === run.id}
            isRenaming={renameRun.isPending && renameRun.variables?.id === run.id}
            isDeleting={deleteRun.isPending && deleteRun.variables === run.id}
            onDraftName={(name) => setNameDrafts((current) => ({ ...current, [run.id]: name }))}
            onStartRename={() => startRename(run)}
            onCancelRename={() => {
              setEditingRunId(null);
              setActionError(null);
            }}
            onSaveRename={() => saveRename(run)}
            onDelete={() => {
              if (run.id === DEMO_RUN_ID) return;
              setActionError(null);
              if (confirmingDelete === run.id) {
                deleteRun.mutate(run.id);
              } else {
                setEditingRunId(null);
                setConfirmingDelete(run.id);
              }
            }}
          />
        ))}
      </div>

      <div style={{ display: "flex", gap: 18, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted-2)" }}>
        <Link href="/input" style={{ color: "inherit", textDecoration: "underline", textUnderlineOffset: 3 }}>
          /input
        </Link>
        <Link href="/dashboard" style={{ color: "inherit", textDecoration: "underline", textUnderlineOffset: 3 }}>
          /dashboard (mock)
        </Link>
        <Link href="/report" style={{ color: "inherit", textDecoration: "underline", textUnderlineOffset: 3 }}>
          /report (mock)
        </Link>
      </div>
    </main>
  );
}

function AlertBanner({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      style={{
        border: "1px solid var(--danger)",
        background: "var(--danger-soft)",
        color: "var(--fg)",
        padding: "10px 14px",
        borderRadius: 6,
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        maxWidth: "60ch",
        textAlign: "center",
      }}
    >
      {children}
    </div>
  );
}

function RunRow({
  run,
  draftName,
  isEditing,
  isConfirmingDelete,
  isRenaming,
  isDeleting,
  onDraftName,
  onStartRename,
  onCancelRename,
  onSaveRename,
  onDelete,
}: {
  run: RunSummary;
  draftName: string;
  isEditing: boolean;
  isConfirmingDelete: boolean;
  isRenaming: boolean;
  isDeleting: boolean;
  onDraftName: (name: string) => void;
  onStartRename: () => void;
  onCancelRename: () => void;
  onSaveRename: () => void;
  onDelete: () => void;
}) {
  const isDemo = run.id === DEMO_RUN_ID;
  return (
    <div
      data-testid={`run-row-${run.id}`}
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr) auto auto",
        alignItems: "center",
        gap: 14,
        padding: "12px 14px",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        color: "var(--fg-2)",
        fontSize: 13,
      }}
    >
      <div style={{ minWidth: 0 }}>
        {isEditing ? (
          <input
            aria-label={`Rename ${run.name}`}
            value={draftName}
            onChange={(event) => onDraftName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") onSaveRename();
              if (event.key === "Escape") onCancelRename();
            }}
            autoFocus
            style={{
              width: "100%",
              background: "var(--bg-2)",
              border: "1px solid var(--border-strong)",
              borderRadius: 4,
              color: "var(--fg)",
              fontSize: 13,
              padding: "7px 9px",
              outline: "none",
            }}
          />
        ) : (
          <Link href={`/dashboard?run=${run.id}`} style={{ color: "inherit", textDecoration: "none" }}>
            <div style={{ color: "var(--fg)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {run.name}
            </div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", marginTop: 3 }}>
              {run.id.slice(0, 8)}... - {new Date(run.created_at).toLocaleString()} - {run.multiverse_count}{" "}
              multiverse{run.multiverse_count !== 1 ? "s" : ""}
            </div>
          </Link>
        )}
      </div>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: STATUS_TONE[run.status] ?? "var(--muted)",
          border: "1px solid var(--border)",
          padding: "2px 7px",
          borderRadius: 3,
        }}
      >
        {run.status}
      </span>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        {isEditing ? (
          <>
            <button className="dash-btn" onClick={onSaveRename} disabled={isRenaming || !draftName.trim()} type="button">
              {isRenaming ? "Saving" : "Save"}
            </button>
            <button className="dash-btn" onClick={onCancelRename} disabled={isRenaming} type="button">
              Cancel
            </button>
          </>
        ) : (
          <>
            <button className="dash-btn" onClick={onStartRename} disabled={isDemo} type="button">
              Rename
            </button>
            <button
              className="dash-btn is-danger"
              onClick={onDelete}
              disabled={isDemo || isDeleting}
              type="button"
              title={isConfirmingDelete ? "Click again to archive this simulation" : "Archive this simulation"}
            >
              {isDeleting ? "Deleting" : isConfirmingDelete ? "Confirm" : "Delete"}
            </button>
            <Link href={`/dashboard?run=${run.id}`} className="dash-btn is-primary" style={{ textDecoration: "none" }}>
              Open
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
