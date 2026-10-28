/** Dashboard sections, one per stage of the loop (spec §1.1). Pages arrive with the tasks noted. */
export type Section = { slug: string; label: string; summary: string; story: string };

export const sections: readonly Section[] = [
  // T045
  { slug: "catalogo", label: "Catálogo", summary: "Productos, precios y preguntas frecuentes con los que responde el agente.", story: "US-1" },
  // T045
  { slug: "creativos", label: "Creativos", summary: "Tus fotos convertidas en escenas de anuncio, verificadas contra el producto real.", story: "US-2" },
  // T045
  { slug: "anuncios", label: "Anuncios", summary: "Anuncios aprobados con su código y su enlace de WhatsApp.", story: "US-2" },
  // T045
  { slug: "conversaciones", label: "Conversaciones", summary: "Chats que atiende el agente y los que esperan tu respuesta.", story: "US-3" },
  // no dashboard task in tasks.md: built with the compliance and learning modules
  { slug: "contactos", label: "Contactos", summary: "Tus clientes y su autorización para recibir promociones.", story: "US-6" },
  // G6
  { slug: "campanas", label: "Campañas", summary: "Reactivación solo con consentimiento y dentro del horario legal.", story: "US-6" },
  // G6
  { slug: "informes", label: "Informe semanal", summary: "Qué funcionó, qué frenó a tus clientes y cuáles son los próximos anuncios.", story: "US-5" },
  // T073
  { slug: "metricas", label: "Métricas", summary: "Calidad del agente, del verificador y costos por negocio.", story: "US-7" },
];
