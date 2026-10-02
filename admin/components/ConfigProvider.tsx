"use client";

/**
 * Client-side source of truth for the editing session:
 *   base     — the files as they are at the branch head (from GET /api/config)
 *   pending  — files the operator changed, as full YAML text (comment-preserving patches)
 * Every edit goes through applyOps(file, ops); "Commit" ships `pending` with base_sha.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ConfigSnapshot } from "@/lib/types";
import { applyPatch, parseYaml, type PatchOp } from "@/lib/yaml-patch";

const STORAGE_KEY = "cw-admin-pending-v1";

interface ConfigState {
  base: ConfigSnapshot | null;
  pending: Record<string, string>;
  files: Record<string, string>;
  loading: boolean;
  error: string | null;
  notice: string | null;
  dirtyFiles: string[];
  reload: () => Promise<void>;
  applyOps: (file: string, ops: PatchOp[]) => string | null;
  setValue: (file: string, path: (string | number)[], value: unknown) => void;
  setText: (file: string, text: string) => void;
  revertFile: (file: string) => void;
  revertAll: () => void;
  parsed: <T = unknown>(file: string) => T | null;
  commit: (message: string) => Promise<{ sha: string; html_url: string }>;
  selectedRunId: string | null;
  setSelectedRunId: (id: string | null) => void;
  dismissNotice: () => void;
}

const Ctx = createContext<ConfigState | null>(null);

export function ConfigProvider({ children }: { children: React.ReactNode }) {
  const [base, setBase] = useState<ConfigSnapshot | null>(null);
  const [pending, setPending] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const parsedCache = useRef(new Map<string, { text: string; value: unknown }>());

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/config", { cache: "no-store" });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error ?? `HTTP ${res.status}`);
      const snap = (await res.json()) as ConfigSnapshot;
      setBase(snap);
      try {
        const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as { base_sha: string; pending: Record<string, string> } | null;
        if (stored && Object.keys(stored.pending).length) {
          if (stored.base_sha === snap.base_sha) {
            setPending(stored.pending);
          } else {
            setPending({});
            localStorage.removeItem(STORAGE_KEY);
            setNotice(`The repo moved on since your last session (${stored.base_sha.slice(0, 7)} → ${snap.base_sha.slice(0, 7)}); uncommitted edits to ${Object.keys(stored.pending).length} file(s) were discarded.`);
          }
        }
      } catch {
        /* ignore storage errors */
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (!base) return;
    try {
      if (Object.keys(pending).length) localStorage.setItem(STORAGE_KEY, JSON.stringify({ base_sha: base.base_sha, pending }));
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }, [pending, base]);

  const files = useMemo(() => ({ ...(base?.files ?? {}), ...pending }), [base, pending]);
  const dirtyFiles = useMemo(() => Object.keys(pending).filter((f) => base && pending[f] !== base.files[f]), [pending, base]);

  const applyOps = useCallback(
    (file: string, ops: PatchOp[]): string | null => {
      const current = files[file];
      if (current === undefined) {
        setError(`Unknown file ${file}`);
        return null;
      }
      try {
        const next = applyPatch(current, ops);
        setPending((p) => (base && next === base.files[file] ? Object.fromEntries(Object.entries(p).filter(([k]) => k !== file)) : { ...p, [file]: next }));
        return next;
      } catch (e) {
        setError(`Could not apply change to ${file}: ${(e as Error).message}`);
        return null;
      }
    },
    [files, base],
  );

  const setValue = useCallback((file: string, path: (string | number)[], value: unknown) => void applyOps(file, [{ op: "set", path, value }]), [applyOps]);

  const setText = useCallback(
    (file: string, text: string) => {
      setPending((p) => (base && text === base.files[file] ? Object.fromEntries(Object.entries(p).filter(([k]) => k !== file)) : { ...p, [file]: text }));
    },
    [base],
  );

  const revertFile = useCallback((file: string) => setPending((p) => Object.fromEntries(Object.entries(p).filter(([k]) => k !== file))), []);
  const revertAll = useCallback(() => setPending({}), []);

  const parsed = useCallback(
    <T,>(file: string): T | null => {
      const text = files[file];
      if (text === undefined) return null;
      const hit = parsedCache.current.get(file);
      if (hit && hit.text === text) return hit.value as T;
      try {
        const value = parseYaml<T>(text);
        parsedCache.current.set(file, { text, value });
        return value;
      } catch {
        return null;
      }
    },
    [files],
  );

  const commit = useCallback(
    async (message: string) => {
      if (!base) throw new Error("Config not loaded");
      const changed = Object.fromEntries(dirtyFiles.map((f) => [f, pending[f]]));
      const res = await fetch("/api/config/commit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ base_sha: base.base_sha, message, files: changed }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
      setPending({});
      await reload();
      return data as { sha: string; html_url: string };
    },
    [base, dirtyFiles, pending, reload],
  );

  const value: ConfigState = {
    base,
    pending,
    files,
    loading,
    error,
    notice,
    dirtyFiles,
    reload,
    applyOps,
    setValue,
    setText,
    revertFile,
    revertAll,
    parsed,
    commit,
    selectedRunId,
    setSelectedRunId,
    dismissNotice: () => {
      setNotice(null);
      setError(null);
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useConfig(): ConfigState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useConfig outside ConfigProvider");
  return v;
}
