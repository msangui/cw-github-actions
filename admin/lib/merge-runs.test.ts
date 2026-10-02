import { describe, expect, it } from "vitest";
import { mergeRuns } from "./merge-runs";
import type { GitHubRunSummary, RunRecord } from "./types";

const gh = (id: number, status: string, conclusion: string | null): GitHubRunSummary => ({ id, status, conclusion, html_url: `https://gh/${id}`, created_at: `2026-09-2${id}T00:00:00Z`, updated_at: `2026-09-2${id}T01:00:00Z`, run_number: id, event: "workflow_dispatch" });
const rec = (id: string, status: string): RunRecord => ({ run_id: id, status, status_history: [], event_count: 5, source: "events", started_at: `2026-09-2${id}T00:00:00Z` });

describe("mergeRuns", () => {
  it("adds GitHub-only runs with a derived status", () => {
    const out = mergeRuns([], [gh(1, "completed", "success"), gh(2, "in_progress", null), gh(3, "completed", "failure")]);
    expect(out.map((r) => [r.run_id, r.status, r.source])).toEqual([
      ["3", "FAILED", "github"],
      ["2", "IN_PROGRESS", "github"],
      ["1", "COMPLETED", "github"],
    ]);
  });
  it("keeps pipeline status but trusts GitHub when the job died mid-run", () => {
    const out = mergeRuns([rec("1", "PUBLISHED"), rec("2", "WRITING")], [gh(1, "completed", "success"), gh(2, "completed", "failure")]);
    expect(out.find((r) => r.run_id === "1")?.status).toBe("PUBLISHED");
    const two = out.find((r) => r.run_id === "2")!;
    expect(two.status).toBe("FAILED");
    expect(two.source).toBe("github");
    expect(two.github?.id).toBe(2);
  });
});
