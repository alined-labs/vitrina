import { createBrowserClient } from "@supabase/ssr";
import { authEnabled, supabaseAnonKey, supabaseUrl } from "@/lib/env";

type BrowserClient = ReturnType<typeof createBrowserClient>;

let client: BrowserClient | null = null;

/** The browser Supabase client, or null in local mode (no Supabase settings). */
export function browserSupabase(): BrowserClient | null {
  if (!authEnabled) return null;
  client ??= createBrowserClient(supabaseUrl, supabaseAnonKey);
  return client;
}
