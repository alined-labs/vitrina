import { useCallback, useEffect, useRef, useState } from "react";
import { api, type components } from "@/lib/api/client";

export type Conversation = components["schemas"]["Conversation"];
export type Message = components["schemas"]["Message"];

const POLL_MS = 4_000;
const MAX_POLL_MS = 30_000; // the wait grows while reads keep failing, up to this
const READ_TIMEOUT_MS = 12_000; // a read that hangs counts as failed, so the polling goes on
const PAGE_SIZE = 100;

/** One failed read says nothing; this many in a row are worth telling the seller. */
export const FAILURES_BEFORE_NOTICE = 3;

type Page = { cursor: string | undefined; items: Message[] };
type Snapshot = { conversation: Conversation; pages: Page[] };

class ReadFailure extends Error {
  constructor(readonly problem: unknown) {
    super("read failed");
  }
}

/**
 * The conversation and its whole transcript. Later reads start at the second-to-last page and
 * keep the older ones: delivery updates and new messages land at the end, and a long chat does
 * not cost one request per page every few seconds. A read either completes or changes nothing.
 */
async function readConversation(id: string, known: Page[]): Promise<Snapshot> {
  const path = { conversation_id: id };
  const head = await api.GET("/conversations/{conversation_id}", {
    params: { path },
    signal: AbortSignal.timeout(READ_TIMEOUT_MS),
  });
  if (!head.data) throw new ReadFailure(head.error);

  const kept = known.slice(0, Math.max(0, known.length - 2));
  const fresh: Page[] = [];
  let cursor = known[kept.length]?.cursor;
  do {
    const { data, error } = await api.GET("/conversations/{conversation_id}/messages", {
      params: { path, query: cursor ? { limit: PAGE_SIZE, cursor } : { limit: PAGE_SIZE } },
      signal: AbortSignal.timeout(READ_TIMEOUT_MS),
    });
    if (!data) throw new ReadFailure(error);
    fresh.push({ cursor, items: data.items ?? [] });
    cursor = data.next_cursor ?? undefined;
  } while (cursor);
  return { conversation: head.data, pages: [...kept, ...fresh] };
}

function problemCode(problem: unknown): string | undefined {
  return (problem as { code?: string } | null | undefined)?.code;
}

/**
 * Keeps a conversation fresh while its page is open. A failed read keeps what is on screen and
 * tries again; only `failures` reaching FAILURES_BEFORE_NOTICE should reach the seller.
 */
export function useConversation(id: string) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [gone, setGone] = useState(false);
  const [failures, setFailures] = useState(0);
  const [problem, setProblem] = useState<unknown>(null);
  const [syncedAt, setSyncedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now()); // the clock the 24-hour window is read against

  const pages = useRef<Page[]>([]);
  const failed = useRef(0);
  const stopped = useRef(false);
  // Only the newest read may land: an older one, or one that started before a send or a
  // takeover finished, would put the previous state back on screen.
  const newest = useRef(0);

  const refresh = useCallback(async () => {
    const turn = ++newest.current;
    try {
      const next = await readConversation(id, pages.current);
      if (turn !== newest.current) return;
      pages.current = next.pages;
      failed.current = 0;
      setSnapshot(next);
      setFailures(0);
      setProblem(null);
      setSyncedAt(Date.now());
    } catch (error) {
      if (turn !== newest.current) return;
      failed.current += 1;
      const seen = error instanceof ReadFailure ? error.problem : null;
      // Only the first load can say a chat does not exist: once it was here, a 404 may be a
      // passing proxy or deploy problem, and is retried like any other failure.
      const code = problemCode(seen);
      if (pages.current.length === 0 && (code === "NOT_FOUND" || code === "VALIDATION_ERROR")) {
        stopped.current = true;
        setGone(true);
      }
      setFailures(failed.current);
      setProblem(seen);
    }
    setNow(Date.now());
  }, [id]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let alive = true;
    const tick = async () => {
      await refresh();
      if (!alive || stopped.current) return;
      const wait = Math.min(POLL_MS * 1.5 ** failed.current, MAX_POLL_MS);
      timer = setTimeout(() => void tick(), wait);
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      newest.current += 1; // whatever is still in flight belongs to a page that left
    };
  }, [refresh]);

  /** Call before a write: a read already in flight no longer lands. */
  const beginWrite = useCallback(() => {
    newest.current += 1;
  }, []);

  const showConversation = useCallback((conversation: Conversation) => {
    setSnapshot((current) => current && { ...current, conversation });
  }, []);

  const showMessage = useCallback((message: Message) => {
    const known = pages.current;
    if (known.some((page) => page.items.some((item) => item.id === message.id))) return;
    const last = known.at(-1) ?? { cursor: undefined, items: [] };
    pages.current = [...known.slice(0, -1), { ...last, items: [...last.items, message] }];
    const next = pages.current;
    setSnapshot((current) => current && { ...current, pages: next });
  }, []);

  return {
    conversation: snapshot?.conversation ?? null,
    messages: snapshot?.pages.flatMap((page) => page.items) ?? [],
    gone,
    failures,
    problem,
    syncedAt,
    now,
    refresh,
    beginWrite,
    showConversation,
    showMessage,
  };
}
