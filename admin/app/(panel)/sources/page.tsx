"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useConfig } from "@/components/ConfigProvider";
import { Field, NumberField, Section, Select, TextField, Toggle } from "@/components/fields";
import { fmtTime } from "@/components/runs";
import { latestHealthRun, verdictBadge } from "@/components/source-health";
import type { RunRecord, SourceHealthRow } from "@/lib/types";

const FILE = "config/sources.yaml";
const CUR = "config/curation.yaml";

interface Source {
  name: string;
  url: string;
  tier: string;
  /** Missing means active: the pipeline defaults to true. */
  active?: boolean;
  /** Per-feed cap overriding max_entries_per_feed (firehoses such as arXiv or HuggingFace). */
  max_entries?: number;
}
interface Sources {
  window_hours?: number;
  max_entries_per_feed?: number;
  main?: Source[];
  aisle?: { enabled?: boolean; sources?: Source[] };
}
interface Curation {
  main?: { min_score?: number; max_stories?: number; deep_dive_count?: number; deep_dive_min_coverage?: number; max_per_source?: number; dedup_window_days?: number; title_overlap_threshold?: number; embedding_distance_threshold?: number };
  aisle?: { max_stories?: number; dedup_window_days?: number; title_overlap_threshold?: number };
  coverage?: { max_stories_to_search?: number; concurrency?: number };
}

const TIERS = [
  { value: "0", label: "0 · lab-direct (+3)" },
  { value: "1", label: "1 · original (+1)" },
  { value: "2", label: "2 · other" },
];

const isActive = (s: Source) => s.active !== false;

/** Health of each feed URL from the latest run that reported it. OK rows are not stored on the record, so absence = OK. */
function useLatestHealth() {
  const [run, setRun] = useState<RunRecord | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch("/api/runs", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { runs?: RunRecord[] } | null) => {
        if (!cancelled && data?.runs) setRun(latestHealthRun(data.runs));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  const byUrl = useMemo(() => new Map((run?.source_health?.unhealthy ?? []).map((r) => [r.url, r] as const)), [run]);
  return { run, byUrl };
}

export default function SourcesPage() {
  const cfg = useConfig();
  const src = cfg.parsed<Sources>(FILE);
  const cur = cfg.parsed<Curation>(CUR);
  const health = useLatestHealth();
  if (!src) return <div className="text-muted">sources.yaml not loaded.</div>;
  const h = health.run?.source_health;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Ingest window" description="How far back each feed is read. 120 h covers the Thursday → Tuesday gap.">
          <Field label="Window (hours)" inline>
            <NumberField value={src.window_hours} min={12} max={720} onChange={(v) => cfg.setValue(FILE, ["window_hours"], Math.round(v))} />
          </Field>
          <Field label="Max entries per feed" hint="Default cap; a feed's own “max” column overrides it." inline>
            <NumberField value={src.max_entries_per_feed} min={5} max={500} onChange={(v) => cfg.setValue(FILE, ["max_entries_per_feed"], Math.round(v))} />
          </Field>
        </Section>
        {cur && (
          <Section title="Curation" description="Deterministic scoring before Claude sees the list (config/curation.yaml).">
            <div className="grid sm:grid-cols-2 gap-x-6">
              <Field label="Main: stories per episode" inline>
                <NumberField value={cur.main?.max_stories} min={3} max={30} onChange={(v) => cfg.setValue(CUR, ["main", "max_stories"], Math.round(v))} />
              </Field>
              <Field label="Main: max per outlet" hint="No single feed takes more slots than this. 0 = no cap." inline>
                <NumberField value={cur.main?.max_per_source ?? 3} min={0} max={30} onChange={(v) => cfg.setValue(CUR, ["main", "max_per_source"], Math.round(v))} />
              </Field>
              <Field label="Main: deep dives" inline>
                <NumberField value={cur.main?.deep_dive_count} min={0} max={10} onChange={(v) => cfg.setValue(CUR, ["main", "deep_dive_count"], Math.round(v))} />
              </Field>
              <Field label="Deep dive: min outlets" hint="Outlets that must carry a story before it can be a deep dive." inline>
                <NumberField value={cur.main?.deep_dive_min_coverage} min={1} max={10} onChange={(v) => cfg.setValue(CUR, ["main", "deep_dive_min_coverage"], Math.round(v))} />
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

      {health.run && h && (
        <div className={`card p-3 text-[13px] flex flex-wrap items-center gap-2 ${h.unhealthy.length ? "border-warn" : ""}`}>
          <span className="font-medium">Feed health</span>
          <span className="text-muted">from run</span>
          <Link href={`/runs/${health.run.run_id}`} className="mono hover:underline">
            #{health.run.github?.run_number ?? health.run.run_id}
          </Link>
          <span className="text-muted">({fmtTime(health.run.started_at)}):</span>
          <span>
            {h.ok}/{h.total} feeds OK
          </span>
          {h.unhealthy.length > 0 && <span className="text-muted">· {h.unhealthy.length} flagged below — toggle Active off for feeds that stay dead, or fix the URL</span>}
        </div>
      )}

      <SourceTable title="Main feeds" description="Tier 0 stories are auto-included by the curator. Toggle Active to pause a feed without losing it." path={["main"]} items={src.main ?? []} health={health.byUrl} hasHealth={!!h} />
      <Section
        title="The Aisle (CPG / Retail)"
        description="Optional segment spliced into the extended edition."
        right={<Toggle checked={src.aisle?.enabled ?? true} onChange={(v) => cfg.setValue(FILE, ["aisle", "enabled"], v)} label={src.aisle?.enabled === false ? "disabled" : "enabled"} />}
      >
        <SourceRows path={["aisle", "sources"]} items={src.aisle?.sources ?? []} health={health.byUrl} hasHealth={!!h} />
      </Section>
    </div>
  );
}

function SourceTable({ title, description, path, items, health, hasHealth }: { title: string; description: string; path: (string | number)[]; items: Source[]; health: Map<string, SourceHealthRow>; hasHealth: boolean }) {
  const active = items.filter(isActive).length;
  return (
    <Section title={`${title} · ${active}/${items.length} active`} description={description}>
      <SourceRows path={path} items={items} health={health} hasHealth={hasHealth} />
    </Section>
  );
}

const GRID = "grid grid-cols-[1fr_2fr_9rem_4rem_4.5rem_6rem_2rem] gap-2";

function SourceRows({ path, items, health, hasHealth }: { path: (string | number)[]; items: Source[]; health: Map<string, SourceHealthRow>; hasHealth: boolean }) {
  const cfg = useConfig();
  return (
    <div className="space-y-1">
      <div className={`${GRID} text-muted text-xs px-1`}>
        <span>Name</span>
        <span>Feed URL</span>
        <span>Tier</span>
        <span title="Per-feed entry cap (blank = default)">Max</span>
        <span>Active</span>
        <span>Last run</span>
        <span />
      </div>
      {items.map((s, i) => {
        const row = health.get(s.url);
        return (
          <div key={i} className={`${GRID} items-center rounded-md px-1 py-0.5 ${isActive(s) ? "" : "opacity-60"}`}>
            <TextField value={s.name} onChange={(v) => cfg.setValue(FILE, [...path, i, "name"], v)} />
            <TextField value={s.url} mono onChange={(v) => cfg.setValue(FILE, [...path, i, "url"], v)} />
            <Select value={String(s.tier)} options={TIERS} onChange={(v) => cfg.setValue(FILE, [...path, i, "tier"], v)} />
            <MaxEntries value={s.max_entries} onChange={(v) => (v === undefined ? cfg.applyOps(FILE, [{ op: "delete", path: [...path, i, "max_entries"] }]) : cfg.setValue(FILE, [...path, i, "max_entries"], v))} />
            <Toggle checked={isActive(s)} onChange={(v) => cfg.setValue(FILE, [...path, i, "active"], v)} />
            <span className="text-xs" title={row ? `${row.verdict}${row.error ? ` — ${row.error}` : ""} (entries ${row.entries}, kept ${row.kept})` : ""}>
              {row ? verdictBadge(row.verdict) : hasHealth && isActive(s) ? <span className="text-muted">ok</span> : null}
            </span>
            <button className="btn btn-sm btn-danger" title="Remove feed" onClick={() => cfg.applyOps(FILE, [{ op: "delete", path: [...path, i] }])}>
              ×
            </button>
          </div>
        );
      })}
      <button className="btn btn-sm mt-2" onClick={() => cfg.applyOps(FILE, [{ op: "append", path, value: { name: "New feed", url: "https://", tier: "2", active: false } }])}>
        + add feed
      </button>
    </div>
  );
}

/** Blank = inherit max_entries_per_feed; clearing the box removes the key from the YAML. */
function MaxEntries({ value, onChange }: { value: number | undefined; onChange: (v: number | undefined) => void }) {
  const [draft, setDraft] = useState(value === undefined ? "" : String(value));
  useEffect(() => setDraft(value === undefined ? "" : String(value)), [value]);
  return (
    <input
      className="input mono"
      type="number"
      min={1}
      max={500}
      placeholder="—"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (draft.trim() === "") {
          if (value !== undefined) onChange(undefined);
          return;
        }
        const n = Math.round(parseFloat(draft));
        if (Number.isFinite(n) && n > 0 && n !== value) onChange(n);
        else setDraft(value === undefined ? "" : String(value));
      }}
    />
  );
}
