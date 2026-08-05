"use client";

/**
 * Last-resort boundary: catches errors thrown in the root layout itself, where
 * `app/error.tsx` cannot render. It has to supply its own <html>/<body>.
 */
export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <html lang="en">
      <body
        style={{
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: "#0a0b12",
          color: "#f1f2f6",
          fontFamily: "system-ui, sans-serif",
          margin: 0,
        }}
      >
        <div style={{ textAlign: "center", padding: "1.5rem", maxWidth: "28rem" }}>
          <h1 style={{ fontSize: "1.125rem", fontWeight: 600 }}>Embroidery Genie failed to load</h1>
          <p style={{ marginTop: "0.5rem", fontSize: "0.875rem", opacity: 0.7 }}>
            Reload the page. If this keeps happening, the API may be unreachable.
          </p>
          <button
            onClick={reset}
            style={{
              marginTop: "1.5rem",
              padding: "0.5rem 1rem",
              borderRadius: "0.5rem",
              border: 0,
              background: "#7c3aed",
              color: "white",
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
