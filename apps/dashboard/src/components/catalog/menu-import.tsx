"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Loader } from "@/components/brand/logo";
import { api, type components } from "@/lib/api/client";
import { allProducts, catalogProblem, OFFLINE, type Product } from "@/lib/catalog";
import { problemMessage } from "@/lib/format";
import { useLoad } from "@/lib/use-load";
import { ProductCard } from "./product-card";
import { Field } from "./product-form";

type Job = components["schemas"]["Job"];

const PHOTO_MAX_BYTES = 10 * 1024 * 1024; // the API's limit (FR-010)
const HINT_MAX = 300;
const POLL_MS = 2000;
const SLOW_MS = 90_000;
// One job runs up to three attempts of five minutes each; past this one nobody is working on it.
const GIVE_UP_MS = 20 * 60_000;
const POLL_FAILURES_MAX = 5;

const STAGE: Record<string, string> = {
  reading: "Leyendo los productos y precios de tu foto…",
  saving: "Guardando tus borradores…",
};

// Only the message of a code written for sellers is shown; the others carry developer text.
const JOB_TEXT: Record<string, string> = {
  PROVIDER_ERROR: "No pudimos leer los productos de la foto. Inténtalo de nuevo.",
  PROVIDER_UNAVAILABLE: "La lectura de fotos no está disponible en este momento. Intenta de nuevo en unos minutos.",
  NOT_FOUND: "La foto ya no está guardada. Súbela de nuevo.",
  VALIDATION_ERROR: "Esa foto no pertenece a tu cuenta. Súbela de nuevo.",
};
const SELLER_MESSAGE: ReadonlySet<string> = new Set(["INSUFFICIENT_DATA"]);

function failureText(job: Job): string {
  if (job.status === "cancelled") return "Esta importación se canceló. Sube la foto de nuevo si aún la necesitas.";
  const code = job.error?.code ?? "";
  if (SELLER_MESSAGE.has(code) && job.error?.message) return job.error.message;
  return JOB_TEXT[code] ?? "No pudimos importar esta foto. Intenta de nuevo o agrega los productos a mano.";
}

type Watch = { job: Job | null; slow: boolean; failure: string | null; found: number | null };
const NO_WATCH: Watch = { job: null, slow: false, failure: null, found: null };

/** A photo of a menu becomes drafts the seller reviews and activates (AS-1.3, FR-003). */
export function MenuImport() {
  const [jobId, setJobId] = useState(useSearchParams().get("trabajo"));
  const [watch, setWatch] = useState<Watch>(NO_WATCH);
  const [drafts, setDrafts] = useState<Product[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const latest = useRef(0);

  const loadDrafts = useCallback(async () => {
    const request = ++latest.current;
    const { items, error } = await allProducts("draft");
    if (request !== latest.current) return null; // a newer load is on its way
    if (items) setDrafts(items);
    else setMessage(problemMessage(error, "No pudimos cargar tus borradores."));
    return items ?? null;
  }, []);

  useLoad(loadDrafts);

  // The job runs in the background: poll it, and when it ends read the drafts again.
  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    const tick = async () => {
      let answer: Awaited<ReturnType<typeof readJob>> | null = null;
      try {
        answer = await readJob(jobId);
      } catch {
        // No answer at all counts like a failed poll below.
      }
      if (stopped) return;
      const job = answer?.data;
      if (!job) {
        failures += 1;
        const status = answer?.response.status;
        if (status === 404) {
          setWatch({ ...NO_WATCH, failure: "No encontramos esta importación. Sube la foto de nuevo." });
        } else if (status === 401 || status === 403) {
          setWatch({ ...NO_WATCH, failure: problemMessage(answer?.error) });
        } else if (failures >= POLL_FAILURES_MAX) {
          setWatch({
            ...NO_WATCH,
            failure: "Perdimos la conexión mientras leíamos tu foto. Recarga esta página en un momento para ver cómo va.",
          });
        } else timer = setTimeout(() => void tick(), POLL_MS);
        return;
      }
      failures = 0;
      if (job.status === "queued" || job.status === "running") {
        const waited = Date.now() - new Date(job.created_at).getTime();
        if (waited > GIVE_UP_MS) {
          setWatch({
            ...NO_WATCH,
            failure: "Tu foto tardó demasiado y dejamos de esperarla. Súbela de nuevo o agrega los productos a mano.",
          });
          return;
        }
        setWatch({ ...NO_WATCH, job, slow: waited > SLOW_MS });
        timer = setTimeout(() => void tick(), POLL_MS);
        return;
      }
      if (job.status !== "succeeded") {
        setWatch({ ...NO_WATCH, failure: failureText(job) });
        return;
      }
      const items = await loadDrafts();
      if (stopped) return;
      const started = new Date(job.created_at).getTime();
      const found = items?.filter((item) => item.source === "image_import" && new Date(item.created_at).getTime() >= started);
      setWatch({ ...NO_WATCH, job, found: found?.length ?? 0 });
    };

    void tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, loadDrafts]);

  function started(id: string) {
    setWatch(NO_WATCH);
    setNote(null);
    setJobId(id);
    // In the address, so a reload keeps watching the same job.
    window.history.replaceState(null, "", `/catalogo/importar?trabajo=${id}`);
  }

  function saved(product: Product, text?: string) {
    latest.current++; // a load already in flight may not know about this change
    setDrafts(
      (current) =>
        current?.flatMap((item) => (item.id !== product.id ? [item] : product.status === "draft" ? [product] : [])) ?? null,
    );
    setNote(text ?? null);
  }

  async function archived(product: Product) {
    setNote(`«${product.name}» salió de los borradores. Lo encuentras en Archivados, en el catálogo.`);
    await loadDrafts();
  }

  const working = jobId !== null && watch.failure === null && watch.found === null;

  return (
    <div className="space-y-8">
      {working ? (
        <JobProgress job={watch.job} slow={watch.slow} />
      ) : (
        <>
          {watch.failure && (
            <p role="alert" className="notice notice--danger">
              {watch.failure}
            </p>
          )}
          {watch.found !== null && (
            <p role="status" className={watch.found > 0 ? "notice notice--success" : "notice notice--info"}>
              {watch.found === 0
                ? "Leímos la foto y no quedan borradores nuevos por revisar: lo que encontramos ya está en tu catálogo."
                : watch.found === 1
                  ? "Leímos la foto: encontramos 1 producto nuevo. Revísalo abajo y actívalo si está bien."
                  : `Leímos la foto: encontramos ${watch.found} productos nuevos. Revísalos abajo y activa los que estén bien.`}
            </p>
          )}
          <PhotoForm onStarted={started} />
        </>
      )}
      <section className="space-y-4">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">Borradores por revisar</h2>
          <p className="text-sm text-fg-muted">
            El agente no cita un borrador hasta que lo actives. Corrige el precio aquí si la foto se leyó mal.
          </p>
        </div>
        {message && (
          <p role="alert" className="notice notice--danger">
            {message}
          </p>
        )}
        {note && (
          <p role="status" className="notice notice--success">
            {note}
          </p>
        )}
        {drafts === null ? (
          !message && <p className="text-sm text-fg-subtle">Cargando borradores…</p>
        ) : drafts.length === 0 ? (
          <p className="empty">
            No tienes borradores por revisar.{" "}
            <Link href="/catalogo" className="inline-flex min-h-6 items-center font-semibold text-primary underline">
              Ver el catálogo
            </Link>
          </p>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2">
            {drafts.map((product) => (
              <ProductCard key={product.id} product={product} onSaved={saved} onArchived={archived} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function readJob(jobId: string) {
  return api.GET("/jobs/{job_id}", { params: { path: { job_id: jobId } } });
}

function JobProgress({ job, slow }: { job: Job | null; slow: boolean }) {
  const percent = Math.round((job?.progress ?? 0) * 100);
  const retrying = job?.status === "queued" && job.error;
  const text = retrying
    ? "Hubo un problema leyendo tu foto. Lo estamos intentando de nuevo…"
    : job?.status === "running"
      ? (STAGE[job.stage ?? ""] ?? "Trabajando en tu foto…")
      : "Tu foto está en cola…";
  return (
    <section className="space-y-3 card p-4" aria-live="polite">
      <div className="flex items-center gap-3">
        <Loader size={28} />
        <p className="font-medium">{text}</p>
      </div>
      <div
        className="progress"
        role="progressbar"
        aria-label="Progreso de la importación"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <div style={{ width: `${percent}%` }} />
      </div>
      {slow && (
        <p className="text-sm text-fg-muted">
          Está tardando más de lo normal. Puedes dejar esta página: cuando termine, tus borradores aparecerán aquí y en el catálogo.
        </p>
      )}
    </section>
  );
}

function PhotoForm({ onStarted }: { onStarted: (jobId: string) => void }) {
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function submit(form: HTMLFormElement) {
    setMessage(null);
    const fields = new FormData(form);
    const file = fields.get("image");
    if (!(file instanceof File) || file.size === 0) {
      setFileError("Elige la foto de tu menú o lista de precios.");
      return;
    }
    if (file.size > PHOTO_MAX_BYTES) {
      setFileError("Esa foto pesa más de 10 MB. Prueba con una más liviana.");
      return;
    }
    const hint = String(fields.get("hint") ?? "").trim();
    setSending(true);
    try {
      const { data, error } = await api.POST("/products/import", {
        body: { image: file as unknown as string, ...(hint ? { hint } : {}) },
        bodySerializer(body) {
          const sent = new FormData();
          sent.append("image", body.image as unknown as Blob);
          if (body.hint) sent.append("hint", body.hint);
          return sent;
        },
      });
      if (data) onStarted(data.id);
      else setMessage(catalogProblem(error, "No pudimos subir la foto."));
    } catch {
      setMessage(OFFLINE);
    }
    setSending(false);
  }

  return (
    <form
      noValidate
      aria-label="Importar desde una foto"
      className="space-y-4 card p-4"
      onSubmit={(event) => {
        event.preventDefault();
        void submit(event.currentTarget);
      }}
    >
      <Field label="Foto del menú o lista de precios" error={fileError ?? undefined} hint="JPEG, PNG o WebP, de máximo 10 MB.">
        {({ id, describedBy, invalid }) => (
          // The button is ours: a browser writes its native file button in its own language.
          <div className="flex flex-wrap items-center gap-3">
            <label className="btn btn--secondary has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus">
              Elegir foto
              <input
                id={id}
                type="file"
                name="image"
                accept="image/jpeg,image/png,image/webp"
                disabled={sending}
                aria-invalid={invalid}
                aria-describedby={describedBy}
                className="sr-only"
                onChange={(event) => {
                  setFileName(event.target.files?.[0]?.name ?? null);
                  setFileError(null);
                }}
              />
            </label>
            <span className="min-w-0 text-sm text-fg-muted [overflow-wrap:anywhere]">{fileName ?? "Ninguna foto elegida"}</span>
          </div>
        )}
      </Field>
      <Field label="Nota para quien lee la foto (opcional)">
        {({ id }) => (
          <input
            id={id}
            name="hint"
            maxLength={HINT_MAX}
            disabled={sending}
            placeholder="Ej.: menú de almuerzos"
            className="input w-full min-w-0"
          />
        )}
      </Field>
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      <button type="submit" disabled={sending} className="btn btn--primary">
        {sending ? "Subiendo tu foto…" : "Leer la foto"}
      </button>
    </form>
  );
}
