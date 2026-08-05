"use client";

import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { ToastProvider } from "@/components/ui/overlay";
import { api, ApiError, type Me, type StudioReference } from "@/lib/api";
import { isSupabaseConfigured } from "@/lib/supabase";

type SessionState = {
  me: Me | null;
  reference: StudioReference | null;
  loading: boolean;
  error: string | null;
  unauthenticated: boolean;
  refresh: () => Promise<void>;
};

const SessionContext = React.createContext<SessionState | null>(null);

/**
 * Loads the caller's identity and the studio reference data once, then shares
 * them app-wide. Reference data (fabrics, formats, machine presets, mockup
 * templates) is static per deployment, so fetching it per page would be pure
 * waste.
 */
export function AppProviders({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = React.useState<Me | null>(null);
  const [reference, setReference] = React.useState<StudioReference | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [unauthenticated, setUnauthenticated] = React.useState(false);

  const refresh = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [profile, ref] = await Promise.all([api.me(), api.reference()]);
      setMe(profile);
      setReference(ref);
      setUnauthenticated(false);
    } catch (exception) {
      if (exception instanceof ApiError && exception.isUnauthorized) {
        // The API rejected the token. Never leave the shell up pretending to
        // be signed in — the data would all be 401s anyway.
        setUnauthenticated(true);
        setMe(null);
        setError(null);
      } else {
        setError(
          exception instanceof ApiError
            ? exception.message
            : "Could not reach the API. Is the backend running?",
        );
        setMe(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (!unauthenticated || !isSupabaseConfigured) return;
    if (pathname === "/login" || pathname === "/") return;
    router.replace("/login");
  }, [unauthenticated, pathname, router]);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = React.useMemo(
    () => ({ me, reference, loading, error, unauthenticated, refresh }),
    [me, reference, loading, error, unauthenticated, refresh],
  );

  return (
    <SessionContext.Provider value={value}>
      <ToastProvider>{children}</ToastProvider>
    </SessionContext.Provider>
  );
}

export function useSession() {
  const context = React.useContext(SessionContext);
  if (!context) throw new Error("useSession must be used inside <AppProviders>");
  return context;
}
