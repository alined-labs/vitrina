import { useEffect } from "react";

/**
 * Runs `load` after render, and again whenever it changes (its inputs are its dependencies).
 * `load` fetches and then sets state; starting it from a promise callback keeps every state
 * update out of the effect's synchronous body (react-hooks/set-state-in-effect).
 */
export function useLoad(load: () => Promise<unknown>): void {
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);
}
