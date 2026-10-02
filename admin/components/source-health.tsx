"use client";

import type { RunEvent, RunRecord, SourceHealthRow } from "@/lib/types";

/** Badge colour per verdict from pipeline/stages/ingest.py: OK, DEAD, HTTP <code>, EMPTY, STALE. */
export function verdictBadge(verdict: string) {
  const v = verdict.toUpperCase();
  const cls = v === "OK" ? "badge-ok" : v === "DEAD" || v.startsWith("HTTP") ? "badge-err" : "badge-warn";
  return <span className={`badge ${cls}`}>{v}</span>;
}

const ORDER: Record<string, number> = { DEAD: 0, EMPTY: 2, STALE: 3, OK: 9 };
function rank(v: string) {
  return ORDER[v] ?? 1; // HTTP 403 / 429 sort between DEAD and EMPTY
}

/** Full rows from the run's `source_health` event (the record only keeps the unhealthy ones). */
export function healthRowsFromEvents(events: RunEvent[]): SourceHealthRow[] | null {
  const ev = [...events].reverse().find((e) => e.type === "source_health");
  return ev && Array.isArray(ev.rows) ? (ev.rows as SourceHealthRow[]) : null;
}

/** The most recent run that reported feed health, for the Sources page. */
export function latestHealthRun(runs: RunRecord[]): RunRecord | null {
  return runs.filter((r) => r.source_health).sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""))[0] ?? null;
}

export function SourceHealthTable({ rows, windowHours }: { rows: SourceHealthRow[]; windowHours?: number }) {
  const sorted = [...rows].sort((a, b) => rank(a.verdict) - rank(b.verdict) || a.group.localeCompare(b.group) || a.tier.localeCompare(b.tier) || a.source.localeCompare(b.source));
  const unhealthy = rows.filter((r) => r.verdict !== "OK").length;
  return (
    <div>
      <div className="text-[13px] mb-2">
        <span className="font-medium">{rows.length - unhealthy}</span> of <span className="font-medium">{rows.length}</span> feeds delivered stories
        {windowHours ? <span className="text-muted"> inside the {windowHours} h window</span> : null}
        {unhealthy > 0 && <span className="text-muted"> · {unhealthy} need attention (listed first)</span>}
      </div>
      <table className="text-[13px] w-full">
        <thead className="text-muted text-xs">
          <tr>
            <th className="text-left">verdict</th>
            <th className="text-left">group</th>
            <th className="text-left">tier</th>
            <th className="text-left">source</th>
            <th className="text-right">entries</th>
            <th className="text-right">kept</th>
            <th className="text-left pl-3">note</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={`${r.group}:${r.url}`} className={r.verdict === "OK" ? "text-muted" : ""}>
              <td className="py-0.5">{verdictBadge(r.verdict)}</td>
              <td>{r.group}</td>
              <td className="mono">{r.tier}</td>
              <td>
                <a href={r.url} target="_blank" rel="noreferrer" className="hover:underline" title={r.url}>
                  {r.source}
                </a>
              </td>
              <td className="mono text-right">{r.entries}</td>
              <td className="mono text-right">{r.kept}</td>
              <td className="pl-3 text-xs text-muted truncate max-w-xs" title={r.error}>
                {r.error || (r.status && r.status !== 200 ? `HTTP ${r.status}` : "")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
