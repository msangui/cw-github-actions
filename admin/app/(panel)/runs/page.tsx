"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useConfig } from "@/components/ConfigProvider";
import { Section, Toggle } from "@/components/fields";
import { RunRow } from "@/components/runs";
import type { RunRecord } from "@/lib/types";

export default function RunsPage() {
  const cfg = useConfig();
  const router = useRouter();
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [actionsUrl, setActionsUrl] = useState<string | null>(null);
  const [ghError, setGhError] = useState<string | undefined>();
  const [form, setForm] = useState({ episode_date: "", dry_run: true, fresh: false, skip_aisle: false });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/api/runs", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    setRuns(data.runs);
    setActionsUrl(data.actions_url);
    setGhError(data.github_error);
  }, []);

  useEffect(() => {
    cfg.setSelectedRunId(null);
    void load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load, cfg]);

  async function trigger() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
      if (data.run) router.push(`/runs/${data.run.run_id}`);
      else {
        setMsg(data.note ?? "Dispatched.");
        void load();
      }
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const live = runs.filter((r) => !r.finished_at && !["FAILED", "PUBLISHED", "SAFE_MODE", "DRY_RUN", "CANCELLED", "PAUSED", "AUDIO_READY", "ALREADY_PUBLISHED"].includes(r.status));

  return (
    <div className="space-y-4">
      <Section
        title="Trigger an episode"
        description={`Dispatches daily-episode.yml on ${cfg.base?.branch ?? "main"} with the config committed there${cfg.dirtyFiles.length ? ` — you have ${cfg.dirtyFiles.length} uncommitted file(s); commit first if the run should use them` : ""}.`}
        right={
          actionsUrl && (
            <a className="btn btn-sm" href={actionsUrl} target="_blank" rel="noreferrer">
              GitHub Actions ↗
            </a>
          )
        }
      >
        <div className="flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="text-[13px] font-medium">Episode date</span>
            <input type="date" className="input mt-1 !w-44" value={form.episode_date} onChange={(e) => setForm({ ...form, episode_date: e.target.value })} />
            <span className="block text-muted text-xs">empty = today (UTC)</span>
          </label>
          <Toggle checked={form.dry_run} onChange={(v) => setForm({ ...form, dry_run: v })} label="Dry run (text only, ~$1–2, no audio)" />
          <Toggle checked={form.fresh} onChange={(v) => setForm({ ...form, fresh: v })} label="Fresh (ignore checkpoints, re-pays TTS)" />
          <Toggle checked={form.skip_aisle} onChange={(v) => setForm({ ...form, skip_aisle: v })} label="Skip The Aisle" />
          <button className={`btn ${form.dry_run ? "" : "btn-primary"}`} disabled={busy || live.length > 0} onClick={trigger} title={live.length ? "A run is already in progress (the workflow runs one episode at a time)" : ""}>
            {busy ? "Dispatching…" : form.dry_run ? "Run dry run" : "Produce & publish episode"}
          </button>
        </div>
        {msg && <div className="text-[13px] mt-2 text-warn">{msg}</div>}
        {!form.dry_run && <div className="text-xs text-muted mt-2">A full run spends ~$8–10 (ElevenLabs dominates) and publishes to the live feed if the editor approves.</div>}
      </Section>
      <Section title="Recent runs" description="Panel records merged with GitHub Actions. Runs that never reported events show GitHub's status." right={ghError && <span className="text-err text-xs">GitHub: {ghError}</span>}>
        <div className="grid grid-cols-[7rem_10rem_1fr_7rem_6rem_5rem] gap-3 px-3 text-muted text-xs">
          <span>Run</span>
          <span>Started</span>
          <span>Episode</span>
          <span>Status</span>
          <span>Cost</span>
          <span className="text-right">Duration</span>
        </div>
        {runs.length === 0 && <div className="text-muted p-3">No runs yet.</div>}
        {runs.map((r) => (
          <RunRow key={r.run_id} run={r} />
        ))}
      </Section>
    </div>
  );
}
