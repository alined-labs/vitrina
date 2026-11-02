import Link from "next/link";
import { Suspense } from "react";
import { MenuImport } from "@/components/catalog/menu-import";

export default function MenuImportPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <section className="space-y-2">
        <Link href="/catalogo" className="inline-flex min-h-6 items-center text-sm font-semibold text-primary underline">
          Volver al catálogo
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Importar desde una foto</h1>
        <p className="text-fg-muted">
          Sube la foto de tu menú o lista de precios. Leemos los productos y sus precios, y quedan como borradores para que los
          revises: nada llega a tus clientes hasta que lo actives.
        </p>
      </section>
      <Suspense fallback={<p className="text-sm text-fg-subtle">Cargando…</p>}>
        <MenuImport />
      </Suspense>
    </div>
  );
}
