/**
 * Two callers, two checks:
 *   - humans: Clerk session (Google Workspace SSO configured in the Clerk dashboard), plus an
 *     optional ALLOWED_EMAIL_DOMAIN guard so a stray personal Google account is refused even if
 *     the Clerk instance is misconfigured;
 *   - the pipeline: a bearer token (PANEL_TOKEN) on POST /api/runs/{id}/events only.
 */
import { auth, currentUser } from "@clerk/nextjs/server";
import { timingSafeEqual } from "node:crypto";
import { NextResponse } from "next/server";

export class AuthError extends Error {
  constructor(
    message: string,
    public status: 401 | 403,
  ) {
    super(message);
  }
}

export interface PanelUser {
  id: string;
  email: string;
  name: string;
}

export async function requireUser(): Promise<PanelUser> {
  const { userId } = await auth();
  if (!userId) throw new AuthError("Sign in required", 401);
  const user = await currentUser();
  const email = user?.primaryEmailAddress?.emailAddress ?? user?.emailAddresses?.[0]?.emailAddress ?? "";
  const domain = (process.env.ALLOWED_EMAIL_DOMAIN ?? "").trim().toLowerCase();
  if (domain && !email.toLowerCase().endsWith(`@${domain}`)) throw new AuthError(`Only ${domain} accounts may use this panel`, 403);
  const name = [user?.firstName, user?.lastName].filter(Boolean).join(" ") || email || userId;
  return { id: userId, email, name };
}

export function requireBearer(req: Request): void {
  const expected = process.env.PANEL_TOKEN ?? "";
  if (!expected) throw new AuthError("PANEL_TOKEN is not configured on the panel", 401);
  const header = req.headers.get("authorization") ?? "";
  const m = /^Bearer\s+(.+)$/i.exec(header);
  if (!m) throw new AuthError("Missing bearer token", 401);
  const a = Buffer.from(m[1]);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !timingSafeEqual(a, b)) throw new AuthError("Invalid token", 401);
}

/** Uniform JSON error responses for API routes. */
export function errorResponse(e: unknown): NextResponse {
  if (e instanceof AuthError) return NextResponse.json({ error: e.message }, { status: e.status });
  const status = typeof (e as { status?: number })?.status === "number" ? (e as { status: number }).status : 500;
  const message = e instanceof Error ? e.message : String(e);
  if (status >= 500) console.error(e);
  return NextResponse.json({ error: message }, { status: status >= 400 && status < 600 ? status : 500 });
}
