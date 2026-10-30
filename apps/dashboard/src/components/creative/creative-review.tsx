"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { formatDateTime, jobErrorMessage, problemMessage } from "@/lib/format";
import { ANGLE, FORMAT, HOOK, STAGE, copyPreview, decimal, percent } from "@/lib/labels";

type Job = components["schemas"]["CreativeJob"];
type Creative = components["schemas"]["Creative"];
type Copy = components["schemas"]["CopyVariant"];
type Ad = components["schemas"]["Ad"];

const POLL_MS = 2000;

/** Creative jobs, their verified scenes and scored copy, and ad approval (US-2; AS-2.1–2.5). */
export function CreativeReview() {
  const router = useRouter();
  const selected = useSearchParams().get("trabajo");
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const [{ data: jobPage, error }, { data: productPage }] = await Promise.all([
        api.GET("/creative/jobs", { params: { query: { limit: 20 } } }),
        api.GET("/products", { params: { query: { limit: 100 } } }),
      ]);
      if (jobPage) setJobs(jobPage.items ?? []);
      else setMessage(problemMessage(error, "No pudimos cargar los trabajos."));
      if (productPage) setNames(Object.fromEntries((productPage.items ?? []).map((p) => [p.id, p.name])));
    })();
  }, [selected]);

  return (
    <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
      <aside className="space-y-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-subtle">Trabajos</h2>
        {message && <p className="text-sm text-danger-fg">{message}</p>}
        {jobs === null ? (
          <p className="text-sm text-fg-subtle">Cargando…</p>
        ) : jobs.length === 0 ? (
          <p className="text-sm text-fg-subtle">
            Aún no hay trabajos. Empieza desde el <Link href="/catalogo" className="underline">catálogo</Link>.
          </p>
        ) : (
          <ul className="space-y-1">
            {jobs.map((job) => (
              <li key={job.id}>
                <button
                  type="button"
                  onClick={() => router.replace(`/creativos?trabajo=${job.id}`)}
                  aria-current={job.id === selected ? "true" : undefined}
                  className="list-item"
                >
                  <span className="block font-medium">{names[job.product_id] ?? "Producto"}</span>
                  <span className="block text-xs opacity-75">
                    {STAGE[job.stage]} · {formatDateTime(job.created_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      {selected ? (
        <JobDetail key={selected} jobId={selected} productName={names} />
      ) : (
        <p className="text-sm text-fg-subtle">Elige un trabajo para revisar sus escenas y textos.</p>
      )}
    </div>
  );
}

function JobDetail({ jobId, productName }: { jobId: string; productName: Record<string, string> }) {
  const [job, setJob] = useState<Job | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [creativeId, setCreativeId] = useState<string | null>(null);
  const [copyId, setCopyId] = useState<string | null>(null);
  const [approved, setApproved] = useState<Ad | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const { data, error } = await api.GET("/creative/jobs/{job_id}", { params: { path: { job_id: jobId } } });
    if (error) setMessage(problemMessage(error, "No pudimos cargar el trabajo."));
    else setJob(data);
    return data;
  }, [jobId]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let alive = true;
    const tick = async () => {
      const data = await load();
      if (alive && data && (data.status === "queued" || data.status === "running")) {
        timer = setTimeout(() => void tick(), POLL_MS);
      }
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [load]);

  async function approve() {
    if (!creativeId || !copyId) return;
    setBusy(true);
    setMessage(null);
    const draft = await api.POST("/ads", { body: { creative_id: creativeId, copy_variant_id: copyId } });
    if (draft.error) {
      setBusy(false);
      setMessage(problemMessage(draft.error, "No pudimos crear el anuncio."));
      return;
    }
    const done = await api.POST("/ads/{ad_id}/approve", { params: { path: { ad_id: draft.data.id } } });
    setBusy(false);
    if (done.error) setMessage(problemMessage(done.error, "No pudimos aprobar el anuncio."));
    else {
      setApproved(done.data);
      setCreativeId(null);
      setCopyId(null);
      void load();
    }
  }

  if (!job) return <p className="text-sm text-fg-subtle">{message ?? "Cargando el trabajo…"}</p>;
  const running = job.status === "queued" || job.status === "running";
  const creatives = job.creatives ?? [];
  const copies = job.copy_variants ?? [];
  const canApprove = creatives.some((creative) => creative.verification.status === "passed") && copies.length > 0;

  return (
    <section className="min-w-0 space-y-6">
      <header className="space-y-2">
        <h2 className="text-lg font-semibold">{productName[job.product_id] ?? "Producto"}</h2>
        <p className="text-sm text-fg-muted">{STAGE[job.stage]}</p>
        {running && (
          <div
            className="progress"
            role="progressbar"
            aria-label="Progreso"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round((job.progress ?? 0) * 100)}
          >
            <div style={{ width: `${Math.round((job.progress ?? 0) * 100)}%` }} />
          </div>
        )}
        {job.status === "failed" && (
          <p role="alert" className="notice notice--danger">
            {jobErrorMessage(job.error)}
          </p>
        )}
      </header>
      {!canApprove && <Notices message={message} approved={approved} />}
      {creatives.length > 0 && (
        <div className="space-y-3">
          <h3 className="font-medium">Escenas</h3>
          <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {creatives.map((creative) => (
              <SceneCard
                key={creative.id}
                creative={creative}
                chosen={creative.id === creativeId}
                onChoose={() => setCreativeId(creative.id)}
              />
            ))}
          </ul>
        </div>
      )}
      {copies.length > 0 && (
        <div className="space-y-3">
          <h3 className="font-medium">Textos</h3>
          <ul className="space-y-2">
            {copies.map((copy) => (
              <CopyRow key={copy.id} copy={copy} chosen={copy.id === copyId} onChoose={() => setCopyId(copy.id)} />
            ))}
          </ul>
        </div>
      )}
      {job.status === "succeeded" && creatives.length > 0 && copies.length === 0 && (
        <p className="text-sm text-fg-muted">
          Ninguna escena pasó la verificación, así que no escribimos textos. Prueba con otra foto del producto.
        </p>
      )}
      {canApprove && (
        <div className="sticky bottom-0 space-y-3 border-t border-line bg-surface py-3">
          <Notices message={message} approved={approved} />
          <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={!creativeId || !copyId || busy}
            onClick={() => void approve()}
            className="btn btn--primary"
          >
            Aprobar anuncio
          </button>
          <span className="text-sm text-fg-subtle">
            {creativeId && copyId ? "Se asigna un código y el enlace de WhatsApp." : "Elige una escena verificada y un texto."}
          </span>
          </div>
        </div>
      )}
    </section>
  );
}

function Notices({ message, approved }: { message: string | null; approved: Ad | null }) {
  return (
    <>
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      {approved && (
        <div role="status" className="notice notice--success space-y-1">
          <p className="font-medium">Anuncio #{approved.code} aprobado.</p>
          {approved.wa_link && <p className="break-all">{approved.wa_link}</p>}
          <Link href="/anuncios" className="underline">
            Ver tus anuncios
          </Link>
        </div>
      )}
    </>
  );
}

function SceneCard({ creative, chosen, onChoose }: { creative: Creative; chosen: boolean; onChoose: () => void }) {
  const check = creative.verification;
  const passed = check.status === "passed";
  const badge = { passed: "bg-success-bg text-success-fg", blocked: "bg-danger-bg text-danger-fg", pending: "bg-sunken text-fg" };
  const label = { passed: "Verificada", blocked: "Bloqueada", pending: "Pendiente" };
  return (
    <li className={`card overflow-hidden ${chosen ? "card--chosen" : ""}`}>
      {/* Signed links from the blob store; next/image would need every storage host configured. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={creative.image_url} alt={`Escena ${FORMAT[creative.format]}`} className={`aspect-square w-full object-cover ${passed ? "" : "opacity-60 grayscale"}`} />
      <div className="space-y-2 p-3 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-fg-muted">{FORMAT[creative.format]}</span>
          <span className={`rounded px-2 py-0.5 text-xs ${badge[check.status]}`}>{label[check.status]}</span>
        </div>
        {check.reason && <p className={passed ? "text-fg-muted" : "font-medium text-danger-fg"}>{check.reason}</p>}
        <dl className="grid grid-cols-2 gap-x-2 text-xs text-fg-muted">
          <dt>Forma</dt>
          <dd className="text-right">
            {percent(check.mask_iou)} <span className="text-fg-subtle">(mín. {percent(check.thresholds.mask_iou_min)})</span>
          </dd>
          <dt>Color</dt>
          <dd className="text-right">
            {decimal(check.color_delta)} <span className="text-fg-subtle">(máx. {decimal(check.thresholds.color_delta_max)})</span>
          </dd>
          <dt>Revisión visual</dt>
          <dd className="text-right">{check.visual_review.altered ? "cambios" : "sin cambios"}</dd>
        </dl>
        {passed ? (
          <button type="button" onClick={onChoose} className="btn btn--secondary btn--sm btn--block">
            {chosen ? "Elegida" : "Usar esta escena"}
          </button>
        ) : (
          <p className="text-xs text-fg-subtle">Se guarda para auditoría y nunca se publica.</p>
        )}
      </div>
    </li>
  );
}

function CopyRow({ copy, chosen, onChoose }: { copy: Copy; chosen: boolean; onChoose: () => void }) {
  const dropped = copy.status === "dropped_low_voice" || copy.status === "rejected";
  return (
    <li className={`card flex items-start gap-3 p-3 text-sm ${chosen ? "card--chosen" : ""} ${dropped ? "opacity-60" : ""}`}>
      <input
        type="radio"
        name="copy"
        aria-label="Usar este texto"
        disabled={dropped}
        checked={chosen}
        onChange={onChoose}
        className="mt-1"
      />
      <div className="min-w-0 flex-1 space-y-1">
        <p>{copyPreview(copy.text)}</p>
        <p className="text-xs text-fg-subtle">
          {HOOK[copy.hook]} · {ANGLE[copy.angle]}
          {copy.brand_voice_score != null && ` · voz de marca ${decimal(copy.brand_voice_score, 1)}/5`}
          {dropped && " · descartado por no sonar como tu marca"}
          {copy.status === "approved" && " · en un anuncio aprobado"}
        </p>
      </div>
    </li>
  );
}
