/** Turn a propose_config_patch tool call into a validated, diffable proposal. Shared by server and client. */
import { createTwoFilesPatch } from "diff";
import { z } from "zod";
import { isConfigPath } from "./config-files";
import type { ConfigPatchProposal } from "./types";
import { applyPatch, PatchOp } from "./yaml-patch";

export const ProposalInput = z.object({
  title: z.string().min(3).max(120),
  rationale: z.string().min(3).max(4000),
  file: z.string(),
  ops: z.array(PatchOp).min(1).max(50),
});
export type ProposalInput = z.infer<typeof ProposalInput>;

let counter = 0;
export function newProposalId(): string {
  counter += 1;
  return `p_${Date.now().toString(36)}_${counter}`;
}

export function unifiedDiff(file: string, before: string, after: string): string {
  return createTwoFilesPatch(`a/${file}`, `b/${file}`, before, after, undefined, undefined, { context: 3 });
}

/** Validate and dry-run a proposal against the current file texts. Never throws. */
export function buildProposal(input: unknown, files: Record<string, string>, id = newProposalId()): ConfigPatchProposal {
  const parsed = ProposalInput.safeParse(input);
  if (!parsed.success) {
    return { id, title: "Invalid proposal", rationale: "", file: "", ops: [], before: "", after: "", diff: "", error: parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ") };
  }
  const { title, rationale, file, ops } = parsed.data;
  const base = { id, title, rationale, file, ops, before: files[file] ?? "", after: "", diff: "" };
  if (!isConfigPath(file)) return { ...base, error: `Not an editable config file: ${file}` };
  if (files[file] === undefined) return { ...base, error: `File not loaded in the panel: ${file}` };
  try {
    const after = applyPatch(files[file], ops);
    if (after === files[file]) return { ...base, after, error: "The patch does not change the file" };
    return { ...base, after, diff: unifiedDiff(file, files[file], after) };
  } catch (e) {
    return { ...base, error: (e as Error).message };
  }
}
