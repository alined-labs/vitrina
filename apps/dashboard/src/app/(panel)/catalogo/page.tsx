import { Catalog } from "@/components/catalog/catalog";
import { Facts } from "@/components/catalog/facts";

export default function CatalogPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-10">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Catálogo</h1>
        <p className="text-fg-muted">
          Tus productos y las respuestas del negocio. El agente solo cita lo que está aquí, con su precio exacto.
        </p>
      </section>
      <Catalog />
      <Facts />
    </div>
  );
}
