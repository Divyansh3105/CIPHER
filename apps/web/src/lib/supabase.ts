// The browser's Supabase client, used only for auth. Data still goes through
// the FastAPI backend (lib/api.ts), which verifies the session token this
// client holds.
//
// Both values are public by design: the anon key grants nothing on its own,
// and the backend never trusts the browser's word for who the user is.

import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!url || !anonKey) {
  throw new Error(
    "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY must be set (apps/web/.env.local)."
  );
}

export const supabase = createClient(url, anonKey);

export async function authHeader(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  return data.session ? { Authorization: `Bearer ${data.session.access_token}` } : {};
}
