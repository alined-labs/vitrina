import createClient, { type Middleware } from "openapi-fetch";
import { apiBaseUrl, devAccessToken } from "@/lib/env";
import { connectionProblem } from "@/lib/problem-text";
import { browserSupabase } from "@/lib/supabase/browser";
import type { paths } from "./schema";

// Typed client for the Vitrina API. `schema.d.ts` is generated from the frozen contract
// (specs/001-vitrina-api/contracts/openapi.yaml) by scripts/gen-client; never edit it by hand.
// Browser use: every request carries the seller's Supabase access token (sellerAuth), or in
// development the local dev token when Supabase is not configured.
//
// A call never rejects for lack of an answer: a dropped connection or a request past its deadline
// comes back as `error` with a 503 response, so no screen stays on its "loading" state.

// A read that takes this long has stalled. A send may carry a large photo over a slow phone line.
const READ_DEADLINE_MS = 30_000;
const SEND_DEADLINE_MS = 120_000;

/** Reads the whole answer here, so a connection lost halfway counts as a failed request too. */
async function fetchWithDeadline(request: Request): Promise<Response> {
  const deadline = request.method === "GET" ? READ_DEADLINE_MS : SEND_DEADLINE_MS;
  const response = await fetch(request, { signal: AbortSignal.timeout(deadline) });
  if (response.body === null) return response;
  const { status, statusText, headers } = response;
  return new Response(await response.arrayBuffer(), { status, statusText, headers });
}

let leaving = false;

/** The API refused the session: clear it here, or /login would send the seller straight back. */
async function endExpiredSession(): Promise<void> {
  const supabase = browserSupabase();
  if (!supabase || leaving) return; // local mode has no sign-in to go back to
  leaving = true;
  try {
    await supabase.auth.signOut({ scope: "local" });
  } catch {
    // The redirect below still shows the sign-in form or, with a live session, the panel.
  }
  // A full load, not a router push: this runs outside any component, and the page must drop the session.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign("/login?sesion=expirada");
}

const session: Middleware = {
  async onRequest({ request }) {
    const supabase = browserSupabase();
    let token = "";
    if (supabase) {
      try {
        const { data } = await supabase.auth.getSession();
        token = data.session?.access_token ?? "";
      } catch {
        // No session could be read: the request goes without a token and the API answers 401.
      }
    }
    token ||= devAccessToken; // local mode in development: `make dev-token`
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  async onResponse({ response }) {
    if (response.status === 401) await endExpiredSession();
    return response;
  },
  onError() {
    return new Response(connectionProblem, { status: 503, headers: { "Content-Type": "application/problem+json" } });
  },
};

export const api = createClient<paths>({ baseUrl: apiBaseUrl, fetch: fetchWithDeadline });
api.use(session);

export type { components, paths } from "./schema";
