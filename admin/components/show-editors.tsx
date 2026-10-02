"use client";

import { useConfig } from "@/components/ConfigProvider";
import { Section, StringList } from "@/components/fields";
import type { ShowConfig } from "@/lib/compile-prompt";

const FILE = "config/show.yaml";

export function labelOf(d: string) {
  return d.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function BandEditor({ show, group, dials, title, description }: { show: ShowConfig; group: "host" | "dynamics"; dials: string[]; title: string; description: string }) {
  const cfg = useConfig();
  return (
    <Section title={title} description={description}>
      <div className="grid gap-4 md:grid-cols-2">
        {dials.map((d) => (
          <div key={d}>
            <div className="text-[13px] font-medium mb-1">{labelOf(d)}</div>
            <StringList fixedLength items={show.bands?.[group]?.[d] ?? ["", "", "", "", ""]} onChange={(v) => cfg.setValue(FILE, ["bands", group, d], v)} />
          </div>
        ))}
      </div>
    </Section>
  );
}
