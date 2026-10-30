import { Metrics } from "@/components/metrics/metrics";

export default function MetricsPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-10">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Métricas</h1>
        <p className="text-fg-muted">
          Cómo responde el agente, qué bloquea el verificador y cuánto cuesta atender tu negocio.
        </p>
      </section>
      <Metrics />
    </div>
  );
}
