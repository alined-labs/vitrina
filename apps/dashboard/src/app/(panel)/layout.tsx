import Link from "next/link";
import { NavLinks } from "@/components/nav-links";
import { SignOutButton } from "@/components/sign-out-button";
import { devAccessToken } from "@/lib/env";
import { serverSupabase } from "@/lib/supabase/server";

export default async function PanelLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const supabase = await serverSupabase();
  const email = supabase ? ((await supabase.auth.getUser()).data.user?.email ?? null) : null;

  return (
    <div className="min-h-screen md:grid md:grid-cols-[15rem_1fr]">
      <aside className="border-b border-zinc-200 bg-white md:border-b-0 md:border-r">
        <div className="px-5 py-4">
          <Link href="/" className="text-lg font-semibold tracking-tight">
            Vitrina
          </Link>
        </div>
        <NavLinks />
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="flex items-center justify-end gap-3 border-b border-zinc-200 bg-white px-6 py-3 text-sm">
          {supabase ? (
            <>
              <span className="text-zinc-600">{email}</span>
              <SignOutButton />
            </>
          ) : (
            <span className="rounded bg-amber-100 px-2 py-1 text-amber-900">
              {devAccessToken ? "Modo local: token de desarrollo" : "Modo local: sin inicio de sesión"}
            </span>
          )}
        </header>
        <main className="flex-1 px-6 py-8">{children}</main>
      </div>
    </div>
  );
}
