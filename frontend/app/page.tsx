"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

const STATUS_TONE: Record<string, string> = {
  running: "var(--accent)",
  paused: "var(--warn)",
  draft: "var(--muted)",
  completed: "var(--muted-2)",
  terminated: "var(--muted-2)",
  failed: "var(--danger)",
};

export default function Home() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["runs", "summary"],
    queryFn: () => api.listRuns(20),
    refetchInterval: 3000,
  });

  const runs = data?.data ?? [];

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
          worldfork &nbsp;·&nbsp; runs
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
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>→</span>
        </Link>
        <Link
          href="/dashboard?run=demo"
          title="Render the dashboard against an animated synthetic run — no backend or LLM calls."
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
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
            no llm
          </span>
        </Link>
      </div>

      <div style={{ display: "grid", gap: 8, width: "100%", maxWidth: 720 }}>
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
            backend unreachable &nbsp;·&nbsp; {(error as Error).message}
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
            loading runs…
          </div>
        )}
        {runs.map((r) => (
          <Link
            key={r.id}
            href={`/dashboard?run=${r.id}`}
            style={{
              display: "grid",
              gridTemplateColumns: "1fr auto auto",
              alignItems: "center",
              gap: 18,
              padding: "14px 16px",
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              color: "var(--fg-2)",
              textDecoration: "none",
              fontSize: 13,
            }}
          >
            <span>
              <div style={{ color: "var(--fg)", fontWeight: 500 }}>{r.name}</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", marginTop: 3 }}>
                {r.id.slice(0, 8)}… &nbsp;·&nbsp; {new Date(r.created_at).toLocaleString()} &nbsp;·&nbsp;{" "}
                {r.multiverse_count} multiverse{r.multiverse_count !== 1 ? "s" : ""}
              </div>
            </span>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: STATUS_TONE[r.status] ?? "var(--muted)",
                border: "1px solid var(--border)",
                padding: "2px 7px",
                borderRadius: 3,
              }}
            >
              {r.status}
            </span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--accent)" }}>→</span>
          </Link>
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
