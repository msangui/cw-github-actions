"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useConfig } from "@/components/ConfigProvider";
import { fmtDuration, fmtTime, statusBadge } from "@/components/runs";
import { healthRowsFromEvents, SourceHealthTable } from "@/components/source-health";
import { formatLogLine } from "@/lib/agent-prompt";
import type { RunEvent, RunRecord } from "@/lib/types";

const STAGES = ["INGESTING", "CURATING", "WRITING", "EDITING", "GENERATING_AUDIO", "STITCHING", "PUBLISHING", "PUBLISHED"];

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const cfg = useConfig();
  const [run, setRun] = useState<RunRecord | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [script, setScript] = useState<string | null>(null);
  const [live, setLive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"logs" | "script" | "cost" | "sources">("logs");
  const [level, setLevel] = useState<"all" | "info" | "warning">("all");
  const [filter, setFilter] = useState("");
  const [follow, setFollow] = useState(true);
  const logBox = useRef<HTMLDivElement>(null);
  const lastSeq = useRef(0);
  const scriptStale = useRef(true);

  useEffect(() => {
    cfg.setSelectedRunId(id);
    return () => cfg.setSelectedRunId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const poll = useCallback(async () => {
    try {
      const wantScript = scriptStale.current ? "&script=1" : "";
      const res = await fetch(`/api/runs/${id}?since=${lastSeq.current}${wantScript}`, { cache: "no-store" });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error ?? `HTTP ${res.status}`);
      const data = (await res.json()) as { run: RunRecord; events: RunEvent[]; script?: string | null; live: boolean };
      setRun(data.run);
      setLive(data.live);
      if (data.events.length) {
        setEvents((prev) => {
          const seen = new Set(prev.map((e) => e.seq));
          const fresh = data.events.filter((e) => !seen.has(e.seq));
          if (fresh.some((e) => e.type === "script")) scriptStale.current = true;
          return fresh.length ? [...prev, ...fresh] : prev;
        });
        lastSeq.current = Math.max(lastSeq.current, ...data.events.map((e) => e.seq));
      }
      if (data.script !== undefined) {
        setScript(data.script);
        scriptStale.current = false;
      }
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    void poll();
  }, [poll]);
  useEffect(() => {
    if (!live) return;
    const t = setInterval(poll, 3000);
    return () => clearInterval(t);
  }, [live, poll]);

  const logs = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return events.filter((e) => e.type === "log").filter((e) => (level === "all" ? true : level === "warning" ? e.level === "warning" || e.level === "error" : e.level !== "debug")).filter((e) => (q ? formatLogLine(e).toLowerCase().includes(q) : true));
  }, [events, level, filter]);

  useEffect(() => {
    if (follow && tab === "logs") logBox.current?.scrollTo({ top: logBox.current.scrollHeight });
  }, [logs, follow, tab]);

  const reached = run?.status_history.map((s) => s.status) ?? [];
  const current = run?.status ?? "UNKNOWN";
  const llmCalls = events.filter((e) => e.type === "log" && e.event === "LLM call complete");
  const healthRows = useMemo(() => healthRowsFromEvents(events), [events]);
  const health = run?.source_health;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/runs" className="text-muted hover:text-text text-[13px]">
          ← Runs
        </Link>
        <h1 className="font-semibold text-lg">
          Run {run?.github?.run_number ? `#${run.github.run_number}` : id}
          {run?.episode_date && <span className="text-muted font-normal"> · episode {run.episode_date}</span>}
          {run?.dry_run && <span className="badge ml-2">dry run</span>}
        </h1>
        {run && statusBadge(current)}
        {live && <span className="badge badge-live animate-pulse">live</span>}
        <span className="text-muted text-[13px]">
          {fmtTime(run?.started_at)} · {fmtDuration(run?.started_at, run?.finished_at)}
          {run?.source === "github" && " · status from GitHub (no pipeline events yet)"}
        </span>
        <div className="ml-auto flex gap-2">
          {run?.run_url && (
            <a className="btn btn-sm" href={run.run_url} target="_blank" rel="noreferrer">
              GitHub Actions run ↗
            </a>
          )}
        </div>
      </div>
      {error && <div className="text-err text-[13px]">{error}</div>}

      <div className="card p-3 flex flex-wrap gap-1.5 items-center">
        {STAGES.map((s, i) => {
          const done = reached.includes(s) && s !== current;
          const active = s === current;
          return (
            <span key={s} className="flex items-center gap-1.5">
              <span className={`badge ${active ? "badge-live" : done ? "badge-ok" : ""}`}>{s.replace(/_/g, " ")}</span>
              {i < STAGES.length - 1 && <span className="text-muted">›</span>}
            </span>
          );
        })}
        {["SAFE_MODE", "FAILED", "DRY_RUN", "DRY_RUN_COMPLETE", "PAUSED", "AUDIO_READY", "CANCELLED"].includes(current) && <span className="ml-2">{statusBadge(current)}</span>}
        {run?.result?.error ? <span className="text-err text-xs ml-2 truncate max-w-xl">{String(run.result.error)}</span> : null}
      </div>

      <div className="flex items-center gap-2 border-b border-border">
        {(["logs", "script", "cost", "sources"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`px-3 py-2 text-[13px] border-b-2 -mb-px ${tab === t ? "border-accent font-semibold" : "border-transparent text-muted"}`}>
            {t === "logs"
              ? `Logs (${logs.length})`
              : t === "script"
                ? `Script${run?.word_count ? ` · ${run.word_count} words` : ""}`
                : t === "cost"
                  ? `Cost${run?.cost ? ` · $${run.cost.total_usd.toFixed(2)}` : ""}`
                  : `Sources${health ? ` · ${health.ok}/${health.total} ok` : ""}`}
            {t === "sources" && health && health.unhealthy.some((r) => r.tier === "0") && <span className="badge badge-err ml-1.5">tier 0</span>}
          </button>
        ))}
        {tab === "logs" && (
          <div className="ml-auto flex items-center gap-2 py-1">
            <select className="input !w-auto text-xs" value={level} onChange={(e) => setLevel(e.target.value as typeof level)}>
              <option value="all">all levels</option>
              <option value="info">info+</option>
              <option value="warning">warnings & errors</option>
            </select>
            <input className="input !w-56 text-xs" placeholder="filter (stage, text…)" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <label className="text-xs text-muted flex items-center gap-1">
              <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow
            </label>
          </div>
        )}
      </div>

      {tab === "logs" && (
        <div ref={logBox} className="card mono p-3 h-[60vh] overflow-auto whitespace-pre leading-5">
          {logs.length === 0 && <div className="text-muted">{live ? "Waiting for the pipeline to report… (the job may still be installing dependencies)" : "No log events were reported for this run. Check the GitHub Actions log."}</div>}
          {logs.map((e) => (
            <div key={e.seq} className={`log-${e.level ?? "info"}`}>
              {formatLogLine(e)}
            </div>
          ))}
        </div>
      )}
      {tab === "script" && (
        <div className="card p-4 h-[60vh] overflow-auto">
          {run?.title && <h2 className="font-semibold mb-2">{run.title}</h2>}
          {script ? (
            <div className="space-y-1.5 text-[13px] leading-relaxed max-w-4xl">
              {script.split("\n").map((ln, i) => {
                const m = /^(CLAIRE|FLINT):\s*(.*)$/.exec(ln);
                if (!m) return ln.trim() ? <div key={i} className="mono text-accent-2">{ln}</div> : null;
                return (
                  <div key={i} className="grid grid-cols-[4.5rem_1fr] gap-2">
                    <span className={`mono font-semibold ${m[1] === "CLAIRE" ? "text-accent-2" : "text-accent"}`}>{m[1]}</span>
                    <span>{m[2]}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-muted">No script reported yet{live ? " — it arrives after the editor approves." : "."}</div>
          )}
        </div>
      )}
      {tab === "sources" && (
        <div className="card p-4 h-[60vh] overflow-auto">
          {healthRows ? (
            <SourceHealthTable rows={healthRows} windowHours={health?.window_hours} />
          ) : health ? (
            <SourceHealthTable rows={health.unhealthy} windowHours={health.window_hours} />
          ) : (
            <div className="text-muted">
              {live ? "Feed health arrives right after ingest." : "This run did not report feed health — ingest resumed from a checkpoint, or the run predates the report."}
              {" "}
              <Link href="/sources" className="hover:underline">
                Open Sources
              </Link>{" "}
              to edit feeds.
            </div>
          )}
        </div>
      )}
      {tab === "cost" && (
        <div className="card p-4 grid gap-4 md:grid-cols-2">
          {run?.cost ? (
            <>
              <div>
                <h3 className="font-semibold mb-2">Totals</h3>
                <table className="text-[13px]">
                  <tbody>
                    {[
                      ["LLM", run.cost.llm_usd],
                      ["TTS", run.cost.tts_usd],
                      ["Serper", run.cost.serper_usd],
                      ["Total", run.cost.total_usd],
                    ].map(([k, v]) => (
                      <tr key={String(k)}>
                        <td className="pr-6 py-0.5 text-muted">{k}</td>
                        <td className="mono text-right">${Number(v).toFixed(4)}</td>
                      </tr>
                    ))}
                    {run.cost.tts_chars ? (
                      <tr>
                        <td className="pr-6 py-0.5 text-muted">TTS characters</td>
                        <td className="mono text-right">{run.cost.tts_chars.toLocaleString()}</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
              <div>
                <h3 className="font-semibold mb-2">LLM by agent</h3>
                <table className="text-[13px]">
                  <tbody>
                    {Object.entries(run.cost.llm_by_agent ?? {}).map(([a, c]) => (
                      <tr key={a}>
                        <td className="pr-6 py-0.5 text-muted">{a}</td>
                        <td className="mono text-right">${c.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="text-muted">Cost is reported at the end of the text stages.</div>
          )}
          {llmCalls.length > 0 && (
            <div className="md:col-span-2">
              <h3 className="font-semibold mb-2">LLM calls</h3>
              <table className="text-[13px] w-full">
                <thead className="text-muted text-xs">
                  <tr>
                    <th className="text-left">agent</th>
                    <th className="text-left">model</th>
                    <th className="text-right">in</th>
                    <th className="text-right">out</th>
                    <th className="text-right">s</th>
                    <th className="text-left pl-3">stop</th>
                  </tr>
                </thead>
                <tbody className="mono">
                  {llmCalls.map((e) => (
                    <tr key={e.seq}>
                      <td>{String(e.agent ?? "")}</td>
                      <td>{String(e.model ?? "")}</td>
                      <td className="text-right">{String(e.input_tokens ?? "")}</td>
                      <td className="text-right">{String(e.output_tokens ?? "")}</td>
                      <td className="text-right">{String(e.seconds ?? "")}</td>
                      <td className="pl-3">{String(e.stop_reason ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
