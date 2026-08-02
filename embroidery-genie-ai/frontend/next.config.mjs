/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    // Proxy API calls in development so the browser sees one origin and no
    // CORS preflight on every upload.
    const backend = process.env.BACKEND_URL || "http://localhost:8000";
    return [{ source: "/api/v1/:path*", destination: `${backend}/api/v1/:path*` }];
  },
};

export default nextConfig;
