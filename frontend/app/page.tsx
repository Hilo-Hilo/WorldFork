import Link from "next/link";

const PAGES = [
  { href: "/input", num: "01", title: "Input page", sub: "configure scenario · empty / loading / error" },
  { href: "/dashboard", num: "02", title: "Run dashboard", sub: "multiverse tree · live tick progress · operation log" },
  { href: "/report", num: "03", title: "Report viewer", sub: "final report · branch comparison · metrics" },
];

export default function Home() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 28,
        fontFamily: "var(--font-sans)",
        color: "var(--fg)",
        padding: "48px 24px",
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
          worldfork &nbsp;·&nbsp; ui preview
        </div>
        <h1 style={{ fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em", margin: "0 0 12px" }}>
          Three surfaces.
        </h1>
        <p style={{ color: "var(--muted)", margin: 0 }}>
          Static UI ports of the Claude Design handoff. No backend wiring yet — every value is mock data baked into
          the components.
        </p>
      </div>

      <nav style={{ display: "grid", gap: 12, width: "100%", maxWidth: 560 }}>
        {PAGES.map((p) => (
          <Link
            key={p.href}
            href={p.href}
            style={{
              display: "grid",
              gridTemplateColumns: "auto 1fr auto",
              gap: 18,
              alignItems: "center",
              padding: "16px 18px",
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              color: "var(--fg-2)",
              textDecoration: "none",
              transition: "border-color .15s ease, background .15s ease",
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "var(--muted-2)",
                letterSpacing: "0.05em",
              }}
            >
              {p.num}
            </span>
            <span>
              <div style={{ fontSize: 14, fontWeight: 500, color: "var(--fg)" }}>{p.title}</div>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--muted)",
                  marginTop: 2,
                }}
              >
                {p.sub}
              </div>
            </span>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 13,
                color: "var(--accent)",
              }}
            >
              →
            </span>
          </Link>
        ))}
      </nav>

      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--muted-2)",
          marginTop: 12,
        }}
      >
        try /input?state=loading or /input?state=error · /dashboard?orientation=vertical
      </div>
    </main>
  );
}
