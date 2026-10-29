"use client";

import { useRouter } from "next/navigation";
import { browserSupabase } from "@/lib/supabase/browser";

export function SignOutButton() {
  const router = useRouter();
  async function signOut() {
    try {
      await browserSupabase()?.auth.signOut();
    } catch {
      // The session stays; /login sends a signed-in seller back to the panel.
    }
    router.replace("/login");
    router.refresh();
  }
  return (
    <button
      type="button"
      onClick={() => void signOut()}
      className="rounded-md border border-zinc-300 px-3 py-1.5 text-zinc-700 hover:bg-zinc-100"
    >
      Salir
    </button>
  );
}
