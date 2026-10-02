"use client";

import { useEffect, useState } from "react";

export function Field({ label, hint, children, inline = false }: { label: string; hint?: string; children: React.ReactNode; inline?: boolean }) {
  return (
    <label className={inline ? "flex items-center justify-between gap-3 py-1" : "block py-1.5"}>
      <span className="block">
        <span className="text-[13px] font-medium">{label}</span>
        {hint && <span className="block text-muted text-xs leading-snug">{hint}</span>}
      </span>
      <span className={inline ? "shrink-0 w-44" : "block mt-1"}>{children}</span>
    </label>
  );
}

/** Text input that commits on blur/Enter so every keystroke does not re-serialize YAML. */
export function TextField({ value, onChange, placeholder, mono }: { value: string; onChange: (v: string) => void; placeholder?: string; mono?: boolean }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <input
      className={`input ${mono ? "mono" : ""}`}
      value={draft}
      placeholder={placeholder}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => draft !== value && onChange(draft)}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}

export function TextArea({ value, onChange, rows = 6, mono = true }: { value: string; onChange: (v: string) => void; rows?: number; mono?: boolean }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return <textarea className={`input ${mono ? "mono" : ""} leading-relaxed`} rows={rows} value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={() => draft !== value && onChange(draft)} />;
}

export function NumberField({ value, onChange, min, max, step = 1 }: { value: number | undefined; onChange: (v: number) => void; min?: number; max?: number; step?: number }) {
  const [draft, setDraft] = useState(value === undefined ? "" : String(value));
  useEffect(() => setDraft(value === undefined ? "" : String(value)), [value]);
  return (
    <input
      className="input mono"
      type="number"
      value={draft}
      min={min}
      max={max}
      step={step}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const n = parseFloat(draft);
        if (Number.isFinite(n) && n !== value) onChange(n);
        else setDraft(value === undefined ? "" : String(value));
      }}
    />
  );
}

export function Select<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  const known = options.some((o) => o.value === value);
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value as T)}>
      {!known && <option value={value}>{value} (custom)</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)} className="inline-flex items-center gap-2 cursor-pointer">
      <span className={`relative inline-block h-5 w-9 rounded-full transition ${checked ? "bg-accent" : "bg-border"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition ${checked ? "left-[18px]" : "left-0.5"}`} />
      </span>
      {label && <span className="text-[13px]">{label}</span>}
    </button>
  );
}

/** 0–100 dial with the current band sentence shown underneath. */
export function Dial({ label, value, onChange, band, bandIndex, hint }: { label: string; value: number; onChange: (v: number) => void; band: string; bandIndex: number; hint?: string }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <div className="py-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] font-medium">
          {label}
          {hint && <span className="text-muted font-normal"> · {hint}</span>}
        </span>
        <span className="mono text-muted">
          {draft} <span className="opacity-60">· band {bandIndex + 1}/5</span>
        </span>
      </div>
      <input type="range" min={0} max={100} step={1} value={draft} onChange={(e) => setDraft(parseInt(e.target.value, 10))} onMouseUp={() => draft !== value && onChange(draft)} onTouchEnd={() => draft !== value && onChange(draft)} onKeyUp={() => draft !== value && onChange(draft)} />
      <div className="text-xs text-muted italic leading-snug -mt-0.5">“{band}”</div>
    </div>
  );
}

/** Editable list of short strings (traits, quirks, catchphrases, band sentences). */
export function StringList({ items, onChange, placeholder, fixedLength }: { items: string[]; onChange: (items: string[]) => void; placeholder?: string; fixedLength?: boolean }) {
  return (
    <div className="space-y-1.5">
      {items.map((it, i) => (
        <div key={i} className="flex gap-1.5 items-start">
          {fixedLength && <span className="mono text-muted pt-1.5 w-4 text-right">{i}</span>}
          <TextField value={it} onChange={(v) => onChange(items.map((x, j) => (j === i ? v : x)))} placeholder={placeholder} />
          {!fixedLength && (
            <button className="btn btn-sm btn-danger" title="Remove" onClick={() => onChange(items.filter((_, j) => j !== i))}>
              ×
            </button>
          )}
        </div>
      ))}
      {!fixedLength && (
        <button className="btn btn-sm" onClick={() => onChange([...items, ""])}>
          + add
        </button>
      )}
    </div>
  );
}

export function Section({ title, description, children, right }: { title: string; description?: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="card p-4">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h2 className="text-base font-semibold">{title}</h2>
          {description && <p className="text-muted text-xs mt-0.5 leading-snug max-w-prose">{description}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}
