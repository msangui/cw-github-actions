import { NextResponse } from "next/server";
import { z } from "zod";
import { errorResponse, requireBearer } from "@/lib/auth";
import { getStore } from "@/lib/store";
import type { EventsPayload, RunEvent } from "@/lib/types";

export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 4 * 1024 * 1024; // a full script + a batch of logs is well under 1 MB

const Event = z
  .object({ seq: z.number().int().nonnegative(), ts: z.string(), type: z.string() })
  .catchall(z.unknown());
const Body = z.object({
  run: z.object({}).catchall(z.unknown()).optional(),
  events: z.array(Event).max(2000).default([]),
});

/**
 * POST /api/runs/{id}/events — the pipeline's live feed (pipeline/log.py PanelReporter).
 * Bearer-token protected (PANEL_TOKEN); Clerk is bypassed for this route in middleware.ts.
 * Idempotent enough: a retried batch re-appends the same seqs, the UI dedups on seq.
 */
export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }) {
  try {
    requireBearer(req);
    const { id } = await ctx.params;
    if (!/^[\w.-]{1,64}$/.test(id)) return NextResponse.json({ error: "bad run id" }, { status: 400 });
    const len = parseInt(req.headers.get("content-length") ?? "0", 10);
    if (len > MAX_BODY_BYTES) return NextResponse.json({ error: "payload too large" }, { status: 413 });
    const body = Body.parse(await req.json());
    const payload: EventsPayload = { run: body.run ? { ...(body.run as Record<string, unknown>), run_id: id } : undefined, events: body.events as RunEvent[] };
    const run = await getStore().appendEvents(id, payload);
    return NextResponse.json({ ok: true, received: body.events.length, event_count: run.event_count, status: run.status }, { status: 202 });
  } catch (e) {
    return errorResponse(e);
  }
}
