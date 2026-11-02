import { useCallback, useEffect, useRef, useState } from "react";

/** What one round found: keep watching, finished, or no answer this time. */
export type PollStep = "again" | "done" | "failed";

const STALLED_AFTER_FAILURES = 3;
const LONGEST_WAIT_MS = 30_000;

/**
 * Runs `step` every `everyMs` until it says "done". A failed round never ends the watch: it waits
 * longer each time, and after a few failures in a row `stalled` turns true so the screen can say
 * so. The next answer clears it and the watch carries on from there.
 */
export function usePoll(step: () => Promise<PollStep>, everyMs: number, active = true) {
  const [failures, setFailures] = useState(0);
  const [restarts, setRestarts] = useState(0);
  const failed = useRef(0);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      let result: PollStep;
      try {
        result = await step();
      } catch {
        result = "failed";
      }
      failed.current = result === "failed" ? failed.current + 1 : 0;
      setFailures(failed.current);
      if (!alive || result === "done") return;
      const wait = result === "failed" ? Math.min(everyMs * 2 ** failed.current, LONGEST_WAIT_MS) : everyMs;
      timer = setTimeout(() => void tick(), wait);
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [step, everyMs, active, restarts]);

  const retryNow = useCallback(() => setRestarts((count) => count + 1), []);
  return { stalled: active && failures >= STALLED_AFTER_FAILURES, retryNow };
}
