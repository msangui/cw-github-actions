import { NextResponse } from "next/server";
import { z } from "zod";
import { errorResponse, requireUser } from "@/lib/auth";
import { isConfigPath } from "@/lib/config-files";
import { commitFiles, repoConfig } from "@/lib/github";
import { parseYaml } from "@/lib/yaml-patch";

export const dynamic = "force-dynamic";

const Body = z.object({
  base_sha: z.string().min(7),
  message: z.string().min(3).max(2000),
  files: z.record(z.string(), z.string()).refine((f) => Object.keys(f).length > 0, "no files"),
});

/** POST /api/config/commit → one git commit with the given files on top of base_sha. */
export async function POST(req: Request) {
  try {
    const user = await requireUser();
    const body = Body.parse(await req.json());
    for (const [path, text] of Object.entries(body.files)) {
      if (!isConfigPath(path)) return NextResponse.json({ error: `Not an editable config file: ${path}` }, { status: 400 });
      try {
        parseYaml(text);
      } catch (e) {
        return NextResponse.json({ error: `${path} is not valid YAML: ${(e as Error).message}` }, { status: 400 });
      }
    }
    const cfg = repoConfig();
    const trailer = `\n\nCommitted from the admin panel by ${user.name}${user.email ? ` <${user.email}>` : ""}.`;
    const result = await commitFiles(cfg, body.base_sha, body.files, body.message.trimEnd() + trailer);
    return NextResponse.json({ ok: true, ...result, files: Object.keys(body.files) });
  } catch (e) {
    return errorResponse(e);
  }
}
