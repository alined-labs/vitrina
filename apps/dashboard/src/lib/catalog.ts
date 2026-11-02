import { api, type components } from "@/lib/api/client";
import { problemMessage } from "@/lib/format";

export type Product = components["schemas"]["Product"];
export type ProductStatus = components["schemas"]["ProductStatus"];
export type Fact = components["schemas"]["Fact"];

/** Shown when a request never reached the API. */
export const OFFLINE = "No pudimos conectarnos. Revisa tu internet e intenta de nuevo.";

/** The largest whole number a price or stock column holds (int4). */
const WHOLE_MAX = 2_147_483_647;
const PAGE = 100;
// A safety stop for the page-by-page loops below, far above any shop's catalog.
const MAX_PAGES = 50;

export type ProductValues = { name: string; price: string; stock: string; description: string };
export type ProductField = keyof ProductValues;
export type ProductErrors = Partial<Record<ProductField, string | undefined>>;
export type ProductData = { name: string; price_cop: number; stock: number | null; description: string };

export const NO_PRODUCT: ProductValues = { name: "", price: "", stock: "", description: "" };

export function productValues(product: Product): ProductValues {
  return {
    name: product.name,
    price: String(product.price_cop),
    stock: product.stock == null ? "" : String(product.stock),
    description: product.description ?? "",
  };
}

/**
 * A whole number as a Colombian types it: "85000", "85.000", "$ 1.250.000". Dots and commas count
 * only as thousands separators, so "85.5" is refused instead of being stored as a wrong price.
 */
export function parseWhole(text: string): number | null {
  const compact = text.replace(/[\s$]/g, "");
  if (!/^(\d+|\d{1,3}([.,]\d{3})+)$/.test(compact)) return null;
  return Number(compact.replace(/[.,]/g, ""));
}

/** The seller's sentences for a product form; `data` is present only when every field is valid. */
export function checkProduct(values: ProductValues): { errors: ProductErrors; data?: ProductData } {
  const errors: ProductErrors = {};
  const name = values.name.trim();
  if (!name) errors.name = "Escribe el nombre del producto.";
  else if (name.length > 120) errors.name = "El nombre puede tener hasta 120 caracteres.";

  const price = parseWhole(values.price);
  if (!values.price.trim()) errors.price = "Escribe el precio en pesos.";
  else if (price === null) errors.price = "Escribe el precio en pesos enteros, sin decimales: 85000 o 85.000.";
  else if (price === 0) errors.price = "El precio debe ser mayor que cero.";
  else if (price > WHOLE_MAX) errors.price = "Ese precio es demasiado alto. Revisa los ceros.";

  const stock = values.stock.trim() ? parseWhole(values.stock) : null;
  if (values.stock.trim() && stock === null) {
    errors.stock = "Escribe el inventario como un número entero, o déjalo vacío si no lo controlas.";
  } else if (stock !== null && stock > WHOLE_MAX) {
    errors.stock = "Ese inventario es demasiado alto. Revisa los ceros.";
  }

  const description = values.description.trim();
  if (description.length > 2000) errors.description = "La descripción puede tener hasta 2000 caracteres.";

  if (Object.keys(errors).length > 0 || price === null) return { errors };
  return { errors, data: { name, price_cop: price, stock, description } };
}

// The API's validation messages are written for developers, in English.
const FIELD_TEXT: Record<string, string> = {
  name: "Revisa el nombre del producto.",
  price_cop: "Revisa el precio: debe ser un número entero de pesos.",
  stock: "Revisa el inventario: debe ser un número entero, o vacío.",
  description: "Revisa la descripción: puede tener hasta 2000 caracteres.",
  kind: "Elige el tipo de respuesta.",
  text: "Revisa la respuesta: puede tener hasta 1000 caracteres.",
  image: "No pudimos usar ese archivo. Manda una foto JPEG, PNG o WebP de máximo 10 MB.",
  hint: "La nota puede tener hasta 300 caracteres.",
};

/** Like problemMessage, but a validation error always reads in Spanish. */
export function catalogProblem(error: unknown, fallback: string): string {
  const fields = (error as { errors?: { field?: string }[] } | undefined)?.errors;
  if (fields?.length) return FIELD_TEXT[fields[0]?.field ?? ""] ?? "Revisa los datos e intenta de nuevo.";
  return problemMessage(error, fallback);
}

type Listed<T> = { items: T[]; error?: undefined } | { items?: undefined; error: unknown };

/** Every product, following the pages: a draft on a later page must not stay hidden. */
export async function allProducts(status?: ProductStatus): Promise<Listed<Product>> {
  const items: Product[] = [];
  let cursor: string | undefined;
  try {
    for (let page = 0; page < MAX_PAGES; page++) {
      const query = { limit: PAGE, ...(status ? { status } : {}), ...(cursor ? { cursor } : {}) };
      const { data, error } = await api.GET("/products", { params: { query } });
      if (!data) return { error };
      items.push(...(data.items ?? []));
      if (!data.next_cursor) break;
      cursor = data.next_cursor;
    }
  } catch {
    return { error: undefined };
  }
  return { items };
}

/** Every fact, following the pages. */
export async function allFacts(): Promise<Listed<Fact>> {
  const items: Fact[] = [];
  let cursor: string | undefined;
  try {
    for (let page = 0; page < MAX_PAGES; page++) {
      const query = { limit: PAGE, ...(cursor ? { cursor } : {}) };
      const { data, error } = await api.GET("/facts", { params: { query } });
      if (!data) return { error };
      items.push(...(data.items ?? []));
      if (!data.next_cursor) break;
      cursor = data.next_cursor;
    }
  } catch {
    return { error: undefined };
  }
  return { items };
}
