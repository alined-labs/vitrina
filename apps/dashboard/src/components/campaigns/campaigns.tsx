"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { formatCount, formatDate, formatDateTime, problemMessage } from "@/lib/format";
import { CAMPAIGN_STATUS, DECISION, DECISION_REASON, OBJECTION } from "@/lib/labels";

type Campaign = components["schemas"]["ReactivationCampaign"];
type Decision = components["schemas"]["SendDecision"];
type ObjectionTag = components["schemas"]["ObjectionTag"];
type Ad = components["schemas"]["Ad"];

const TAGS = Object.keys(OBJECTION) as ObjectionTag[];

const DECISION_STYLE: Record<Decision["decision"], string> = {
  allowed: "bg-success-bg text-success-fg",
  sent: "bg-success-bg text-success-fg",
  deferred: "bg-warning-bg text-warning-fg",
  blocked: "bg-danger-bg text-danger-fg",
  failed: "bg-danger-bg text-danger-fg",
};

/** Reactivation campaigns: a preview of who may receive a promotion and why not, then the send (US-6). */
export function Campaigns() {
  const router = useRouter();
  const selected = useSearchParams().get("campana");
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [state, setState] = useState<"ready" | "not_connected">("ready");
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data, error, response } = await api.GET("/reactivation/campaigns", { params: { query: { limit: 50 } } });
    if (data) setCampaigns(data.items ?? []);
    else if (response.status === 404) setState("not_connected"); // T064 has not published its routes yet
    else setMessage(problemMessage(error, "No pudimos cargar tus campañas."));
  }, []);

  // Reloads when the selection changes too (a new campaign opens by its id). Started from a
  // promise callback, as in useLoad, so no state update runs inside the effect itself.
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load, selected]);

  if (state === "not_connected") {
    return (
      <p className="empty">
        Las campañas aparecerán aquí cuando el módulo de reactivación esté conectado.
      </p>
    );
  }

  const open = (id: string) => router.push(`/campanas?campana=${id}`);
  return (
    <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
      <aside className="space-y-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-subtle">Campañas</h2>
        <button type="button" onClick={() => router.push("/campanas")} aria-current={selected ? undefined : "true"} className="list-item">
          Nueva campaña
        </button>
        {campaigns?.map((campaign) => (
          <button
            key={campaign.id}
            type="button"
            onClick={() => open(campaign.id)}
            aria-current={campaign.id === selected ? "true" : undefined}
            className="list-item"
          >
            <span className="block">{formatDate(campaign.created_at)} · {CAMPAIGN_STATUS[campaign.status]}</span>
            <span className="block text-xs opacity-70">
              {formatCount(campaign.counts.allowed + campaign.counts.sent)} de {formatCount(campaign.decisions.length)} pueden recibirla
            </span>
          </button>
        ))}
      </aside>
      <div className="space-y-4">
        {message && (
          <p role="alert" className="notice notice--danger">
            {message}
          </p>
        )}
        {selected ? <CampaignDetail key={selected} id={selected} onChange={load} /> : <NewCampaign onCreated={open} />}
      </div>
    </div>
  );
}

function NewCampaign({ onCreated }: { onCreated: (id: string) => void }) {
  const [template, setTemplate] = useState("");
  const [tags, setTags] = useState<ObjectionTag[]>([]);
  const [days, setDays] = useState(3);
  const [max, setMax] = useState(100);
  const [adId, setAdId] = useState("");
  const [ads, setAds] = useState<Ad[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void api.GET("/ads", { params: { query: { status: "approved", limit: 100 } } }).then(({ data }) => setAds(data?.items ?? []));
  }, []);

  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    const segment = { no_purchase_since_days: days, max_contacts: max, ...(tags.length ? { objection_tags: tags } : {}) };
    const { data, error } = await api.POST("/reactivation/campaigns", {
      body: { template_name: template.trim(), template_language: "es", segment, ad_id: adId || null },
    });
    setBusy(false);
    if (data) onCreated(data.id);
    else setMessage(problemMessage(error, "No pudimos preparar la campaña."));
  }

  const toggle = (tag: ObjectionTag) => setTags(tags.includes(tag) ? tags.filter((item) => item !== tag) : [...tags, tag]);
  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-4 card p-4 text-sm">
      <p className="text-fg-muted">
        Primero ves a quién le llegaría y a quién no, con el motivo. Nada se envía hasta que lo confirmes.
      </p>
      <fieldset className="space-y-2">
        <legend className="text-xs text-fg-muted">Clientes que se frenaron por (vacío = todos)</legend>
        <div className="flex flex-wrap gap-2">
          {TAGS.map((tag) => (
            <button
              key={tag}
              type="button"
              aria-pressed={tags.includes(tag)}
              onClick={() => toggle(tag)}
              className="chip chip--sm"
            >
              {OBJECTION[tag]}
            </button>
          ))}
        </div>
      </fieldset>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-1">
          <span className="block text-xs text-fg-muted">Sin comprar desde hace (días)</span>
          <input name="no_purchase_since_days" type="number" min={1} value={days} onChange={(e) => setDays(Number(e.target.value))} className="input input--sm w-full" />
        </label>
        <label className="space-y-1">
          <span className="block text-xs text-fg-muted">Máximo de contactos</span>
          <input name="max_contacts" type="number" min={1} max={500} value={max} onChange={(e) => setMax(Number(e.target.value))} className="input input--sm w-full" />
        </label>
        <label className="space-y-1">
          <span className="block text-xs text-fg-muted">Plantilla de WhatsApp aprobada</span>
          <input name="template_name" required value={template} onChange={(e) => setTemplate(e.target.value)} placeholder="nombre_de_la_plantilla" className="input input--sm w-full font-mono" />
        </label>
        <label className="space-y-1">
          <span className="block text-xs text-fg-muted">Anuncio que acompaña el mensaje</span>
          <select name="ad_id" value={adId} onChange={(e) => setAdId(e.target.value)} className="input input--sm w-full">
            <option value="">Según lo que le interesa a cada uno</option>
            {ads.map((ad) => (
              <option key={ad.id} value={ad.id}>
                #{ad.code}
              </option>
            ))}
          </select>
        </label>
      </div>
      {message && (
        <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-danger-fg">
          {message}
        </p>
      )}
      <button type="submit" disabled={busy} className="btn btn--primary">
        {busy ? "Revisando contactos…" : "Ver vista previa"}
      </button>
    </form>
  );
}

function CampaignDetail({ id, onChange }: { id: string; onChange: () => Promise<void> }) {
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void api.GET("/reactivation/campaigns/{campaign_id}", { params: { path: { campaign_id: id } } }).then(({ data, error }) => {
      if (data) setCampaign(data);
      else setMessage(problemMessage(error, "No pudimos cargar la campaña."));
    });
    void api.GET("/contacts", { params: { query: { limit: 200 } } }).then(({ data }) => {
      if (data) setNames(Object.fromEntries((data.items ?? []).map((c) => [c.id, c.display_name ?? c.phone_masked ?? "Cliente"])));
    });
  }, [id]);

  async function send() {
    if (!campaign) return;
    const question = `Se enviará a ${campaign.counts.allowed} contactos ahora y a ${campaign.counts.deferred} cuando abra el horario permitido. ¿Enviar?`;
    if (!window.confirm(question)) return;
    setBusy(true);
    setMessage(null);
    const { data, error } = await api.POST("/reactivation/campaigns/{campaign_id}/send", { params: { path: { campaign_id: id } } });
    setBusy(false);
    if (data) {
      setCampaign(data);
      await onChange();
    } else setMessage(problemMessage(error, "No pudimos enviar la campaña."));
  }

  if (!campaign) return message ? <p role="alert" className="notice notice--danger">{message}</p> : <p className="text-sm text-fg-subtle">Cargando…</p>;

  const counts: [Decision["decision"], number][] = [
    ["allowed", campaign.counts.allowed],
    ["deferred", campaign.counts.deferred],
    ["blocked", campaign.counts.blocked],
    ["sent", campaign.counts.sent],
    ["failed", campaign.counts.failed],
  ];
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="text-lg font-semibold">{CAMPAIGN_STATUS[campaign.status]}</h2>
        <span className="font-mono text-sm text-fg-subtle">{campaign.template_name}</span>
        {campaign.scheduled_for && <span className="text-sm text-fg-subtle">· {formatDateTime(campaign.scheduled_for)}</span>}
      </div>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {counts.map(([decision, value]) => (
          <div key={decision} className="card px-3 py-2">
            <dt className="text-xs text-fg-subtle">{DECISION[decision]}</dt>
            <dd className="text-2xl font-semibold tabular-nums">{formatCount(value)}</dd>
          </div>
        ))}
      </dl>
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      {campaign.status === "preview" && (
        <button type="button" disabled={busy || campaign.counts.allowed + campaign.counts.deferred === 0} onClick={() => void send()} className="btn btn--primary">
          {busy ? "Enviando…" : "Enviar a los permitidos"}
        </button>
      )}
      <table className="w-full card overflow-hidden text-sm">
        <thead className="bg-sunken text-left text-xs text-fg-subtle">
          <tr>
            <th className="px-3 py-2 font-normal">Contacto</th>
            <th className="px-3 py-2 font-normal">Decisión</th>
            <th className="px-3 py-2 font-normal">Motivo</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {campaign.decisions.map((decision) => (
            <tr key={decision.id ?? decision.contact_id}>
              <td className="px-3 py-2">{names[decision.contact_id] ?? "Cliente"}</td>
              <td className="px-3 py-2">
                <span className={`rounded px-2 py-0.5 text-xs ${DECISION_STYLE[decision.decision]}`}>{DECISION[decision.decision]}</span>
              </td>
              <td className="px-3 py-2 text-fg">
                {decision.reason === "OK" ? "—" : (decision.reason_text_es ?? DECISION_REASON[decision.reason])}
                {decision.next_allowed_at && (
                  <span className="block text-xs text-fg-subtle">Desde {formatDateTime(decision.next_allowed_at)}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
