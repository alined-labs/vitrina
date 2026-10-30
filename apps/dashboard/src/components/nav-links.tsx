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
            className="nav-link"
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
