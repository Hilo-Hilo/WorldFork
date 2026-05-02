"use client";

import { useMemo, useState } from "react";

/* ----------------------------------------------------------------------------
 * Run Dashboard — ported from the Claude Design handoff:
 *   project/dashboard.jsx + tree.jsx
 *
 * Three-column dense workspace: multiverse rail, SVG tree, detail panel,
 * with a bottom log strip. Mock tree + events + log.
 * Wire to GET /api/big-bangs/<id>/multiverses + /logs when the backend is
 * connected.
 * -------------------------------------------------------------------------- */

type TreeNode = {
  id: string;
  label: string;
  kind?: "bigbang";
  tick: number;
  status: "running" | "paused" | "terminal" | "failed";
  score?: number;
  children?: TreeNode[];
};

const TREE: TreeNode = {
  id: "BB",
  label: "Atlas Resilience Crisis",
  kind: "bigbang",
  tick: 0,
  status: "terminal",
  children: [
    {
      id: "M1",
      label: "Root timeline",
      tick: 14,
      status: "terminal",
      score: 0.62,
      children: [
        {
          id: "M2",
          label: "Transparent dashboard",
          tick: 47,
          status: "running",
          score: 0.71,
          children: [
            {
              id: "M5",
              label: "Court disclosure order",
              tick: 62,
              status: "running",
              score: 0.58,
              children: [
                { id: "M9", label: "Audit reform path", tick: 78, status: "running", score: 0.66 },
                { id: "M10", label: "Apology + appeals", tick: 71, status: "paused", score: 0.55 },
              ],
            },
            { id: "M6", label: "Hill zone backlash", tick: 53, status: "terminal", score: 0.52 },
          ],
        },
        {
          id: "M3",
          label: "Command stability",
          tick: 39,
          status: "paused",
          score: 0.69,
          children: [
            { id: "M7", label: "Labor bottleneck", tick: 51, status: "failed", score: 0.61 },
            { id: "M8", label: "Mutual-aid federation", tick: 64, status: "terminal", score: 0.57 },
          ],
        },
        { id: "M4", label: "ACCS audit freeze", tick: 28, status: "terminal", score: 0.56 },
      ],
    },
  ],
};

type FlatNode = TreeNode & { depth: number };

function flatten(node: TreeNode, depth = 0, acc: FlatNode[] = []): FlatNode[] {
  acc.push({ ...node, depth });
  (node.children || []).forEach((c) => flatten(c, depth + 1, acc));
  return acc;
}

const MULTIVERSES = flatten(TREE).filter((n) => n.kind !== "bigbang");

const RECENT_EVENTS = [
  { t: "00:42", tag: "fork",  kind: "fork",  h: "Branched M9 from M5", s: "God-agent score 0.66 · trigger: court_disclosure_order accepted" },
  { t: "00:39", tag: "god",   kind: "god",   h: "God-agent review · M2", s: "Marked tick 47 as branch-worthy: dashboard publish split cohort 3 ways." },
  { t: "00:31", tag: "tick",  kind: "",      h: "M2 · tick 47 committed", s: "Cohort updates 16/16 · 412 events · 3 graph deltas" },
  { t: "00:26", tag: "human", kind: "human", h: "Manual injection on M3", s: "Operator added Day-9 utility leak event before tick 39." },
  { t: "00:18", tag: "tick",  kind: "",      h: "M5 · tick 62 started", s: "Reading from checkpoint cp_M5_61_8b3a · resume-safe" },
];

const TICK_STAGES = [
  { id: "actor", label: "Actor decisions", s: "done", frac: "12 / 12" },
  { id: "event", label: "Event generation", s: "done", frac: "16 / 16" },
  { id: "soc", label: "Sociology update", s: "active", frac: "9 / 16 cohorts" },
  { id: "graph", label: "Graph update", s: "queued", frac: "—" },
  { id: "god", label: "God-agent review", s: "queued", frac: "—" },
  { id: "summary", label: "Tick summary", s: "queued", frac: "—" },
];

const STREAM_LOG = [
  { t: "00:42.6", tag: "queue", v: "claim mv=M9 worker=w-3 lease=30s" },
  { t: "00:42.4", tag: "checkpoint", v: "wrote cp_M9_77_2f1c (412 KB)" },
  { t: "00:42.2", tag: "tool", v: "graph.update_edges trust(-0.04)" },
  { t: "00:42.0", tag: "llm", v: "openai-codex/gpt-5.4 · 2,342 → 1,108 tok · 3.4s" },
  { t: "00:41.7", tag: "soc", v: "cohort=hill_zone_households split.eval=0.42" },
  { t: "00:41.5", tag: "actor", v: "hero=mira_venn decided · tool=mutual_aid_dispatch" },
  { t: "00:41.2", tag: "node", v: "node_attempt[N-93412] ok · attempt 1/3" },
];

/* ===== Tree layout ===== */

type LaidOutNode = TreeNode & { _x: number; _y: number; _lane: number };

function layoutTree(root: TreeNode, orientation: "horizontal" | "vertical" = "horizontal") {
  const nodes: LaidOutNode[] = [];
  const edges: { from: LaidOutNode; to: LaidOutNode }[] = [];
  let lane = 0;

  const laneCache = new WeakMap<TreeNode, number>();
  const assignLane = (n: TreeNode): number => {
    if (!n.children || n.children.length === 0) {
      const l = lane++;
      laneCache.set(n, l);
      return l;
    }
    const childLanes = n.children.map(assignLane);
    const l = (Math.min(...childLanes) + Math.max(...childLanes)) / 2;
    laneCache.set(n, l);
    return l;
  };
  assignLane(root);

  const lanePx = 70;
  const tickPx = 11;
  const padX = 80;
  const padY = 50;

  const walk = (n: TreeNode, parent: LaidOutNode | null) => {
    const ln = laneCache.get(n) ?? 0;
    const node: LaidOutNode = {
      ...n,
      _lane: ln,
      _x: orientation === "horizontal" ? padX + n.tick * tickPx : padX + ln * lanePx,
      _y: orientation === "horizontal" ? padY + ln * lanePx : padY + n.tick * tickPx,
    };
    nodes.push(node);
    if (parent) edges.push({ from: parent, to: node });
    (n.children || []).forEach((c) => walk(c, node));
  };
  walk(root, null);

  const maxTick = Math.max(...nodes.map((n) => n.tick), 1);
  const maxLane = Math.max(...nodes.map((n) => n._lane), 0);

  return {
    nodes,
    edges,
    maxTick,
    maxLane,
    width: padX * 2 + (orientation === "horizontal" ? maxTick * tickPx : maxLane * lanePx),
    height: padY * 2 + (orientation === "horizontal" ? maxLane * lanePx : maxTick * tickPx),
  };
}

function lineageOf(root: TreeNode, id: string): Set<string> {
  const path: TreeNode[] = [];
  const find = (n: TreeNode, acc: TreeNode[]): boolean => {
    if (n.id === id) {
      path.push(...acc, n);
      return true;
    }
    for (const c of n.children || []) {
      if (find(c, [...acc, n])) return true;
    }
    return false;
  };
  find(root, []);
  return new Set(path.map((n) => n.id));
}

function MultiverseTree({
  selectedId,
  onSelect,
  orientation = "horizontal",
  showScores = true,
}: {
  selectedId: string;
  onSelect: (id: string) => void;
  orientation?: "horizontal" | "vertical";
  showScores?: boolean;
}) {
  const { nodes, edges, width, height, maxTick } = useMemo(() => layoutTree(TREE, orientation), [orientation]);
  const lineageIds = useMemo(() => lineageOf(TREE, selectedId), [selectedId]);

  const tickMarks: number[] = [];
  for (let t = 0; t <= maxTick; t += 30) tickMarks.push(t);

  return (
    <svg className="tree-svg" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet">
      {orientation === "horizontal" &&
        tickMarks.map((t) => (
          <g key={t}>
            <line
              x1={80 + t * 11}
              y1={20}
              x2={80 + t * 11}
              y2={height - 20}
              stroke="var(--border)"
              strokeDasharray="2 4"
              strokeWidth="1"
            />
            <text className="branch-tick-axis" x={80 + t * 11} y={16} textAnchor="middle">
              t{t}
            </text>
          </g>
        ))}

      {edges.map((e, i) => {
        const inLineage = lineageIds.has(e.from.id) && lineageIds.has(e.to.id);
        const isActive = e.to.status === "running";
        const cls = `edge ${inLineage ? "is-lineage" : ""} ${isActive ? "is-active" : ""}`;
        const dx = e.to._x - e.from._x;
        const c1x = e.from._x + dx * 0.5;
        const c1y = e.from._y;
        const c2x = e.from._x + dx * 0.5;
        const c2y = e.to._y;
        const d = `M ${e.from._x} ${e.from._y} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${e.to._x} ${e.to._y}`;
        return <path key={i} className={cls} d={d} />;
      })}

      {nodes.map((n) => {
        const isRoot = n.kind === "bigbang";
        const isSelected = n.id === selectedId;
        const r = isRoot ? 9 : 7;
        return (
          <g
            key={n.id}
            className={`node ${isSelected ? "is-selected" : ""} ${isRoot ? "is-root" : ""}`}
            data-status={n.status}
            transform={`translate(${n._x}, ${n._y})`}
            onClick={() => onSelect(n.id)}
          >
            {n.status === "running" && <circle className="node-pulse" cx="0" cy="0" r="0" />}
            <circle className="node-bg" cx="0" cy="0" r={r} />
            {isRoot && <circle cx="0" cy="0" r="3" fill="var(--accent)" />}
            {!isRoot && (
              <text className="node-label" x="0" y="0">
                {n.id.replace(/^M/, "")}
              </text>
            )}
            <text className="node-tag" x="0" y={r + 12} style={isRoot ? { fill: "var(--fg-2)" } : undefined}>
              {isRoot ? "Big Bang" : n.label}
            </text>
            {showScores && !isRoot && (
              <text className="node-score" x="0" y={r + 23}>
                t{n.tick}
                {n.score ? `  ·  ${n.score.toFixed(2)}` : ""}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/* ===== Dashboard page ===== */

export default function DashboardPage({
  orientation = "horizontal",
  showScores = true,
}: {
  orientation?: "horizontal" | "vertical";
  showScores?: boolean;
}) {
  const [selected, setSelected] = useState("M5");
  const sel = MULTIVERSES.find((m) => m.id === selected) || MULTIVERSES[0];

  return (
    <div className="dash" data-screen-label="02 Run dashboard">
      {/* TOP BAR */}
      <header className="dash-top">
        <div className="left">
          <span style={{ color: "var(--accent)", display: "inline-flex" }}>
            <svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
              <circle cx="3" cy="9" r="1.4" fill="currentColor" />
              <circle cx="14" cy="3.5" r="1.4" />
              <circle cx="14" cy="9" r="1.4" />
              <circle cx="14" cy="14.5" r="1.4" fill="currentColor" />
              <path d="M4.4 9h3.5c1.5 0 2.5-1 3-2 .5-1 1.4-2 3-2" />
              <path d="M4.4 9h8.2" />
              <path d="M4.4 9h3.5c1.5 0 2.5 1 3 2 .5 1 1.4 2 3 2" />
            </svg>
          </span>
          <span style={{ fontWeight: 600 }}>worldfork</span>
          <span className="sep">/</span>
          <span className="run-name">Atlas Resilience Crisis</span>
          <span className="run-id">bb_01HF3Q…2X</span>
        </div>
        <div className="center">
          <span className="status">
            <span className="dot" />
            running
          </span>
          <span>tick&nbsp;<b>78</b>&nbsp;/&nbsp;180</span>
          <span>multiverses&nbsp;<b>9</b>&nbsp;active&nbsp;·&nbsp;5 terminal</span>
          <span>wallclock&nbsp;<b>02:14:08</b></span>
          <span>queue&nbsp;<b>3</b>&nbsp;/&nbsp;3</span>
        </div>
        <div className="right">
          <button className="dash-btn">Inject event</button>
          <button className="dash-btn">Fork from tick</button>
          <button className="dash-btn is-danger">
            Pause<span className="kbd">⌘P</span>
          </button>
          <button className="dash-btn is-primary">Open report</button>
        </div>
      </header>

      {/* BODY */}
      <div className="dash-body">
        {/* LEFT */}
        <aside className="dash-rail">
          <div className="rail-head">
            <h3>Multiverses</h3>
            <span className="count">{MULTIVERSES.length}</span>
          </div>
          <div className="rail-list">
            {MULTIVERSES.map((m) => (
              <div
                key={m.id}
                className={`mv-item ${selected === m.id ? "is-active" : ""}`}
                data-status={m.status}
                onClick={() => setSelected(m.id)}
              >
                <span className="mv-dot" />
                <div className="mv-body">
                  <div className="mv-name">
                    {m.id}&nbsp;·&nbsp;{m.label}
                  </div>
                  <div className="mv-meta">
                    <span>tick {m.tick}</span>
                    <span className="sep">·</span>
                    <span>{m.status}</span>
                    {m.score && (
                      <>
                        <span className="sep">·</span>
                        <span>score {m.score.toFixed(2)}</span>
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
              <button>timeline</button>
              <button>graph</button>
            </div>
            <div className="seg">
              <button>−</button>
              <button>fit</button>
              <button>+</button>
            </div>
          </div>

          <div className="center-stats">
            <span>nodes&nbsp;<b>{flatten(TREE).length}</b></span>
            <span>depth&nbsp;<b>4</b>&nbsp;/&nbsp;5</span>
            <span>active&nbsp;<b>4</b></span>
            <span>branch&nbsp;rate&nbsp;<b>1.6/tick</b></span>
          </div>

          <MultiverseTree selectedId={selected} onSelect={setSelected} orientation={orientation} showScores={showScores} />

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
          <div className="detail-section">
            <h4>
              Selected <span className="extra">{sel.id}</span>
            </h4>
            <p className="detail-name">{sel.label}</p>
            <p className="detail-id">
              mv_{sel.id.toLowerCase()}_01HF3Q… &nbsp;·&nbsp; parent {sel.id === "M1" ? "BB" : "M2"}
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
                <span className="v">{sel.tick} / 180</span>
              </div>
              <div>
                <span className="k">score</span>
                <span className="v">{sel.score ? sel.score.toFixed(2) : "—"}</span>
              </div>
              <div>
                <span className="k">forked at</span>
                <span className="v">tick {Math.max(0, sel.tick - 14)}</span>
              </div>
              <div>
                <span className="k">checkpoint</span>
                <span className="v">cp_{sel.id.toLowerCase()}_…8b3a</span>
              </div>
            </div>
          </div>

          <div className="detail-section">
            <h4>
              Current tick <span className="extra">tick {sel.tick + 1} &nbsp;·&nbsp; resume-safe</span>
            </h4>
            <div className="tick-progress">
              <div className="tick-stages">
                {TICK_STAGES.map((s) => (
                  <div key={s.id} className="tick-stage" data-s={s.s}>
                    <span className="ic" />
                    <span>{s.label}</span>
                    <span className="frac">{s.frac}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="detail-section">
            <h4>
              Recent events <span className="extra">last 5 min</span>
            </h4>
            <div className="events">
              {RECENT_EVENTS.map((e, i) => (
                <div key={i} className="event">
                  <span className="t">{e.t}</span>
                  <div className="b">
                    <div className="h">
                      <span>{e.h}</span>
                      <span className={`tag ${e.kind}`}>{e.tag}</span>
                    </div>
                    <div className="s">{e.s}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </aside>
      </div>

      {/* BOTTOM: log strip */}
      <footer className="dash-bottom">
        <span className="label">live log</span>
        <span className="stream">
          {STREAM_LOG.map((l, i) => (
            <span key={i} className="l">
              <span className="t">{l.t}</span>
              <span className="tag">[{l.tag}]</span>
              <span className="v">{l.v}</span>
            </span>
          ))}
        </span>
        <span className="right">tail&nbsp;·&nbsp;3.2k&nbsp;lines/min</span>
      </footer>
    </div>
  );
}
