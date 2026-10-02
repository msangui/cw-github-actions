"use client";

import { useMemo, useState } from "react";
import { useConfig } from "./ConfigProvider";
import { compileSystemPrompt, type ShowConfig } from "@/lib/compile-prompt";

/** Read-only compiled writer prompt; recomputes from pending edits on every render. */
export function PromptPreview({ agent = "writer" }: { agent?: "writer" | "aisle_writer" }) {
  const cfg = useConfig();
  const [which, setWhich] = useState<"writer" | "aisle_writer">(agent);
  const compiled = useMemo(() => {
    const show = cfg.parsed<ShowConfig>("config/show.yaml");
    const a = cfg.parsed<{ system_prompt?: string; model?: string; effort?: string }>(`config/agents/${which}.yaml`);
    if (!a) return { text: "(agent config not loaded)", model: "", effort: "" };
    return { text: compileSystemPrompt(a.system_prompt ?? "", show), model: a.model ?? "", effort: a.effort ?? "" };
  }, [cfg, which]);
  const words = compiled.text.split(/\s+/).filter(Boolean).length;
  return (
    <aside className="card p-3 sticky top-14 max-h-[calc(100vh-5rem)] flex flex-col">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div>
          <div className="font-semibold text-[13px]">Compiled system prompt</div>
          <div className="text-muted text-xs">
            {compiled.model} · effort {compiled.effort} · ~{words} words · read-only
          </div>
        </div>
        <select className="input !w-auto text-xs" value={which} onChange={(e) => setWhich(e.target.value as "writer" | "aisle_writer")}>
          <option value="writer">writer</option>
          <option value="aisle_writer">aisle_writer</option>
        </select>
      </div>
      <pre className="mono whitespace-pre-wrap leading-5 overflow-y-auto bg-panel-2 border border-border rounded-md p-2 flex-1">{compiled.text}</pre>
    </aside>
  );
}
