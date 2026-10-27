"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { browserSupabase } from "@/lib/supabase/browser";

export default function LoginPage() {
  const router = useRouter();
  const supabase = browserSupabase();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) return;
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    const { error: signInError } = await supabase.auth.signInWithPassword({
      email: String(form.get("email") ?? ""),
      password: String(form.get("password") ?? ""),
    });
    setPending(false);
    if (signInError) {
      setError("Correo o contraseña incorrectos.");
      return;
    }
    router.replace("/");
    router.refresh();
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6 rounded-lg border border-zinc-200 bg-white p-6">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">Vitrina</h1>
          <p className="text-sm text-zinc-600">Entra para ver tu catálogo, tus anuncios y tus conversaciones.</p>
        </div>
        {supabase ? (
          <form onSubmit={onSubmit} className="space-y-4">
            <label className="block space-y-1 text-sm">
              <span>Correo</span>
              <input name="email" type="email" required autoComplete="email" className="w-full rounded-md border border-zinc-300 px-3 py-2" />
            </label>
            <label className="block space-y-1 text-sm">
              <span>Contraseña</span>
              <input name="password" type="password" required autoComplete="current-password" className="w-full rounded-md border border-zinc-300 px-3 py-2" />
            </label>
            {error ? (
              <p role="alert" className="text-sm text-red-700">
                {error}
              </p>
            ) : null}
            <button type="submit" disabled={pending} className="w-full rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-60">
              {pending ? "Entrando…" : "Entrar"}
            </button>
          </form>
        ) : (
          <div className="space-y-3 text-sm">
            <p className="rounded bg-amber-100 px-3 py-2 text-amber-900">
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
