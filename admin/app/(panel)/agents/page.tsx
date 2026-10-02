"use client";

import { useState } from "react";
import { useConfig } from "@/components/ConfigProvider";
import { Field, NumberField, Section, Select, TextArea } from "@/components/fields";
import { PromptPreview } from "@/components/PromptPreview";
import { AGENT_FILES, agentKey, EFFORT_OPTIONS, MODEL_OPTIONS } from "@/lib/config-files";

interface AgentCfg {
  model?: string;
  max_tokens?: number;
  effort?: string;
  temperature?: number;
  system_prompt?: string;
  word_count_min_hard?: number;
  word_count_max_hard?: number;
}

const DESCRIPTIONS: Record<string, string> = {
  curator: "Ranks the scored stories and writes the editorial brief (order, timing, comedy angles, deep dives, cold open).",
  aisle_curator: "Same for The Aisle (CPG/Retail) segment.",
  writer: "Writes the full FLINT/CLAIRE script. Dominates LLM cost. Hosts + dynamics are injected at {{SHOW}} from show.yaml.",
  aisle_writer: "Writes The Aisle segment; spliced before Read These in the extended edition.",
  editor: "Fact-checks against sources, fixes inline, approves or sends the run to SAFE_MODE.",
  newsletter: "Extracts the newsletter content from the approved script.",
};

export default function AgentsPage() {
  const cfg = useConfig();
  const [sel, setSel] = useState<string>("config/agents/writer.yaml");
  const a = cfg.parsed<AgentCfg>(sel);
  const key = agentKey(sel);
  const showPreview = key === "writer" || key === "aisle_writer";
  return (
    <div className={`grid gap-4 ${showPreview ? "xl:grid-cols-[14rem_minmax(0,1fr)_28rem]" : "xl:grid-cols-[14rem_minmax(0,1fr)]"}`}>
      <nav className="card p-2 self-start">
        {AGENT_FILES.map((f) => {
          const k = agentKey(f);
          const c = cfg.parsed<AgentCfg>(f);
          const dirty = cfg.dirtyFiles.includes(f);
          return (
            <button key={f} onClick={() => setSel(f)} className={`w-full text-left px-3 py-2 rounded-md text-[13px] ${sel === f ? "bg-panel-2 font-semibold" : "hover:bg-panel-2/60"}`}>
              <div className="flex items-center gap-1">
                {k}
                {dirty && <span className="text-warn">•</span>}
              </div>
              <div className="text-muted text-xs mono">
                {c?.model ?? "?"} · {c?.effort ?? "—"}
              </div>
            </button>
          );
        })}
      </nav>
      <div className="space-y-4 min-w-0">
        <Section title={key} description={DESCRIPTIONS[key]}>
          {!a ? (
            <div className="text-muted">Not loaded.</div>
          ) : (
            <>
              <div className="grid gap-x-4 sm:grid-cols-3">
                <Field label="Model" hint="Opus for quality, Sonnet to save ~60%.">
                  <Select value={a.model ?? "claude-opus-5"} options={MODEL_OPTIONS.map((m) => ({ value: m.id, label: m.label }))} onChange={(v) => cfg.setValue(sel, ["model"], v)} />
                </Field>
                <Field label="Effort" hint="Thinking depth on Claude 5.">
                  <Select value={(a.effort ?? "high") as (typeof EFFORT_OPTIONS)[number]} options={EFFORT_OPTIONS.map((e) => ({ value: e, label: e }))} onChange={(v) => cfg.setValue(sel, ["effort"], v)} />
                </Field>
                <Field label="Max output tokens" hint="Cap for the reply; the writer needs ~12k for a script.">
                  <NumberField value={a.max_tokens} min={256} max={128000} step={256} onChange={(v) => cfg.setValue(sel, ["max_tokens"], Math.round(v))} />
                </Field>
              </div>
              {key === "writer" && (
                <div className="grid gap-x-4 sm:grid-cols-2">
                  <Field label="Hard minimum words" hint="Below this the script is regenerated (default 2000).">
                    <NumberField value={a.word_count_min_hard ?? 2000} min={200} max={10000} step={100} onChange={(v) => cfg.setValue(sel, ["word_count_min_hard"], Math.round(v))} />
                  </Field>
                  <Field label="Hard maximum words" hint="Default 5000.">
                    <NumberField value={a.word_count_max_hard ?? 5000} min={500} max={20000} step={100} onChange={(v) => cfg.setValue(sel, ["word_count_max_hard"], Math.round(v))} />
                  </Field>
                </div>
              )}
              <Field label="System prompt" hint={showPreview ? "Keep {{SHOW}} where the hosts should appear, and leave the verbatim sign-off lines and # SECTION:READ_THESE alone — the stitcher depends on them." : "Sent verbatim as the system prompt."}>
                <TextArea value={a.system_prompt ?? ""} rows={26} onChange={(v) => cfg.setValue(sel, ["system_prompt"], v)} />
              </Field>
              {showPreview && a.system_prompt && !a.system_prompt.includes("{{SHOW}}") && <div className="text-warn text-xs">Warning: no {"{{SHOW}}"} placeholder — the hosts and dials from show.yaml will not be injected.</div>}
            </>
          )}
        </Section>
      </div>
      {showPreview && <PromptPreview agent={key as "writer" | "aisle_writer"} />}
    </div>
  );
}
