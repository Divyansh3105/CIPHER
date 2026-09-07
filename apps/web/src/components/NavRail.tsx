"use client";

// The 72px rail shared by every screen.
//
// It replaces the ad-hoc cross-links each page used to grow in its own
// header, where "Memory" sat in four different places depending on which
// page you were on. One shell, five destinations, one active state.

import Link from "next/link";
import { usePathname } from "next/navigation";
import Icon, { type IconName } from "@/components/Icon";

const DESTINATIONS: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Chat", icon: "chat" },
  { href: "/memory", label: "Memory", icon: "memory" },
  { href: "/documents", label: "Docs", icon: "document" },
  { href: "/agents", label: "Agents", icon: "agents" },
  { href: "/control", label: "Control", icon: "control" },
];

export default function NavRail() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Primary"
      className="z-30 flex w-[72px] shrink-0 select-none flex-col items-center justify-between border-r border-zinc-800 bg-zinc-925 py-3.5"
    >
      <div className="flex w-full flex-col items-center gap-5">
        <div
          title="CIPHER"
          className="flex h-9 w-9 items-center justify-center rounded border border-zinc-700/80 bg-zinc-900 font-mono text-xs font-semibold tracking-wider text-zinc-100"
        >
          CP
        </div>

        <div className="flex w-full flex-col items-center gap-1">
          {DESTINATIONS.map((destination) => {
            // Exact match for "/" so every other route does not light up the
            // Chat tab as well.
            const active =
              destination.href === "/"
                ? pathname === "/"
                : pathname.startsWith(destination.href);
            return (
              <Link
                key={destination.href}
                href={destination.href}
                aria-current={active ? "page" : undefined}
                className={[
                  "flex w-full flex-col items-center border-l-2 px-1 py-2 transition-colors",
                  active
                    ? "border-indigo-500 bg-zinc-850/70 text-zinc-100"
                    : "border-transparent text-zinc-400 hover:bg-zinc-850/40 hover:text-zinc-200",
                ].join(" ")}
              >
                <Icon name={destination.icon} className="mb-1 h-4 w-4" />
                <span className="text-[10px] font-medium tracking-tight">{destination.label}</span>
              </Link>
            );
          })}
        </div>
      </div>

      <div className="flex flex-col items-center gap-2">
        <span
          className="h-2 w-2 rounded-full bg-emerald-500/80 ring-2 ring-emerald-500/20"
          title="Local backend"
        />
        <span className="font-mono text-[9px] text-zinc-500">v0.8</span>
      </div>
    </nav>
  );
}
