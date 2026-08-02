/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Baseline security headers. Next.js sends none by default.
  // Note: no Content-Security-Policy is set here — the app uses inline styles
  // from Tailwind and framer-motion, so a CSP needs a nonce pipeline to avoid
  // 'unsafe-inline'. That is tracked as a known limitation rather than shipped
  // as a policy weak enough to be theatre.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            // Microphone stays enabled for voice command mode.
            value: "camera=(), geolocation=(), microphone=(self)",
          },
        ],
      },
    ];
  },

  async rewrites() {
    // Proxy API calls in development so the browser sees one origin and no
    // CORS preflight on every upload.
    const backend = process.env.BACKEND_URL || "http://localhost:8000";
    return [{ source: "/api/v1/:path*", destination: `${backend}/api/v1/:path*` }];
  },
};

export default nextConfig;
