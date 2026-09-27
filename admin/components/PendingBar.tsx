"use client";

import { useMemo, useState } from "react";
import { useConfig } from "./ConfigProvider";
import { DiffView } from "./DiffView";
import { unifiedDiff } from "@/lib/proposals";

export function PendingBar() {
  const cfg = useConfig();
  const [open, setOpen] = useState(false);
  if (!cfg.dirtyFiles.length) return null;
  return (
    <>
      <div className="fixed bottom-0 inset-x-0 z-30 border-t border-border bg-panel/95 backdrop-blur">
        <div className="flex items-center gap-3 px-4 h-12">
          <span className="badge badge-warn">uncommitted</span>
          <span className="text-[13px]">
            {cfg.dirtyFiles.length} file{cfg.dirtyFiles.length > 1 ? "s" : ""} changed: <span className="mono text-muted">{cfg.dirtyFiles.map((f) => f.replace("config/", "")).join(", ")}</span>
          </span>
          <div className="ml-auto flex gap-2">
            <button className="btn btn-sm" onClick={cfg.revertAll}>
              Discard all
            </button>
            <button className="btn btn-sm btn-primary" onClick={() => setOpen(true)}>
              Review & commit
            </button>
          </div>
        </div>
      </div>
      {open && <CommitModal onClose={() => setOpen(false)} />}
    </>
  );
}

function CommitModal({ onClose }: { onClose: () => void }) {
  const cfg = useConfig();
  const [message, setMessage] = useState(() => defaultMessage(cfg.dirtyFiles));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ sha: string; html_url: string } | null>(null);
  const diffs = useMemo(() => cfg.dirtyFiles.map((f) => ({ file: f, diff: unifiedDiff(f, cfg.base?.files[f] ?? "", cfg.pending[f] ?? "") })), [cfg.dirtyFiles, cfg.base, cfg.pending]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      setDone(await cfg.commit(message));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="card w-full max-w-4xl max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div>
            <h2 className="font-semibold">Commit to {cfg.base?.repo} · {cfg.base?.branch}</h2>
            <div className="text-muted text-xs">
              Parent {cfg.base?.base_sha.slice(0, 7)} · the next episode run picks these up automatically.
            </div>
          </div>
          <button className="btn btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="p-4 overflow-y-auto space-y-4 flex-1">
          {done ? (
            <div className="card p-4 border-ok/40">
              <div className="font-semibold text-ok">Committed {done.sha.slice(0, 7)}</div>
              <a className="text-accent-2 underline mono" href={done.html_url} target="_blank" rel="noreferrer">
                {done.html_url}
              </a>
            </div>
          ) : (
            <>
              <label className="block">
                <span className="text-[13px] font-medium">Commit message</span>
                <textarea className="input mono mt-1" rows={3} value={message} onChange={(e) => setMessage(e.target.value)} />
              </label>
              {diffs.map((d) => (
                <div key={d.file}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="mono font-semibold">{d.file}</span>
                    <button className="btn btn-sm btn-danger" onClick={() => cfg.revertFile(d.file)}>
                      Discard this file
                    </button>
                  </div>
                  <DiffView diff={d.diff} />
                </div>
              ))}
            </>
          )}
          {error && <div className="text-err text-[13px]">{error}</div>}
        </div>
        {!done && (
          <div className="p-4 border-t border-border flex justify-end gap-2">
            <button className="btn" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button className="btn btn-primary" onClick={submit} disabled={busy || message.trim().length < 3 || !cfg.dirtyFiles.length}>
              {busy ? "Committing…" : `Commit ${cfg.dirtyFiles.length} file${cfg.dirtyFiles.length > 1 ? "s" : ""}`}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function defaultMessage(files: string[]): string {
  const short = files.map((f) => f.replace(/^config\//, "").replace(/\.yaml$/, ""));
  return `Tune show config: ${short.join(", ")}`;
}
