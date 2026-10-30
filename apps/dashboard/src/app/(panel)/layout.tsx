import Link from "next/link";
import { Logo } from "@/components/brand/logo";
import { NavLinks } from "@/components/nav-links";
import { SignOutButton } from "@/components/sign-out-button";
import { devAccessToken } from "@/lib/env";
import { serverSupabase } from "@/lib/supabase/server";

export default async function PanelLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const supabase = await serverSupabase();
  const email = supabase ? ((await supabase.auth.getUser()).data.user?.email ?? null) : null;

  return (
    <div className="min-h-screen md:grid md:grid-cols-[15rem_1fr]">
      <aside className="border-b border-line bg-surface md:border-b-0 md:border-r">
        <div className="px-5 py-4">
          <Link href="/" className="inline-block rounded-md">
            <Logo size={28} />
          </Link>
        </div>
        <NavLinks />
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="flex items-center justify-end gap-3 border-b border-line bg-surface px-6 py-3 text-sm">
          {supabase ? (
            <>
              <span className="text-fg-muted">{email}</span>
              <SignOutButton />
            </>
          ) : (
            <span className="badge badge--warning">
              {devAccessToken ? "Modo local: token de desarrollo" : "Modo local: sin inicio de sesión"}
            </span>
          )}
        </header>
        <main className="flex-1 px-6 py-8">{children}</main>
      </div>
    </div>
  );
}
