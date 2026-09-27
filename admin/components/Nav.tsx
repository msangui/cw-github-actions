"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserButton } from "@clerk/nextjs";

export const TABS = [
  { href: "/hosts", label: "Hosts & dials" },
  { href: "/dynamics", label: "Dynamics" },
  { href: "/agents", label: "Agents" },
  { href: "/sources", label: "Sources" },
  { href: "/voices", label: "Voices & mix" },
  { href: "/budget", label: "Budget" },
  { href: "/podcast", label: "Podcast" },
  { href: "/runs", label: "Runs" },
];

export function Nav({ onToggleAgent, agentOpen }: { onToggleAgent: () => void; agentOpen: boolean }) {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-bg/90 backdrop-blur">
      <div className="flex items-center gap-4 px-4 h-12">
        <Link href="/hosts" className="font-bold tracking-tight whitespace-nowrap">
          <span className="text-accent">●</span> Context Window
        </Link>
        <nav className="flex gap-1 overflow-x-auto">
          {TABS.map((t) => {
            const active = path === t.href || path.startsWith(t.href + "/");
            return (
              <Link key={t.href} href={t.href} className={`px-3 py-1.5 rounded-md text-[13px] whitespace-nowrap ${active ? "bg-panel-2 text-text font-semibold" : "text-muted hover:text-text"}`}>
                {t.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <button className={`btn btn-sm ${agentOpen ? "btn-primary" : ""}`} onClick={onToggleAgent} title="Producer agent">
            ✦ Producer
          </button>
          <UserButton />
        </div>
      </div>
    </header>
  );
}
