"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { sections } from "@/lib/nav";

const items = [{ slug: "", label: "Inicio" }, ...sections];

export function NavLinks() {
  const pathname = usePathname();
  return (
    <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:overflow-visible">
      {items.map((item) => {
        const href = `/${item.slug}`;
        const active = item.slug === "" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={`whitespace-nowrap rounded-md px-3 py-2 text-sm ${
              active ? "bg-zinc-900 text-white" : "text-zinc-700 hover:bg-zinc-100"
            }`}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
