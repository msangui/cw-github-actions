"use client";

import { useConfig } from "@/components/ConfigProvider";
import { Dial, Section } from "@/components/fields";
import { PromptPreview } from "@/components/PromptPreview";
import { bandIndex, bandText, DYNAMICS_DIALS, type ShowConfig } from "@/lib/compile-prompt";
import { BandEditor, labelOf } from "@/components/show-editors";

const FILE = "config/show.yaml";
const HINTS: Record<string, string> = {
  joke_density: "laughs per story",
  banter: "off-news back-and-forth",
  disagreement: "how hard they push each other",
  interruptions: "clean turns ↔ overlapping",
  tangents: "how far they wander",
  callbacks: "running bits across the episode",
  audience_address: "talking to the listener",
  pace: "deliberate ↔ rapid-fire",
};

export default function DynamicsPage() {
  const cfg = useConfig();
  const show = cfg.parsed<ShowConfig>(FILE);
  if (!show) return <div className="text-muted">show.yaml not loaded.</div>;
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_28rem]">
      <div className="space-y-4">
        <Section title="Interaction dynamics" description="Show-level dials: how FLINT and CLAIRE play off each other. Each maps to one of five sentences below; the writer only sees the sentence.">
          <div className="grid md:grid-cols-2 gap-x-8 divide-y md:divide-y-0 divide-border/60">
            {DYNAMICS_DIALS.map((d) => {
              const v = show.dynamics?.[d] ?? 50;
              return <Dial key={d} label={labelOf(d)} hint={HINTS[d]} value={v} band={bandText(show, "dynamics", d, v)} bandIndex={bandIndex(v, show.band_edges)} onChange={(nv) => cfg.setValue(FILE, ["dynamics", d], nv)} />;
            })}
          </div>
        </Section>
        <BandEditor show={show} group="dynamics" dials={[...DYNAMICS_DIALS]} title="Dynamics dial language" description="Five sentences per dynamic (bands 1–5)." />
      </div>
      <PromptPreview />
    </div>
  );
}
