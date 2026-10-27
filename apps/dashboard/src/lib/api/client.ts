import createClient, { type Middleware } from "openapi-fetch";
import { apiBaseUrl, devAccessToken } from "@/lib/env";
import { browserSupabase } from "@/lib/supabase/browser";
import type { paths } from "./schema";

// Typed client for the Vitrina API. `schema.d.ts` is generated from the frozen contract
// (specs/001-vitrina-api/contracts/openapi.yaml) by scripts/gen-client; never edit it by hand.
// Browser use: every request carries the seller's Supabase access token (sellerAuth), or in
// development the local dev token when Supabase is not configured.

const sellerToken: Middleware = {
  async onRequest({ request }) {
    const supabase = browserSupabase();
    let token = "";
    if (supabase) {
      const { data } = await supabase.auth.getSession();
      token = data.session?.access_token ?? "";
    }
    token ||= devAccessToken; // local mode in development: `make dev-token`
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: apiBaseUrl });
api.use(sellerToken);

export type { components, paths } from "./schema";
