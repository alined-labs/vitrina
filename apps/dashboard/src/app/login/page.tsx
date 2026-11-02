"use client";

import { isAuthRetryableFetchError, type AuthError } from "@supabase/supabase-js";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type SubmitEvent } from "react";
import { Logo } from "@/components/brand/logo";
import { demoAccount } from "@/lib/env";
import { browserSupabase } from "@/lib/supabase/browser";

const NO_CONNECTION = "No pudimos conectar. Revisa tu conexión e intenta de nuevo.";

/** Only a refusal of these credentials reads as a wrong password. */
function signInMessage(error: AuthError): string {
  if (isAuthRetryableFetchError(error)) return NO_CONNECTION;
  if (error.code === "invalid_credentials" || error.status === 400) return "Correo o contraseña incorrectos.";
  if (error.status === 429) return "Demasiados intentos seguidos. Espera un momento y vuelve a intentar.";
  return "No pudimos iniciar sesión. Intenta de nuevo en unos minutos.";
}

// useSearchParams needs a Suspense boundary for this page to prerender.
export default function LoginPage() {
  return (
    <Suspense>
      <Login />
    </Suspense>
  );
}

function Login() {
  const router = useRouter();
  const expired = useSearchParams().get("sesion") === "expirada";
  const supabase = browserSupabase();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) return;
    setPending(true);
    setError(null);
    try {
      const { error: signInError } = await supabase.auth.signInWithPassword({ email, password });
      if (signInError) {
        setError(signInMessage(signInError));
        return;
      }
    } catch {
      // A failed request must not leave the button stuck on "Entrando…".
      setError(NO_CONNECTION);
      return;
    } finally {
      setPending(false);
    }
    router.replace("/");
    router.refresh();
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-8">
      <div className="w-full max-w-sm space-y-6 card p-6">
        <div className="space-y-3">
          <h1>
            <Logo size={36} />
          </h1>
          <p className="text-sm text-fg-muted">Entra para ver tu catálogo, tus anuncios y tus conversaciones.</p>
        </div>
        {expired && !error && (
          <p role="status" className="notice notice--info">
            Tu sesión terminó. Vuelve a iniciar sesión.
          </p>
        )}
        {supabase ? (
          <form onSubmit={(event) => void onSubmit(event)} className="space-y-4">
            {demoAccount && (
              <DemoAccount
                account={demoAccount}
                onUse={({ email: demoEmail, password: demoPassword }) => {
                  setEmail(demoEmail);
                  setPassword(demoPassword);
                }}
              />
            )}
            <label className="block space-y-1 text-sm">
              <span>Correo</span>
              <input
                name="email"
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="input w-full"
              />
            </label>
            <label className="block space-y-1 text-sm">
              <span>Contraseña</span>
              <input
                name="password"
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="input w-full"
              />
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
        <div className="space-y-3 border-t border-line pt-4 text-sm">
          <p className="text-fg-muted">¿Aún no tienes cuenta? Prueba Vitrina con la foto de un producto, sin registrarte.</p>
          <Link href="/foto" className="btn btn--secondary btn--block">
            Probar con una foto
          </Link>
          <p className="text-center">
            <Link href="/como-funciona" className="underline">
              Ver cómo funciona
            </Link>
          </p>
        </div>
      </div>
    </main>
  );
}

type Account = { email: string; password: string };

function DemoAccount({ account, onUse }: { account: Account; onUse: (account: Account) => void }) {
  return (
    <section aria-label="Cuenta de demostración" className="space-y-2 rounded-md bg-sunken p-3 text-sm">
      <p className="font-semibold">Cuenta de demostración</p>
      <p className="text-fg-muted">Para probar el panel sin una tienda propia.</p>
      <dl className="space-y-1">
        <div>
          <dt className="inline text-fg-muted">Correo: </dt>
          <dd className="inline break-all">{account.email}</dd>
        </div>
        <div>
          <dt className="inline text-fg-muted">Contraseña: </dt>
          <dd className="inline break-all">{account.password}</dd>
        </div>
      </dl>
      <button type="button" onClick={() => onUse(account)} className="btn btn--secondary btn--sm">
        Usar estos datos
      </button>
    </section>
  );
}
