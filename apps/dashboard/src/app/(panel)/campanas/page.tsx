import { Suspense } from "react";
import { Campaigns } from "@/components/campaigns/campaigns";

export default function CampaignsPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Campañas</h1>
        <p className="text-fg-muted">
          Vuelve a escribirles a quienes se frenaron. Vitrina revisa a cada contacto antes de enviar: sin autorización no
          hay mensaje, y fuera del horario legal (Ley 2300) el envío espera.
        </p>
      </section>
      <Suspense fallback={<p className="text-sm text-fg-subtle">Cargando…</p>}>
        <Campaigns />
      </Suspense>
    </div>
  );
}
