"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { AgentLog, Multiverse, Tick } from "@/lib/types";

/** Per-multiverse tick progress derived from /api/multiverses/{id}/ticks. */
type TickProgress = { current: number; running: boolean };
type TickByMv = Map<string, TickProgress>;

/* ----------------------------------------------------------------------------
 * Translate a thrown error into a user-friendly { title, detail } pair.
 * Detects common backend cases (402 out-of-credits, 503 LLM unavailable,
 * 409 paused, 500 generic) so the dashboard banner is actionable instead of
 * surfacing raw HTTP status codes the user can't act on.
 * -------------------------------------------------------------------------- */
function explainError(err: unknown, action: string): { title: string; detail: string } {
  if (err instanceof ApiError) {
    let parsed: { detail?: string; error?: { message?: string } } | null = null;
    try {
      parsed = JSON.parse(err.body);
    } catch {
      /* not JSON */
    }
    const inner = parsed?.detail || parsed?.error?.message || err.body || "";
    const innerLc = inner.toLowerCase();

    if (err.status === 402 || innerLc.includes("payment required") || innerLc.includes("more credits")) {
      return {
        title: "LLM provider out of credits",
        detail: `${action} hit OpenRouter HTTP 402. Add credits at https://openrouter.ai/settings/credits and retry.`,
      };
    }
    if (err.status === 409) {
      return { title: "Conflict", detail: inner || "The big bang is in a state that doesn't accept this action right now." };
    }
    if (err.status === 503 || innerLc.includes("llm unavailable")) {
      return {
        title: "LLM provider unavailable",
        detail: inner || "The configured model returned 503. Check provider routing or try again later.",
      };
    }
    if (err.status === 500) {
      return {
        title: `${action} failed (HTTP 500)`,
        detail: inner.slice(0, 600) || "Backend raised an unhandled exception. Check api logs for the traceback.",
      };
    }
    return { title: `${action} failed (HTTP ${err.status})`, detail: inner.slice(0, 600) || err.message };
  }
  return { title: `${action} failed`, detail: (err as Error)?.message || String(err) };
}

function ErrorBanner({
  title,
  detail,
  onDismiss,
  testId,
}: {
  title: string;
  detail: string;
  onDismiss?: () => void;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      style={{
        margin: "10px 14px 0",
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
      {onDismiss && (
        <button
          onClick={onDismiss}
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
      )}
    </div>
  );
}

/* ----------------------------------------------------------------------------
 * Run Dashboard — wired to the backend.
 *
 * Reads ?run=<big-bang-id> from URL.
 * Polls:
 *   GET /api/big-bangs/<id>           every 2s   (status, name)
 *   GET /api/big-bangs/<id>/multiverses every 2s (tree shape, statuses)
 *   GET /api/agent/logs               every 2s   (bottom log strip)
 * Renders the multiverse tree via SVG with parent_multiverse_id + fork_tick_index.
 * -------------------------------------------------------------------------- */

function deriveTick(mv: Multiverse, tickByMv?: TickByMv): number {
  // Prefer the live tick progress sourced from /api/multiverses/{id}/ticks —
  // the Multiverse payload itself does not expose a current-tick counter, so
  // without this the node would pin at t0 even while the worker is mid-run.
  const live = tickByMv?.get(mv.id);
  if (live) return live.current;

  // Fallback: probe a few legacy `mv.state.*` fields, then fork_tick_index.
  const s = mv.state || {};
  const candidates = [
    (s as any).tick_index,
    (s as any).last_tick_index,
    (s as any).tick,
    (s as any).completed_tick_index,
    mv.fork_tick_index,
  ];
  for (const c of candidates) {
    if (typeof c === "number" && Number.isFinite(c)) return c;
  }
  return 0;
}

function effectiveStatus(mv: Multiverse, tickByMv?: TickByMv): string {
  // If the multiverse has a tick in flight, surface "running" to the tree so
  // the node pulses — `mv.status === "active"` is treated as running too, but
  // some flows leave the multiverse in a non-active state between ticks.
  if (tickByMv?.get(mv.id)?.running) return "running";
  return mv.status;
}

/* ===== Tidy-tree layout =====
 * Reingold-Tilford-ish: Y by depth, X via recursive leaf count so the parent
 * sits at the centroid of its children. Forking parents get a "continuation"
 * leaf injected as their leftmost child so the parent's own ongoing trajectory
 * is visible alongside the diverged branches.
 */

type TidyKind = "root" | "fork" | "leaf";

type TidyNode = {
  id: string;
  mvId: string | null;
  label: string;
  kind: TidyKind;
  status: string;
  value: number | null; // path_probability ∈ [0,1] — drives color ramp
  isContinuation?: boolean;
  forkTickIndex: number | null;
  children: TidyNode[];
  _x: number;
  _y: number;
};

const NODE_GAP_X = 110;
const ROW_GAP_Y = 92;
const TOP_PAD = 60;
const LEFT_PAD = 60;

function buildTidyTree(bigBangName: string, multiverses: Multiverse[]): {
  root: TidyNode;
  links: { from: TidyNode; to: TidyNode }[];
  nodes: TidyNode[];
  width: number;
  height: number;
} {
  // Index multiverses + group by parent so we can walk the lineage.
  const byParent = new Map<string | null, Multiverse[]>();
  for (const m of multiverses) {
    const key = m.parent_multiverse_id ?? null;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key)!.push(m);
  }
  // Stable, deterministic order: by fork_tick_index, then created_at.
  for (const list of byParent.values()) {
    list.sort((a, b) => {
      const at = a.fork_tick_index ?? -1;
      const bt = b.fork_tick_index ?? -1;
      if (at !== bt) return at - bt;
      return a.created_at.localeCompare(b.created_at);
    });
  }

  const tidyFor = (mv: Multiverse): TidyNode => {
    const childMvs = byParent.get(mv.id) ?? [];
    const node: TidyNode = {
      id: `mv-${mv.id}`,
      mvId: mv.id,
      label: mv.ui_label,
      kind: childMvs.length > 0 ? "fork" : "leaf",
      status: mv.status,
      value: typeof mv.path_probability === "number" ? mv.path_probability : null,
      forkTickIndex: mv.fork_tick_index,
      children: [],
    } as TidyNode;
    if (childMvs.length > 0) {
      // Continuation leaf — represents the parent's own continued trajectory
      // alongside its diverged children. Anchored leftmost so the eye reads
      // it as the original timeline.
      node.children.push({
        id: `mv-${mv.id}__cont`,
        mvId: mv.id,
        label: `${mv.ui_label} cont.`,
        kind: "leaf",
        status: mv.status,
        value: typeof mv.path_probability === "number" ? mv.path_probability : null,
        isContinuation: true,
        forkTickIndex: mv.fork_tick_index,
        children: [],
      } as TidyNode);
      for (const c of childMvs) node.children.push(tidyFor(c));
    }
    return node;
  };

  const roots = byParent.get(null) ?? [];
  const root: TidyNode = {
    id: "bigbang",
    mvId: null,
    label: bigBangName,
    kind: "root",
    status: "running",
    value: null,
    forkTickIndex: null,
    children: roots.map(tidyFor),
  } as TidyNode;

  // Tidy layout: x by leaf-count walk, y by depth. Leaves get a leftmost
  // anchor for now; we re-center the whole layout horizontally below so the
  // tree stays centered in the SVG regardless of how many branches exist.
  let cursor = 0;
  const place = (n: TidyNode, depth: number): void => {
    n._y = TOP_PAD + depth * ROW_GAP_Y;
    if (n.children.length === 0) {
      n._x = cursor * NODE_GAP_X;
      cursor += 1;
      return;
    }
    for (const c of n.children) place(c, depth + 1);
    const xs = n.children.map((c) => c._x);
    n._x = (Math.min(...xs) + Math.max(...xs)) / 2;
  };
  place(root, 0);

  const nodes: TidyNode[] = [];
  const links: { from: TidyNode; to: TidyNode }[] = [];
  const walk = (n: TidyNode) => {
    nodes.push(n);
    for (const c of n.children) {
      links.push({ from: n, to: c });
      walk(c);
    }
  };
  walk(root);

  // Choose an SVG width that fits the layout with breathing room, but never
  // smaller than the min so the panel doesn't collapse. Then shift every node
  // so the layout's horizontal centroid lands on width / 2 — this is what
  // makes the tree appear stable + centered as branches grow in.
  const minX = Math.min(...nodes.map((n) => n._x), 0);
  const maxX = Math.max(...nodes.map((n) => n._x), 0);
  const maxY = Math.max(...nodes.map((n) => n._y), TOP_PAD);
  const layoutWidth = maxX - minX;
  const width = Math.max(layoutWidth + LEFT_PAD * 2, 720);
  const dx = width / 2 - (minX + maxX) / 2;
  for (const n of nodes) n._x += dx;

  const height = Math.max(maxY + 80, 420);
  return { root, links, nodes, width, height };
}

/** Map value v∈[0,1] to a mint→indigo→magenta ramp (oklch). */
function rampColor(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "oklch(0.85 0.008 240)";
  const t = Math.max(0, Math.min(1, v));
  const hue = 175 + t * 155;
  const chroma = 0.13 + t * 0.05;
  const lightness = 0.7 - t * 0.1;
  return `oklch(${lightness} ${chroma} ${hue})`;
}

function lineageOfTidy(root: TidyNode, id: string): Set<string> {
  const path: TidyNode[] = [];
  const find = (n: TidyNode, acc: TidyNode[]): boolean => {
    if (n.id === id) {
      path.push(...acc, n);
      return true;
    }
    for (const c of n.children) {
      if (find(c, [...acc, n])) return true;
    }
    return false;
  };
  find(root, []);
  return new Set(path.map((n) => n.id));
}

function MultiverseTree({
  bigBangName,
  multiverses,
  selectedId,
  onSelect,
}: {
  bigBangName: string;
  multiverses: Multiverse[];
  selectedId: string | null;
  onSelect: (mvId: string) => void;
}) {
  const { root, nodes, links, width, height } = useMemo(
    () => buildTidyTree(bigBangName, multiverses),
    [bigBangName, multiverses],
  );
  // selectedId is a multiverse id from the rail; resolve to the matching
  // tree-node id (the non-continuation entry takes precedence on highlight).
  const selectedTidyId = useMemo(() => {
    if (!selectedId) return null;
    const direct = nodes.find((n) => n.mvId === selectedId && !n.isContinuation);
    return direct?.id ?? null;
  }, [selectedId, nodes]);
  const lineageIds = useMemo(
    () => (selectedTidyId ? lineageOfTidy(root, selectedTidyId) : new Set<string>()),
    [root, selectedTidyId],
  );
  const hasSelection = Boolean(selectedTidyId);

  return (
    <svg className="tree-svg" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet">
      {/* Links: hairline cubic S-curves, colored by destination value */}
      {links.map((l, i) => {
        const x1 = l.from._x;
        const y1 = l.from._y;
        const x2 = l.to._x;
        const y2 = l.to._y;
        const midY = (y1 + y2) / 2;
        const d = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
        const inLineage = lineageIds.has(l.from.id) && lineageIds.has(l.to.id);
        const isTrunk = l.from.kind === "root";
        const isLeafLink = l.to.kind === "leaf";
        const dim = hasSelection && !inLineage && isLeafLink;
        const color = isTrunk && l.to.value == null ? "var(--border-strong)" : rampColor(l.to.value);
        return (
          <path
            key={i}
            className={`node-link ${inLineage ? "is-lineage" : ""}`}
            d={d}
            fill="none"
            stroke={color}
            strokeWidth={inLineage ? 2.4 : 1.4}
            opacity={dim ? 0.22 : 0.9}
            style={{ transition: "all 200ms ease" }}
          />
        );
      })}

      {/* Nodes */}
      {nodes.map((n) => {
        const isSelected = n.id === selectedTidyId;
        const isActive = n.status === "running" || n.status === "active";
        // CSS transforms (rather than SVG transform="…") so layout shifts
        // animate via the .node-group transition. Existing nodes slide to
        // their new centroid positions when a new branch appears.
        const groupStyle = {
          transform: `translate(${n._x}px, ${n._y}px)`,
        } as const;
        if (n.kind === "root") {
          return (
            <g key={n.id} className="node-group" style={groupStyle}>
              <circle
                className={`node-circle root ${isSelected ? "is-selected" : ""}`}
                cx="0"
                cy="0"
                r={isSelected ? 8 : 6}
              />
              <text className="node-label" x="0" y="-14" textAnchor="middle">
                Big Bang · {n.label.length > 28 ? n.label.slice(0, 27) + "…" : n.label}
              </text>
            </g>
          );
        }
        if (n.kind === "fork") {
          const stroke = rampColor(n.value);
          return (
            <g
              key={n.id}
              className="node-group"
              style={{ ...groupStyle, cursor: "pointer" }}
              onClick={() => n.mvId && onSelect(n.mvId)}
            >
              {isActive && <circle className="node-pulse" cx="0" cy="0" r="0" />}
              <circle
                className={`node-circle fork ${isSelected ? "is-selected" : ""}`}
                cx="0"
                cy="0"
                r={isSelected ? 7.5 : 6}
                style={{ stroke }}
              />
              <text className="node-leaf-label" x="0" y="-14" textAnchor="middle">
                fork @ t{n.forkTickIndex ?? 0}
              </text>
            </g>
          );
        }
        // Leaf
        const fill = rampColor(n.value);
        const labelText = n.isContinuation ? `${n.label}` : n.label;
        return (
          <g
            key={n.id}
            className="node-group"
            style={{ ...groupStyle, cursor: "pointer" }}
            onClick={() => n.mvId && onSelect(n.mvId)}
          >
            {isActive && <circle className="node-pulse" cx="0" cy="0" r="0" />}
            <circle
              className={`node-leaf ${isSelected ? "is-selected" : ""}`}
              cx="0"
              cy="0"
              r={isSelected ? 7.5 : 5.5}
              style={{ fill }}
            />
            <text className="node-leaf-label" x="0" y="22" textAnchor="middle">
              {labelText.length > 18 ? labelText.slice(0, 17) + "…" : labelText}
            </text>
            {n.value != null && (
              <text className="node-leaf-value" x="0" y="36" textAnchor="middle">
                {(n.value * 100).toFixed(0)}%
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/* ===== Brand mark used in the top bar ===== */

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

/* ===== Empty state when no run is selected ===== */

function EmptyState() {
  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 14,
        background:
          "radial-gradient(circle at 1px 1px, oklch(0.30 0.008 260) 1px, transparent 0) 0 0 / 22px 22px, var(--bg)",
        color: "var(--fg)",
      }}
    >
      <h1 style={{ fontSize: 22, fontWeight: 500, letterSpacing: "-0.01em", margin: 0 }}>No run selected.</h1>
      <p style={{ color: "var(--muted)", maxWidth: "44ch", textAlign: "center", margin: 0, fontSize: 13.5 }}>
        Open a run from the runs list, or configure a new scenario.
      </p>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
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
        <Link
          href="/input"
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
          New scenario
        </Link>
      </div>
    </div>
  );
}

/* ===== Main DashboardPage ===== */

export type DashboardProps = {
  runId?: string;
  orientation?: "horizontal" | "vertical";
  showScores?: boolean;
};

export default function DashboardPage({ runId, orientation = "horizontal", showScores = true }: DashboardProps) {
  if (!runId) return <EmptyState />;
  return <DashboardWired runId={runId} orientation={orientation} showScores={showScores} />;
}

function DashboardWired({
  runId,
  orientation,
  showScores,
}: {
  runId: string;
  orientation: "horizontal" | "vertical";
  showScores: boolean;
}) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);

  const bigBang = useQuery({
    queryKey: ["bigBang", runId],
    queryFn: () => api.getBigBang(runId),
    refetchInterval: 2000,
  });

  const multiverses = useQuery({
    queryKey: ["multiverses", runId],
    queryFn: () => api.listMultiverses(runId),
    refetchInterval: 2000,
  });

  const logs = useQuery({
    queryKey: ["agentLogs"],
    queryFn: () => api.agentLogs(20),
    refetchInterval: 2500,
  });

  const jobs = useQuery({
    queryKey: ["jobs", runId],
    queryFn: () => api.listJobs(runId, 20),
    refetchInterval: 2000,
  });

  // Per-multiverse tick progress. The Multiverse payload itself has no current-tick
  // field, so the dashboard tree pins every node at t0 without this. Fan out one
  // query per multiverse — typically a handful, so the request cost is fine.
  const mvList = multiverses.data || [];
  const tickQueries = useQueries({
    queries: mvList.map((m) => ({
      queryKey: ["multiverseTicks", m.id],
      queryFn: () => api.listMultiverseTicks(m.id),
      refetchInterval: 2000,
    })),
  });
  const tickByMv: TickByMv = useMemo(() => {
    const map = new Map<string, TickProgress>();
    mvList.forEach((m, i) => {
      const ticks = (tickQueries[i]?.data as Tick[] | undefined) || [];
      if (ticks.length === 0) return;
      const finals = ticks.filter((t) => t.status === "final").map((t) => t.tick_index);
      const running = ticks.find((t) => t.status === "running");
      const current = running
        ? running.tick_index
        : finals.length
          ? Math.max(...finals)
          : 0;
      map.set(m.id, { current, running: Boolean(running) });
    });
    return map;
  // Dependency intentionally serializes the per-mv tick fingerprints so the map
  // recomputes when any tick transitions running→final.
  }, [mvList, tickQueries.map((q) => (q.data as Tick[] | undefined)?.map((t) => `${t.tick_index}:${t.status}`).join(",")).join("|")]);

  const [dismissedErr, setDismissedErr] = useState<string | null>(null);

  const pause = useMutation({
    mutationFn: () => api.pauseBigBang(runId),
    onSuccess: () => {
      setDismissedErr(null);
      qc.invalidateQueries({ queryKey: ["bigBang", runId] });
    },
  });
  const resume = useMutation({
    mutationFn: () => api.resumeBigBang(runId),
    onSuccess: () => {
      setDismissedErr(null);
      qc.invalidateQueries({ queryKey: ["bigBang", runId] });
    },
  });
  const runAll = useMutation({
    mutationFn: () => api.createRunUntilCompleteJob(runId, 32),
    onSuccess: () => {
      setDismissedErr(null);
      qc.invalidateQueries({ queryKey: ["jobs", runId] });
      qc.invalidateQueries({ queryKey: ["bigBang", runId] });
      qc.invalidateQueries({ queryKey: ["multiverses", runId] });
      qc.invalidateQueries({ queryKey: ["agentLogs"] });
    },
  });

  // Resolve the most relevant active error to surface in the banner.
  // Mutation errors take priority over query polling errors so the user sees
  // feedback for the action they just took. Query errors only show if no
  // mutation has failed and the cached data is stale (i.e. data is empty).
  const activeErr = (() => {
    if (runAll.error) return { err: runAll.error, action: "Run to completion", reset: () => runAll.reset() };
    if (pause.error) return { err: pause.error, action: "Pause", reset: () => pause.reset() };
    if (resume.error) return { err: resume.error, action: "Resume", reset: () => resume.reset() };
    if (bigBang.error && !bigBang.data) return { err: bigBang.error, action: "Loading run", reset: null };
    if (multiverses.error && !multiverses.data) return { err: multiverses.error, action: "Loading multiverses", reset: null };
    return null;
  })();
  const errKey = activeErr ? `${activeErr.action}:${(activeErr.err as Error)?.message}` : null;
  const showErr = activeErr && errKey !== dismissedErr;

  // auto-select the deepest leaf with status running, else the deepest, else first
  const selId = selected ?? (() => {
    const list = multiverses.data || [];
    if (list.length === 0) return null;
    const running = list.filter((m) => m.status === "running");
    return (running[0] || list.reduce((a, b) => (b.depth > a.depth ? b : a), list[0])).id;
  })();

  const sel = (multiverses.data || []).find((m) => m.id === selId) || null;
  const isPaused = bigBang.data?.status === "paused";
  const isRunningSomewhere = (multiverses.data || []).some((m) => m.status === "running" || m.status === "active");
  const allTerminal =
    multiverses.data && multiverses.data.length > 0 &&
    multiverses.data.every((m) => ["completed", "terminated", "frozen", "failed"].includes(m.status));
  const activeRunJob = (jobs.data || []).find(
    (job) =>
      job.job_type === "run_big_bang_until_complete" &&
      ["queued", "running", "paused", "interrupt_requested"].includes(job.status),
  );
  const runButtonLabel = (() => {
    if (runAll.isPending) return "Starting...";
    if (activeRunJob?.status === "queued") return "Queued...";
    if (activeRunJob?.status === "running" || activeRunJob?.status === "interrupt_requested") return "Running...";
    if (activeRunJob?.status === "paused") return "Paused";
    return "Run to completion";
  })();

  // logs scoped to this big bang (api/agent/logs returns global; filter by message)
  const runLogs = (logs.data?.data || []).filter(
    (l) =>
      // match the big-bang id substring or any of the multiverse ids
      l.message.includes(runId.slice(0, 8)) ||
      (multiverses.data || []).some((m) => l.message.includes(m.id.slice(0, 8))),
  );
  const initializerOutput = bigBang.data?.scenario_input?.initializer_output as Record<string, unknown> | undefined;
  const initializerFallbackUsed = Boolean(initializerOutput?.fallback);
  const displayLog = (log: AgentLog): AgentLog =>
    initializerFallbackUsed &&
    log.source === "llm" &&
    log.status === "failed" &&
    log.message === `initializer_agent_${runId}`
      ? {
          ...log,
          status: "fallback used",
          message: "initializer LLM output was invalid; deterministic seed used",
      }
      : log;

  return (
    <div className="dash" data-screen-label="02 Run dashboard">
      {/* TOP BAR */}
      <header className="dash-top">
        <div className="left">
          <span style={{ color: "var(--accent)", display: "inline-flex" }}>
            <BrandMark />
          </span>
          <Link href="/" style={{ color: "inherit", textDecoration: "none", fontWeight: 600 }}>
            worldfork
          </Link>
          <span className="sep">/</span>
          <span className="run-name">{bigBang.data?.name || "loading…"}</span>
          <span className="run-id">{runId.slice(0, 8)}…{runId.slice(-2)}</span>
        </div>
        <div className="center">
          <span className="status">
            <span className="dot" />
            {bigBang.data?.status || "…"}
          </span>
          <span>
            multiverses&nbsp;<b>{(multiverses.data || []).length}</b>
          </span>
          <span>
            active&nbsp;<b>{(multiverses.data || []).filter((m) => m.status === "running" || m.status === "active").length}</b>
          </span>
          <span>
            polling&nbsp;<b>2s</b>
          </span>
        </div>
        <div className="right">
          {!isPaused && (
            <button className="dash-btn is-danger" onClick={() => pause.mutate()} disabled={pause.isPending}>
              Pause
            </button>
          )}
          {isPaused && (
            <button className="dash-btn" onClick={() => resume.mutate()} disabled={resume.isPending}>
              Resume
            </button>
          )}
          <button className="dash-btn" onClick={() => runAll.mutate()} disabled={runAll.isPending || Boolean(activeRunJob) || allTerminal}>
            {runButtonLabel}
          </button>
          <Link href={`/report?run=${runId}`} className="dash-btn is-primary" style={{ textDecoration: "none" }}>
            Open report
          </Link>
        </div>
      </header>

      {showErr && activeErr && (() => {
        const { title, detail } = explainError(activeErr.err, activeErr.action);
        return (
          <ErrorBanner
            testId="dash-error-banner"
            title={title}
            detail={detail}
            onDismiss={activeErr.reset ? () => { activeErr.reset!(); setDismissedErr(null); } : () => setDismissedErr(errKey!)}
          />
        );
      })()}

      {/* BODY */}
      <div className="dash-body">
        {/* LEFT: rail */}
        <aside className="dash-rail">
          <div className="rail-head">
            <h3>Multiverses</h3>
            <span className="count">{(multiverses.data || []).length}</span>
          </div>
          <div className="rail-list">
            {multiverses.isLoading && (
              <div style={{ padding: "12px 14px", fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--muted)" }}>
                loading…
              </div>
            )}
            {multiverses.error && (
              <div style={{ padding: "12px 14px", fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--danger)" }}>
                {(multiverses.error as Error).message}
              </div>
            )}
            {(multiverses.data || []).map((m) => (
              <div
                key={m.id}
                className={`mv-item ${selId === m.id ? "is-active" : ""}`}
                data-status={m.status}
                onClick={() => setSelected(m.id)}
              >
                <span className="mv-dot" />
                <div className="mv-body">
                  <div className="mv-name">
                    {m.ui_label}&nbsp;·&nbsp;{m.branch_reason || (m.parent_multiverse_id ? "Branch" : "Root")}
                  </div>
                  <div className="mv-meta">
                    <span>tick {deriveTick(m, tickByMv)}{tickByMv.get(m.id)?.running ? " (running)" : ""}</span>
                    <span className="sep">·</span>
                    <span>{effectiveStatus(m, tickByMv)}</span>
                    {m.branch_probability != null && (
                      <>
                        <span className="sep">·</span>
                        <span>p {m.branch_probability.toFixed(2)}</span>
                      </>
                    )}
                  </div>
                </div>
                <span className="mv-depth">d{m.depth}</span>
              </div>
            ))}
          </div>
        </aside>

        {/* CENTER */}
        <section className="dash-center">
          <div className="center-toolbar">
            <div className="seg">
              <button aria-pressed="true">tree</button>
            </div>
          </div>

          <div className="center-stats">
            <span>
              nodes&nbsp;<b>{(multiverses.data || []).length}</b>
            </span>
            {bigBang.data && (
              <span>
                status&nbsp;<b>{bigBang.data.status}</b>
              </span>
            )}
            {isRunningSomewhere && <span style={{ color: "var(--accent)" }}>● live</span>}
          </div>

          {(multiverses.data || []).length === 0 && !multiverses.isLoading && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--muted)",
                fontSize: 13,
                fontFamily: "var(--font-mono)",
                pointerEvents: "none",
              }}
            >
              no multiverses yet — try Run to completion
            </div>
          )}

          {(multiverses.data || []).length > 0 && (
            <MultiverseTree
              bigBangName={bigBang.data?.name || "Run"}
              multiverses={mvList}
              selectedId={selId}
              onSelect={setSelected}
            />
          )}

          <div className="center-legend">
            <span className="key"><span className="sw running" />running</span>
            <span className="key"><span className="sw paused" />paused</span>
            <span className="key"><span className="sw terminal" />terminal</span>
            <span className="key"><span className="sw failed" />failed</span>
            <span className="key">— lineage</span>
          </div>
        </section>

        {/* RIGHT */}
        <aside className="dash-rail right">
          {sel ? (
            <>
              <div className="detail-section">
                <h4>
                  Selected <span className="extra">{sel.ui_label}</span>
                </h4>
                <p className="detail-name">{sel.branch_reason || "Root timeline"}</p>
                <p className="detail-id">
                  {sel.id.slice(0, 8)}… &nbsp;·&nbsp; parent {sel.parent_multiverse_id ? sel.parent_multiverse_id.slice(0, 6) + "…" : "—"}
                </p>
                <div className="detail-grid">
                  <div>
                    <span className="k">status</span>
                    <span className="v">{sel.status}</span>
                  </div>
                  <div>
                    <span className="k">depth</span>
                    <span className="v">d{sel.depth}</span>
                  </div>
                  <div>
                    <span className="k">last tick</span>
                    <span className="v">{deriveTick(sel, tickByMv)}{tickByMv.get(sel.id)?.running ? " (running)" : ""}</span>
                  </div>
                  <div>
                    <span className="k">branch p</span>
                    <span className="v">{sel.branch_probability?.toFixed(3) || "—"}</span>
                  </div>
                  <div>
                    <span className="k">path p</span>
                    <span className="v">{sel.path_probability?.toFixed(3) || "—"}</span>
                  </div>
                  <div>
                    <span className="k">forked at</span>
                    <span className="v">{sel.fork_tick_index != null ? `tick ${sel.fork_tick_index}` : "root"}</span>
                  </div>
                </div>
              </div>

              <div className="detail-section">
                <h4>
                  Recent run logs <span className="extra">{runLogs.length} matched</span>
                </h4>
                <div className="events">
                  {runLogs.length === 0 && (
                    <div
                      style={{
                        padding: "12px 0",
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: "var(--muted-2)",
                      }}
                    >
                      no recent log entries reference this run
                    </div>
                  )}
                  {runLogs.slice(0, 8).map((rawLog) => {
                    const l = displayLog(rawLog);
                    return (
                    <div key={rawLog.id} className="event">
                      <span className="t">{new Date(l.created_at).toLocaleTimeString().slice(0, 5)}</span>
                      <div className="b">
                        <div className="h">
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {l.message}
                          </span>
                          <span className={`tag ${l.source}`}>{l.source}</span>
                        </div>
                        <div className="s">{l.status}</div>
                      </div>
                    </div>
                    );
                  })}
                </div>
              </div>
            </>
          ) : (
            <div className="detail-section">
              <h4>Selected</h4>
              <p className="detail-name">—</p>
              <p className="detail-id">click a node in the tree</p>
            </div>
          )}
        </aside>
      </div>

      {/* BOTTOM */}
      <footer className="dash-bottom">
        <span className="label">live log</span>
        <span className="stream">
          {(logs.data?.data || []).slice(0, 8).map((rawLog) => {
            const l = displayLog(rawLog);
            return (
            <span key={rawLog.id} className="l">
              <span className="t">{new Date(l.created_at).toLocaleTimeString().slice(0, 8)}</span>
              <span className="tag">[{l.source}]</span>
              <span className="v">{l.message.slice(0, 80)}</span>
            </span>
            );
          })}
          {(!logs.data || logs.data.data.length === 0) && (
            <span className="l">
              <span className="v">no logs yet</span>
            </span>
          )}
        </span>
        <span className="right">tail · 2.5s poll</span>
      </footer>
    </div>
  );
}
