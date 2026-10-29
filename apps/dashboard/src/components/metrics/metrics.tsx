"use client";

import { useEffect, useState } from "react";
import { api, type components } from "@/lib/api/client";
import {
  bogotaDate,
  formatCount,
  formatDateTime,
  formatLatency,
  formatRate,
  formatUsd,
  problemMessage,
} from "@/lib/format";
import { StatusLabel, type Status } from "./status";

type Summary = components["schemas"]["MetricsSummary"];
type Costs = components["schemas"]["CostReport"];
type EvalMetrics = components["schemas"]["EvalMetrics"];
type ProviderState = "ok" | "degraded" | "down" | "mocked";
type Providers = Record<string, { status: ProviderState; latency_ms?: number; checked_at?: string; detail?: string }>;

const DAYS = 7;

/** The metrics page (T073): latest eval, live counters, model costs and provider health (US-7). */
export function Metrics() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [costs, setCosts] = useState<Costs | null>(null);
  const [providers, setProviders] = useState<Providers | "missing" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const [summaryResult, costsResult, health] = await Promise.all([
        api.GET("/metrics/summary"),
        api.GET("/metrics/costs", { params: { query: { from: bogotaDate(1 - DAYS), to: bogotaDate() } } }),
        api.GET("/health/providers"),
      ]);
      if (summaryResult.data) setSummary(summaryResult.data);
      else setMessage(problemMessage(summaryResult.error, "No pudimos cargar las métricas."));
      if (costsResult.data) setCosts(costsResult.data);
      if (health.data) setProviders(health.data);
      else setProviders("missing"); // the API answers 404 until provider checks exist
    })();
  }, []);

  if (message) {
    return (
      <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
        {message}
      </p>
    );
  }
  if (!summary) return <p className="text-sm text-zinc-500">Cargando métricas…</p>;

  const evals = summary.latest_eval.metrics ?? {};
  const live = summary.live;
  return (
    <div className="space-y-10">
      <InventedPrices metrics={evals} finishedAt={summary.latest_eval.finished_at} />
      <TileGroup
        title="Calidad del agente"
        note="De la última evaluación con conversaciones grabadas."
        tiles={[
          ["Respuestas con fuente citada", rate(evals.groundedness_rate)],
          ["Traspasos correctos", rate(evals.handoff_precision)],
          ["Traspasos detectados", rate(evals.handoff_recall)],
          ["Voz de marca", evals.brand_voice_mean == null ? null : `${evals.brand_voice_mean.toFixed(1).replace(".", ",")} / 5`],
          ["Preguntas fuera de tema rechazadas", rate(evals.off_topic_refusal_rate)],
          ["Tiempo de respuesta (p95)", evals.latency_p95_ms == null ? null : formatLatency(evals.latency_p95_ms)],
        ]}
      />
      <TileGroup
        title="Verificador y horario legal"
        tiles={[
          ["Escenas que pasan la verificación", rate(evals.verifier_pass_rate)],
          ["Productos alterados que se bloquean", rate(evals.verifier_block_rate_on_altered)],
          ["Decisiones de envío correctas", rate(evals.consent_scheduler_accuracy)],
        ]}
      />
      <TileGroup
        title="Hoy"
        tiles={[
          ["Conversaciones", count(live.conversations_today)],
          ["Atribuidas a un anuncio", rate(live.attributed_rate)],
          ["Esperan tu respuesta", count(live.handoffs_open)],
          ["Envíos bloqueados por ley", count(live.blocked_sends_today)],
          ["Escenas bloqueadas (total)", count(live.creatives_blocked_total)],
          ["Costo estimado por negocio", live.estimated_cost_per_business_usd == null ? null : formatUsd(live.estimated_cost_per_business_usd)],
        ]}
      />
      <CostTable costs={costs} />
      <ProviderHealth providers={providers} />
    </div>
  );
}

function rate(value: number | undefined): string | null {
  return value == null ? null : formatRate(value);
}

function count(value: number | undefined): string | null {
  return value == null ? null : formatCount(value);
}

function InventedPrices({ metrics, finishedAt }: { metrics: EvalMetrics; finishedAt?: string | undefined }) {
  const value = metrics.invented_price_rate;
  const status: Status = value == null ? "neutral" : value === 0 ? "good" : "critical";
  return (
    <section className="space-y-2 rounded-lg border border-zinc-200 bg-white p-6">
      <h2 className="text-sm text-zinc-600">Precios inventados por el agente</h2>
      <p className="text-5xl font-semibold tracking-tight">{value == null ? "—" : formatRate(value)}</p>
      <StatusLabel status={status}>
        {value == null
          ? "Aún no hay evaluaciones."
          : value === 0
            ? "Meta cumplida: el agente solo da precios de tu catálogo."
            : "Fuera de la meta (0 %): ningún cambio se publica así."}
      </StatusLabel>
      {finishedAt && (
        <p className="text-xs text-zinc-500">Última evaluación: {formatDateTime(finishedAt)}</p>
      )}
    </section>
  );
}

function TileGroup({ title, note, tiles }: { title: string; note?: string; tiles: [string, string | null][] }) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {note && <p className="text-sm text-zinc-500">{note}</p>}
      </div>
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {tiles.map(([label, value]) => (
          <div key={label} className="rounded-lg border border-zinc-200 bg-white p-4">
            <dt className="text-sm text-zinc-600">{label}</dt>
            <dd className="mt-1 text-2xl font-semibold" title={value == null ? "Sin datos todavía" : undefined}>
              {value ?? "—"}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function CostTable({ costs }: { costs: Costs | null }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">Costos de los últimos {DAYS} días</h2>
        {costs && <p className="text-2xl font-semibold">{formatUsd(costs.total_usd)}</p>}
      </div>
      {!costs ? (
        <p className="text-sm text-zinc-500">Sin datos de costos.</p>
      ) : costs.by_provider_model.length === 0 ? (
        <p className="text-sm text-zinc-500">Ninguna llamada a modelos en este periodo.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 text-left text-zinc-600">
              <tr>
                <th className="px-4 py-2 font-medium">Proveedor</th>
                <th className="px-4 py-2 font-medium">Modelo</th>
                <th className="px-4 py-2 text-right font-medium">Llamadas</th>
                <th className="px-4 py-2 text-right font-medium">Tokens (entrada / salida)</th>
                <th className="px-4 py-2 text-right font-medium">Costo</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 tabular-nums">
              {costs.by_provider_model.map((row, index) => (
                <tr key={`${row.provider}-${row.model}-${index}`}>
                  <td className="px-4 py-2">{row.provider ?? "—"}</td>
                  <td className="px-4 py-2 font-mono text-xs">{row.model ?? "—"}</td>
                  <td className="px-4 py-2 text-right">{row.calls == null ? "—" : formatCount(row.calls)}</td>
                  <td className="px-4 py-2 text-right">
                    {row.tokens_in == null ? "—" : formatCount(row.tokens_in)} / {row.tokens_out == null ? "—" : formatCount(row.tokens_out)}
                  </td>
                  <td className="px-4 py-2 text-right">{row.cost_usd == null ? "—" : formatUsd(row.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {costs?.whatsapp_messages && (
        <p className="text-sm text-zinc-600">
          WhatsApp: {formatCount(costs.whatsapp_messages.service ?? 0)} mensajes de servicio y{" "}
          {formatCount(costs.whatsapp_messages.marketing ?? 0)} de marketing
          {costs.whatsapp_messages.cost_usd != null && ` · ${formatUsd(costs.whatsapp_messages.cost_usd)}`}
        </p>
      )}
    </section>
  );
}

const PROVIDER_STATUS: Record<ProviderState, [Status, string]> = {
  ok: ["good", "Funciona"],
  degraded: ["warning", "Lento o con errores"],
  down: ["critical", "Caído"],
  mocked: ["neutral", "Simulado (modo demo)"],
};

// Row keys from GET /health/providers are stable ids; unknown ones show as sent.
const PROVIDER_NAME: Record<string, string> = {
  meta_model_api: "Modelos de Meta",
  whatsapp: "WhatsApp",
  modal: "Muse Glimmer (Modal)",
  database: "Base de datos",
  queue: "Cola de trabajos",
};

function ProviderHealth({ providers }: { providers: Providers | "missing" | null }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Proveedores</h2>
      {providers === null ? (
        <p className="text-sm text-zinc-500">Comprobando…</p>
      ) : providers === "missing" ? (
        <p className="text-sm text-zinc-500">El estado de los proveedores aparecerá aquí cuando la API lo publique.</p>
      ) : (
        <ul className="divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white">
          {Object.entries(providers).map(([name, health]) => {
            const [status, label] = PROVIDER_STATUS[health.status];
            return (
              <li key={name} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
                <span>
                  <span className="font-medium">{PROVIDER_NAME[name] ?? name}</span>
                  {(health.status === "down" || health.status === "degraded") && health.detail && (
                    <span className="block text-xs text-zinc-500">{health.detail}</span>
                  )}
                </span>
                <span className="flex items-center gap-3">
                  {health.latency_ms != null && <span className="text-zinc-500 tabular-nums">{formatLatency(health.latency_ms)}</span>}
                  <StatusLabel status={status}>{label}</StatusLabel>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
