import { Suspense } from "react";
import { Briefs } from "@/components/briefs/briefs";

export default function BriefsPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Informe semanal</h1>
        <p className="text-zinc-600">
          Lo que dijeron tus clientes y qué anuncios trajeron chats, convertido en los próximos anuncios y en a quién volver
          a escribir.
        </p>
      </section>
      <Suspense fallback={<p className="text-sm text-zinc-500">Cargando…</p>}>
        <Briefs />
      </Suspense>
    </div>
  );
}
