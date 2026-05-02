"use client";

import { useEffect, useRef, useState } from "react";

/* ----------------------------------------------------------------------------
 * Input Page — ported from the Claude Design handoff:
 *   project/app.jsx + components.jsx + states.jsx
 *
 * Three states are exposed via the `state` prop / `?state=` query:
 *   "empty"   — the default form
 *   "loading" — initializer agent running, with stage list + live log
 *   "error"   — parse failure or LLM 503 failure
 *
 * Mock data only. Wire to POST /api/big-bangs when the backend is connected.
 * -------------------------------------------------------------------------- */

export type InputState = "empty" | "loading" | "error";
export type ErrorKind = "parse" | "llm";

/* ===== Icons ===== */

const Icon = {
  Upload: (p: React.SVGProps<SVGSVGElement>) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 3v13" />
      <path d="m7 8 5-5 5 5" />
      <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </svg>
  ),
  Chev: (p: React.SVGProps<SVGSVGElement>) => (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="m6 4 4 4-4 4" />
    </svg>
  ),
  Arrow: (p: React.SVGProps<SVGSVGElement>) => (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M3 8h10" />
      <path d="m9 4 4 4-4 4" />
    </svg>
  ),
  Alert: (p: React.SVGProps<SVGSVGElement>) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
      <path d="M10.3 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
    </svg>
  ),
  Logo: (p: React.SVGProps<SVGSVGElement>) => (
    <svg viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" {...p}>
      <circle cx="3" cy="9" r="1.4" fill="currentColor" />
      <circle cx="14" cy="3.5" r="1.4" />
      <circle cx="14" cy="9" r="1.4" />
      <circle cx="14" cy="14.5" r="1.4" fill="currentColor" />
      <path d="M4.4 9h3.5c1.5 0 2.5-1 3-2 .5-1 1.4-2 3-2" />
      <path d="M4.4 9h8.2" />
      <path d="M4.4 9h3.5c1.5 0 2.5 1 3 2 .5 1 1.4 2 3 2" />
    </svg>
  ),
  Close: (p: React.SVGProps<SVGSVGElement>) => (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" {...p}>
      <path d="m4 4 8 8M12 4l-8 8" />
    </svg>
  ),
};

/* ===== Topbar ===== */

function Topbar() {
  return (
    <header className="wf-topbar">
      <div className="wf-brand">
        <Icon.Logo className="wf-brand-mark" style={{ color: "var(--accent)" }} />
        <span className="wf-brand-name">worldfork</span>
        <span className="wf-brand-sep">/</span>
        <span className="wf-brand-page">new&nbsp;run</span>
      </div>
      <div className="wf-topbar-meta">
        <span><span className="dot" />backend&nbsp;<b>healthy</b></span>
        <span>queue&nbsp;<b>0&nbsp;/&nbsp;3</b></span>
        <span>v<b>4.0.0</b></span>
      </div>
    </header>
  );
}

/* ===== Drop zone ===== */

const SAMPLE_PREVIEW = `# Test Big Bang: The Atlas Resilience Crisis

## Purpose

This is the canonical long-form WorldFork demonstration scenario.
It is designed to show branching, multiverse divergence, manual
intervention, institutional trust shifts, public narrative feedback,
cohort splits, reports, and long-horizon simulation pressure.`;

type ScenarioFile = { kind: "file"; name: string; size: number; preview: string };
type ScenarioPaste = { kind: "paste"; text: string };
type Scenario = ScenarioFile | ScenarioPaste | null;

function fmtBytes(n: number) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / (1024 * 1024)).toFixed(2) + " MB";
}

function DropZone({ value, onChange }: { value: Scenario; onChange: (v: Scenario) => void }) {
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = (file: File) => {
    onChange({ kind: "file", name: file.name, size: file.size, preview: SAMPLE_PREVIEW });
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  };

  const removeFile = () => onChange(null);

  return (
    <div>
      <div className="wf-tab-row" role="tablist">
        <button className="wf-tab" role="tab" aria-selected={mode === "file"} onClick={() => setMode("file")}>
          upload .md
        </button>
        <button className="wf-tab" role="tab" aria-selected={mode === "paste"} onClick={() => setMode("paste")}>
          paste text
        </button>
      </div>

      {mode === "file" && (
        <div
          className={`wf-drop ${over ? "is-over" : ""} ${value?.kind === "file" ? "has-file" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
        >
          {value?.kind === "file" ? (
            <>
              <div className="wf-file">
                <div className="wf-file-thumb">MD</div>
                <div>
                  <div className="wf-file-name">{value.name}</div>
                  <div className="wf-file-meta">
                    {fmtBytes(value.size)} &nbsp;·&nbsp; parsed &nbsp;·&nbsp;{" "}
                    {value.preview.split("\n").length} lines &nbsp;·&nbsp; ~{Math.round(value.size / 4)} tokens
                  </div>
                </div>
                <button className="wf-file-remove" onClick={removeFile} title="Remove">
                  <Icon.Close style={{ width: 12, height: 12 }} />
                </button>
              </div>
              <pre className="wf-file-preview">
                <code>
                  <span className="h"># Test Big Bang: The Atlas Resilience Crisis</span>
                  {"\n\n"}
                  <span className="h">## Purpose</span>
                  {"\n"}
                  This is the canonical long-form WorldFork demonstration scenario.
                  {"\n"}
                  It is designed to show branching, multiverse divergence, manual
                  {"\n"}
                  intervention, institutional trust shifts, public narrative...{"\n"}
                </code>
              </pre>
            </>
          ) : (
            <>
              <Icon.Upload className="wf-drop-icon" />
              <div className="wf-drop-title">Drop a scenario file here</div>
              <div className="wf-drop-sub">.md &nbsp;·&nbsp; up to 2&thinsp;MB &nbsp;·&nbsp; UTF-8</div>
              <div className="wf-drop-actions">
                <span>or</span>
                <button className="wf-link" onClick={() => inputRef.current?.click()}>
                  browse
                </button>
                <span>·</span>
                <button
                  className="wf-link"
                  onClick={() =>
                    onChange({ kind: "file", name: "test-big-bang.md", size: 22901, preview: SAMPLE_PREVIEW })
                  }
                >
                  use example
                </button>
              </div>
              <input
                ref={inputRef}
                type="file"
                accept=".md,text/markdown"
                hidden
                onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
              />
            </>
          )}
        </div>
      )}

      {mode === "paste" && (
        <textarea
          className="wf-paste"
          placeholder={`# My Scenario\n\n## Purpose\nDescribe the world, the actors, the initial shock...\n\n## Cohorts\n- ...\n\n## Branch triggers\n- ...`}
          defaultValue={value?.kind === "paste" ? value.text : ""}
          onChange={(e) => onChange(e.target.value ? { kind: "paste", text: e.target.value } : null)}
        />
      )}
    </div>
  );
}

/* ===== Number field ===== */

function NumberField({
  label,
  help,
  value,
  onChange,
  min,
  max,
  step = 1,
  unit,
}: {
  label: string;
  help?: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
}) {
  return (
    <label className="wf-field">
      <div className="wf-field-label">
        <span className="wf-field-name">{label}</span>
        {help && <span className="wf-field-help">{help}</span>}
      </div>
      <div className="wf-num-suffix">
        <input
          className="wf-num"
          type="number"
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          min={min}
          max={max}
          step={step}
        />
        {unit && <span className="unit">{unit}</span>}
      </div>
    </label>
  );
}

/* ===== Loading state ===== */

const LOGS = [
  { t: "00:00.0", tag: "scenario", msg: "received test-big-bang.md (22.4 KB)" },
  { t: "00:00.4", tag: "parse", msg: "tokens=5483 sections=18" },
  { t: "00:01.6", tag: "validate", msg: "actor_schema v3 ok · cohort_state_schema v2 ok" },
  { t: "00:02.1", tag: "initializer", msg: "calling openai-codex/gpt-5.4 ..." },
  { t: "00:08.7", tag: "initializer", msg: "actors discovered: 6 hero · 12 cohort" },
  { t: "00:11.2", tag: "initializer", msg: "drafting initial world state ..." },
  { t: "00:14.0", tag: "initializer", msg: "writing graph edges (412 / 612)" },
];

function fmtDur(s: number) {
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

function LoadingState({ scenarioName, onCancel }: { scenarioName: string; onCancel: () => void }) {
  const [elapsed, setElapsed] = useState(14);
  const [logIdx, setLogIdx] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const id = setInterval(() => setLogIdx((i) => Math.min(i + 1, LOGS.length)), 850);
    return () => clearInterval(id);
  }, []);

  const stages = [
    { id: "parse", label: "Parse scenario markdown", status: "done", t: "0.4s" },
    { id: "schema", label: "Validate against actor + cohort schemas", status: "done", t: "1.2s" },
    { id: "init", label: "Initializer agent  ·  gpt-5.4", status: "active", t: "running" },
    { id: "world", label: "Seed world state + graph", status: "queued", t: "—" },
    { id: "tick0", label: "Persist root multiverse  ·  tick 0", status: "queued", t: "—" },
  ];

  return (
    <div className="wf-loading">
      <div className="wf-loading-head">
        <div className="wf-loading-title">
          <span className="pulse" />
          Initializing&nbsp;<span style={{ fontFamily: "var(--font-mono)", color: "var(--fg-2)" }}>{scenarioName}</span>
        </div>
        <div className="wf-loading-time">
          {fmtDur(elapsed)} &nbsp;·&nbsp; est. 30–90s &nbsp;·&nbsp; initializer.openai-codex
        </div>
      </div>

      <div className="wf-progress" />

      <div className="wf-stages">
        {stages.map((s) => (
          <div key={s.id} className="wf-stage" data-status={s.status === "queued" ? "" : s.status}>
            <span className="icon" />
            <span>{s.label}</span>
            <span className="t">{s.t}</span>
          </div>
        ))}
      </div>

      <div className="wf-loading-log">
        {LOGS.slice(0, logIdx).map((l, i) => (
          <span key={i} className="l">
            <span className="t">{l.t}</span>
            <span className="tag">[{l.tag}]</span>{" "}
            <span className="v">{l.msg}</span>
          </span>
        ))}
      </div>

      <div className="wf-loading-actions">
        <button className="wf-secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/* ===== Error state ===== */

type TraceRow = [string, string, React.ReactNode];

const ERROR_PRESETS: Record<ErrorKind, { code: string; title: string; msg: string; trace: TraceRow[] }> = {
  parse: {
    code: "ERR_PARSE_002",
    title: "Couldn't parse this scenario",
    msg: "The markdown loaded, but a required section is missing or malformed. The initializer needs at least Purpose, Initial Shock, and one cohort block to seed a world.",
    trace: [
      ["1", "scenario.md:42  ", <span key="key" className="key">## Initial Shock</span>],
      ["2", "scenario.md:43  ", <span key="empty">(empty body)</span>],
      ["3", "validator       ", <span key="missing" className="err">missing required section: Cohorts</span>],
      ["4", "validator       ", <span key="not-found" className="err">expected one of {"{Cohorts, Initial Cohorts, Actors}"} not found</span>],
    ],
  },
  llm: {
    code: "ERR_LLM_503",
    title: "Initializer model unavailable",
    msg: "openai-codex/gpt-5.4 returned 503 from the upstream provider after 3 retries. Your scenario is saved — try again, or fall back to deepseek-v4-flash for cheaper initialization.",
    trace: [
      ["1", "openrouter      ", <span key="post">POST /v1/chat/completions</span>],
      ["2", "attempt 1/3     ", <span key="a1" className="err">503 Service Unavailable · retry in 2s</span>],
      ["3", "attempt 2/3     ", <span key="a2" className="err">503 Service Unavailable · retry in 4s</span>],
      ["4", "attempt 3/3     ", <span key="a3" className="err">503 Service Unavailable · escalation halted</span>],
    ],
  },
};

function ErrorState({ kind, onDismiss, onRetry }: { kind: ErrorKind; onDismiss: () => void; onRetry: () => void }) {
  const p = ERROR_PRESETS[kind];

  return (
    <div className="wf-error">
      <div>
        <Icon.Alert className="wf-error-icon" />
      </div>
      <div>
        <h3 className="wf-error-title">
          {p.title}
          <span className="wf-error-code">{p.code}</span>
        </h3>
        <p className="wf-error-msg">{p.msg}</p>
        <pre className="wf-error-trace">
          {p.trace.map((row, i) => (
            <div key={i}>
              <span className="ln">{row[0]}</span>
              {row[1]}
              {row[2]}
              {i < p.trace.length - 1 ? "\n" : ""}
            </div>
          ))}
        </pre>
        <div className="wf-error-actions">
          <button className="wf-secondary" onClick={onDismiss}>
            Edit scenario
          </button>
          <button className="wf-cta" onClick={onRetry} style={{ padding: "8px 14px", fontSize: 12 }}>
            Retry initializer
          </button>
        </div>
      </div>
    </div>
  );
}

/* ===== Main InputPage ===== */

export default function InputPage({ initialState = "empty", errorKind = "parse" }: { initialState?: InputState; errorKind?: ErrorKind }) {
  const [state, setState] = useState<InputState>(initialState);
  const [scenario, setScenario] = useState<Scenario>({
    kind: "file",
    name: "test-big-bang.md",
    size: 22901,
    preview: SAMPLE_PREVIEW,
  });
  const [initPrompt, setInitPrompt] = useState(
    "Focus on transparency vs. command-stability divergence. Bias the initializer toward producing visible early branches around the dashboard and ACCS authority decisions.",
  );
  const [maxTicks, setMaxTicks] = useState(180);
  const [tickMins, setTickMins] = useState(720);
  const [policyOpen, setPolicyOpen] = useState(true);
  const [policy, setPolicy] = useState({ depth: 5, maxActive: 24, perTick: 2, scoreThreshold: 0.55 });

  const canSubmit = !!scenario && state === "empty";

  return (
    <div className="wf-page">
      <Topbar />
      <main className="wf-main">
        <span className="wf-pageno">page&nbsp;1&nbsp;/&nbsp;3 &nbsp;·&nbsp; input</span>

        <div className="wf-eyebrow">
          <span className="rule" />
          new&nbsp;big&nbsp;bang
        </div>
        <h1 className="wf-title">Configure a scenario run.</h1>
        <p className="wf-lede">
          Upload a markdown dossier and set bounds for the multiverse tree. The initializer will read your scenario,
          seed a root world state, and queue the first tick.
        </p>

        {/* 01 Scenario */}
        <section className="wf-section">
          <div className="wf-section-head">
            <span className="wf-section-num">01</span>
            <span className="wf-section-title">Scenario source</span>
            <span className="wf-section-hint">required</span>
          </div>
          <DropZone value={scenario} onChange={setScenario} />
        </section>

        {/* 02 Initializer prompt */}
        <section className="wf-section">
          <div className="wf-section-head">
            <span className="wf-section-num">02</span>
            <span className="wf-section-title">Initializer prompt</span>
            <span className="wf-section-hint">optional &nbsp;·&nbsp; steers the seed agent</span>
          </div>
          <textarea
            className="wf-textarea"
            placeholder="e.g. Bias the initializer toward early divergence around governance choices, and surface at least 4 named hero actors with conflicting incentives."
            value={initPrompt}
            onChange={(e) => setInitPrompt(e.target.value)}
          />
        </section>

        {/* 03 Simulation config */}
        <section className="wf-section">
          <div className="wf-section-head">
            <span className="wf-section-num">03</span>
            <span className="wf-section-title">Simulation config</span>
            <span className="wf-section-hint">bounded by queue capacity</span>
          </div>

          <div className="wf-config">
            <NumberField
              label="max_ticks"
              help="Hard ceiling on tick count across the whole run."
              value={maxTicks}
              onChange={setMaxTicks}
              min={1}
              max={1000}
              unit="ticks"
            />
            <NumberField
              label="tick_duration_minutes"
              help="Simulated minutes per tick. 720 = 12h."
              value={tickMins}
              onChange={setTickMins}
              min={1}
              max={10080}
              unit="min"
            />
          </div>

          {/* branch policy accordion */}
          <div className="wf-accordion" data-open={policyOpen} style={{ marginTop: 12 }}>
            <button className="wf-accordion-head" onClick={() => setPolicyOpen(!policyOpen)}>
              <div className="wf-accordion-head-l">
                <Icon.Chev className="wf-accordion-chev" />
                <span className="wf-accordion-name">branch_policy</span>
              </div>
              <span className="wf-accordion-summary">
                depth&nbsp;{policy.depth} &nbsp;·&nbsp; active&nbsp;{policy.maxActive} &nbsp;·&nbsp; per&nbsp;tick&nbsp;
                {policy.perTick} &nbsp;·&nbsp; threshold&nbsp;{policy.scoreThreshold}
              </span>
            </button>
            {policyOpen && (
              <div className="wf-accordion-body">
                <NumberField
                  label="depth"
                  help="Maximum branch depth."
                  value={policy.depth}
                  onChange={(v) => setPolicy({ ...policy, depth: v })}
                  min={1}
                  max={20}
                />
                <NumberField
                  label="max_active_multiverses"
                  help="Cap on simultaneously live branches."
                  value={policy.maxActive}
                  onChange={(v) => setPolicy({ ...policy, maxActive: v })}
                  min={1}
                  max={256}
                />
                <NumberField
                  label="max_branches_per_tick"
                  help="How many forks one tick may produce."
                  value={policy.perTick}
                  onChange={(v) => setPolicy({ ...policy, perTick: v })}
                  min={1}
                  max={16}
                />
                <NumberField
                  label="score_threshold"
                  help="God-agent score required to fork."
                  value={policy.scoreThreshold}
                  onChange={(v) => setPolicy({ ...policy, scoreThreshold: v })}
                  min={0}
                  max={1}
                  step={0.05}
                />
              </div>
            )}
          </div>
        </section>

        {/* state-specific surfaces */}
        {state === "loading" && (
          <LoadingState scenarioName={scenario?.kind === "file" ? scenario.name : "scenario.md"} onCancel={() => setState("empty")} />
        )}

        {state === "error" && (
          <ErrorState kind={errorKind} onDismiss={() => setState("empty")} onRetry={() => setState("loading")} />
        )}

        {/* CTA */}
        <div className="wf-actions">
          <div className="wf-actions-meta">
            <span>est. tokens&nbsp;<b style={{ color: "var(--fg-2)" }}>~5.5k</b></span>
            <span>queue position&nbsp;<b style={{ color: "var(--fg-2)" }}>1</b></span>
            <span>initializer&nbsp;<b style={{ color: "var(--fg-2)" }}>gpt-5.4</b></span>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="wf-secondary">Save draft</button>
            <button className="wf-cta" disabled={!canSubmit} onClick={() => setState("loading")}>
              Run simulation
              <Icon.Arrow />
              <span className="kbd">⌘&nbsp;↵</span>
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
