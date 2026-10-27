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
