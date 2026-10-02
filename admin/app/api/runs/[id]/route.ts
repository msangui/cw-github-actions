import { NextResponse } from "next/server";
import { errorResponse, requireUser } from "@/lib/auth";
import { getWorkflowRun, repoConfig } from "@/lib/github";
import { getStore } from "@/lib/store";
import { TERMINAL_STATUSES } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * GET /api/runs/{id}?since=<seq>&script=1
 * Run record + events after `since` (seq is 1-based; pass the last seq you have). The UI polls this.
 * When the pipeline has not reported anything, the GitHub Actions API supplies the status.
 */
export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }) {
  try {
    await requireUser();
    const { id } = await ctx.params;
    const url = new URL(req.url);
    const since = Math.max(0, parseInt(url.searchParams.get("since") ?? "0", 10) || 0);
    const wantScript = url.searchParams.get("script") === "1";
    const store = getStore();
    let run = await store.getRun(id);
    let github = null;
    try {
      github = await getWorkflowRun(repoConfig(), id);
    } catch {
      /* GitHub unreachable: fall through with what the store has */
    }
    if (!run && !github) return NextResponse.json({ error: "Run not found" }, { status: 404 });
    if (!run) {
      run = { run_id: id, status: "UNKNOWN", status_history: [], event_count: 0, source: "github" };
    }
    if (github) {
      run = { ...run, github, run_url: run.run_url ?? github.html_url, started_at: run.started_at ?? github.created_at };
      const terminal = TERMINAL_STATUSES.includes(run.status as never);
      if (github.status === "completed" && !terminal) {
        run.status = github.conclusion === "success" ? (run.event_count ? run.status : "PUBLISHED") : github.conclusion === "cancelled" ? "CANCELLED" : "FAILED";
        run.source = "github";
        run.finished_at ??= github.updated_at;
      } else if (github.status !== "completed" && (run.status === "UNKNOWN" || run.status === "QUEUED") && !run.event_count) {
        run.status = github.status === "queued" ? "QUEUED" : "IN_PROGRESS";
        run.source = "github";
      }
    }
    const events = await store.getEvents(id, since);
    const script = wantScript ? await store.getScript(id) : undefined;
    return NextResponse.json({ run, events, script, live: !TERMINAL_STATUSES.includes(run.status as never) && github?.status !== "completed" });
  } catch (e) {
    return errorResponse(e);
  }
}
