"use client";

import { useRouter } from "next/navigation";
import { browserSupabase } from "@/lib/supabase/browser";

export function SignOutButton() {
  const router = useRouter();
  async function signOut() {
    await browserSupabase()?.auth.signOut();
    router.replace("/login");
    router.refresh();
  }
  return (
    <button
      type="button"
      onClick={signOut}
      className="rounded-md border border-zinc-300 px-3 py-1.5 text-zinc-700 hover:bg-zinc-100"
    >
      Salir
    </button>
  );
}
