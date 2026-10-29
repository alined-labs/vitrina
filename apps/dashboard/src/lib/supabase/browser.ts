import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { authEnabled, supabaseAnonKey, supabaseUrl } from "@/lib/env";
import { sessionCookieOptions } from "@/lib/supabase/cookie-options";

let client: SupabaseClient | null = null;

/** The browser Supabase client, or null in local mode (no Supabase settings). */
export function browserSupabase(): SupabaseClient | null {
  if (!authEnabled) return null;
  client ??= createBrowserClient(supabaseUrl, supabaseAnonKey, { cookieOptions: sessionCookieOptions });
  return client;
}
