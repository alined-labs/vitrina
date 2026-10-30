"use client";

import { useCallback, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { problemMessage } from "@/lib/format";
import { useLoad } from "@/lib/use-load";

type Fact = components["schemas"]["Fact"];
type FactKind = components["schemas"]["FactKind"];

const KINDS: Record<FactKind, string> = {
  faq: "Pregunta frecuente",
  shipping: "Envíos",
  payment: "Pagos",
  policy: "Políticas",
  hours: "Horarios",
  other: "Otro",
};

/** What the agent may cite besides products: shipping, payment, hours (FR-002, FR-004). */
export function Facts() {
  const [facts, setFacts] = useState<Fact[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const { data, error } = await api.GET("/facts", { params: { query: { limit: 100 } } });
    if (error) setMessage(problemMessage(error, "No pudimos cargar las respuestas."));
    else setFacts(data.items ?? []);
  }, []);

  useLoad(load);

  async function submit(form: HTMLFormElement) {
    const fields = new FormData(form);
    setSaving(true);
    setMessage(null);
    const { error } = await api.POST("/facts", {
      body: { kind: fields.get("kind") as FactKind, text: String(fields.get("text") ?? "").trim() },
    });
    setSaving(false);
    if (error) {
      setMessage(problemMessage(error, "No pudimos guardar la respuesta."));
      return;
    }
    form.reset();
    await load();
  }

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">Respuestas del negocio</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit(event.currentTarget);
        }}
        className="grid gap-3 card border-dashed border-control p-4 sm:grid-cols-[12rem_1fr_auto]"
      >
        <select name="kind" aria-label="Tipo de respuesta" defaultValue="shipping" className="input min-w-0">
          {Object.entries(KINDS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <input
          name="text"
          aria-label="Respuesta"
          required
          maxLength={1000}
          placeholder="Ej.: Envío a Medellín $12.000, llega en 2 a 3 días"
          className="input min-w-0"
        />
        <button type="submit" disabled={saving} className="btn btn--primary">
          Agregar
        </button>
      </form>
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      {facts === null ? (
        <p className="text-sm text-fg-subtle">Cargando…</p>
      ) : facts.length === 0 ? (
        <p className="text-sm text-fg-subtle">
          Aún no hay respuestas. Agrega envíos, pagos y horarios para que el agente no tenga que inventarlos.
        </p>
      ) : (
        <ul className="card divide-y divide-line">
          {facts.map((fact) => (
            <li key={fact.id} className="flex items-start justify-between gap-4 px-4 py-3 text-sm">
              <div>
                <p className="text-xs uppercase tracking-wide text-fg-subtle">{KINDS[fact.kind]}</p>
                <p>{fact.text}</p>
              </div>
              {fact.status === "draft" && (
                <span className="badge badge--warning">Borrador</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
