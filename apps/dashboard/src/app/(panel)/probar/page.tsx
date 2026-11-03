import { TryAsCustomer } from "@/components/try/try-as-customer";

export default function TryPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Probar como cliente</h1>
        <p className="text-fg-muted">
          Así responde el agente a tus clientes por WhatsApp: solo con los datos de tu catálogo, y cada respuesta pasa
          por reglas que el modelo no puede saltarse.
        </p>
        <p lang="en" className="text-sm text-fg-subtle">
          Try as a customer. This is how the agent answers on WhatsApp: only from the shop&apos;s catalog, and every
          reply goes through rules the model cannot skip.
        </p>
      </section>
      <TryAsCustomer />
    </div>
  );
}
