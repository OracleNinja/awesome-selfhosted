import { createClient, type SupabaseClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

/**
 * False until both public Supabase values are present. The app shows a setup
 * screen rather than failing with a stack trace, which matters because the
 * very first thing a new deployment does is get these wrong.
 *
 * Only the anon key belongs here. It is designed to be public: every request
 * it signs is still filtered by row level security. The service_role key must
 * never appear in a VITE_ variable.
 */
export const isConfigured = Boolean(url && anonKey);

export const supabase: SupabaseClient = createClient(
  url ?? 'http://localhost:54321',
  anonKey ?? 'public-anon-key-not-set',
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  },
);
