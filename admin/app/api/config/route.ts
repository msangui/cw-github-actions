import { NextResponse } from "next/server";
import { errorResponse, requireUser } from "@/lib/auth";
import { CONFIG_FILES } from "@/lib/config-files";
import { readFiles, repoConfig } from "@/lib/github";
import type { ConfigSnapshot } from "@/lib/types";

export const dynamic = "force-dynamic";

/** GET /api/config → every editable YAML file at the branch head, plus the SHA to commit against. */
export async function GET() {
  try {
    await requireUser();
    const cfg = repoConfig();
    const { sha, files } = await readFiles(cfg, CONFIG_FILES);
    const snapshot: ConfigSnapshot = { base_sha: sha, branch: cfg.branch, repo: `${cfg.owner}/${cfg.repo}`, files };
    return NextResponse.json(snapshot);
  } catch (e) {
    return errorResponse(e);
  }
}
