/**
 * Thin GitHub REST client (fetch-based, no Octokit). Everything the panel does with the repo:
 *   - read the config files at the branch head
 *   - commit a set of files atomically via the Git Data API (blobs → tree → commit → ref)
 *   - dispatch the daily-episode workflow and list its runs
 * Authenticated with a fine-grained PAT (Contents rw, Actions rw, Metadata r) held in Vercel env.
 */
import type { GitHubRunSummary } from "./types";

const API = "https://api.github.com";

export class GitHubError extends Error {
  constructor(
    message: string,
    public status: number,
    public body?: unknown,
  ) {
    super(message);
  }
}

export interface RepoConfig {
  owner: string;
  repo: string;
  branch: string;
  workflow: string;
  token: string;
}

export function repoConfig(): RepoConfig {
  const full = process.env.GITHUB_REPO ?? "";
  const [owner, repo] = full.split("/");
  const token = process.env.GITHUB_TOKEN ?? "";
  if (!owner || !repo) throw new GitHubError("GITHUB_REPO must be owner/repo", 500);
  if (!token) throw new GitHubError("GITHUB_TOKEN is not set", 500);
  return { owner, repo, branch: process.env.GITHUB_BRANCH || "main", workflow: process.env.GITHUB_WORKFLOW || "daily-episode.yml", token };
}

async function gh<T>(cfg: RepoConfig, method: string, path: string, body?: unknown, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${cfg.token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
    ...init,
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let json: unknown = undefined;
  try {
    json = text ? JSON.parse(text) : undefined;
  } catch {
    json = text;
  }
  if (!res.ok) {
    const msg = (json as { message?: string } | undefined)?.message ?? res.statusText;
    throw new GitHubError(`GitHub ${method} ${path} → ${res.status}: ${msg}`, res.status, json);
  }
  return json as T;
}

export async function getBranchHead(cfg: RepoConfig): Promise<string> {
  const ref = await gh<{ object: { sha: string } }>(cfg, "GET", `/repos/${cfg.owner}/${cfg.repo}/git/ref/heads/${encodeURIComponent(cfg.branch)}`);
  return ref.object.sha;
}

export async function readFileAt(cfg: RepoConfig, path: string, ref: string): Promise<string> {
  const data = await gh<{ content: string; encoding: string }>(cfg, "GET", `/repos/${cfg.owner}/${cfg.repo}/contents/${path}?ref=${encodeURIComponent(ref)}`);
  if (data.encoding !== "base64") throw new GitHubError(`Unexpected encoding for ${path}`, 500);
  return Buffer.from(data.content.replace(/\n/g, ""), "base64").toString("utf8");
}

export async function readFiles(cfg: RepoConfig, paths: readonly string[]): Promise<{ sha: string; files: Record<string, string> }> {
  const sha = await getBranchHead(cfg);
  const entries = await Promise.all(paths.map(async (p) => [p, await readFileAt(cfg, p, sha)] as const));
  return { sha, files: Object.fromEntries(entries) };
}

export interface CommitResult {
  sha: string;
  html_url: string;
}

/**
 * Commit several files in one commit on top of `parentSha`. If the branch has moved since
 * (someone else committed), the ref update is rejected with 422 and we surface a conflict —
 * the caller reloads and re-applies. No force pushes, ever.
 */
export async function commitFiles(cfg: RepoConfig, parentSha: string, files: Record<string, string>, message: string, author?: { name: string; email: string }): Promise<CommitResult> {
  const base = `/repos/${cfg.owner}/${cfg.repo}`;
  const parent = await gh<{ tree: { sha: string } }>(cfg, "GET", `${base}/git/commits/${parentSha}`);
  const blobs = await Promise.all(
    Object.entries(files).map(async ([path, content]) => {
      const blob = await gh<{ sha: string }>(cfg, "POST", `${base}/git/blobs`, { content: Buffer.from(content, "utf8").toString("base64"), encoding: "base64" });
      return { path, mode: "100644" as const, type: "blob" as const, sha: blob.sha };
    }),
  );
  const tree = await gh<{ sha: string }>(cfg, "POST", `${base}/git/trees`, { base_tree: parent.tree.sha, tree: blobs });
  const commit = await gh<{ sha: string; html_url: string }>(cfg, "POST", `${base}/git/commits`, {
    message,
    tree: tree.sha,
    parents: [parentSha],
    ...(author ? { author: { ...author, date: new Date().toISOString() } } : {}),
  });
  try {
    await gh(cfg, "PATCH", `${base}/git/refs/heads/${encodeURIComponent(cfg.branch)}`, { sha: commit.sha, force: false });
  } catch (e) {
    if (e instanceof GitHubError && e.status === 422) {
      throw new GitHubError("The branch moved since you loaded the config (not a fast-forward). Reload and re-apply your changes.", 409, e.body);
    }
    throw e;
  }
  return { sha: commit.sha, html_url: commit.html_url };
}

export interface DispatchInputs {
  episode_date?: string;
  dry_run?: boolean;
  fresh?: boolean;
  skip_aisle?: boolean;
}

export async function dispatchWorkflow(cfg: RepoConfig, inputs: DispatchInputs): Promise<void> {
  // workflow_dispatch inputs are strings on the wire; booleans must be "true"/"false".
  const wire: Record<string, string> = {};
  if (inputs.episode_date) wire.episode_date = inputs.episode_date;
  for (const k of ["dry_run", "fresh", "skip_aisle"] as const) if (inputs[k] !== undefined) wire[k] = inputs[k] ? "true" : "false";
  await gh(cfg, "POST", `/repos/${cfg.owner}/${cfg.repo}/actions/workflows/${encodeURIComponent(cfg.workflow)}/dispatches`, { ref: cfg.branch, inputs: wire });
}

export async function listWorkflowRuns(cfg: RepoConfig, perPage = 20): Promise<GitHubRunSummary[]> {
  const data = await gh<{ workflow_runs: GitHubRunSummary[] }>(cfg, "GET", `/repos/${cfg.owner}/${cfg.repo}/actions/workflows/${encodeURIComponent(cfg.workflow)}/runs?per_page=${perPage}`);
  return data.workflow_runs.map(summarize);
}

export async function getWorkflowRun(cfg: RepoConfig, runId: string): Promise<GitHubRunSummary | null> {
  if (!/^\d+$/.test(runId)) return null;
  try {
    return summarize(await gh<GitHubRunSummary>(cfg, "GET", `/repos/${cfg.owner}/${cfg.repo}/actions/runs/${runId}`));
  } catch (e) {
    if (e instanceof GitHubError && e.status === 404) return null;
    throw e;
  }
}

function summarize(r: GitHubRunSummary): GitHubRunSummary {
  return { id: r.id, status: r.status, conclusion: r.conclusion, html_url: r.html_url, created_at: r.created_at, updated_at: r.updated_at, run_number: r.run_number, event: r.event, display_title: r.display_title };
}

export function actionsUrl(cfg: RepoConfig): string {
  return `https://github.com/${cfg.owner}/${cfg.repo}/actions/workflows/${cfg.workflow}`;
}

/**
 * workflow_dispatch returns 204 with no run id. Poll the run list for a new dispatch run that
 * appeared after `since`; give up after ~12 s and let the UI fall back to the Actions page.
 */
export async function findDispatchedRun(cfg: RepoConfig, since: Date, knownIds: Set<number>, attempts = 6, delayMs = 2000): Promise<GitHubRunSummary | null> {
  for (let i = 0; i < attempts; i++) {
    await new Promise((r) => setTimeout(r, delayMs));
    const runs = await listWorkflowRuns(cfg, 10);
    const fresh = runs.filter((r) => r.event === "workflow_dispatch" && !knownIds.has(r.id) && new Date(r.created_at).getTime() >= since.getTime() - 60_000);
    if (fresh.length) return fresh.sort((a, b) => b.id - a.id)[0];
  }
  return null;
}
