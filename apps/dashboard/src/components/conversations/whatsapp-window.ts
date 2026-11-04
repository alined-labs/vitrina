import type { Message } from "./use-conversation";

// WhatsApp lets a shop write free text only within 24 hours of the customer's last message.
const WINDOW_MS = 24 * 60 * 60 * 1000;

/** Whether free text sent at `at` falls outside the window `lastCustomerAt` opened. */
export function outsideWindow(lastCustomerAt: string | null, at: number): boolean {
  return lastCustomerAt === null || at - new Date(lastCustomerAt).getTime() > WINDOW_MS;
}

/** When the customer last wrote, from a complete transcript; null if they never did. */
export function lastCustomerMessageAt(messages: Message[]): string | null {
  return messages.findLast((message) => message.direction === "inbound")?.created_at ?? null;
}

export const WINDOW_CLOSED_WARNING =
  "Pasaron más de 24 horas desde el último mensaje de este cliente. WhatsApp no deja enviar texto libre fuera de ese plazo, así que lo más probable es que este mensaje no le llegue. Si te vuelve a escribir, podrás responderle.";

/**
 * The API says that a message failed but not why, so the reason is read from the transcript:
 * it is said as likely, never as what WhatsApp answered.
 */
export function failureReason(wasOutsideWindow: boolean): string {
  return wasOutsideWindow
    ? "Lo más probable es que WhatsApp lo rechazara porque habían pasado más de 24 horas desde el último mensaje del cliente. Podrás responderle cuando vuelva a escribirte."
    : "WhatsApp no entregó este mensaje. No tenemos el motivo exacto; puedes intentar de nuevo.";
}
