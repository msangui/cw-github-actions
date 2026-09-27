import type { GitHubRunSummary, RunRecord } from "./types";

export function githubStatus(g: GitHubRunSummary): string {
  if (g.status === "completed") {
    if (g.conclusion === "success") return "COMPLETED";
    if (g.conclusion === "cancelled") return "CANCELLED";
    return "FAILED";
  }
  return g.status === "queued" ? "QUEUED" : "IN_PROGRESS";
}

/** Merge the panel's run records with the GitHub Actions run list (fallback when no events arrived). */
export function mergeRuns(stored: RunRecord[], github: GitHubRunSummary[]): RunRecord[] {
  const byId = new Map<string, RunRecord>(stored.map((r) => [r.run_id, { ...r }]));
  for (const g of github) {
    const id = String(g.id);
    const cur = byId.get(id);
    if (cur) {
      cur.github = g;
      cur.run_url ??= g.html_url;
      cur.started_at ??= g.created_at;
      // Events say what the pipeline thinks; GitHub says whether the job is still alive. If the
      // job finished but the pipeline never reported a terminal status, trust GitHub.
      const terminal = ["PUBLISHED", "SAFE_MODE", "DRY_RUN", "AUDIO_READY", "PAUSED", "FAILED", "ALREADY_PUBLISHED", "CANCELLED"].includes(cur.status);
      if (g.status === "completed" && !terminal) {
        cur.status = githubStatus(g) === "COMPLETED" ? cur.status : githubStatus(g);
        cur.source = "github";
        cur.finished_at ??= g.updated_at;
      }
    } else {
      byId.set(id, {
        run_id: id,
        run_url: g.html_url,
        started_at: g.created_at,
        updated_at: g.updated_at,
        finished_at: g.status === "completed" ? g.updated_at : undefined,
        status: githubStatus(g),
        status_history: [],
        event_count: 0,
        source: "github",
        github: g,
      });
    }
  }
  return [...byId.values()].sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""));
}

