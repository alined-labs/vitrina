import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { authEnabled, supabaseAnonKey, supabaseUrl } from "@/lib/env";

// Refreshes the seller's Supabase session on every page request and sends signed-out
// visitors to /login. In local mode (no Supabase settings) every page stays open.
// Foto → Vitrina is public (FR-072): the upload page and the shareable /v/{slug} pages.
const PUBLIC_PAGES = /^\/(foto|v\/[a-z0-9-]+)\/?$/;

// The API proxy drops forwarding headers, so behind Cloudflare the API would see every visitor
// at the proxy's address. The visitor's address travels in this header instead; it is always
// overwritten here, so a browser cannot supply its own (the public upload limit reads it).
const VISITOR_HEADER = "x-vitrina-visitor";

function withVisitor(request: NextRequest): NextResponse {
  const headers = new Headers(request.headers);
  const visitor = request.headers.get("cf-connecting-ip");
  if (visitor) headers.set(VISITOR_HEADER, visitor);
  else headers.delete(VISITOR_HEADER);
  return NextResponse.next({ request: { headers } });
}

export async function middleware(request: NextRequest) {
  if (request.nextUrl.pathname.startsWith("/api/")) return withVisitor(request);
  if (!authEnabled || PUBLIC_PAGES.test(request.nextUrl.pathname)) return NextResponse.next();

  let response = NextResponse.next({ request });
  const supabase = createServerClient(supabaseUrl, supabaseAnonKey, {
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
