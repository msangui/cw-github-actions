"use client";

import { useConfig } from "@/components/ConfigProvider";
import { Field, NumberField, Section } from "@/components/fields";

const FILE = "config/budget.yaml";

interface Budget {
  pricing?: { llm?: Record<string, { input: number; output: number }>; elevenlabs_per_1k_chars?: number; serper_per_query?: number };
  thresholds?: { daily_alert?: number; daily_alert_breakdown?: number; monthly_warn?: number; monthly_hard_pause?: number; target_monthly?: number };
}

export default function BudgetPage() {
  const cfg = useConfig();
  const b = cfg.parsed<Budget>(FILE);
  if (!b) return <div className="text-muted">budget.yaml not loaded.</div>;
  const t = b.thresholds ?? {};
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title="Thresholds (USD)" description="The CFO stage alerts on Telegram and refuses to start a run past the monthly hard pause. Override a pause with the CW_IGNORE_BUDGET repo variable.">
        <Field label="Daily alert" hint="alert, investigate, do not pause" inline>
          <NumberField value={t.daily_alert} min={0} step={0.5} onChange={(v) => cfg.setValue(FILE, ["thresholds", "daily_alert"], v)} />
        </Field>
        <Field label="Daily alert with breakdown" inline>
          <NumberField value={t.daily_alert_breakdown} min={0} step={0.5} onChange={(v) => cfg.setValue(FILE, ["thresholds", "daily_alert_breakdown"], v)} />
        </Field>
        <Field label="Monthly warn" hint="alert with month-end projection" inline>
          <NumberField value={t.monthly_warn} min={0} step={5} onChange={(v) => cfg.setValue(FILE, ["thresholds", "monthly_warn"], v)} />
        </Field>
        <Field label="Monthly hard pause" hint="new runs refuse to start" inline>
          <NumberField value={t.monthly_hard_pause} min={0} step={5} onChange={(v) => cfg.setValue(FILE, ["thresholds", "monthly_hard_pause"], v)} />
        </Field>
        <Field label="Target monthly" hint="informational" inline>
          <NumberField value={t.target_monthly} min={0} step={1} onChange={(v) => cfg.setValue(FILE, ["thresholds", "target_monthly"], v)} />
        </Field>
      </Section>
      <Section title="Pricing used for estimates" description="USD per 1M tokens by model prefix (first match wins). Keep in sync with Anthropic's price list; this is what cost.json is computed from.">
        {Object.entries(b.pricing?.llm ?? {}).map(([prefix, rate]) => (
          <div key={prefix} className="grid grid-cols-[1fr_7rem_7rem] gap-2 items-center py-1">
            <span className="mono">{prefix}</span>
            <NumberField value={rate.input} min={0} step={0.25} onChange={(v) => cfg.setValue(FILE, ["pricing", "llm", prefix, "input"], v)} />
            <NumberField value={rate.output} min={0} step={0.25} onChange={(v) => cfg.setValue(FILE, ["pricing", "llm", prefix, "output"], v)} />
          </div>
        ))}
        <div className="grid grid-cols-[1fr_7rem_7rem] gap-2 text-muted text-xs px-0.5">
          <span />
          <span>input $/1M</span>
          <span>output $/1M</span>
        </div>
        <div className="mt-3 border-t border-border pt-3">
          <Field label="ElevenLabs $ per 1k characters" inline>
            <NumberField value={b.pricing?.elevenlabs_per_1k_chars} min={0} step={0.01} onChange={(v) => cfg.setValue(FILE, ["pricing", "elevenlabs_per_1k_chars"], v)} />
          </Field>
          <Field label="Serper $ per query" inline>
            <NumberField value={b.pricing?.serper_per_query} min={0} step={0.001} onChange={(v) => cfg.setValue(FILE, ["pricing", "serper_per_query"], v)} />
          </Field>
        </div>
      </Section>
    </div>
  );
}
