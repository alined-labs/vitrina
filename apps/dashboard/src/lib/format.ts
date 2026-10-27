import type { components } from "@/lib/api/client";

type Problem = components["schemas"]["Problem"];
type Code = Problem["code"];

const cop = new Intl.NumberFormat("es-CO", {
  style: "currency",
  currency: "COP",
  maximumFractionDigits: 0,
});

/** Integer pesos as a Colombian reads them: 85000 → "$ 85.000" (FR-085: amounts are integer COP). */
export function formatCop(amount: number): string {
  return cop.format(amount);
}

// What a seller reads for each problem code. The API's own detail is shown only for the codes
// whose details are written for sellers (conflicts and validation); other details are for
// developers and appear only in development builds.
const SELLER_TEXT: Partial<Record<Code, string>> = {
  UNAUTHORIZED: "Tu sesión terminó. Vuelve a iniciar sesión.",
  FORBIDDEN: "No tienes permiso para hacer esto.",
  NOT_FOUND: "No lo encontramos; puede que ya no exista.",
  RATE_LIMITED: "Demasiados intentos seguidos. Espera un momento y vuelve a intentar.",
  PROVIDER_UNAVAILABLE: "El servicio no está disponible en este momento. Intenta más tarde.",
  PAYLOAD_TOO_LARGE: "El archivo es demasiado grande.",
  IDEMPOTENCY_CONFLICT: "Esta acción ya se envió con otros datos. Recarga la página.",
};
const SELLER_DETAIL: ReadonlySet<Code> = new Set(["VALIDATION_ERROR", "CONFLICT", "CREATIVE_BLOCKED"]);

/** The sentence to show for a problem+json error, in Spanish. */
export function problemMessage(error: unknown, fallback = "Algo salió mal. Intenta de nuevo."): string {
  if (!error || typeof error !== "object") return fallback;
  const problem = error as Partial<Problem>;
  const field = problem.errors?.[0]?.message;
  if (field) return field;
  if (problem.code && SELLER_DETAIL.has(problem.code) && problem.detail) return problem.detail;
  const text = (problem.code && SELLER_TEXT[problem.code]) || fallback;
  const debug = process.env.NODE_ENV !== "production" && problem.detail ? ` (${problem.detail})` : "";
  return text + debug;
}
