"use client";

import { useConfig } from "@/components/ConfigProvider";
import { Dial, Field, Section, StringList, TextArea, TextField } from "@/components/fields";
import { PromptPreview } from "@/components/PromptPreview";
import { bandIndex, bandText, HOST_DIALS, type ShowConfig, type ShowHost } from "@/lib/compile-prompt";
import { BandEditor, labelOf } from "@/components/show-editors";

const FILE = "config/show.yaml";
const DIAL_HINTS: Record<string, string> = {
  humor: "how often they go for the laugh",
  straightforwardness: "hedging vs. blunt",
  warmth: "cold ↔ affectionate",
  skepticism: "believer ↔ prove it",
  verbosity: "clipped ↔ monologues",
  energy: "sleepy ↔ bursting",
  technical_depth: "headline ↔ researcher-grade",
};

export default function HostsPage() {
  const cfg = useConfig();
  const show = cfg.parsed<ShowConfig>(FILE);
  if (!show) return <div className="text-muted">show.yaml not loaded{cfg.files[FILE] === undefined ? " — commit config/show.yaml to the repo first." : " (invalid YAML?)."}</div>;
  const hosts = Object.entries(show.hosts ?? {});
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_28rem]">
      {hosts.map(([key, host]) => (
        <HostCard key={key} hostKey={key} host={host ?? {}} show={show} />
      ))}
      <div className="xl:col-span-2 space-y-4">
        <Section title="Show" description="One-line premise and audience note. Both go at the top of the writer prompt.">
          <Field label="Premise">
            <TextField value={show.show?.premise ?? ""} onChange={(v) => cfg.setValue(FILE, ["show", "premise"], v)} />
          </Field>
          <Field label="Audience">
            <TextField value={show.show?.audience ?? ""} onChange={(v) => cfg.setValue(FILE, ["show", "audience"], v)} />
          </Field>
        </Section>
        <BandEditor show={show} group="host" dials={[...HOST_DIALS]} title="Host dial language" description="The five sentences each host dial can resolve to (bands 1–5). The writer reads these, not the numbers. Edit to change what a dial position means." />
      </div>
      <div className="xl:row-start-1 xl:col-start-3 xl:row-span-3">
        <PromptPreview />
      </div>
    </div>
  );
}

function HostCard({ hostKey, host, show }: { hostKey: string; host: ShowHost; show: ShowConfig }) {
  const cfg = useConfig();
  const p = (...rest: (string | number)[]) => ["hosts", hostKey, ...rest];
  return (
    <Section title={`${hostKey} · ${host.role ?? ""}`} description={host.bio}>
      <div className="grid gap-x-4 sm:grid-cols-2">
        <Field label="Display name">
          <TextField value={host.name ?? ""} onChange={(v) => cfg.setValue(FILE, p("name"), v)} />
        </Field>
        <Field label="Role">
          <TextField value={host.role ?? ""} onChange={(v) => cfg.setValue(FILE, p("role"), v)} />
        </Field>
      </div>
      <Field label="Bio" hint="One sentence. Goes right after the name.">
        <TextField value={host.bio ?? ""} onChange={(v) => cfg.setValue(FILE, p("bio"), v)} />
      </Field>
      <Field label="Voice notes" hint="How they sound; the writer uses it for rhythm and word choice.">
        <TextArea value={host.voice_notes ?? ""} onChange={(v) => cfg.setValue(FILE, p("voice_notes"), v)} rows={2} mono={false} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-3 mt-2">
        <Field label="Traits">
          <StringList items={host.traits ?? []} onChange={(v) => cfg.setValue(FILE, p("traits"), v)} placeholder="trait" />
        </Field>
        <Field label="Quirks" hint="Verbal tics the script should show.">
          <StringList items={host.quirks ?? []} onChange={(v) => cfg.setValue(FILE, p("quirks"), v)} placeholder="quirk" />
        </Field>
        <Field label="Catchphrases" hint="Used sparingly.">
          <StringList items={host.catchphrases ?? []} onChange={(v) => cfg.setValue(FILE, p("catchphrases"), v)} placeholder="phrase" />
        </Field>
      </div>
      <h3 className="font-semibold text-[13px] mt-4 mb-1">Manner dials</h3>
      <div className="divide-y divide-border/60">
        {HOST_DIALS.map((d) => {
          const v = host.dials?.[d] ?? 50;
          return <Dial key={d} label={labelOf(d)} hint={DIAL_HINTS[d]} value={v} band={bandText(show, "host", d, v)} bandIndex={bandIndex(v, show.band_edges)} onChange={(nv) => cfg.setValue(FILE, p("dials", d), nv)} />;
        })}
      </div>
    </Section>
  );
}
