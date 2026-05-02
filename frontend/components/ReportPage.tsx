"use client";

import { useState } from "react";

/* ----------------------------------------------------------------------------
 * Report Viewer — ported from the Claude Design handoff (worldfork-ui/report.jsx).
 * Mock data is intentional: the design package shipped with this content baked
 * in, and the user asked to wire UI only (no backend connection yet).
 * -------------------------------------------------------------------------- */

type Version = {
  id: string;
  name: string;
  ver: string;
  time: string;
  time2: string;
  final?: boolean;
};

const VERSIONS: Version[] = [
  { id: "v3", name: "Final · Big Bang report", ver: "v3", time: "12 min ago", time2: "180 ticks · 9 multiverses", final: true },
  { id: "v2", name: "M5 · Court disclosure", ver: "v2", time: "38 min ago", time2: "tick 78 · partial" },
  { id: "v1", name: "M2 · Transparent dashboard", ver: "v2", time: "1h 04m ago", time2: "tick 47 · checkpoint" },
  { id: "v0", name: "Mid-run synthesis", ver: "v1", time: "2h 22m ago", time2: "tick 32 · auto" },
];

type TocItem = { id: string; label: string; depth: number };

const TOC: TocItem[] = [
  { id: "summary", label: "Executive summary", depth: 1 },
  { id: "divergence", label: "Largest divergence", depth: 1 },
  { id: "transparency", label: "Did transparency build trust?", depth: 1 },
  { id: "accs", label: "ACCS as legitimacy sink", depth: 1 },
  { id: "branches", label: "Branch comparison", depth: 1 },
  { id: "cohorts", label: "Cohort splits", depth: 1 },
  { id: "courts", label: "Courts: stabilize or paralyze?", depth: 1 },
  { id: "recommend", label: "Policymaker takeaways", depth: 1 },
];

function Spark({ values, color = "var(--accent)" }: { values: number[]; color?: string }) {
  const w = 240;
  const h = 36;
  const p = 2;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = (w - p * 2) / (values.length - 1);
  const pts = values.map(
    (v, i) => `${p + i * step},${p + (h - p * 2) * (1 - (v - min) / range)}`,
  );
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.4" />
      <polyline
        points={`${p},${h - p} ${pts.join(" ")} ${w - p},${h - p}`}
        fill={color}
        fillOpacity="0.08"
        stroke="none"
      />
    </svg>
  );
}

type CompareRow = {
  name: string;
  sub: string;
  v: number[];
  best?: number;
  worst?: number;
};

const COMPARE_ROWS: CompareRow[] = [
  { name: "Public trust", sub: "cohort-weighted", v: [0.71, 0.34, 0.64, 0.66], best: 0, worst: 1 },
  { name: "Service speed", sub: "repair throughput", v: [0.62, 0.74, 0.51, 0.57], best: 1 },
  { name: "Rumor velocity", sub: "lower is better", v: [0.22, 0.78, 0.28, 0.30], worst: 1, best: 0 },
  { name: "Court load", sub: "filings / tick", v: [0.42, 0.81, 0.74, 0.45], worst: 1 },
  { name: "Mutual aid", sub: "active hubs", v: [0.65, 0.31, 0.55, 0.62], best: 0 },
  { name: "ACCS fairness", sub: "perceived", v: [0.61, 0.22, 0.58, 0.71], best: 3, worst: 1 },
];

export default function ReportPage() {
  const [activeVer, setActiveVer] = useState("v3");
  const [activeSec, setActiveSec] = useState("divergence");

  return (
    <div className="rpt" data-screen-label="03 Report viewer">
      {/* TOP */}
      <header className="rpt-top">
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
          <span className="name">Atlas Resilience Crisis</span>
          <span className="sep">/</span>
          <span className="meta">reports</span>
        </div>
        <div className="center">
          <span>report&nbsp;<b>final.v3</b></span>
          <span>rendered&nbsp;<b>12&nbsp;min&nbsp;ago</b></span>
          <span>scope&nbsp;<b>9&nbsp;multiverses</b></span>
          <span>signed&nbsp;<b>sha256:c3a2…f81e</b></span>
        </div>
        <div className="right">
          <button className="dash-btn">Compare versions</button>
          <button className="dash-btn">Re-render</button>
          <button className="dash-btn">Export&nbsp;.md</button>
          <button className="dash-btn is-primary">Export&nbsp;.pdf</button>
        </div>
      </header>

      {/* BODY */}
      <div className="rpt-body">
        {/* LEFT NAV */}
        <aside className="rpt-nav">
          <div className="rpt-nav-section">
            <h3>Versions</h3>
            {VERSIONS.map((v) => (
              <div
                key={v.id}
                className={`rpt-version ${activeVer === v.id ? "is-active" : ""} ${v.final ? "final" : ""}`}
                onClick={() => setActiveVer(v.id)}
              >
                <div>
                  <div className="name">{v.name}</div>
                  <div className="meta">
                    <span>{v.time}</span>
                    <span className="sep">·</span>
                    <span>{v.time2}</span>
                  </div>
                </div>
                <span className="ver">{v.ver}</span>
              </div>
            ))}
          </div>
          <div className="rpt-toc">
            <h3>Contents</h3>
            <ol>
              {TOC.map((t) => (
                <li
                  key={t.id}
                  className={`depth-${t.depth} ${activeSec === t.id ? "is-active" : ""}`}
                  onClick={() => setActiveSec(t.id)}
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
            <div className="eyebrow">
              <span className="rule" />
              final report &nbsp;·&nbsp; v3
            </div>
            <h1>Atlas: which governance choices preserved legitimacy?</h1>
            <p className="deck">
              A 180-tick run produced nine multiverses. Three preserved both legitimacy and service
              capacity; four traded one for the other; two collapsed on rumor velocity. The branch that
              mattered most happened on tick&nbsp;14.
            </p>

            <div className="frontmatter">
              <div>
                <span className="k">scenario</span>
                <span className="v">test-big-bang.md</span>
              </div>
              <div>
                <span className="k">ticks</span>
                <span className="v">180 / 180</span>
              </div>
              <div>
                <span className="k">multiverses</span>
                <span className="v">9 (4 terminal, 5 paused)</span>
              </div>
              <div>
                <span className="k">runtime</span>
                <span className="v">04h&thinsp;12m</span>
              </div>
            </div>

            <h2>Executive summary</h2>
            <p>
              Across nine timelines, the single decision with the largest downstream effect was whether
              the Atlas Regional Council <strong>published a live rationing dashboard on day&nbsp;1</strong>.
              Branches that did saw rumor velocity drop ~38% by tick&nbsp;30; branches that delayed
              disclosure saw legitimacy compounding losses that no later intervention recovered.
            </p>
            <p>
              ACCS authority was a <em>secondary</em> driver. Where ACCS remained advisory and was paired
              with a public audit panel — see&nbsp;
              <a className="mvref" href="#">M9 ↗ Audit reform</a> — outcomes converged on the
              cooperative-resilience archetype. Where ACCS authority expanded under ambiguity (
              <a className="mvref" href="#">M3 ↗ Command stability</a>), service speed improved briefly
              but legitimacy fell below recovery threshold by tick&nbsp;90.
            </p>

            <div className="rpt-finding">
              <div className="head">
                <span className="tag">finding</span>
                <span className="id">F-01 &nbsp;·&nbsp; confidence 0.81</span>
              </div>
              <h4>The dashboard branch fixed the rumor problem before it started.</h4>
              <p>
                Publishing pressure-district data <em>with acknowledged uncertainty</em> outperformed any
                later correction strategy in 7&nbsp;of&nbsp;9 multiverses. Cohort
                <code>&nbsp;low_pressure_district_residents&nbsp;</code>shifted from oppositional to
                procedural-trust within 6 ticks of disclosure.
              </p>
            </div>

            <h2>Largest divergence</h2>
            <p>
              The dashboard fork at tick&nbsp;14 split the multiverse along the transparency axis. The
              chart below tracks public-trust (cohort-weighted) for the four leading branches.
            </p>

            <div className="rpt-sparks">
              <div className="rpt-spark">
                <span className="label">M2 · transparent</span>
                <div className="value">
                  0.71<span className="delta">+0.18</span>
                </div>
                <Spark values={[0.45, 0.48, 0.5, 0.55, 0.58, 0.62, 0.65, 0.68, 0.7, 0.71]} />
              </div>
              <div className="rpt-spark">
                <span className="label">M3 · command</span>
                <div className="value">
                  0.34<span className="delta down">−0.11</span>
                </div>
                <Spark
                  values={[0.45, 0.46, 0.45, 0.42, 0.4, 0.39, 0.37, 0.36, 0.35, 0.34]}
                  color="var(--danger)"
                />
              </div>
              <div className="rpt-spark">
                <span className="label">M4 · audit freeze</span>
                <div className="value">
                  0.58<span className="delta">+0.05</span>
                </div>
                <Spark values={[0.45, 0.46, 0.48, 0.5, 0.52, 0.54, 0.55, 0.56, 0.57, 0.58]} />
              </div>
              <div className="rpt-spark">
                <span className="label">M9 · audit reform</span>
                <div className="value">
                  0.66<span className="delta">+0.12</span>
                </div>
                <Spark values={[0.5, 0.52, 0.55, 0.58, 0.6, 0.61, 0.63, 0.64, 0.65, 0.66]} />
              </div>
            </div>

            <h2>Branch comparison</h2>
            <table className="rpt-table">
              <thead>
                <tr>
                  <th>Multiverse</th>
                  <th>Trust</th>
                  <th>Service</th>
                  <th>Rumor velocity</th>
                  <th>Court load</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>M2 · transparent</td>
                  <td className="delta-up">0.71</td>
                  <td className="delta-up">0.62</td>
                  <td className="delta-up">low</td>
                  <td className="delta-flat">moderate</td>
                  <td>cooperative resilience</td>
                </tr>
                <tr>
                  <td>M3 · command</td>
                  <td className="delta-down">0.34</td>
                  <td className="delta-up">0.74</td>
                  <td className="delta-down">high</td>
                  <td className="delta-down">heavy</td>
                  <td>opaque emergency regime</td>
                </tr>
                <tr>
                  <td>M5 · court disclosure</td>
                  <td className="delta-up">0.64</td>
                  <td className="delta-flat">0.51</td>
                  <td className="delta-up">low</td>
                  <td className="delta-down">heavy</td>
                  <td>legitimacy reset</td>
                </tr>
                <tr>
                  <td>M9 · audit reform</td>
                  <td className="delta-up">0.66</td>
                  <td className="delta-flat">0.57</td>
                  <td className="delta-up">low</td>
                  <td className="delta-flat">moderate</td>
                  <td>civic-tech reform</td>
                </tr>
              </tbody>
            </table>

            <h2>Did transparency build trust?</h2>
            <p>
              Yes — but conditionally. Transparency reduced rumor velocity only when the published data{" "}
              <em>matched ground truth within ~10%</em>. In one branch (
              <a className="mvref" href="#">M6 ↗ Hill zone backlash</a>), publishing a dashboard whose
              pressure estimates lagged actual outages by 4 hours <strong>amplified</strong> rather than
              corrected rumors.
            </p>
            <ul>
              <li>Disclosure reduced rumor velocity in 6/9 multiverses.</li>
              <li>Disclosure increased legal pressure in 4/9 — survivable in 3.</li>
              <li>Hill-zone households flipped to &ldquo;targeted&rdquo; framing in 2 branches.</li>
            </ul>

            <h2>ACCS as legitimacy sink</h2>
            <p>
              When ACCS authority was ambiguous, every faction projected blame onto it. The pattern is
              consistent across <a className="mvref" href="#">M3</a>,{" "}
              <a className="mvref" href="#">M7</a>, and <a className="mvref" href="#">M8</a>. Branches
              that committed to either <em>freeze</em> or <em>structured oversight</em> outperformed
              branches that left the question open.
            </p>

            <h2>Policymaker takeaways</h2>
            <ul>
              <li>Disclose early, with calibrated uncertainty bands.</li>
              <li>Treat AI authority as a binary; ambiguity is the worst option.</li>
              <li>Integrate mutual-aid logistics under a public accountability rule.</li>
              <li>Court disclosure orders stabilize legitimacy faster than apology.</li>
            </ul>
          </div>
        </article>

        {/* RIGHT: aside */}
        <aside className="rpt-aside">
          <div className="rpt-aside-section">
            <h4>
              Render <span className="hint">live</span>
            </h4>
            <div className="rpt-render-status">
              <span className="dot" />
              <span>
                up to date &nbsp;·&nbsp; rendered from version&nbsp;
                <b style={{ color: "var(--fg-2)" }}>final.v3</b>
              </span>
            </div>
          </div>

          <div className="rpt-aside-section">
            <h4>
              Outcomes <span className="hint">archetypes</span>
            </h4>
            <div className="outcome-list">
              <div className="outcome-item is-best">
                <span className="id">M2</span>
                <span className="label">Transparent resilience</span>
                <span className="pct">22%</span>
              </div>
              <div className="outcome-item">
                <span className="id">M9</span>
                <span className="label">Audit reform</span>
                <span className="pct">19%</span>
              </div>
              <div className="outcome-item">
                <span className="id">M5</span>
                <span className="label">Court-governed emergency</span>
                <span className="pct">15%</span>
              </div>
              <div className="outcome-item">
                <span className="id">M8</span>
                <span className="label">Neighborhood federation</span>
                <span className="pct">14%</span>
              </div>
              <div className="outcome-item">
                <span className="id">M3</span>
                <span className="label">Command stability</span>
                <span className="pct">11%</span>
              </div>
              <div className="outcome-item">
                <span className="id">M7</span>
                <span className="label">Labor bottleneck</span>
                <span className="pct">10%</span>
              </div>
            </div>
          </div>

          <div className="rpt-aside-section">
            <h4>
              Compare metrics <span className="hint">M2 · M3 · M5 · M9</span>
            </h4>
            {COMPARE_ROWS.map((row) => (
              <div key={row.name} className="compare-row">
                <div className="name">
                  {row.name}
                  <span className="sub">{row.sub}</span>
                </div>
                <div className="bars">
                  {row.v.map((v, i) => (
                    <div
                      key={i}
                      className={`bar ${row.best === i ? "is-best" : ""} ${row.worst === i ? "is-worst" : ""}`}
                      style={{ height: `${Math.max(2, v * 28)}px` }}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="rpt-aside-section">
            <h4>Actions</h4>
            <div className="rpt-actions">
              <button className="rpt-action">
                Open in dashboard<span className="kbd">⌘D</span>
              </button>
              <button className="rpt-action">
                Branch from finding F-01<span className="kbd">⌘B</span>
              </button>
              <button className="rpt-action">
                Continue M5 to tick 180<span className="kbd">⌘↵</span>
              </button>
              <button className="rpt-action">
                Copy citation<span className="kbd">⌘C</span>
              </button>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
