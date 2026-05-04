# Demos

WorldFork has two first-class live workflows: the full runtime smoke and the Atlas onboarding demo.

## Full Runtime Smoke

```bash
worldfork smoke live
```

This is the end-to-end health check for a configured local backend. It uses real API credits and verifies that audited LLM calls use:

```text
openrouter/moonshotai/kimi-k2.6
openrouter/deepseek/deepseek-v4-flash
```

The smoke validates:

- readiness checks
- model configuration
- settings patch, reread, and restoration
- Big Bang pause/resume behavior
- root and branch tick execution
- manual branch intervention
- runtime checkpoints and node attempts
- job pause and synchronous run
- multiverse reports, final reports, Markdown render, and PDF render
- logs and final readiness

## Atlas Onboarding

```bash
worldfork demo atlas
```

Atlas reads `examples/test-big-bang.md`, creates the Atlas Resilience Crisis Big Bang, runs a root timeline, creates a manual transparency branch, allows God-agent-created branches under generous caps, drains every discovered timeline to terminal state, generates per-multiverse reports, generates a final cross-multiverse report, can render a PDF on request, and audits the default OpenRouter/DeepSeek model configuration.

While Atlas runs, the same runtime phases described in
[Runtime Walkthrough](runtime.md) apply: Big Bang initialization, checkpointed
tick execution, bounded parallel cohort decisions, aggregate event summary,
God-agent review with audited JSON tool calls, endpoint-ledger updates,
branching, report generation, and cost/timing collection.

Default Atlas sizing:

| Setting                     | Default           |
| --------------------------- | ----------------- |
| Tick duration               | 720 minutes       |
| Horizon                     | 30 simulated days |
| Derived terminal tick index | 60                |
| Active multiverse cap       | 64                |
| Branch depth cap            | 8                 |
| Branches per tick cap       | 8                 |
| Completion request cap      | 1000              |

Override defaults when you need a shorter or larger demonstration:

```bash
worldfork demo atlas \
  --tick-duration-minutes 720 \
  --horizon-days 7 \
  --max-active-multiverses 16 \
  --max-branch-depth 4 \
  --max-branches-per-tick 3 \
  --completion-max-requests 300
```

## Reading Demo Output

At completion, Atlas prints:

- `big_bang_id`
- `root_multiverse_id`
- `child_multiverse_id`
- terminal multiverse count
- final report version ID
- audited LLM call count
- follow-up `worldfork reports view` command
- follow-up `worldfork reports render --output` command
- follow-up `worldfork watch` command

Useful follow-up inspection commands:

```bash
worldfork runs workspace <big-bang-id>
worldfork runs cost <big-bang-id> --include-calls
worldfork runs estimate <big-bang-id>
worldfork ledgers list <big-bang-id>
worldfork ledgers path-mass <big-bang-id>
worldfork reports pack <big-bang-id> --mode summary
worldfork reports adjudication <big-bang-id>
worldfork logs list --status failed
```

Use the final report command first:

```bash
worldfork reports view <report-version-id>
```

Then render PDF only when you need a file artifact:

```bash
worldfork reports render <report-version-id> --format pdf --output report.pdf
```
