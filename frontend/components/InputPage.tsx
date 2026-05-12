"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

/* ----------------------------------------------------------------------------
 * Input Page — wired to POST /api/big-bangs.
 *
 * Cost guardrails:
 *   - use_initializer_agent defaults to FALSE (skips the initializer LLM call
 *     and the chunker; about 0 LLM calls during init).
 *   - max_ticks defaults to 4.
 *   - We warn if scenario_text exceeds the chunker's direct-context budget
 *     (18000 chars) since flipping use_initializer_agent on would burn calls
 *     summarising chunks.
 * -------------------------------------------------------------------------- */

export type InputState = "empty" | "loading" | "error";
export type ErrorKind = "parse" | "llm";

const CHUNKER_BUDGET = 18000;
const TINY_SAMPLE = `# Demo scenario

## Purpose
A small civic-tech rumor scenario for demoing WorldFork.

## Initial shock
A six-second cellphone clip alleging police misconduct goes viral
on social media before officials confirm or deny.

## Cohorts
- outraged residents
- institutional-trust residents
- public-safety voters
- silent majority
- counter-protest bloc

## Branch triggers
- early apology
- official denial
- second video
- protest splintering
`;
const TINY_SAMPLE_QUESTION =
  "Will the city administration regain public trust within the simulated crisis window?";
const TINY_SAMPLE_RESOLUTION =
  "Answer yes if retained timelines show trust-recovery signals overtaking mobilization and rumor persistence by the final tick. Answer no if distrust, protest escalation, or unresolved information gaps dominate the retained path mass.";
const TINY_SAMPLE_SUPPORTING =
  "Which response path restores trust fastest?\nWhich cohorts remain mobilized at the end?\nWhich branch triggers create durable divergence?";

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

/* ===== Topbar with live readyz check ===== */

function Topbar() {
  const { data, error } = useQuery({ queryKey: ["readyz"], queryFn: () => api.readyz(), refetchInterval: 5000 });
  const status = error
    ? { label: "unreachable", tone: "var(--danger)" }
    : data?.ok
      ? { label: "healthy", tone: "var(--accent)" }
      : data
        ? { label: "degraded", tone: "var(--warn)" }
        : { label: "checking", tone: "var(--muted)" };

  return (
    <header className="wf-topbar">
      <div className="wf-brand">
        <Icon.Logo className="wf-brand-mark" style={{ color: "var(--accent)" }} />
        <span className="wf-brand-name">worldfork</span>
        <span className="wf-brand-sep">/</span>
        <span className="wf-brand-page">new&nbsp;run</span>
      </div>
      <div className="wf-topbar-meta">
        <span>
          <span className="dot" style={{ background: status.tone, boxShadow: `0 0 0 3px ${status.tone}33` }} />
          backend&nbsp;<b style={{ color: status.tone }}>{status.label}</b>
        </span>
      </div>
    </header>
  );
}

/* ===== Drop zone ===== */

type ScenarioFile = { kind: "file"; name: string; size: number; preview: string; text: string };
type ScenarioPaste = { kind: "paste"; text: string };
type Scenario = ScenarioFile | ScenarioPaste | null;

function fmtBytes(n: number) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / (1024 * 1024)).toFixed(2) + " MB";
}

function DropZone({
  value,
  onChange,
  onUseTinyDemo,
}: {
  value: Scenario;
  onChange: (v: Scenario) => void;
  onUseTinyDemo: () => void;
}) {
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    const text = await file.text();
    const preview = text.slice(0, 600);
    onChange({ kind: "file", name: file.name, size: file.size, preview, text });
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) void handleFile(f);
  };

  const removeFile = () => onChange(null);

  const previewLines = value?.kind === "file" ? value.preview.split("\n").slice(0, 5) : [];

  return (
    <div>
      <div className="wf-tab-row" role="tablist">
        <button className="wf-tab" role="tab" aria-selected={mode === "file"} onClick={() => setMode("file")} type="button">
          upload .md
        </button>
        <button className="wf-tab" role="tab" aria-selected={mode === "paste"} onClick={() => setMode("paste")} type="button">
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
                    {fmtBytes(value.size)} &nbsp;·&nbsp; {value.text.split("\n").length} lines &nbsp;·&nbsp; ~
                    {Math.round(value.text.length / 4)} tokens
                  </div>
                </div>
                <button className="wf-file-remove" onClick={removeFile} title="Remove" type="button">
                  <Icon.Close style={{ width: 12, height: 12 }} />
                </button>
              </div>
              <pre className="wf-file-preview">
                <code>
                  {previewLines.map((l, i) => (
                    <span key={i} className={l.startsWith("#") ? "h" : ""}>
                      {l}
                      {"\n"}
                    </span>
                  ))}
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
                <button className="wf-link" onClick={() => inputRef.current?.click()} type="button">
                  browse
                </button>
                <span>·</span>
                <button
                  className="wf-link"
                  onClick={onUseTinyDemo}
                  type="button"
                >
                  use tiny demo (~1 KB)
                </button>
              </div>
              <input
                ref={inputRef}
                type="file"
                accept=".md,text/markdown,text/plain"
                hidden
                onChange={(e) => e.target.files?.[0] && void handleFile(e.target.files[0])}
              />
            </>
          )}
        </div>
      )}

      {mode === "paste" && (
        <textarea
          className="wf-paste"
          placeholder={`# My Scenario\n\n## Purpose\nDescribe the world, the actors, the initial shock...\n\n## Cohorts\n- ...\n\n## Branch triggers\n- ...`}
          defaultValue={value?.kind === "paste" ? value.text : value?.kind === "file" ? value.text : ""}
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

/* ===== Loading state (uses real API for elapsed time) ===== */

const LOG_TEMPLATES = [
  { tag: "scenario", msg: "received scenario" },
  { tag: "parse", msg: "validating against actor + cohort schemas" },
  { tag: "validate", msg: "schema ok" },
  { tag: "init", msg: "creating Big Bang record" },
  { tag: "init", msg: "snapshotting source-of-truth corpus" },
  { tag: "init", msg: "writing config artifact" },
];

function fmtDur(s: number) {
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

function LoadingState({ scenarioName, useInitializer }: { scenarioName: string; useInitializer: boolean }) {
  const [elapsed, setElapsed] = useState(0);
  const [logIdx, setLogIdx] = useState(0);
  const initializerWaitWindow = 300;

  useEffect(() => {
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const id = setInterval(() => setLogIdx((i) => Math.min(i + 1, LOG_TEMPLATES.length)), 750);
    return () => clearInterval(id);
  }, []);

  const stages = [
    { id: "parse", label: "Parse scenario markdown", status: "done", t: "0.4s" },
    { id: "schema", label: "Validate against actor + cohort schemas", status: "done", t: "1.2s" },
    {
      id: "init",
      label: useInitializer ? "Initializer agent  ·  LLM call" : "Creating Big Bang record",
      status: "active",
      t: useInitializer ? (elapsed < initializerWaitWindow ? `${initializerWaitWindow - elapsed}s backend window` : "awaiting backend") : "running",
    },
    { id: "world", label: "Persist root multiverse  ·  tick 0", status: "queued", t: "—" },
  ];

  const renderedLogs = LOG_TEMPLATES.slice(0, logIdx);
  const initializerWaitLog =
    useInitializer && elapsed >= 6
      ? {
          tag: "llm",
          msg:
            elapsed < initializerWaitWindow
              ? "waiting on OpenRouter initializer response"
              : "still waiting for the backend response",
        }
      : null;

  return (
    <div className="wf-loading">
      <div className="wf-loading-head">
        <div className="wf-loading-title">
          <span className="pulse" />
          Initializing&nbsp;<span style={{ fontFamily: "var(--font-mono)", color: "var(--fg-2)" }}>{scenarioName}</span>
        </div>
        <div className="wf-loading-time">
          {fmtDur(elapsed)} &nbsp;·&nbsp; {useInitializer ? "initializer LLM can take several minutes" : "fast init (no LLM)"}
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
        {renderedLogs.map((l, i) => (
          <span key={i} className="l">
            <span className="t">{fmtDur(Math.min(i, elapsed))}</span>
            <span className="tag">[{l.tag}]</span> <span className="v">{l.msg}</span>
          </span>
        ))}
        {initializerWaitLog && (
          <span className="l">
            <span className="t">{fmtDur(elapsed)}</span>
            <span className="tag">[{initializerWaitLog.tag}]</span>{" "}
            <span className="v">{initializerWaitLog.msg}</span>
          </span>
        )}
      </div>
    </div>
  );
}

/* ===== Error state — live error from the API instead of presets ===== */

function ErrorState({
  code,
  title,
  message,
  detail,
  onDismiss,
  onRetry,
}: {
  code: string;
  title: string;
  message: string;
  detail?: string;
  onDismiss: () => void;
  onRetry: () => void;
}) {
  return (
    <div className="wf-error">
      <div>
        <Icon.Alert className="wf-error-icon" />
      </div>
      <div>
        <h3 className="wf-error-title">
          {title}
          <span className="wf-error-code">{code}</span>
        </h3>
        <p className="wf-error-msg">{message}</p>
        {detail && (
          <pre className="wf-error-trace">
            <span className="err">{detail}</span>
          </pre>
        )}
        <div className="wf-error-actions">
          <button className="wf-secondary" onClick={onDismiss} type="button">
            Edit scenario
          </button>
          <button className="wf-cta" onClick={onRetry} style={{ padding: "8px 14px", fontSize: 12 }} type="button">
            Retry
          </button>
        </div>
      </div>
    </div>
  );
}

/* ===== Main InputPage ===== */

export default function InputPage() {
  const router = useRouter();
  const [scenario, setScenario] = useState<Scenario>(null);
  const [primaryQuestion, setPrimaryQuestion] = useState("");
  const [resolutionCriteria, setResolutionCriteria] = useState("");
  const [supportingQuestions, setSupportingQuestions] = useState("");
  const [initPrompt, setInitPrompt] = useState("");
  const [maxTicks, setMaxTicks] = useState(4);
  const [tickMins, setTickMins] = useState(720);
  const [policyOpen, setPolicyOpen] = useState(false);
  const [policy, setPolicy] = useState({ depth: 2, maxActive: 6, perTick: 2, scoreThreshold: 0.55 });
  const [useInitializer, setUseInitializer] = useState(false);

  const scenarioText = scenario?.kind === "file" ? scenario.text : scenario?.kind === "paste" ? scenario.text : "";
  const trimmedPrimaryQuestion = primaryQuestion.trim();
  const trimmedResolutionCriteria = resolutionCriteria.trim();
  const supportingQuestionList = supportingQuestions
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
  const overChunkBudget = useInitializer && scenarioText.length > CHUNKER_BUDGET;

  const useTinyDemo = () => {
    setScenario({
      kind: "file",
      name: "demo-scenario.md",
      size: TINY_SAMPLE.length,
      preview: TINY_SAMPLE.slice(0, 600),
      text: TINY_SAMPLE,
    });
    setPrimaryQuestion((value) => value || TINY_SAMPLE_QUESTION);
    setResolutionCriteria((value) => value || TINY_SAMPLE_RESOLUTION);
    setSupportingQuestions((value) => value || TINY_SAMPLE_SUPPORTING);
  };

  const submit = useMutation({
    mutationFn: () =>
      api.createBigBang({
        name: `Scenario ${new Date().toLocaleString()}`,
        scenario_text: scenarioText,
        scenario_input: {
          primary_question: trimmedPrimaryQuestion,
          forecast_question: trimmedPrimaryQuestion,
          resolution_criteria: trimmedResolutionCriteria || undefined,
          supporting_questions: supportingQuestionList,
          reporting_questions: [trimmedPrimaryQuestion, ...supportingQuestionList],
        },
        simulation_config: { max_ticks: maxTicks, tick_duration_minutes: tickMins },
        branch_policy: {
          max_branch_depth: policy.depth,
          max_active_multiverses: policy.maxActive,
          max_branches_per_tick: policy.perTick,
          branch_score_threshold: policy.scoreThreshold,
        },
        use_initializer_agent: useInitializer,
        initializer_prompt: initPrompt || undefined,
      }),
    onSuccess: (bb) => router.push(`/dashboard?run=${bb.id}`),
  });

  const canSubmit = !!scenarioText && !!trimmedPrimaryQuestion && !submit.isPending;

  const apiErr = submit.error instanceof ApiError ? submit.error : null;
  const errCode = apiErr ? `HTTP_${apiErr.status}` : "ERR_UNKNOWN";

  let errTitle = "Couldn't create Big Bang";
  let errMessage = (submit.error as Error)?.message || "";
  if (apiErr) {
    let parsedBody: { detail?: string; error?: { message?: string } } | null = null;
    try {
      parsedBody = JSON.parse(apiErr.body);
    } catch {
      /* not JSON */
    }
    const inner = parsedBody?.detail || parsedBody?.error?.message || apiErr.body || "";
    const innerLc = inner.toLowerCase();
    if (apiErr.status === 402 || innerLc.includes("payment required") || innerLc.includes("more credits")) {
      errTitle = "LLM provider out of credits";
      errMessage =
        "Submission hit OpenRouter HTTP 402 Payment Required. Add credits at https://openrouter.ai/settings/credits, then retry. The simulation cannot run without LLM credits.";
    } else if (apiErr.status === 503) {
      errTitle = "LLM provider unavailable";
      errMessage =
        innerLc === "llm unavailable"
          ? "The initializer LLM did not complete through OpenRouter before the backend timeout. Disable the initializer for a fast local setup, or retry after provider latency or credits are resolved."
          : inner ||
            "The configured initializer model returned 503 from upstream. Try disabling the initializer agent or using a shorter scenario.";
    } else {
      errMessage = `${apiErr.status} ${apiErr.message}`;
    }
  }
  const errDetail = apiErr?.body?.slice(0, 600);

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
          Upload a markdown dossier and set bounds for the multiverse tree. Defaults are tuned for a cheap,
          short demo run — flip the initializer toggle and bump max_ticks once you trust the loop.
        </p>

        {/* 01 Scenario */}
        <section className="wf-section">
          <div className="wf-section-head">
            <span className="wf-section-num">01</span>
            <span className="wf-section-title">Scenario source</span>
            <span className="wf-section-hint">required</span>
          </div>
          <DropZone value={scenario} onChange={setScenario} onUseTinyDemo={useTinyDemo} />
        </section>

        {/* 02 Question */}
        <section className="wf-section">
          <div className="wf-section-head">
            <span className="wf-section-num">02</span>
            <span className="wf-section-title">Question to answer</span>
            <span className="wf-section-hint">required for reports</span>
          </div>
          <div className="wf-question-grid">
            <label className="wf-question-field">
              <span className="wf-question-label">Primary question</span>
              <span className="wf-question-help">The final report will answer this directly.</span>
              <textarea
                className="wf-textarea"
                placeholder="e.g. Will the coalition pass the emergency housing bill before the final tick?"
                value={primaryQuestion}
                onChange={(e) => setPrimaryQuestion(e.target.value)}
                required
              />
            </label>
            <label className="wf-question-field">
              <span className="wf-question-label">Resolution criteria</span>
              <span className="wf-question-help">Define what counts as yes, no, or unresolved.</span>
              <textarea
                className="wf-textarea"
                placeholder="e.g. Yes means the bill passes with implementation funding; no means it fails, is delayed beyond the horizon, or passes without enforcement authority."
                value={resolutionCriteria}
                onChange={(e) => setResolutionCriteria(e.target.value)}
              />
            </label>
            <label className="wf-question-field">
              <span className="wf-question-label">Supporting questions</span>
              <span className="wf-question-help">Optional, one per line. These guide report sections and evidence review.</span>
              <textarea
                className="wf-textarea"
                placeholder={"Which actors drive the decisive branch?\nWhich cohorts change position?\nWhat evidence would change the answer?"}
                value={supportingQuestions}
                onChange={(e) => setSupportingQuestions(e.target.value)}
              />
            </label>
          </div>
        </section>

        {/* 03 Initializer prompt */}
        <section className="wf-section">
          <div className="wf-section-head">
            <span className="wf-section-num">03</span>
            <span className="wf-section-title">Initializer prompt</span>
            <span className="wf-section-hint">optional &nbsp;·&nbsp; only used if initializer agent is on</span>
          </div>
          <textarea
            className="wf-textarea"
            placeholder="e.g. Bias the initializer toward early divergence around governance choices, and surface at least 4 named hero actors with conflicting incentives."
            value={initPrompt}
            onChange={(e) => setInitPrompt(e.target.value)}
            disabled={!useInitializer}
            style={!useInitializer ? { opacity: 0.55 } : undefined}
          />
        </section>

        {/* 04 Simulation config */}
        <section className="wf-section">
          <div className="wf-section-head">
            <span className="wf-section-num">04</span>
            <span className="wf-section-title">Simulation config</span>
            <span className="wf-section-hint">cheap defaults</span>
          </div>

          <div className="wf-config">
            <NumberField
              label="max_ticks"
              help="Hard ceiling on tick count. 4 = quick demo."
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

          {/* initializer toggle */}
          <label className="wf-field" style={{ marginTop: 12, gridTemplateColumns: "1fr auto" }}>
            <div className="wf-field-label">
              <span className="wf-field-name">use_initializer_agent</span>
              <span className="wf-field-help">
                When off, init makes 0 LLM calls. When on, runs the initializer + (over {CHUNKER_BUDGET.toLocaleString()} chars) the corpus chunker.
              </span>
            </div>
            <div className="wf-num-suffix">
              <button
                onClick={() => setUseInitializer(!useInitializer)}
                type="button"
                style={{
                  padding: "5px 11px",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  background: useInitializer ? "var(--accent)" : "var(--bg-2)",
                  color: useInitializer ? "var(--accent-fg)" : "var(--muted)",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  cursor: "pointer",
                  fontWeight: 600,
                }}
              >
                {useInitializer ? "on" : "off"}
              </button>
            </div>
          </label>

          {overChunkBudget && (
            <div
              style={{
                marginTop: 10,
                padding: "10px 12px",
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderLeft: "2px solid var(--warn)",
                borderRadius: 4,
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "var(--warn)",
              }}
            >
              warning &nbsp;·&nbsp; scenario is {scenarioText.length.toLocaleString()} chars, over the
              {" "}
              {CHUNKER_BUDGET.toLocaleString()}-char direct-context budget. With initializer on, the chunker will fire
              one LLM call per chunk.
            </div>
          )}

          {/* branch policy accordion */}
          <div className="wf-accordion" data-open={policyOpen} style={{ marginTop: 12 }}>
            <button className="wf-accordion-head" onClick={() => setPolicyOpen(!policyOpen)} type="button">
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
                <NumberField label="depth" help="Maximum branch depth." value={policy.depth} onChange={(v) => setPolicy({ ...policy, depth: v })} min={1} max={20} />
                <NumberField label="max_active_multiverses" help="Cap on simultaneously live branches." value={policy.maxActive} onChange={(v) => setPolicy({ ...policy, maxActive: v })} min={1} max={256} />
                <NumberField label="max_branches_per_tick" help="How many forks one tick may produce." value={policy.perTick} onChange={(v) => setPolicy({ ...policy, perTick: v })} min={1} max={16} />
                <NumberField label="score_threshold" help="God-agent score required to fork." value={policy.scoreThreshold} onChange={(v) => setPolicy({ ...policy, scoreThreshold: v })} min={0} max={1} step={0.05} />
              </div>
            )}
          </div>
        </section>

        {/* state-specific surfaces */}
        {submit.isPending && (
          <LoadingState
            scenarioName={scenario?.kind === "file" ? scenario.name : "scenario"}
            useInitializer={useInitializer}
          />
        )}

        {submit.isError && (
          <ErrorState
            code={errCode}
            title={errTitle}
            message={errMessage}
            detail={errDetail}
            onDismiss={() => submit.reset()}
            onRetry={() => submit.mutate()}
          />
        )}

        {/* CTA */}
        <div className="wf-actions">
          <div className="wf-actions-meta">
            <span>scenario&nbsp;<b style={{ color: "var(--fg-2)" }}>{scenarioText ? `${scenarioText.length.toLocaleString()} chars` : "—"}</b></span>
            <span>question&nbsp;<b style={{ color: trimmedPrimaryQuestion ? "var(--fg-2)" : "var(--warn)" }}>{trimmedPrimaryQuestion ? "set" : "required"}</b></span>
            <span>init LLM&nbsp;<b style={{ color: useInitializer ? "var(--warn)" : "var(--fg-2)" }}>{useInitializer ? "on" : "off"}</b></span>
            <span>max ticks&nbsp;<b style={{ color: "var(--fg-2)" }}>{maxTicks}</b></span>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            {/* Draft persistence isn't wired yet; keep the button visible so
              * the layout doesn't shift but disable it with an explanatory
              * tooltip so users don't think they saved something they didn't. */}
            <button
              className="wf-secondary"
              disabled
              title="Draft saving is not wired yet — submit the form to persist."
              type="button"
            >
              Save draft
            </button>
            <button className="wf-cta" disabled={!canSubmit} onClick={() => submit.mutate()} type="button">
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
