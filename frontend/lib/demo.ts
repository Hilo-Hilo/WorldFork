/* Frontend-only demo run.
 *
 * Lets the dashboard render a rich, animated multiverse without any backend or
 * LLM calls. Triggered by navigating to /dashboard?run=demo (or any id starting
 * with the DEMO_PREFIX). All `api.*` functions short-circuit to the synthesizers
 * here when they see a demo id, so polling/UI code stays unchanged.
 *
 * Animation: a single module-level wall clock starts on first demo call and
 * advances "ticks" at TICK_INTERVAL_MS. Multiverses are emitted only once their
 * fork_tick_index is reached, ticks fill in as the clock progresses, and end
 * states (completed/terminated/frozen) flip in when a multiverse hits its endTick.
 */

import type {
  AgentEnvelope,
  AgentLog,
  BigBang,
  Job,
  Multiverse,
  RunSummary,
  Tick,
} from "./types";

export const DEMO_RUN_ID = "demo";
const DEMO_PREFIX = "demo";
const TICK_INTERVAL_MS = 1200; // one tick lands every ~1.2s
const MAX_TICK = 19; // 20 ticks total (t0..t19)
const RESET_PAUSE_MS = 4000; // after the run completes, hold for 4s, then loop

export function isDemoRun(id: string | null | undefined): boolean {
  return typeof id === "string" && (id === DEMO_PREFIX || id.startsWith(`${DEMO_PREFIX}:`));
}

/* ===== Animation clock =====
 * Single module-level state so concurrent api calls within one tab share it.
 * Resets when the loop runs to completion, so the demo loops forever. */

type ClockState = { startedAt: number };
const clock: { state: ClockState | null } = { state: null };

function ensureClock(): ClockState {
  if (clock.state === null) clock.state = { startedAt: Date.now() };
  return clock.state;
}

function elapsedTickFloat(): number {
  const c = ensureClock();
  const elapsedMs = Date.now() - c.startedAt;
  const totalCycleMs = (MAX_TICK + 1) * TICK_INTERVAL_MS + RESET_PAUSE_MS;
  // Loop the demo indefinitely so a real demo session can be left running.
  const phase = elapsedMs % totalCycleMs;
  if (phase >= (MAX_TICK + 1) * TICK_INTERVAL_MS) {
    // Hold past the last tick (the simulation appears "complete" briefly).
    return MAX_TICK + 0.999;
  }
  return phase / TICK_INTERVAL_MS;
}

function currentTick(): number {
  return Math.min(MAX_TICK, Math.floor(elapsedTickFloat()));
}

/* ===== Multiverse layout =====
 * Hand-tuned for visual interest: a primary timeline with a couple of
 * deepening branches and a couple of dead ends. Status transitions land at
 * endTick so the dashboard shows the lineage filling in over time. */

type DemoMv = {
  id: string;
  uiLabel: string;
  parent: string | null;
  forkTick: number | null; // null = root
  startTick: number; // tick this multiverse first exists at
  endTick: number; // last tick this multiverse runs (inclusive)
  depth: number;
  branchProb: number;
  pathProb: number;
  branchReason: string;
  endStatus: "completed" | "terminated" | "frozen";
};

const MULTIVERSES: DemoMv[] = [
  {
    id: "demo-mv-1",
    uiLabel: "M1",
    parent: null,
    forkTick: null,
    startTick: 0,
    endTick: MAX_TICK,
    depth: 0,
    branchProb: 1.0,
    pathProb: 1.0,
    branchReason: "Root timeline",
    endStatus: "completed",
  },
  {
    id: "demo-mv-2",
    uiLabel: "M2",
    parent: "demo-mv-1",
    forkTick: 3,
    startTick: 3,
    endTick: 12,
    depth: 1,
    branchProb: 0.42,
    pathProb: 0.42,
    branchReason: "Mayor pre-empts with apology",
    endStatus: "terminated",
  },
  {
    id: "demo-mv-3",
    uiLabel: "M3",
    parent: "demo-mv-2",
    forkTick: 6,
    startTick: 6,
    endTick: 14,
    depth: 2,
    branchProb: 0.31,
    pathProb: 0.13,
    branchReason: "Police union threatens job action",
    endStatus: "completed",
  },
  {
    id: "demo-mv-4",
    uiLabel: "M4",
    parent: "demo-mv-2",
    forkTick: 9,
    startTick: 9,
    endTick: MAX_TICK,
    depth: 2,
    branchProb: 0.55,
    pathProb: 0.23,
    branchReason: "Second video surfaces",
    endStatus: "completed",
  },
  {
    id: "demo-mv-5",
    uiLabel: "M5",
    parent: "demo-mv-3",
    forkTick: 10,
    startTick: 10,
    endTick: MAX_TICK,
    depth: 3,
    branchProb: 0.28,
    pathProb: 0.04,
    branchReason: "Counter-protest mobilizes downtown",
    endStatus: "completed",
  },
  {
    id: "demo-mv-6",
    uiLabel: "M6",
    parent: "demo-mv-1",
    forkTick: 8,
    startTick: 8,
    endTick: 15,
    depth: 1,
    branchProb: 0.18,
    pathProb: 0.18,
    branchReason: "Federal oversight request issued",
    endStatus: "frozen",
  },
  {
    id: "demo-mv-7",
    uiLabel: "M7",
    parent: "demo-mv-1",
    forkTick: 12,
    startTick: 12,
    endTick: MAX_TICK,
    depth: 1,
    branchProb: 0.49,
    pathProb: 0.49,
    branchReason: "Closed-door deal with police union",
    endStatus: "completed",
  },
];

function visibleMultiverses(t: number): DemoMv[] {
  return MULTIVERSES.filter((m) => m.startTick <= t);
}

function statusFor(m: DemoMv, t: number): string {
  if (t < m.startTick) return "candidate";
  if (t >= m.endTick) return m.endStatus;
  return "active";
}

/* ===== api.* synthesizers ===== */

const DEMO_BB_ID = "demo";
const DEMO_BB_NAME = "Demo run · viral civic incident";
const DEMO_CREATED_AT = "2026-05-04T05:00:00.000Z";

export function demoBigBang(): BigBang {
  const t = currentTick();
  const status = t >= MAX_TICK ? "completed" : "running";
  return {
    id: DEMO_BB_ID,
    name: DEMO_BB_NAME,
    description: "Synthetic demo — no backend or LLM calls.",
    scenario_input: {
      scenario_text_present: true,
      scenario_text_char_count: 480,
      // Empty initializer_output keeps the dashboard's "fallback used" banner
      // off while still being a non-null object the UI can read from.
      initializer_output: {},
    },
    status,
    current_config_version: 1,
    source_snapshot_id: null,
    created_at: DEMO_CREATED_AT,
    updated_at: new Date().toISOString(),
  };
}

export function demoMultiverses(): Multiverse[] {
  const t = currentTick();
  return visibleMultiverses(t).map((m) => ({
    id: m.id,
    big_bang_id: DEMO_BB_ID,
    parent_multiverse_id: m.parent,
    fork_tick_index: m.forkTick,
    ui_label: m.uiLabel,
    version: 1,
    depth: m.depth,
    status: statusFor(m, t),
    branch_reason: m.branchReason,
    branch_probability: m.branchProb,
    path_probability: m.pathProb,
    state: {},
    report_status: t >= m.endTick ? "ready" : "not_ready",
    ended_at: t >= m.endTick ? new Date().toISOString() : null,
    continued_from_report_version_id: null,
    created_at: DEMO_CREATED_AT,
    updated_at: new Date().toISOString(),
  }));
}

export function demoMultiverseTicks(multiverseId: string): Tick[] {
  const t = currentTick();
  const m = MULTIVERSES.find((x) => x.id === multiverseId);
  if (!m || t < m.startTick) return [];
  const last = Math.min(t, m.endTick);
  const ticks: Tick[] = [];
  for (let i = m.startTick; i <= last; i += 1) {
    const isCurrent = i === last && t < m.endTick;
    ticks.push({
      id: `${m.id}-tick-${i}`,
      big_bang_id: DEMO_BB_ID,
      multiverse_id: m.id,
      tick_index: i,
      ui_label: `${m.uiLabel}:T${i}`,
      status: isCurrent ? "running" : "final",
      summary: isCurrent ? `Tick ${i} simulation in progress for ${m.uiLabel}.` : null,
      artifact_id: null,
      idempotency_key: `${m.id}:tick:${i}`,
      created_at: DEMO_CREATED_AT,
      updated_at: new Date().toISOString(),
    });
  }
  return ticks;
}

export function demoJobs(): Job[] {
  const t = currentTick();
  const status = t >= MAX_TICK ? "completed" : "running";
  return [
    {
      id: "demo-job-1",
      job_type: "run_big_bang_until_complete",
      queue_name: "p1",
      status,
      big_bang_id: DEMO_BB_ID,
      payload: { big_bang_id: DEMO_BB_ID, max_total_ticks: MAX_TICK + 1 },
      result: {},
      error: null,
      idempotency_key: `demo:run-complete:${DEMO_BB_ID}`,
      attempt_number: 1,
      max_attempts: 3,
      retryable: true,
      queued_at: DEMO_CREATED_AT,
      started_at: DEMO_CREATED_AT,
      paused_at: null,
      interrupt_requested_at: null,
      interrupted_at: null,
      finished_at: status === "completed" ? new Date().toISOString() : null,
      created_at: DEMO_CREATED_AT,
      updated_at: new Date().toISOString(),
    },
  ];
}

/* Fake bottom log strip. The dashboard already filters logs by message
 * substring matching `runId.slice(0, 8)`, so each message embeds the demo id
 * plus a multiverse id so the right-rail "matched" filter also works. */
const ACTOR_NAMES = [
  "outraged_residents",
  "institutional_trust_residents",
  "public_safety_voters",
  "silent_majority",
  "counter_protest_bloc",
  "city_mayor",
  "police_chief",
  "platform_mod_lead",
  "local_news_media",
  "police_union",
];

export function demoAgentLogs(limit = 30): AgentLog[] {
  const t = currentTick();
  const visible = visibleMultiverses(t);
  const logs: AgentLog[] = [];
  // Walk back a few "recent" ticks across all visible multiverses so the
  // bottom strip shows a believable mix of activity.
  for (let mvIdx = 0; mvIdx < visible.length; mvIdx += 1) {
    const m = visible[mvIdx];
    const lastTick = Math.min(t, m.endTick);
    for (let tickOffset = 0; tickOffset < 3; tickOffset += 1) {
      const tickIdx = lastTick - tickOffset;
      if (tickIdx < m.startTick) break;
      // Mix of message types per tick.
      const msgs = [
        { src: "llm", status: "succeeded", template: (a: string) => `agent_cohort_${a}_tick_${tickIdx}` },
        { src: "llm", status: "succeeded", template: (a: string) => `agent_hero_${a}_tick_${tickIdx}` },
        { src: "llm", status: "succeeded", template: (a: string) => `event_summary_${m.id.slice(-8)}_v${tickIdx + 1}` },
        { src: "llm", status: tickOffset === 0 && tickIdx === lastTick && t < m.endTick ? "running" : "succeeded",
          template: () => `god_review_${m.id}_tick_${tickIdx}` },
      ];
      for (let mi = 0; mi < msgs.length; mi += 1) {
        const actor = ACTOR_NAMES[(mvIdx * 3 + tickOffset + mi) % ACTOR_NAMES.length];
        const msg = msgs[mi];
        logs.push({
          id: `demo-log-${m.id}-${tickIdx}-${mi}`,
          source: msg.src,
          status: msg.status,
          message: msg.template(actor),
          created_at: new Date(
            Date.now() - (tickOffset * 1500 + mi * 250 + mvIdx * 80),
          ).toISOString(),
        });
      }
    }
  }
  return logs.slice(0, limit);
}

export function demoAgentLogsEnvelope(limit = 30): AgentEnvelope<AgentLog[]> {
  const data = demoAgentLogs(limit);
  return { ok: true, data, meta: { total: data.length, limit, verbosity: "summary" } };
}

export function demoRunsListEnvelope(limit = 20): AgentEnvelope<RunSummary[]> {
  const t = currentTick();
  const visibleCount = visibleMultiverses(t).length;
  return {
    ok: true,
    data: [
      {
        id: DEMO_BB_ID,
        name: DEMO_BB_NAME,
        status: t >= MAX_TICK ? "completed" : "running",
        created_at: DEMO_CREATED_AT,
        multiverse_count: visibleCount,
      },
    ],
    meta: { total: 1, limit, verbosity: "summary" },
  };
}
