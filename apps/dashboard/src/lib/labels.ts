import type { components } from "@/lib/api/client";

type Schemas = components["schemas"];

/** Spanish names for the contract's enums, as a seller reads them. */
export const STAGE: Record<Schemas["CreativeJobStage"], string> = {
  queued: "En cola",
  segmenting: "Recortando tu producto",
  composing: "Creando escenas",
  verifying: "Verificando que sea tu producto",
  copywriting: "Escribiendo textos",
  scoring: "Revisando la voz de tu marca",
  ready: "Listo",
  failed: "No se pudo completar",
};

export const FORMAT: Record<Schemas["CreativeFormat"], string> = {
  story_9x16: "Historia 9:16",
  catalog_1x1: "Catálogo 1:1",
  feed_4x5: "Feed 4:5",
};

export const ANGLE: Record<Schemas["CopyAngle"], string> = {
  price: "Precio",
  quality: "Calidad",
  handmade: "Hecho a mano",
  urgency: "Urgencia",
  gift: "Regalo",
  local_pride: "Orgullo local",
};

export const HOOK: Record<Schemas["CopyHook"], string> = {
  question: "Pregunta",
  bold_claim: "Afirmación",
  story: "Historia",
  social_proof_free: "Prueba social real",
  scarcity: "Pocas unidades",
  benefit: "Beneficio",
};

export const AD_STATUS: Record<Schemas["AdStatus"], string> = {
  draft: "Borrador",
  approved: "Aprobado",
  archived: "Archivado",
};

export const MODE: Record<Schemas["ConversationMode"], string> = {
  agent: "Atiende el agente",
  human: "Te espera a ti",
  closed: "Cerrada",
};

export const HANDOFF_REASON: Record<Schemas["HandoffReason"], string> = {
  customer_request: "Pidió hablar contigo",
  complaint_refund: "Reclamo o devolución",
  payment_issue: "Problema con un pago",
  medical_topic: "Tema de salud",
  low_confidence_twice: "El agente no estaba seguro",
  negative_sentiment: "Cliente molesto",
  amount_over_threshold: "Compra por un monto alto",
  price_unverified: "Precio sin verificar",
  policy_exception: "Pide una excepción a tus políticas",
  other: "Otro motivo",
};

export const OBJECTION: Record<Schemas["ObjectionTag"], string> = {
  price: "Precio",
  shipping: "Envío",
  size: "Talla",
  availability: "Disponibilidad",
  trust: "Confianza",
  timing: "Tiempo",
  payment: "Pago",
  off_topic: "Fuera de tema",
  other: "Otro",
};

/** 0.97 → "97 %"; 0.083 → "0,08" (Colombian decimals). */
export function percent(value: number): string {
  return `${Math.round(value * 100)} %`;
}

export function decimal(value: number, digits = 2): string {
  return value.toFixed(digits).replace(".", ",");
}

/** The copy as the seller will post it, with the link marker shown as words. */
export function copyPreview(text: string): string {
  return text.replace("{wa_link}", "‹enlace de WhatsApp›");
}

export const CONSENT: Record<Schemas["Contact"]["marketing_consent"], string> = {
  granted: "Acepta promociones",
  none: "Sin autorización",
  revoked: "Se dio de baja",
};

export const CONSENT_PURPOSE: Record<Schemas["ConsentRecordCreate"]["purpose"], string> = {
  marketing: "Promociones",
  data_processing: "Tratamiento de datos",
};

export const CONSENT_CHANNEL: Record<Schemas["ConsentRecordCreate"]["channel"], string> = {
  whatsapp: "por WhatsApp",
  in_person: "en persona",
  web: "en la web",
  other: "otro medio",
};

export const RECORDED_BY: Record<NonNullable<Schemas["ConsentRecord"]["recorded_by"]>, string> = {
  agent: "registró el agente",
  seller: "registraste tú",
  system: "registró Vitrina",
};

type SendDecision = Schemas["SendDecision"];

export const DECISION: Record<SendDecision["decision"], string> = {
  allowed: "Se enviará",
  blocked: "Bloqueado",
  deferred: "Aplazado",
  sent: "Enviado",
  failed: "Falló",
};

// The same words as vitrina_compliance's REASON_TEXT_ES, for when a decision arrives without them.
export const DECISION_REASON: Record<SendDecision["reason"], string> = {
  OK: "Permitido",
  OPTED_OUT: "Se dio de baja: no recibe mensajes de marketing",
  NO_CONSENT_LEY_1581: "Sin autorización — Ley 1581",
  FREQUENCY_CAP: "Ya recibió un mensaje de marketing en los últimos 7 días",
  OUTSIDE_HOURS_LEY_2300: "Fuera del horario permitido — Ley 2300",
  NO_MATCHING_AD: "No hay un anuncio aprobado para lo que le interesa",
  TEMPLATE_NOT_APPROVED: "La plantilla de WhatsApp aún no está aprobada",
  SEND_FAILED: "WhatsApp no entregó el mensaje",
};

export const CAMPAIGN_STATUS: Record<Schemas["ReactivationCampaign"]["status"], string> = {
  preview: "Vista previa",
  scheduled: "Programada",
  sending: "Enviando",
  sent: "Enviada",
  cancelled: "Cancelada",
};
