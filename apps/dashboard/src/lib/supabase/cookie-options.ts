import type { CookieOptionsWithName } from "@supabase/ssr";

/**
 * Session cookies are `Secure` in production builds, so a plain-HTTP request never carries the
 * session (`@supabase/ssr` leaves the flag off). `httpOnly` stays off: the browser client reads
 * the session from these cookies.
 */
export const sessionCookieOptions: CookieOptionsWithName = {
  secure: process.env.NODE_ENV === "production",
};
