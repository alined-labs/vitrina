import { Conversations } from "@/components/conversations/conversations";

export default function ConversationsPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Conversaciones</h1>
        <p className="text-zinc-600">
          El agente responde con los datos de tu catálogo. Cuando no puede, la conversación pasa a ti con un resumen.
        </p>
      </section>
      <Conversations />
    </div>
  );
}
