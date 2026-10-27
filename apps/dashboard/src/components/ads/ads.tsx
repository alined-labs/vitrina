"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { problemMessage } from "@/lib/format";
import { AD_STATUS, ANGLE } from "@/lib/labels";

type Ad = components["schemas"]["Ad"];
type AdStatus = components["schemas"]["AdStatus"];

const TABS: AdStatus[] = ["approved", "draft", "archived"];
const TAB_LABEL: Record<AdStatus, string> = { approved: "Aprobados", draft: "Borradores", archived: "Archivados" };

/** Approved ads with their code and link, the package to post, and their attribution (US-2, US-4). */
export function Ads() {
  const [status, setStatus] = useState<AdStatus>("approved");
  const [ads, setAds] = useState<Ad[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data, error } = await api.GET("/ads", { params: { query: { status, limit: 100 } } });
    if (data) setAds(data.items ?? []);
    else setMessage(problemMessage(error, "No pudimos cargar tus anuncios."));
  }, [status]);

  useEffect(() => {
    setAds(null);
    void load();
  }, [load]);

  useEffect(() => {
    void api.GET("/products", { params: { query: { limit: 100 } } }).then(({ data }) => {
      if (data) setNames(Object.fromEntries((data.items ?? []).map((p) => [p.id, p.name])));
    });
  }, []);

  return (
    <section className="space-y-4">
      <div role="tablist" className="flex gap-2">
        {TABS.map((tab) => (
          <button
            key={tab}
            role="tab"
            aria-selected={tab === status}
            type="button"
            onClick={() => setStatus(tab)}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === status ? "bg-zinc-900 text-white" : "bg-zinc-100 hover:bg-zinc-200"}`}
          >
            {TAB_LABEL[tab]}
          </button>
        ))}
      </div>
      {message && (
        <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
          {message}
        </p>
      )}
      {ads === null ? (
        <p className="text-sm text-zinc-500">Cargando…</p>
      ) : ads.length === 0 ? (
        <p className="text-sm text-zinc-500">
          {status === "approved" ? "Aún no hay anuncios aprobados. Apruébalos desde Creativos." : "No hay anuncios aquí."}
        </p>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2">
          {ads.map((ad) => (
            <AdCard key={ad.id} ad={ad} product={names[ad.product_id]} onChange={load} onError={setMessage} />
          ))}
        </ul>
      )}
    </section>
  );
}

function AdCard({
  ad,
  product,
  onChange,
  onError,
}: {
  ad: Ad;
  product: string | undefined;
  onChange: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [image, setImage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    void api.GET("/creatives/{creative_id}", { params: { path: { creative_id: ad.creative_id } } }).then(({ data }) => {
      if (data) setImage(data.image_url);
    });
  }, [ad.creative_id]);

  async function act(run: () => Promise<{ error?: unknown }>, failure: string) {
    setBusy(true);
    const { error } = await run();
    setBusy(false);
    if (error) onError(problemMessage(error, failure));
    else await onChange();
  }

  async function download() {
    setBusy(true);
    const { data, error } = await api.GET("/ads/{ad_id}/export", {
      params: { path: { ad_id: ad.id } },
      parseAs: "blob",
    });
    setBusy(false);
    if (!data) {
      onError(problemMessage(error, "No pudimos preparar la descarga."));
      return;
    }
    const url = URL.createObjectURL(data);
    const link = document.createElement("a");
    link.href = url;
    link.download = `anuncio-${ad.code ?? ad.id}.zip`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function copyLink() {
    if (!ad.wa_link) return;
    await navigator.clipboard.writeText(ad.wa_link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const stats = ad.stats;
  return (
    <li className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
      {image && (
        // Signed links from the blob store; next/image would need every storage host configured.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={image} alt={`Anuncio ${ad.code ?? ""}`} className="aspect-video w-full object-cover" />
      )}
      <div className="space-y-3 p-4 text-sm">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="font-medium">{product ?? "Producto"}</p>
            <p className="text-zinc-500">
              {AD_STATUS[ad.status]}
              {ad.angle && ` · ${ANGLE[ad.angle]}`}
            </p>
          </div>
          {ad.code && <span className="rounded bg-zinc-900 px-2 py-1 font-mono text-white">#{ad.code}</span>}
        </div>
        {ad.wa_link && (
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded bg-zinc-100 px-2 py-1 text-xs">{ad.wa_link}</code>
            <button type="button" onClick={() => void copyLink()} className="rounded border border-zinc-300 px-2 py-1 text-xs hover:border-zinc-500">
              {copied ? "Copiado" : "Copiar"}
            </button>
          </div>
        )}
        <dl className="grid grid-cols-3 gap-2 text-center">
          {(
            [
              ["Chats", stats?.chats],
              ["Reservas", stats?.reservations],
              ["Citas", stats?.bookings],
            ] as const
          ).map(([label, value]) => (
            <div key={label} className="rounded bg-zinc-50 py-2">
              <dt className="text-xs text-zinc-500">{label}</dt>
              <dd className="text-base font-medium" title={value === undefined ? "Se cuenta cuando el agente registre las conversaciones" : undefined}>
                {value ?? "—"}
              </dd>
            </div>
          ))}
        </dl>
        <div className="flex flex-wrap gap-2">
          {ad.status === "draft" && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void act(() => api.POST("/ads/{ad_id}/approve", { params: { path: { ad_id: ad.id } } }), "No pudimos aprobarlo.")}
              className="rounded bg-zinc-900 px-3 py-1.5 text-white disabled:bg-zinc-300"
            >
              Aprobar
            </button>
          )}
          {ad.code && (
            <button type="button" disabled={busy} onClick={() => void download()} className="rounded border border-zinc-300 px-3 py-1.5 hover:border-zinc-500 disabled:opacity-50">
              Descargar paquete
            </button>
          )}
          {ad.status !== "archived" && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void act(() => api.DELETE("/ads/{ad_id}", { params: { path: { ad_id: ad.id } } }), "No pudimos archivarlo.")}
              className="rounded px-3 py-1.5 text-zinc-600 hover:bg-zinc-100 disabled:opacity-50"
            >
              Archivar
            </button>
          )}
        </div>
      </div>
    </li>
  );
}
