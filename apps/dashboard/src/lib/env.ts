// Public configuration, inlined at build time (NEXT_PUBLIC_*). Every value is optional so the
// shell builds and runs before the Supabase project exists ("modo local").

/** Where the browser sends API calls: the same-origin proxy in next.config.ts by default. */
export const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || "/api/v1";

export const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

/**
 * Development only: a token from `make dev-token` lets local mode call the API without Supabase.
 * Production builds never read it, so the value cannot end up in a deployed bundle.
 */
export const devAccessToken =
  process.env.NODE_ENV !== "production" ? (process.env.NEXT_PUBLIC_DEV_ACCESS_TOKEN ?? "") : "";

/** Seller sign-in (spec §2, Supabase JWT) is on only when both settings hold real values. */
export const authEnabled =
  /^https?:\/\//.test(supabaseUrl) && supabaseAnonKey !== "" && !supabaseAnonKey.startsWith("<");
