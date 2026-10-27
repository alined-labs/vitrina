import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { authEnabled, supabaseAnonKey, supabaseUrl } from "@/lib/env";

/** A Supabase client bound to the request cookies, or null in local mode. */
export async function serverSupabase() {
  if (!authEnabled) return null;
  const cookieStore = await cookies();
  return createServerClient(supabaseUrl, supabaseAnonKey, {
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: (toSet) => {
        try {
          for (const { name, value, options } of toSet) cookieStore.set(name, value, options);
        } catch {
          // Server Components cannot write cookies; the middleware refreshes the session.
        }
      },
    },
  });
}
