"use client";

import * as React from "react";

import { ToastProvider } from "@/components/ui/overlay";
import { api, ApiError, type Me, type StudioReference } from "@/lib/api";

type SessionState = {
  me: Me | null;
  reference: StudioReference | null;
  loading: boolean;
  error: string | null;
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
  const [me, setMe] = React.useState<Me | null>(null);
  const [reference, setReference] = React.useState<StudioReference | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [profile, ref] = await Promise.all([api.me(), api.reference()]);
      setMe(profile);
      setReference(ref);
    } catch (exception) {
      const message =
        exception instanceof ApiError
          ? exception.message
          : "Could not reach the API. Is the backend running?";
      setError(message);
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = React.useMemo(
    () => ({ me, reference, loading, error, refresh }),
    [me, reference, loading, error, refresh],
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
