"use client";

import { AlertTriangle } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Button } from "@/components/ui/button";

/**
 * Route-level error boundary. Without this, an exception during render shows
 * Next.js's default screen — which in production is a blank page with no way
 * back into the app.
 */
export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    // eslint-disable-next-line no-console
    console.error("Unhandled UI error:", error);
  }, [error]);

  return (
    <div className="grid min-h-[60vh] place-items-center p-6">
      <div className="max-w-md text-center">
        <span className="mx-auto mb-4 grid size-12 place-items-center rounded-xl bg-destructive/15">
          <AlertTriangle className="size-6 text-destructive" />
        </span>
        <h1 className="text-lg font-semibold">Something went wrong on this screen</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Your designs and orders are unaffected — this is a display error. Try again, or
          go back to the dashboard.
        </p>
        {error.digest ? (
          <p className="mt-2 font-mono text-xs text-muted-foreground">ref {error.digest}</p>
        ) : null}
        <div className="mt-6 flex justify-center gap-2">
          <Button onClick={reset}>Try again</Button>
          <Link href="/dashboard">
            <Button variant="outline">Back to dashboard</Button>
          </Link>
        </div>
      </div>
    </div>
  );
}
