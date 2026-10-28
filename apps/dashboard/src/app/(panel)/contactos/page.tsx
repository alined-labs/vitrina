import { Contacts } from "@/components/contacts/contacts";

export default function ContactsPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Contactos</h1>
        <p className="text-zinc-600">
          Solo les llegan promociones a quienes dijeron que sí (Ley 1581). Cada autorización queda guardada con lo que se
          les preguntó y lo que respondieron, y cualquiera puede darse de baja escribiendo BAJA.
        </p>
      </section>
      <Contacts />
    </div>
  );
}
