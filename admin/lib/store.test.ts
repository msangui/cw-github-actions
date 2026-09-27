import { describe, expect, it } from "vitest";
import { emptyRun, foldEvents } from "./store";

describe("foldEvents", () => {
  it("tracks status, script, cost and result", () => {
    const { run, script } = foldEvents(emptyRun("1"), [
      { seq: 1, ts: "t1", type: "log", level: "info", event: "Budget check passed" },
      { seq: 2, ts: "t2", type: "status", status: "WRITING" },
      { seq: 3, ts: "t3", type: "script", script: "FLINT: hi", title: "Ep", word_count: 2 },
      { seq: 4, ts: "t4", type: "cost", cost: { llm_usd: 1, tts_usd: 0, serper_usd: 0, total_usd: 1 } },
      { seq: 5, ts: "t5", type: "result", status: "DRY_RUN", cost: { llm_usd: 1.5, tts_usd: 0, serper_usd: 0, total_usd: 1.5 } },
    ]);
    expect(run.status).toBe("DRY_RUN");
    expect(run.status_history).toEqual([{ status: "WRITING", at: "t2" }]);
    expect(script).toBe("FLINT: hi");
    expect(run.title).toBe("Ep");
    expect(run.cost?.total_usd).toBe(1.5);
    expect(run.finished_at).toBe("t5");
    expect(run.source).toBe("events");
  });

  it("marks a run in progress on the first log line", () => {
    const { run } = foldEvents(emptyRun("2"), [{ seq: 1, ts: "t", type: "log", event: "x" }]);
    expect(run.status).toBe("IN_PROGRESS");
    expect(run.finished_at).toBeUndefined();
  });
});
