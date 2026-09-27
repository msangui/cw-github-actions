"use client";

/**
 * The producer agent chat. Keeps the full Anthropic message history (including tool_use /
 * tool_result blocks) client-side; the server streams SSE and returns the blocks to append.
 */
import type Anthropic from "@anthropic-ai/sdk";
import { useEffect, useRef, useState } from "react";
import { useConfig } from "./ConfigProvider";
import { DiffView } from "./DiffView";
import type { ConfigPatchProposal } from "@/lib/types";

type ChatItem = { kind: "user"; text: string } | { kind: "assistant"; text: string; streaming?: boolean } | { kind: "proposal"; proposal: ConfigPatchProposal; applied?: boolean } | { kind: "error"; text: string };

const SUGGESTIONS = ["Review the selected run's script. Where does the show drag, and which dial or band would fix it?", "Claire agrees too much. Propose a change.", "How can I cut cost per episode without losing quality?", "Explain what today's dials will produce in plain language."];

export function AgentDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const cfg = useConfig();
  const [history, setHistory] = useState<Anthropic.MessageParam[]>([]);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [items]);

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setInput("");
    setBusy(true);
    const nextHistory: Anthropic.MessageParam[] = [...history, { role: "user", content: text }];
    setHistory(nextHistory);
    setItems((it) => [...it, { kind: "user", text }, { kind: "assistant", text: "", streaming: true }]);
    try {
      const res = await fetch("/api/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: nextHistory, files: cfg.files, run_id: cfg.selectedRunId }),
      });
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        throw new Error(err.error ?? `HTTP ${res.status}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const turns: Anthropic.MessageParam[] = [];
      const handle = (evt: Record<string, unknown>) => {
        switch (evt.type) {
          case "text":
            setItems((it) => {
              const last = it[it.length - 1];
              if (last?.kind === "assistant" && last.streaming) return [...it.slice(0, -1), { ...last, text: last.text + (evt.text as string) }];
              return [...it, { kind: "assistant", text: evt.text as string, streaming: true }];
            });
            break;
          case "proposal":
            setItems((it) => {
              const last = it[it.length - 1];
              const base = last?.kind === "assistant" && last.streaming ? [...it.slice(0, -1), { ...last, streaming: false }] : it;
              return [...base, { kind: "proposal", proposal: evt.proposal as ConfigPatchProposal }, { kind: "assistant", text: "", streaming: true }];
            });
            break;
          case "turn":
            turns.push({ role: "assistant", content: evt.assistant as Anthropic.ContentBlockParam[] });
            if (evt.user) turns.push({ role: "user", content: evt.user as Anthropic.ContentBlockParam[] });
            break;
          case "error":
            setItems((it) => [...it, { kind: "error", text: evt.error as string }]);
            break;
        }
      };
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          for (const line of chunk.split("\n")) if (line.startsWith("data: ")) handle(JSON.parse(line.slice(6)));
        }
      }
      setHistory([...nextHistory, ...turns]);
    } catch (e) {
      setItems((it) => [...it, { kind: "error", text: (e as Error).message }]);
    } finally {
      setBusy(false);
      setItems((it) => it.map((x) => (x.kind === "assistant" ? { ...x, streaming: false } : x)).filter((x) => !(x.kind === "assistant" && !x.text.trim())));
    }
  }

  function apply(p: ConfigPatchProposal) {
    const ok = cfg.applyOps(p.file, p.ops);
    if (ok !== null) setItems((it) => it.map((x) => (x.kind === "proposal" && x.proposal.id === p.id ? { ...x, applied: true } : x)));
  }

  if (!open) return null;
  return (
    <aside className="fixed right-0 top-12 bottom-0 z-20 w-full max-w-xl border-l border-border bg-panel flex flex-col shadow-2xl">
      <div className="flex items-center justify-between px-4 h-11 border-b border-border">
        <div className="text-[13px]">
          <span className="font-semibold">✦ Producer agent</span>
          <span className="text-muted"> · {cfg.selectedRunId ? `run ${cfg.selectedRunId}` : "no run selected"} · sees pending config</span>
        </div>
        <div className="flex gap-2">
          <button
            className="btn btn-sm"
            onClick={() => {
              setHistory([]);
              setItems([]);
            }}
          >
            New chat
          </button>
          <button className="btn btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      <div ref={scroller} className="flex-1 overflow-y-auto p-4 space-y-3">
        {items.length === 0 && (
          <div className="space-y-2">
            <p className="text-muted text-[13px]">Ask about the selected run, the hosts, cost, or the prompt. Proposals show up as diff cards; Apply adds them to your pending edits, Commit writes them to git.</p>
            {SUGGESTIONS.map((s) => (
              <button key={s} className="btn btn-sm w-full text-left justify-start whitespace-normal" onClick={() => send(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        {items.map((it, i) => {
          if (it.kind === "user") return <div key={i} className="ml-8 rounded-lg bg-panel-2 border border-border p-3 text-[13px] whitespace-pre-wrap">{it.text}</div>;
          if (it.kind === "assistant") return it.text || it.streaming ? <div key={i} className="prose-chat text-[13px] whitespace-pre-wrap leading-relaxed">{it.text}{it.streaming && <span className="animate-pulse">▍</span>}</div> : null;
          if (it.kind === "error") return <div key={i} className="text-err text-[13px]">{it.text}</div>;
          return <ProposalCard key={i} proposal={it.proposal} applied={!!it.applied} onApply={() => apply(it.proposal)} />;
        })}
      </div>
      <form
        className="p-3 border-t border-border flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <textarea
          className="input"
          rows={2}
          placeholder={busy ? "Thinking…" : "Ask the producer… (Enter to send, Shift+Enter for newline)"}
          value={input}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(input);
            }
          }}
        />
        <button className="btn btn-primary" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </aside>
  );
}

export function ProposalCard({ proposal, applied, onApply }: { proposal: ConfigPatchProposal; applied: boolean; onApply: () => void }) {
  const [open, setOpen] = useState(true);
  return (
    <div className={`card p-3 ${proposal.error ? "border-err/40" : applied ? "border-ok/40" : "border-accent/40"}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-semibold text-[13px]">{proposal.title}</div>
          <div className="mono text-muted">{proposal.file}</div>
        </div>
        <div className="flex gap-1.5 shrink-0">
          <button className="btn btn-sm" onClick={() => setOpen((o) => !o)}>
            {open ? "Hide" : "Diff"}
          </button>
          {proposal.error ? <span className="badge badge-err">rejected</span> : applied ? <span className="badge badge-ok">applied to pending</span> : <button className="btn btn-sm btn-primary" onClick={onApply}>Apply</button>}
        </div>
      </div>
      {proposal.rationale && <p className="text-[13px] mt-2 leading-snug">{proposal.rationale}</p>}
      {proposal.error && <div className="text-err text-xs mt-1">{proposal.error}</div>}
      {open && proposal.diff && (
        <div className="mt-2">
          <DiffView diff={proposal.diff} maxLines={120} />
        </div>
      )}
    </div>
  );
}
