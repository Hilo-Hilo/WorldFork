import Link from "next/link";

export default function Home() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 18,
        fontFamily: "var(--font-sans)",
        color: "var(--fg)",
      }}
    >
      <h1 style={{ fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em" }}>WorldFork</h1>
      <p style={{ color: "var(--muted)", maxWidth: "44ch", textAlign: "center" }}>
        Branching social simulation. Page surfaces are being implemented one at a time.
      </p>
      <nav style={{ display: "flex", gap: 12, marginTop: 12 }}>
        <Link
          href="/report"
          style={{
            background: "var(--accent)",
            color: "var(--accent-fg)",
            padding: "11px 18px",
            borderRadius: 6,
            fontSize: 13.5,
            fontWeight: 600,
            textDecoration: "none",
          }}
        >
          Report viewer →
        </Link>
      </nav>
    </main>
  );
}
