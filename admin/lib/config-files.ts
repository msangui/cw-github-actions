/**
 * The set of repo files the panel is allowed to read and commit. Anything outside this list
 * is refused by the API (and by the producer agent's tool), so a bad patch can never touch code.
 */
export const CONFIG_FILES = [
  "config/show.yaml",
  "config/podcast.yaml",
  "config/budget.yaml",
  "config/voices.yaml",
  "config/stitch.yaml",
  "config/sources.yaml",
  "config/curation.yaml",
  "config/agents/writer.yaml",
  "config/agents/aisle_writer.yaml",
  "config/agents/curator.yaml",
  "config/agents/aisle_curator.yaml",
  "config/agents/editor.yaml",
  "config/agents/newsletter.yaml",
] as const;

export type ConfigPath = (typeof CONFIG_FILES)[number];

export const AGENT_FILES: ConfigPath[] = CONFIG_FILES.filter((p) => p.startsWith("config/agents/")) as ConfigPath[];

export function isConfigPath(p: string): p is ConfigPath {
  return (CONFIG_FILES as readonly string[]).includes(p);
}

export function agentKey(p: string): string {
  return p.replace(/^config\/agents\//, "").replace(/\.yaml$/, "");
}

/** Models the Agents tab offers. Pricing is read from config/budget.yaml, not hardcoded here. */
export const MODEL_OPTIONS = [
  { id: "claude-opus-5", label: "Claude Opus 5 (default)" },
  { id: "claude-sonnet-5", label: "Claude Sonnet 5 (cheaper)" },
  { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5 (fastest)" },
];

export const EFFORT_OPTIONS = ["low", "medium", "high", "xhigh", "max"] as const;
