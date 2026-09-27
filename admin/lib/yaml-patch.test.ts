import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { applyPatch, parseYaml } from "./yaml-patch";

const ROOT = path.resolve(__dirname, "..", "..");

describe("applyPatch", () => {
  it("sets a nested value and keeps comments", () => {
    const src = "# top comment\nhosts:\n  FLINT:\n    dials:\n      humor: 70   # trailing\n";
    const out = applyPatch(src, [{ op: "set", path: ["hosts", "FLINT", "dials", "humor"], value: 15 }]);
    expect(out).toContain("# top comment");
    expect(out).toContain("humor: 15");
    expect(out).toContain("# trailing");
    expect(parseYaml<{ hosts: { FLINT: { dials: { humor: number } } } }>(out).hosts.FLINT.dials.humor).toBe(15);
  });

  it("keeps block literal style for multi-line prompts", () => {
    const src = readFileSync(path.join(ROOT, "config/agents/writer.yaml"), "utf8");
    const out = applyPatch(src, [{ op: "set", path: ["system_prompt"], value: "Line one\nLine two\n{{SHOW}}\nrules\n" }]);
    expect(out).toContain("system_prompt: |");
    expect(out).toContain("# {{SHOW}} is replaced at run time");
    expect(parseYaml<{ system_prompt: string }>(out).system_prompt).toBe("Line one\nLine two\n{{SHOW}}\nrules\n");
  });

  it("appends to and deletes from lists", () => {
    const src = readFileSync(path.join(ROOT, "config/sources.yaml"), "utf8");
    const before = parseYaml<{ main: unknown[] }>(src).main.length;
    let out = applyPatch(src, [{ op: "append", path: ["main"], value: { name: "X", url: "https://x/rss", tier: "2", active: true } }]);
    expect(parseYaml<{ main: unknown[] }>(out).main.length).toBe(before + 1);
    out = applyPatch(out, [{ op: "delete", path: ["main", before] }]);
    expect(parseYaml<{ main: unknown[] }>(out).main.length).toBe(before);
    expect(out).toContain("# Tier 0 — lab-direct, always included");
  });

  it("round-trips every repo config file unchanged in meaning", () => {
    for (const f of ["config/show.yaml", "config/podcast.yaml", "config/budget.yaml", "config/voices.yaml", "config/stitch.yaml", "config/curation.yaml", "config/sources.yaml"]) {
      const src = readFileSync(path.join(ROOT, f), "utf8");
      const out = applyPatch(src, []);
      expect(parseYaml(out)).toEqual(parseYaml(src));
    }
  });

  it("rejects impossible ops", () => {
    expect(() => applyPatch("a: 1\n", [{ op: "delete", path: ["b"] }])).toThrow();
    expect(() => applyPatch("a: 1\n", [{ op: "append", path: ["a"], value: 2 }])).toThrow();
  });
});
