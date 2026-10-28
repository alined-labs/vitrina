"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { bogotaDate, jobErrorMessage, problemMessage } from "@/lib/format";
import { ANGLE, HOOK, OBJECTION } from "@/lib/labels";

type Brief = components["schemas"]["Brief"];
type Applied = { creative_job_ids?: string[]; reactivation_campaign_id?: string | null };

const POLL_MS = 3000;

/** The weekly brief from Muse Wonder: what worked, what held customers back, and the next ads (US-5). */
export function Briefs() {
  const router = useRouter();
  const selected = useSearchParams().get("informe");
  const [briefs, setBriefs] = useState<Brief[] | null>(null);
  const [state, setState] = useState<"ready" | "not_connected">("ready");
  const [message, setMessage] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data, error, response } = await api.GET("/briefs", { params: { query: { limit: 20 } } });
    if (data) setBriefs(data.items ?? []);
    else if (response.status === 404) setState("not_connected"); // T070 has not published its routes yet
    else setMessage(problemMessage(error, "No pudimos cargar tus informes."));
    return data?.items ?? [];
  }, []);

  useEffect(() => {
    void load().then((items) => {
      if (!selected && items[0]) router.replace(`/informes?informe=${items[0].id}`);
    });
  }, [load, selected, router]);

  // A brief is a background job: poll it until Wonder finishes, then open the new brief.
  useEffect(() => {
    if (!running) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const { data } = await api.GET("/jobs/{job_id}", { params: { path: { job_id: running } } });
      if (data?.status === "queued" || data?.status === "running" || !data) {
        timer = setTimeout(() => void tick(), POLL_MS);
        return;
      }
      setRunning(null);
      if (data.status === "succeeded") {
        const items = await load();
        if (items[0]) router.push(`/informes?informe=${items[0].id}`);
      } else setMessage(jobErrorMessage(data.error));
    };
    void tick();
    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [running, load, router]);

  async function generate() {
    setMessage(null);
    const { data, error } = await api.POST("/briefs/generate", { body: { period_from: bogotaDate(-7), period_to: bogotaDate() } });
    if (data) setRunning(data.id);
    else setMessage(problemMessage(error, "No pudimos pedir el informe."));
  }

  if (state === "not_connected") {
    return (
      <p className="rounded-lg border border-dashed border-zinc-300 bg-white px-4 py-6 text-sm text-zinc-600">
        Tu informe semanal aparecerá aquí cuando el módulo de aprendizaje esté conectado.
      </p>
    );
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
      <aside className="space-y-2">
        <button type="button" disabled={running !== null} onClick={() => void generate()} className="w-full rounded-md bg-zinc-900 px-3 py-2 text-sm text-white disabled:opacity-50">
          {running ? "Analizando tu semana…" : "Generar informe de la semana"}
        </button>
        <h2 className="pt-2 text-sm font-semibold uppercase tracking-wide text-zinc-500">Informes</h2>
        {briefs?.length === 0 && <p className="text-sm text-zinc-500">Aún no hay informes.</p>}
        {briefs?.map((brief) => (
          <button
            key={brief.id}
            type="button"
            onClick={() => router.push(`/informes?informe=${brief.id}`)}
            className={`w-full rounded-md px-3 py-2 text-left text-sm ${brief.id === selected ? "bg-zinc-900 text-white" : "hover:bg-zinc-100"}`}
          >
            {brief.period_from} → {brief.period_to}
            {brief.status === "applied" && <span className="block text-xs opacity-70">Aplicado</span>}
          </button>
        ))}
      </aside>
      <div className="space-y-4">
        {message && (
          <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
            {message}
          </p>
        )}
        {selected && <BriefDetail key={selected} id={selected} onApplied={load} />}
      </div>
    </div>
  );
}

function BriefDetail({ id, onApplied }: { id: string; onApplied: () => Promise<Brief[]> }) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [applied, setApplied] = useState<Applied | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void api.GET("/briefs/{brief_id}", { params: { path: { brief_id: id } } }).then(({ data, error }) => {
      if (data) setBrief(data);
      else setMessage(problemMessage(error, "No pudimos cargar el informe."));
    });
    void api.GET("/products", { params: { query: { limit: 100 } } }).then(({ data }) => {
      if (data) setNames(Object.fromEntries((data.items ?? []).map((p) => [p.id, p.name])));
    });
  }, [id]);

  async function apply() {
    const question = "Se crearán los anuncios sugeridos y una vista previa de campaña. Las ofertas no se publican sin tu aprobación. ¿Continuar?";
    if (!window.confirm(question)) return;
    setBusy(true);
    setMessage(null);
    const { data, error } = await api.POST("/briefs/{brief_id}/apply", { params: { path: { brief_id: id } } });
    setBusy(false);
    if (data) {
      setApplied(data);
      await onApplied();
    } else setMessage(problemMessage(error, "No pudimos aplicar el informe."));
  }

  if (!brief) return message ? <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{message}</p> : <p className="text-sm text-zinc-500">Cargando…</p>;
  if (brief.status === "failed") return <p className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">Este informe no se pudo completar. Genera uno nuevo.</p>;

  return (
    <article className="space-y-6 text-sm">
      {brief.headline && <p className="text-xl font-semibold leading-snug tracking-tight">{brief.headline}</p>}
      <Block title="Lo que vimos">
        {brief.insights.map((insight, index) => (
          <li key={index} className="space-y-1">
            <p>{insight.text}</p>
            <Evidence values={insight.evidence} />
          </li>
        ))}
      </Block>
      <Block title="Ángulos recomendados">
        {brief.recommended_angles.map((item, index) => (
          <li key={index}>
            <span className="font-medium">{ANGLE[item.angle]}:</span> {item.rationale}
          </li>
        ))}
      </Block>
      <Block title="Próximos anuncios">
        {brief.creative_directives.map((item, index) => (
          <li key={index} className="space-y-1 rounded-md border border-zinc-200 bg-white px-3 py-2">
            <p className="flex flex-wrap gap-2">
              <span className="font-medium">{names[item.product_id] ?? "Producto"}</span>
              <span className="rounded bg-zinc-100 px-2 py-0.5 text-xs">{ANGLE[item.angle]}</span>
              {item.hook && <span className="rounded bg-zinc-100 px-2 py-0.5 text-xs">{HOOK[item.hook]}</span>}
            </p>
            <p className="text-zinc-700">{item.scene_brief}</p>
            {item.offer && <p className="text-amber-900">Oferta propuesta: {item.offer} · necesita tu aprobación</p>}
          </li>
        ))}
      </Block>
      <Block title="A quién volver a escribir">
        {brief.reactivation_segments.map((item, index) => (
          <li key={index}>
            <span className="font-medium">Se frenaron por {OBJECTION[item.objection_tag].toLowerCase()}:</span> {item.message_angle}
          </li>
        ))}
      </Block>
      {message && (
        <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-red-800">
          {message}
        </p>
      )}
      {applied ? (
        <div className="space-y-1 rounded-md bg-emerald-50 px-4 py-3 text-emerald-900">
          <p>Listo. Se crearon {applied.creative_job_ids?.length ?? 0} trabajos de anuncios.</p>
          {applied.creative_job_ids?.[0] && (
            <Link className="block underline" href={`/creativos?trabajo=${applied.creative_job_ids[0]}`}>
              Revisar los nuevos creativos
            </Link>
          )}
          {applied.reactivation_campaign_id && (
            <Link className="block underline" href={`/campanas?campana=${applied.reactivation_campaign_id}`}>
              Ver la vista previa de la campaña
            </Link>
          )}
        </div>
      ) : (
        brief.status === "ready" && (
          <button type="button" disabled={busy} onClick={() => void apply()} className="rounded-md bg-zinc-900 px-4 py-2 text-white disabled:opacity-50">
            {busy ? "Aplicando…" : "Crear estos anuncios y la campaña"}
          </button>
        )
      )}
    </article>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode[] }) {
  if (children.length === 0) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">{title}</h2>
      <ul className="space-y-2">{children}</ul>
    </section>
  );
}

/** The numbers behind an insight, as Wonder reported them. */
function Evidence({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values).filter(([, value]) => typeof value === "number" || typeof value === "string");
  if (entries.length === 0) return null;
  return (
    <p className="flex flex-wrap gap-1">
      {entries.map(([key, value]) => (
        <span key={key} className="rounded bg-zinc-100 px-2 py-0.5 font-mono text-xs text-zinc-600">
          {key.replaceAll("_", " ")}: {String(value)}
        </span>
      ))}
    </p>
  );
}
