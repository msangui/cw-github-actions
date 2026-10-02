"use client";

import Link from "next/link";
import type { RunRecord } from "@/lib/types";

export function statusBadge(status: string) {
  const s = status.toUpperCase();
  const cls = ["PUBLISHED", "DRY_RUN", "DRY_RUN_COMPLETE", "AUDIO_READY", "ALREADY_PUBLISHED", "COMPLETED"].includes(s) ? "badge-ok" : ["FAILED", "CANCELLED", "PAUSED"].includes(s) ? "badge-err" : ["SAFE_MODE"].includes(s) ? "badge-warn" : ["QUEUED", "UNKNOWN"].includes(s) ? "" : "badge-live";
  return <span className={`badge ${cls}`}>{s.replace(/_/g, " ")}</span>;
}

export function fmtTime(iso?: string) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtDuration(a?: string, b?: string) {
  if (!a) return "";
  const ms = (b ? new Date(b).getTime() : Date.now()) - new Date(a).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "";
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return m ? `${m}m ${s}s` : `${s}s`;
}

export function RunRow({ run }: { run: RunRecord }) {
  return (
    <Link href={`/runs/${run.run_id}`} className="grid grid-cols-[7rem_10rem_1fr_7rem_6rem_5rem] gap-3 items-center px-3 py-2 rounded-md hover:bg-panel-2 text-[13px]">
      <span className="mono text-muted">#{run.github?.run_number ?? run.run_id}</span>
      <span>{fmtTime(run.started_at)}</span>
      <span className="truncate">
        {run.title ?? run.episode_date ?? run.github?.display_title ?? ""}
        {run.dry_run && <span className="badge ml-2">dry run</span>}
        {run.triggered_by && <span className="text-muted"> · {run.triggered_by}</span>}
      </span>
      <span>{statusBadge(run.status)}</span>
      <span className="mono text-muted">{run.cost ? `$${run.cost.total_usd.toFixed(2)}` : ""}</span>
      <span className="mono text-muted text-right">{fmtDuration(run.started_at, run.finished_at)}</span>
    </Link>
  );
}
