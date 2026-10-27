import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { authEnabled, supabaseAnonKey, supabaseUrl } from "@/lib/env";

// Refreshes the seller's Supabase session on every page request and sends signed-out
// visitors to /login. In local mode (no Supabase settings) every page stays open.
// Foto → Vitrina is public (FR-072): the upload page and the shareable /v/{slug} pages.
const PUBLIC_PAGES = /^\/(foto|v\/[a-z0-9-]+)\/?$/;

export async function middleware(request: NextRequest) {
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
  // Pages only: not Next assets, not the API proxy (the API checks the JWT itself), not files.
  matcher: ["/((?!_next/static|_next/image|api/|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
