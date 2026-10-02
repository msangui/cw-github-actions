/**
 * Run store: Upstash Redis in production, a JSON-file directory in local dev.
 *
 * Keys (Redis):
 *   runs                 zset  score = started_at epoch ms, member = run_id
 *   run:{id}             string JSON RunRecord (no events)
 *   run:{id}:events      list   JSON RunEvent, seq == index + 1
 *   run:{id}:script      string latest script text (kept out of the record to keep lists cheap)
 */
import { Redis } from "@upstash/redis";
import { promises as fs } from "node:fs";
import path from "node:path";
import { TERMINAL_STATUSES, type EventsPayload, type RunEvent, type RunRecord } from "./types";

const MAX_EVENTS_PER_RUN = 8000;
const MAX_RUNS_LISTED = 50;

export interface RunStore {
  upsertRun(run: Partial<RunRecord> & { run_id: string }): Promise<RunRecord>;
  appendEvents(runId: string, payload: EventsPayload): Promise<RunRecord>;
  getRun(runId: string): Promise<RunRecord | null>;
  getEvents(runId: string, sinceSeq?: number, limit?: number): Promise<RunEvent[]>;
  getScript(runId: string): Promise<string | null>;
  listRuns(limit?: number): Promise<RunRecord[]>;
}

function nowIso() {
  return new Date().toISOString();
}

export function emptyRun(runId: string): RunRecord {
  return { run_id: runId, status: "UNKNOWN", status_history: [], event_count: 0, source: "panel", started_at: nowIso(), updated_at: nowIso() };
}

/** Fold a batch of events into the run summary. Pure, shared by both backends. */
export function foldEvents(run: RunRecord, events: RunEvent[]): { run: RunRecord; script: string | null } {
  let script: string | null = null;
  const r: RunRecord = { ...run, status_history: [...run.status_history] };
  for (const ev of events) {
    r.updated_at = ev.ts ?? nowIso();
    switch (ev.type) {
      case "status":
        if (typeof ev.status === "string") {
          r.status = ev.status;
          r.source = "events";
          r.status_history.push({ status: ev.status, at: ev.ts });
        }
        break;
      case "script":
        if (typeof ev.script === "string") script = ev.script;
        if (typeof ev.title === "string" && ev.title) r.title = ev.title;
        if (typeof ev.word_count === "number") r.word_count = ev.word_count;
        break;
      case "cost":
        if (ev.cost) r.cost = ev.cost;
        break;
      case "result": {
        const { seq: _s, ts: _t, type: _ty, ...rest } = ev;
        r.result = rest as Record<string, unknown>;
        if (typeof rest.status === "string") {
          r.status = rest.status;
          r.source = "events";
        }
        if (rest.cost && typeof rest.cost === "object") r.cost = rest.cost as RunRecord["cost"];
        if (typeof rest.title === "string" && rest.title) r.title = rest.title;
        r.finished_at = ev.ts;
        break;
      }
      case "log":
        if (r.source === "panel") r.source = "events";
        if (r.status === "UNKNOWN" || r.status === "QUEUED") r.status = "IN_PROGRESS";
        break;
    }
  }
  if (TERMINAL_STATUSES.includes(r.status as never) && !r.finished_at) r.finished_at = r.updated_at;
  return { run: r, script };
}

function mergeMeta(run: RunRecord, meta: Partial<RunRecord> | undefined): RunRecord {
  if (!meta) return run;
  const { status_history: _h, event_count: _c, ...rest } = meta;
  const out = { ...run };
  for (const [k, v] of Object.entries(rest)) if (v !== undefined && v !== null) (out as Record<string, unknown>)[k] = v;
  return out;
}

// ── Redis ─────────────────────────────────────────────────────────────────────
class RedisStore implements RunStore {
  constructor(private redis: Redis) {}

  private k(id: string) {
    return { run: `run:${id}`, events: `run:${id}:events`, script: `run:${id}:script` };
  }

  async upsertRun(meta: Partial<RunRecord> & { run_id: string }): Promise<RunRecord> {
    const k = this.k(meta.run_id);
    const existing = (await this.redis.get<RunRecord>(k.run)) ?? emptyRun(meta.run_id);
    const run = mergeMeta(existing, meta);
    run.updated_at = nowIso();
    await this.redis.set(k.run, run);
    await this.redis.zadd("runs", { score: new Date(run.started_at ?? nowIso()).getTime(), member: run.run_id });
    return run;
  }

  async appendEvents(runId: string, payload: EventsPayload): Promise<RunRecord> {
    const k = this.k(runId);
    const existing = (await this.redis.get<RunRecord>(k.run)) ?? emptyRun(runId);
    const merged = mergeMeta(existing, payload.run);
    const events = payload.events ?? [];
    let stored = 0;
    if (events.length) {
      const room = Math.max(0, MAX_EVENTS_PER_RUN - merged.event_count);
      const slice = events.slice(0, room);
      if (slice.length) {
        await this.redis.rpush(k.events, ...slice.map((e) => JSON.stringify(e)));
        stored = slice.length;
      }
    }
    const { run, script } = foldEvents(merged, events);
    run.event_count = merged.event_count + stored;
    if (script !== null) await this.redis.set(k.script, script);
    await this.redis.set(k.run, run);
    await this.redis.zadd("runs", { score: new Date(run.started_at ?? nowIso()).getTime(), member: run.run_id });
    return run;
  }

  async getRun(runId: string) {
    return (await this.redis.get<RunRecord>(this.k(runId).run)) ?? null;
  }

  async getEvents(runId: string, sinceSeq = 0, limit = 2000): Promise<RunEvent[]> {
    const raw = await this.redis.lrange<string | RunEvent>(this.k(runId).events, sinceSeq, sinceSeq + limit - 1);
    return raw.map((x) => (typeof x === "string" ? (JSON.parse(x) as RunEvent) : x));
  }

  async getScript(runId: string) {
    return (await this.redis.get<string>(this.k(runId).script)) ?? null;
  }

  async listRuns(limit = MAX_RUNS_LISTED): Promise<RunRecord[]> {
    const ids = await this.redis.zrange<string[]>("runs", 0, limit - 1, { rev: true });
    if (!ids.length) return [];
    const runs = await this.redis.mget<(RunRecord | null)[]>(...ids.map((id) => `run:${id}`));
    return runs.filter((r): r is RunRecord => !!r);
  }
}

// ── Local files (dev) ─────────────────────────────────────────────────────────
interface FileRun {
  run: RunRecord;
  events: RunEvent[];
  script: string | null;
}

class FileStore implements RunStore {
  constructor(private dir: string) {}

  private file(id: string) {
    if (!/^[\w.-]+$/.test(id)) throw new Error("bad run id");
    return path.join(this.dir, `${id}.json`);
  }

  private async load(id: string): Promise<FileRun | null> {
    try {
      return JSON.parse(await fs.readFile(this.file(id), "utf8")) as FileRun;
    } catch {
      return null;
    }
  }

  private async save(data: FileRun) {
    await fs.mkdir(this.dir, { recursive: true });
    const tmp = this.file(data.run.run_id) + ".tmp";
    await fs.writeFile(tmp, JSON.stringify(data));
    await fs.rename(tmp, this.file(data.run.run_id));
  }

  async upsertRun(meta: Partial<RunRecord> & { run_id: string }) {
    const cur = (await this.load(meta.run_id)) ?? { run: emptyRun(meta.run_id), events: [], script: null };
    cur.run = mergeMeta(cur.run, meta);
    cur.run.updated_at = nowIso();
    await this.save(cur);
    return cur.run;
  }

  async appendEvents(runId: string, payload: EventsPayload) {
    const cur = (await this.load(runId)) ?? { run: emptyRun(runId), events: [], script: null };
    const merged = mergeMeta(cur.run, payload.run);
    const events = payload.events ?? [];
    const room = Math.max(0, MAX_EVENTS_PER_RUN - cur.events.length);
    cur.events.push(...events.slice(0, room));
    const { run, script } = foldEvents(merged, events);
    run.event_count = cur.events.length;
    if (script !== null) cur.script = script;
    cur.run = run;
    await this.save(cur);
    return run;
  }

  async getRun(runId: string) {
    return (await this.load(runId))?.run ?? null;
  }

  async getEvents(runId: string, sinceSeq = 0, limit = 2000) {
    return ((await this.load(runId))?.events ?? []).slice(sinceSeq, sinceSeq + limit);
  }

  async getScript(runId: string) {
    return (await this.load(runId))?.script ?? null;
  }

  async listRuns(limit = MAX_RUNS_LISTED) {
    let names: string[] = [];
    try {
      names = (await fs.readdir(this.dir)).filter((n) => n.endsWith(".json"));
    } catch {
      return [];
    }
    const runs = (await Promise.all(names.map((n) => this.load(n.replace(/\.json$/, ""))))).filter((x): x is FileRun => !!x).map((x) => x.run);
    return runs.sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? "")).slice(0, limit);
  }
}

let _store: RunStore | null = null;

export function getStore(): RunStore {
  if (_store) return _store;
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  _store = url && token ? new RedisStore(new Redis({ url, token })) : new FileStore(path.join(process.cwd(), ".data", "runs"));
  return _store;
}

export function storeKind(): "redis" | "file" {
  return process.env.UPSTASH_REDIS_REST_URL && process.env.UPSTASH_REDIS_REST_TOKEN ? "redis" : "file";
}
