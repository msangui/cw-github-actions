import { NextResponse } from "next/server";
import { z } from "zod";
import { errorResponse, requireUser } from "@/lib/auth";
import { actionsUrl, dispatchWorkflow, findDispatchedRun, listWorkflowRuns, repoConfig } from "@/lib/github";
import { getStore } from "@/lib/store";
import { mergeRuns } from "@/lib/merge-runs";
import type { GitHubRunSummary } from "@/lib/types";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

/** GET /api/runs → recent runs (panel records ∪ GitHub Actions runs). */
export async function GET() {
  try {
    await requireUser();
    const store = getStore();
    const stored = await store.listRuns(40);
    let github: GitHubRunSummary[] = [];
    let githubError: string | undefined;
    try {
      github = await listWorkflowRuns(repoConfig(), 25);
    } catch (e) {
      githubError = (e as Error).message;
    }
    return NextResponse.json({ runs: mergeRuns(stored, github), actions_url: safeActionsUrl(), github_error: githubError });
  } catch (e) {
    return errorResponse(e);
  }
}

function safeActionsUrl(): string | null {
  try {
    return actionsUrl(repoConfig());
  } catch {
    return null;
  }
}

const TriggerBody = z.object({
  episode_date: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .optional()
    .or(z.literal("")),
  dry_run: z.boolean().default(false),
  fresh: z.boolean().default(false),
  skip_aisle: z.boolean().default(false),
});

/** POST /api/runs → workflow_dispatch on daily-episode.yml; returns the run once GitHub lists it. */
export async function POST(req: Request) {
  try {
    const user = await requireUser();
    const body = TriggerBody.parse(await req.json());
    const cfg = repoConfig();
    const before = await listWorkflowRuns(cfg, 10);
    const since = new Date();
    await dispatchWorkflow(cfg, { episode_date: body.episode_date || undefined, dry_run: body.dry_run, fresh: body.fresh, skip_aisle: body.skip_aisle });
    const run = await findDispatchedRun(cfg, since, new Set(before.map((r) => r.id)));
    const inputs = { episode_date: body.episode_date || "", dry_run: body.dry_run, fresh: body.fresh, skip_aisle: body.skip_aisle };
    if (run) {
      const record = await getStore().upsertRun({
        run_id: String(run.id),
        run_url: run.html_url,
        started_at: run.created_at,
        status: "QUEUED",
        dry_run: body.dry_run,
        skip_aisle: body.skip_aisle,
        fresh: body.fresh,
        episode_date: body.episode_date || undefined,
        triggered_by: user.email || user.name,
        triggered_inputs: inputs,
        source: "panel",
      });
      return NextResponse.json({ ok: true, run: { ...record, github: run }, actions_url: actionsUrl(cfg) });
    }
    return NextResponse.json({ ok: true, run: null, actions_url: actionsUrl(cfg), note: "Dispatched, but GitHub has not listed the run yet. It will appear in the list shortly." });
  } catch (e) {
    return errorResponse(e);
  }
}
