"use client";

/** Renders a unified diff string with per-line coloring. */
export function DiffView({ diff, maxLines = 400 }: { diff: string; maxLines?: number }) {
  const lines = diff.split("\n");
  const shown = lines.slice(0, maxLines);
  return (
    <pre className="mono overflow-x-auto rounded-md border border-border bg-panel-2 p-2 leading-5 whitespace-pre">
      {shown.map((ln, i) => {
        let cls = "";
        if (ln.startsWith("+++") || ln.startsWith("---")) cls = "text-muted";
        else if (ln.startsWith("@@")) cls = "diff-line-hunk";
        else if (ln.startsWith("+")) cls = "diff-line-add";
        else if (ln.startsWith("-")) cls = "diff-line-del";
        return (
          <div key={i} className={cls}>
            {ln || " "}
          </div>
        );
      })}
      {lines.length > maxLines && <div className="text-muted">… {lines.length - maxLines} more lines</div>}
    </pre>
  );
}
