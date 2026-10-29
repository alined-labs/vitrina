import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { authEnabled, supabaseAnonKey, supabaseUrl } from "@/lib/env";
import { sessionCookieOptions } from "@/lib/supabase/cookie-options";

// Refreshes the seller's Supabase session on every page request and sends signed-out
// visitors to /login. In local mode (no Supabase settings) every page stays open.
// Foto → Vitrina is public (FR-072): the upload page and the shareable /v/{slug} pages.
const PUBLIC_PAGES = /^\/(foto|v\/[a-z0-9-]+)\/?$/;

// The API proxy drops forwarding headers, so behind Cloudflare the API would see every visitor
// at the proxy's address. The visitor's address travels in the first header instead, and the
// second carries the secret this proxy shares with the API, which trusts the address only when
// the secret matches: a caller that skips the dashboard cannot choose its own (the public upload
// limit reads it). Both are always overwritten here, so a browser cannot supply either.
const VISITOR_HEADER = "x-vitrina-visitor";
const PROXY_SECRET_HEADER = "x-vitrina-proxy-secret";

function withVisitor(request: NextRequest): NextResponse {
  const headers = new Headers(request.headers);
  const visitor = request.headers.get("cf-connecting-ip");
  // A Worker secret, read on every request; never a NEXT_PUBLIC_ value. Unset in local work.
  const secret = process.env.VISITOR_PROXY_SECRET;
  if (visitor && secret) {
    headers.set(VISITOR_HEADER, visitor);
    headers.set(PROXY_SECRET_HEADER, secret);
  } else {
    headers.delete(VISITOR_HEADER);
    headers.delete(PROXY_SECRET_HEADER);
  }
  return NextResponse.next({ request: { headers } });
}

export async function middleware(request: NextRequest) {
  if (request.nextUrl.pathname.startsWith("/api/")) return withVisitor(request);
  if (!authEnabled || PUBLIC_PAGES.test(request.nextUrl.pathname)) return NextResponse.next();

  let response = NextResponse.next({ request });
  const supabase = createServerClient(supabaseUrl, supabaseAnonKey, {
    cookieOptions: sessionCookieOptions,
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (toSet) => {
        for (const { name, value } of toSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of toSet) response.cookies.set(name, value, options);
      },
    },
  });

  const {
    data: { user },
  } = await supabase.auth.getUser();
  const onLogin = request.nextUrl.pathname === "/login";
  if (!user && !onLogin) return NextResponse.redirect(new URL("/login", request.url));
  if (user && onLogin) return NextResponse.redirect(new URL("/", request.url));
  return response;
}

export const config = {
  // Pages and the API proxy (for the visitor header only: the API checks the JWT itself); not
  // Next assets, not files.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
