import { Suspense } from "react";
import { CreativeReview } from "@/components/creative/creative-review";

export default function CreativesPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Creativos</h1>
        <p className="text-fg-muted">
          Cada escena se compara con tu foto: si cambia la forma, el color o el logo de tu producto, se bloquea y no se
          puede publicar.
        </p>
      </section>
      <Suspense fallback={<p className="text-sm text-fg-subtle">Cargando…</p>}>
        <CreativeReview />
      </Suspense>
    </div>
  );
}
