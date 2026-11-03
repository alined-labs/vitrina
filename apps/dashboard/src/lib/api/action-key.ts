// One Idempotency-Key per user action: a double click sends one request, and a retry after a
// failure sends the same key, so the API runs a paid action once. `action` names what the user
// asked for (what it does and on what); the key lasts until the API gives a final answer.

type Answer = { response: Response; error?: unknown };

const keys = new Map<string, string>();
const running = new Map<string, Promise<Answer>>();

/** Whether the action may still be running or was never run: the retry must reuse the key. */
function outcomeUnknown({ response, error }: Answer): boolean {
  if (response.status >= 500 || response.status === 429) return true;
  // CONFLICT is also how the API answers "this key is still running"; a new key would run it twice.
  return response.status === 409 && (error as { code?: string } | undefined)?.code === "CONFLICT";
}

export function oncePerAction<T extends Answer>(
  action: string,
  call: (headers: { "Idempotency-Key": string }) => Promise<T>,
): Promise<T> {
  const current = running.get(action);
  if (current) return current as Promise<T>;
  const key = keys.get(action) ?? crypto.randomUUID();
  keys.set(action, key);
  const run = call({ "Idempotency-Key": key })
    .then((answer) => {
      if (!outcomeUnknown(answer)) keys.delete(action); // a failed request throws and keeps the key
      return answer;
    })
    .finally(() => running.delete(action));
  running.set(action, run);
  return run;
}
