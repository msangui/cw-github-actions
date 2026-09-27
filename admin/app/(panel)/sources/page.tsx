"use client";

import { useConfig } from "@/components/ConfigProvider";
import { Field, NumberField, Section, Select, TextField, Toggle } from "@/components/fields";

const FILE = "config/sources.yaml";
const CUR = "config/curation.yaml";

interface Source {
  name: string;
  url: string;
  tier: string;
  active: boolean;
}
interface Sources {
  window_hours?: number;
  max_entries_per_feed?: number;
  main?: Source[];
  aisle?: { enabled?: boolean; sources?: Source[] };
}
interface Curation {
  main?: { min_score?: number; max_stories?: number; deep_dive_count?: number; deep_dive_min_coverage?: number; dedup_window_days?: number; title_overlap_threshold?: number; embedding_distance_threshold?: number };
  aisle?: { max_stories?: number; dedup_window_days?: number; title_overlap_threshold?: number };
  coverage?: { max_stories_to_search?: number; concurrency?: number };
}

const TIERS = [
  { value: "0", label: "0 · lab-direct (+3)" },
  { value: "1", label: "1 · original (+1)" },
  { value: "2", label: "2 · other" },
];

export default function SourcesPage() {
  const cfg = useConfig();
  const src = cfg.parsed<Sources>(FILE);
  const cur = cfg.parsed<Curation>(CUR);
  if (!src) return <div className="text-muted">sources.yaml not loaded.</div>;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Ingest window" description="How far back each feed is read. 120 h covers the Thursday → Tuesday gap.">
          <Field label="Window (hours)" inline>
            <NumberField value={src.window_hours} min={12} max={720} onChange={(v) => cfg.setValue(FILE, ["window_hours"], Math.round(v))} />
          </Field>
          <Field label="Max entries per feed" inline>
            <NumberField value={src.max_entries_per_feed} min={5} max={500} onChange={(v) => cfg.setValue(FILE, ["max_entries_per_feed"], Math.round(v))} />
          </Field>
        </Section>
        {cur && (
          <Section title="Curation" description="Deterministic scoring before Claude sees the list (config/curation.yaml).">
            <div className="grid sm:grid-cols-2 gap-x-6">
              <Field label="Main: stories per episode" inline>
                <NumberField value={cur.main?.max_stories} min={3} max={30} onChange={(v) => cfg.setValue(CUR, ["main", "max_stories"], Math.round(v))} />
              </Field>
              <Field label="Main: deep dives" inline>
                <NumberField value={cur.main?.deep_dive_count} min={0} max={10} onChange={(v) => cfg.setValue(CUR, ["main", "deep_dive_count"], Math.round(v))} />
              </Field>
              <Field label="Main: min score" inline>
                <NumberField value={cur.main?.min_score} min={0} max={10} onChange={(v) => cfg.setValue(CUR, ["main", "min_score"], Math.round(v))} />
              </Field>
              <Field label="Dedup window (days)" inline>
                <NumberField value={cur.main?.dedup_window_days} min={1} max={90} onChange={(v) => cfg.setValue(CUR, ["main", "dedup_window_days"], Math.round(v))} />
              </Field>
              <Field label="Aisle: stories" inline>
                <NumberField value={cur.aisle?.max_stories} min={0} max={10} onChange={(v) => cfg.setValue(CUR, ["aisle", "max_stories"], Math.round(v))} />
              </Field>
              <Field label="Serper: stories searched" inline>
                <NumberField value={cur.coverage?.max_stories_to_search} min={0} max={500} onChange={(v) => cfg.setValue(CUR, ["coverage", "max_stories_to_search"], Math.round(v))} />
              </Field>
            </div>
          </Section>
        )}
      </div>
      <SourceTable title="Main feeds" description="Tier 0 stories are auto-included by the curator. Toggle Active to pause a feed without losing it." path={["main"]} items={src.main ?? []} />
      <Section
        title="The Aisle (CPG / Retail)"
        description="Optional segment spliced into the extended edition."
        right={<Toggle checked={src.aisle?.enabled ?? true} onChange={(v) => cfg.setValue(FILE, ["aisle", "enabled"], v)} label={src.aisle?.enabled === false ? "disabled" : "enabled"} />}
      >
        <SourceRows path={["aisle", "sources"]} items={src.aisle?.sources ?? []} />
      </Section>
    </div>
  );
}

function SourceTable({ title, description, path, items }: { title: string; description: string; path: (string | number)[]; items: Source[] }) {
  const active = items.filter((s) => s.active).length;
  return (
    <Section title={`${title} · ${active}/${items.length} active`} description={description}>
      <SourceRows path={path} items={items} />
    </Section>
  );
}

function SourceRows({ path, items }: { path: (string | number)[]; items: Source[] }) {
  const cfg = useConfig();
  return (
    <div className="space-y-1">
      <div className="grid grid-cols-[1fr_2fr_9rem_5rem_2rem] gap-2 text-muted text-xs px-1">
        <span>Name</span>
        <span>Feed URL</span>
        <span>Tier</span>
        <span>Active</span>
        <span />
      </div>
      {items.map((s, i) => (
        <div key={i} className={`grid grid-cols-[1fr_2fr_9rem_5rem_2rem] gap-2 items-center rounded-md px-1 py-0.5 ${s.active ? "" : "opacity-60"}`}>
          <TextField value={s.name} onChange={(v) => cfg.setValue(FILE, [...path, i, "name"], v)} />
          <TextField value={s.url} mono onChange={(v) => cfg.setValue(FILE, [...path, i, "url"], v)} />
          <Select value={String(s.tier)} options={TIERS} onChange={(v) => cfg.setValue(FILE, [...path, i, "tier"], v)} />
          <Toggle checked={!!s.active} onChange={(v) => cfg.setValue(FILE, [...path, i, "active"], v)} />
          <button className="btn btn-sm btn-danger" title="Remove feed" onClick={() => cfg.applyOps(FILE, [{ op: "delete", path: [...path, i] }])}>
            ×
          </button>
        </div>
      ))}
      <button className="btn btn-sm mt-2" onClick={() => cfg.applyOps(FILE, [{ op: "append", path, value: { name: "New feed", url: "https://", tier: "2", active: false } }])}>
        + add feed
      </button>
    </div>
  );
}
