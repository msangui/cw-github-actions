/** System prompt and context assembly for the producer agent. */
import { agentKey, CONFIG_FILES } from "./config-files";
import { compileSystemPrompt, type ShowConfig } from "./compile-prompt";
import type { RunEvent, RunRecord } from "./types";
import { parseYaml } from "./yaml-patch";

/** Stable across turns and users → first system block, cached. */
export const PRODUCER_INSTRUCTIONS = `You are the producer of "Context Window", a fully automated daily AI podcast. Two synthetic hosts, FLINT and CLAIRE, read a script that a Python pipeline writes with Claude, voices with ElevenLabs and publishes to an RSS feed from GitHub Actions. The operator you are talking to is a hands-on executive, not an ML engineer: be concrete, recommend rather than survey, and explain tradeoffs in one or two sentences.

Everything about the show is configuration in git, and you can only change the show by changing that configuration:
- config/show.yaml — hosts (bio, traits, quirks, catchphrases, voice notes) with 0–100 manner dials, show-level dynamics dials, and the five language "bands" each dial maps to (0–19, 20–39, 40–59, 60–79, 80–100). The writer never sees the numbers, only the band sentences, so a dial change only matters if it crosses a band edge; editing the band text is the other lever.
- config/agents/*.yaml — per-agent model, max_tokens, effort (low/medium/high/xhigh/max) and system_prompt. writer.yaml and aisle_writer.yaml contain a {{SHOW}} placeholder where show.yaml is rendered in. The verbatim sign-off lines and the "# SECTION:READ_THESE" marker in writer.yaml are load-bearing: the stitcher and the extended edition depend on them. Never propose removing or rewording them.
- config/sources.yaml (RSS feeds + tiers), config/curation.yaml (scoring), config/voices.yaml (ElevenLabs voice settings), config/stitch.yaml (mix), config/budget.yaml (pricing + thresholds), config/podcast.yaml (feed metadata).

How to work:
1. Read the run's logs, script and cost when they are provided. Point at specific lines or exchanges when you diagnose something (a flat cold open, Claire too agreeable, a story that ran long, a validation retry, an expensive agent).
2. When you recommend a change, call propose_config_patch with a precise YAML patch. One proposal per coherent change; several proposals in one turn are fine. Prefer the smallest change that moves the show: a dial, a band sentence, a trait, an effort level. Do not rewrite whole prompts unless asked.
3. After proposing, summarize in plain language what will be different in the next episode and what to watch for. The operator applies proposals to a pending edit set and commits when ready; you never commit.
4. Costs: the writer dominates LLM spend; TTS is roughly proportional to word count. Budget thresholds live in budget.yaml.

Patch format: ops are applied to the YAML document in order. "set" writes a value at a path (creating it if needed), "delete" removes it, "append" adds to a list. Paths are arrays of keys and list indexes, e.g. ["hosts","CLAIRE","dials","warmth"] or ["bands","dynamics","banter",3] or ["main",12,"active"]. Values must be plain JSON (strings, numbers, booleans, lists, objects). For system_prompt edits, set the whole string and keep {{SHOW}} in place.`;

export function describeConfig(files: Record<string, string>): string {
  const parts: string[] = ["CURRENT CONFIGURATION (pending edits included; this is exactly what would be committed):"];
  for (const p of CONFIG_FILES) {
    if (files[p] === undefined) continue;
    parts.push(`\n--- ${p} ---\n${files[p].trimEnd()}`);
  }
  try {
    const show = parseYaml<ShowConfig>(files["config/show.yaml"] ?? "");
    const writer = parseYaml<{ system_prompt?: string }>(files["config/agents/writer.yaml"] ?? "");
    parts.push(`\n--- COMPILED WRITER SYSTEM PROMPT (what the writer model actually receives) ---\n${compileSystemPrompt(writer.system_prompt ?? "", show)}`);
  } catch (e) {
    parts.push(`\n(compiled prompt unavailable: ${(e as Error).message})`);
  }
  return parts.join("\n");
}

const LOG_KEYS_TO_SKIP = new Set(["seq", "ts", "type", "level", "event", "stage", "component", "date"]);

export function formatLogLine(e: RunEvent): string {
  const where = e.stage ?? e.component ?? "";
  const extras = Object.entries(e)
    .filter(([k, v]) => !LOG_KEYS_TO_SKIP.has(k) && v !== undefined && v !== null)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" ");
  return `${(e.ts ?? "").slice(11, 19)} ${(e.level ?? "info").padEnd(7)} ${where ? `[${where}] ` : ""}${e.event ?? ""}${extras ? " " + extras : ""}`.slice(0, 600);
}

export function describeRun(run: RunRecord | null, events: RunEvent[], script: string | null): string {
  if (!run) return "No run selected. The operator may still ask about configuration.";
  const lines: string[] = [`SELECTED RUN ${run.run_id} — status ${run.status}${run.episode_date ? `, episode ${run.episode_date}` : ""}${run.dry_run ? " (dry run)" : ""}${run.run_url ? `, ${run.run_url}` : ""}`];
  if (run.status_history.length) lines.push("Status timeline: " + run.status_history.map((s) => `${s.status}@${s.at.slice(11, 19)}`).join(" → "));
  if (run.cost) lines.push(`Cost: total $${run.cost.total_usd} (LLM $${run.cost.llm_usd}${run.cost.llm_by_agent ? " — " + Object.entries(run.cost.llm_by_agent).map(([a, c]) => `${a} $${c}`).join(", ") : ""}; TTS $${run.cost.tts_usd}${run.cost.tts_chars ? ` for ${run.cost.tts_chars} chars` : ""}; Serper $${run.cost.serper_usd})`);
  if (run.result?.error) lines.push(`Error: ${String(run.result.error)}`);
  const logs = events.filter((e) => e.type === "log");
  const important = logs.filter((e) => e.level === "warning" || e.level === "error");
  const tail = logs.slice(-160);
  const shown = [...new Map([...important.slice(-60), ...tail].map((e) => [e.seq, e])).values()].sort((a, b) => a.seq - b.seq);
  lines.push(`\nLOG (${logs.length} lines total; showing warnings/errors plus the last ${tail.length}):`);
  lines.push(...shown.map(formatLogLine));
  if (script) {
    const max = 40_000;
    lines.push(`\nSCRIPT${run.title ? ` — "${run.title}"` : ""}${run.word_count ? ` (${run.word_count} words)` : ""}${script.length > max ? " (truncated)" : ""}:`);
    lines.push(script.slice(0, max));
  } else {
    lines.push("\nNo script was reported for this run yet.");
  }
  return lines.join("\n");
}

export function agentDisplayName(path: string): string {
  return agentKey(path);
}
