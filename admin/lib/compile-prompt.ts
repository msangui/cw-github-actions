/**
 * TypeScript port of pipeline/show.py. Used for the live "compiled writer prompt" preview and
 * to give the producer agent the exact prompt the writer will see.
 *
 * Keep byte-for-byte identical to the Python version: lib/compile-prompt.test.ts compares the
 * output against tests/fixtures/compiled_writer_prompt.txt, which the Python tests also check.
 */

export const SHOW_PLACEHOLDER = "{{SHOW}}";
export const DEFAULT_BAND_EDGES = [20, 40, 60, 80];
export const HOST_DIALS = ["humor", "straightforwardness", "warmth", "skepticism", "verbosity", "energy", "technical_depth"] as const;
export const DYNAMICS_DIALS = ["joke_density", "banter", "disagreement", "interruptions", "tangents", "callbacks", "audience_address", "pace"] as const;

export type HostDial = (typeof HOST_DIALS)[number];
export type DynamicsDial = (typeof DYNAMICS_DIALS)[number];

const LABELS: Record<string, string> = {
  humor: "Humor",
  straightforwardness: "Straightforwardness",
  warmth: "Warmth",
  skepticism: "Skepticism",
  verbosity: "Verbosity",
  energy: "Energy",
  technical_depth: "Technical depth",
  joke_density: "Joke density",
  banter: "Banter",
  disagreement: "Disagreement",
  interruptions: "Interruptions",
  tangents: "Tangents",
  callbacks: "Callbacks",
  audience_address: "Audience address",
  pace: "Pace",
};

export interface ShowHost {
  name?: string;
  role?: string;
  bio?: string;
  traits?: string[];
  quirks?: string[];
  catchphrases?: string[];
  voice_notes?: string;
  dials?: Partial<Record<HostDial, number>>;
}

export interface ShowConfig {
  show?: { name?: string; premise?: string; audience?: string };
  hosts?: Record<string, ShowHost>;
  dynamics?: Partial<Record<DynamicsDial, number>>;
  band_edges?: number[];
  bands?: { host?: Record<string, string[]>; dynamics?: Record<string, string[]> };
}

export function clampDial(value: unknown): number {
  const n = typeof value === "number" ? value : parseFloat(String(value));
  const v = Number.isFinite(n) ? Math.round(n) : 50;
  return Math.max(0, Math.min(100, v));
}

export function bandIndex(value: unknown, edges?: number[] | null): number {
  const e = edges && edges.length ? edges : DEFAULT_BAND_EDGES;
  const v = clampDial(value);
  for (let i = 0; i < e.length; i++) if (v < e[i]) return i;
  return e.length;
}

export function bandText(show: ShowConfig, group: "host" | "dynamics", dial: string, value: unknown): string {
  const bands = show.bands?.[group]?.[dial] ?? [];
  if (!bands.length) return `${LABELS[dial] ?? dial}: ${clampDial(value)}/100`;
  const idx = Math.min(bandIndex(value, show.band_edges), bands.length - 1);
  return String(bands[idx]);
}

function label(dial: string): string {
  if (LABELS[dial]) return LABELS[dial];
  const s = dial.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function list(items: unknown): string[] {
  if (!items) return [];
  if (typeof items === "string") return [items];
  if (!Array.isArray(items)) return [];
  return items.map((x) => String(x)).filter((x) => x.trim());
}

export function renderHost(show: ShowConfig, key: string, host: ShowHost): string[] {
  let head = `${key} — ${host.role || "Host"}.`;
  if (host.bio) head += ` ${String(host.bio).trim()}`;
  const lines = [head];
  const traits = list(host.traits);
  if (traits.length) lines.push("  Traits: " + traits.join("; ") + ".");
  const quirks = list(host.quirks);
  if (quirks.length) lines.push("  Quirks: " + quirks.join("; ") + ".");
  const phrases = list(host.catchphrases);
  if (phrases.length) lines.push("  Catchphrases (use sparingly): " + phrases.map((p) => `"${p}"`).join("; ") + ".");
  if (host.voice_notes) lines.push(`  Voice: ${String(host.voice_notes).trim()}`);
  const dials = host.dials ?? {};
  lines.push("  Manner:");
  for (const d of HOST_DIALS) lines.push(`    - ${label(d)} — ${bandText(show, "host", d, dials[d] ?? 50)}`);
  return lines;
}

export function renderShowBlock(show: ShowConfig): string {
  const out: string[] = [];
  const meta = show.show ?? {};
  if (meta.premise || meta.audience) {
    out.push("SHOW:");
    if (meta.premise) out.push(String(meta.premise).trim());
    if (meta.audience) out.push(`Audience: ${String(meta.audience).trim()}`);
    out.push("");
  }
  out.push("HOSTS:");
  for (const [key, host] of Object.entries(show.hosts ?? {})) out.push(...renderHost(show, key, host ?? {}));
  const dynamics = show.dynamics ?? {};
  if (Object.keys(dynamics).length || show.bands?.dynamics) {
    out.push("");
    out.push("DYNAMICS (how the hosts interact):");
    for (const d of DYNAMICS_DIALS) out.push(`- ${label(d)}: ${bandText(show, "dynamics", d, dynamics[d] ?? 50)}`);
  }
  return out.join("\n").replace(/\s+$/, "") + "\n";
}

export function compileSystemPrompt(template: string, show: ShowConfig | null | undefined): string {
  if (!template.includes(SHOW_PLACEHOLDER)) return template;
  const block = show ? renderShowBlock(show) : "";
  const result: string[] = [];
  for (const line of template.split("\n")) {
    if (line.includes(SHOW_PLACEHOLDER)) {
      const indent = line.slice(0, line.length - line.trimStart().length);
      for (const bl of block.replace(/\n+$/, "").split("\n")) result.push(bl ? indent + bl : "");
    } else {
      result.push(line);
    }
  }
  return result.join("\n");
}
