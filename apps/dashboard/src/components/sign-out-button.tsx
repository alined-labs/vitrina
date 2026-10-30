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
      className="btn btn--secondary btn--sm"
    >
      Salir
    </button>
  );
}
