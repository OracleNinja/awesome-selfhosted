import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase } from '../lib/supabase';
import { fetchProfile, touchPresence } from '../lib/api';
import type { Profile } from '../lib/types';

type SessionState = {
  session: Session | null;
  profile: Profile | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

const SessionContext = createContext<SessionState | null>(null);

/** How often we mark the signed-in user as still around. */
const PRESENCE_INTERVAL_MS = 120_000;

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const userId = session?.user.id ?? null;
  const lastPing = useRef(0);

  useEffect(() => {
    let cancelled = false;
    supabase.auth.getSession().then(({ data }) => {
      if (cancelled) return;
      setSession(data.session);
      if (!data.session) setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
      if (!next) {
        setProfile(null);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
      sub.subscription.unsubscribe();
    };
  }, []);

  const load = useCallback(async () => {
    if (!userId) {
      setProfile(null);
      setLoading(false);
      return;
    }
    try {
      setError(null);
      setProfile(await fetchProfile(userId));
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load]);

  // Presence: only ever a timestamp, only while the app is actually open.
  useEffect(() => {
    if (!userId || profile?.status !== 'active') return;
    const ping = () => {
      if (document.visibilityState !== 'visible') return;
      const now = Date.now();
      if (now - lastPing.current < PRESENCE_INTERVAL_MS) return;
      lastPing.current = now;
      void touchPresence();
    };
    ping();
    const timer = window.setInterval(ping, PRESENCE_INTERVAL_MS);
    document.addEventListener('visibilitychange', ping);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', ping);
    };
  }, [userId, profile?.status]);

  const value = useMemo<SessionState>(
    () => ({ session, profile, loading, error, refresh: load }),
    [session, profile, loading, error, load],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession must be used inside a SessionProvider');
  return value;
}

/** The signed-in profile, for screens that only render when one exists. */
export function useProfile(): Profile {
  const { profile } = useSession();
  if (!profile) throw new Error('No profile loaded');
  return profile;
}
