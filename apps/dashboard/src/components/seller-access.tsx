"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useState } from "react";
import { Loader } from "@/components/brand/logo";
import { api } from "@/lib/api/client";

type Access = "checking" | "open" | "no_shop";

const AccessContext = createContext<Access>("checking");

export function useSellerAccess(): Access {
  return useContext(AccessContext);
}

/**
 * Asks once whether this account has a shop. A valid token without a seller is refused on every
 * seller route, so the panel holds its sections until it knows. Any other failure opens the panel,
 * where each section reports its own.
 */
export function SellerAccessProvider({ children }: { children: React.ReactNode }) {
  const [access, setAccess] = useState<Access>("checking");

  useEffect(() => {
    let alive = true;
    void api.GET("/sellers/me").then(({ response }) => {
      if (alive) setAccess(response.status === 403 || response.status === 404 ? "no_shop" : "open");
    });
    return () => {
      alive = false;
    };
  }, []);

  return <AccessContext.Provider value={access}>{children}</AccessContext.Provider>;
}

/** The sections of the panel, or the one message for an account that has no shop yet. */
export function SellerGate({ children }: { children: React.ReactNode }) {
  const access = useSellerAccess();
  if (access === "open") return children;
  if (access === "checking") {
    return (
      <div className="flex justify-center py-16">
        <Loader size={48} label="Cargando tu tienda" />
      </div>
    );
  }
  return (
    <section className="mx-auto max-w-xl space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Esta cuenta todavía no tiene una tienda</h1>
      <p className="text-fg-muted">
        Entraste bien, pero tu correo aún no está conectado a ninguna tienda. Las tiendas las crea quien opera Vitrina:
        cuando conecte la tuya, aquí verás tu catálogo, tus anuncios y tus conversaciones.
      </p>
      <div className="flex flex-wrap gap-3">
        <Link href="/foto" className="btn btn--primary">
          Probar con una foto
        </Link>
        <Link href="/como-funciona" className="btn btn--secondary">
          Ver cómo funciona
        </Link>
      </div>
    </section>
  );
}
