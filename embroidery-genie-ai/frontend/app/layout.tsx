import type { Metadata, Viewport } from "next";
import "./globals.css";

import { AppProviders } from "@/app/providers";

export const metadata: Metadata = {
  title: {
    default: "Embroidery Genie AI",
    template: "%s · Embroidery Genie AI",
  },
  description:
    "Turn artwork into machine-ready embroidery files. AI analysis, auto-digitizing, "
    + "stitch simulation, thread charts and production management in one workspace.",
  applicationName: "Embroidery Genie AI",
  keywords: [
    "embroidery digitizing", "DST", "PES", "stitch file", "auto digitizing",
    "embroidery software", "production management",
  ],
};

export const viewport: Viewport = {
  themeColor: "#0a0b12",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-background font-sans">
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
