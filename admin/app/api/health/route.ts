import { NextResponse } from "next/server";
import { storeKind } from "@/lib/store";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({
    ok: true,
    store: storeKind(),
    github: Boolean(process.env.GITHUB_TOKEN && process.env.GITHUB_REPO),
    panel_token: Boolean(process.env.PANEL_TOKEN),
    agent: Boolean(process.env.ANTHROPIC_API_KEY),
  });
}
