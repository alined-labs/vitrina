"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type SubmitEvent } from "react";
import { Logo } from "@/components/brand/logo";
import { browserSupabase } from "@/lib/supabase/browser";

export default function LoginPage() {
  const router = useRouter();
  const supabase = browserSupabase();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) return;
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: String(form.get("email") ?? ""),
        password: String(form.get("password") ?? ""),
      });
      if (signInError) {
        setError("Correo o contraseña incorrectos.");
        return;
      }
    } catch {
      // A failed request must not leave the button stuck on "Entrando…".
      setError("No pudimos conectar. Revisa tu conexión e intenta de nuevo.");
      return;
    } finally {
      setPending(false);
    }
    router.replace("/");
    router.refresh();
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6 card p-6">
        <div className="space-y-3">
          <h1>
            <Logo size={36} />
          </h1>
          <p className="text-sm text-fg-muted">Entra para ver tu catálogo, tus anuncios y tus conversaciones.</p>
        </div>
        {supabase ? (
          <form onSubmit={(event) => void onSubmit(event)} className="space-y-4">
            <label className="block space-y-1 text-sm">
              <span>Correo</span>
              <input name="email" type="email" required autoComplete="email" className="input w-full" />
            </label>
            <label className="block space-y-1 text-sm">
              <span>Contraseña</span>
              <input name="password" type="password" required autoComplete="current-password" className="input w-full" />
            </label>
            {error ? (
              <p role="alert" className="text-sm text-danger-fg">
                {error}
              </p>
            ) : null}
            <button type="submit" disabled={pending} className="btn btn--primary btn--block">
              {pending ? "Entrando…" : "Entrar"}
            </button>
          </form>
        ) : (
          <div className="space-y-3 text-sm">
            <p className="notice notice--warning">
              Inicio de sesión sin configurar: define NEXT_PUBLIC_SUPABASE_URL y NEXT_PUBLIC_SUPABASE_ANON_KEY en
              apps/dashboard/.env.local.
            </p>
            <Link href="/" className="inline-block underline">
              Seguir en modo local
            </Link>
          </div>
        )}
      </div>
    </main>
  );
}
