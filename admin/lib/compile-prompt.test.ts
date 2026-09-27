import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { parse } from "yaml";
import { bandIndex, compileSystemPrompt, renderShowBlock, type ShowConfig } from "./compile-prompt";

const ROOT = path.resolve(__dirname, "..", "..");
const read = (p: string) => readFileSync(path.join(ROOT, p), "utf8");

describe("compile-prompt (parity with pipeline/show.py)", () => {
  const show = parse(read("config/show.yaml")) as ShowConfig;

  it("matches the Python golden output for the writer", () => {
    const writer = parse(read("config/agents/writer.yaml")) as { system_prompt: string };
    expect(compileSystemPrompt(writer.system_prompt, show)).toBe(read("tests/fixtures/compiled_writer_prompt.txt"));
  });

  it("matches the Python golden output for the aisle writer", () => {
    const aisle = parse(read("config/agents/aisle_writer.yaml")) as { system_prompt: string };
    expect(compileSystemPrompt(aisle.system_prompt, show)).toBe(read("tests/fixtures/compiled_aisle_writer_prompt.txt"));
  });

  it("maps dials to five bands", () => {
    expect([0, 19, 20, 39, 40, 59, 60, 79, 80, 100].map((v) => bandIndex(v))).toEqual([0, 0, 1, 1, 2, 2, 3, 3, 4, 4]);
    expect(bandIndex("junk")).toBe(2);
  });

  it("moving a dial changes the rendered text", () => {
    const a = renderShowBlock(show);
    const b = renderShowBlock({ ...show, dynamics: { ...show.dynamics, joke_density: 0 } });
    expect(a).not.toBe(b);
    expect(b).toContain("Joke density: No jokes anywhere in the script.");
  });

  it("leaves prompts without a placeholder alone", () => {
    expect(compileSystemPrompt("plain", show)).toBe("plain");
  });
});
