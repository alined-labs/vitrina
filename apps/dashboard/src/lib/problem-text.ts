// Spanish sentences for failures that reach the seller without one: a request that never got an
// answer, and validation messages the API writes in English for developers.

export const CONNECTION_TEXT = "No pudimos conectar con Vitrina. Revisa tu conexión e intenta de nuevo.";

const CONNECTION_FAILED = "CONNECTION_FAILED";

/** The body the API client answers with when a request gets no reply; not an API problem code. */
export const connectionProblem = JSON.stringify({ code: CONNECTION_FAILED, status: 503 });

export function isConnectionFailure(error: object): boolean {
  return "code" in error && error.code === CONNECTION_FAILED;
}

/** Worth asking again: no answer, a server fault or a rate limit, not a refusal. */
export function isTemporaryFailure(response: Response): boolean {
  return response.status >= 500 || response.status === 429 || response.status === 408;
}

const FIELD_NAME: Record<string, string> = {
  product_name: "el nombre del producto",
  name: "el nombre",
  business_name: "el nombre del negocio",
  price_cop: "el precio",
  stock: "las existencias",
  city: "la ciudad",
  image: "la foto",
  file: "el archivo",
  text: "el texto",
};

type Rule = [RegExp, (name: string, limit: string) => string];

// The messages the API's request validation and its own checks can send in English.
const RULES: Rule[] = [
  [/^field required$/i, (name) => `Falta ${name}.`],
  [/^string should have at most (\d+) characters?$/i, (name, limit) => `Revisa ${name}: admite hasta ${limit} caracteres.`],
  [/^string should have at least (\d+) characters?$/i, (name, limit) => `Revisa ${name}: necesita al menos ${limit} caracteres.`],
  [/^input should be a valid (?:integer|number)/i, (name) => `Revisa ${name}: debe ser un número.`],
  [/^input should be greater than or equal to (\d+)/i, (name, limit) => `Revisa ${name}: debe ser ${limit} o más.`],
  [/^input should be greater than (\d+)/i, (name, limit) => `Revisa ${name}: debe ser mayor que ${limit}.`],
  [/^input should be less than or equal to (\d+)/i, (name, limit) => `Revisa ${name}: debe ser ${limit} o menos.`],
  [/^must be on or after /i, () => "La fecha final no puede ser anterior a la inicial."],
  [/larger than (\d+) MB$/i, (_name, limit) => `El archivo pesa más de ${limit} MB.`],
  [/^no messages from this name/i, () => "No encontramos mensajes con ese nombre en el chat exportado."],
];

// Words that do not occur in the API's Spanish messages: what none of the rules above recognizes
// but reads as English is replaced, so a new API message never reaches a seller untranslated.
const ENGLISH = /\b(the|this|that|must|should|field|input|string|value|valid|invalid|required|from|with|for|not|is|are|was|already|larger|than|expected)\b/i;

/** The API's sentence when it is written for sellers, else `fallback`. */
export function spanishOr(text: string, fallback: string): string {
  return ENGLISH.test(text) ? fallback : text;
}

/** A field's validation message in Spanish; the API's own Spanish messages pass through. */
export function fieldMessage({ field, message }: { field: string; message: string }): string {
  const name = FIELD_NAME[field.split(".").at(-1) ?? ""] ?? "este dato";
  for (const [pattern, write] of RULES) {
    const match = pattern.exec(message);
    if (match) return write(name, match[1] ?? "");
  }
  return spanishOr(message, "Revisa los datos e intenta de nuevo.");
}
