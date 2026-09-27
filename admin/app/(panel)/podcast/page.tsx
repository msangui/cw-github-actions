"use client";

import { useConfig } from "@/components/ConfigProvider";
import { Field, Section, TextArea, TextField, Toggle } from "@/components/fields";

const FILE = "config/podcast.yaml";

interface Podcast {
  title?: string;
  subtitle?: string;
  description?: string;
  language?: string;
  author?: string;
  owner_name?: string;
  owner_email?: string;
  link?: string;
  copyright?: string;
  explicit?: boolean;
  category?: string;
  subcategory?: string;
  image_key?: string;
  feeds?: Record<string, { key: string; title_suffix: string }>;
}

export default function PodcastPage() {
  const cfg = useConfig();
  const p = cfg.parsed<Podcast>(FILE);
  if (!p) return <div className="text-muted">podcast.yaml not loaded.</div>;
  const set = (k: string) => (v: unknown) => cfg.setValue(FILE, [k], v);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title="Show metadata" description="What Spotify / Apple Podcasts display. Changing these requires a feed rebuild (Actions → Rebuild feed) to take effect for past episodes.">
        <Field label="Title">
          <TextField value={p.title ?? ""} onChange={set("title")} />
        </Field>
        <Field label="Subtitle">
          <TextField value={p.subtitle ?? ""} onChange={set("subtitle")} />
        </Field>
        <Field label="Description">
          <TextArea value={p.description ?? ""} rows={5} mono={false} onChange={set("description")} />
        </Field>
        <div className="grid sm:grid-cols-2 gap-x-4">
          <Field label="Language">
            <TextField value={p.language ?? ""} onChange={set("language")} />
          </Field>
          <Field label="Author">
            <TextField value={p.author ?? ""} onChange={set("author")} />
          </Field>
          <Field label="Category">
            <TextField value={p.category ?? ""} onChange={set("category")} />
          </Field>
          <Field label="Subcategory">
            <TextField value={p.subcategory ?? ""} onChange={set("subcategory")} />
          </Field>
        </div>
        <div className="py-1.5">
          <Toggle checked={!!p.explicit} onChange={set("explicit")} label="Explicit" />
        </div>
      </Section>
      <Section title="Owner & links" description="Spotify verifies ownership by emailing owner_email.">
        <Field label="Owner name">
          <TextField value={p.owner_name ?? ""} onChange={set("owner_name")} />
        </Field>
        <Field label="Owner email">
          <TextField value={p.owner_email ?? ""} onChange={set("owner_email")} />
        </Field>
        <Field label="Website link" hint="Empty = feed's public base URL">
          <TextField value={p.link ?? ""} onChange={set("link")} />
        </Field>
        <Field label="Copyright">
          <TextField value={p.copyright ?? ""} onChange={set("copyright")} />
        </Field>
        <Field label="Cover image key" hint="Object key in the bucket">
          <TextField value={p.image_key ?? ""} mono onChange={set("image_key")} />
        </Field>
        <h3 className="font-semibold text-[13px] mt-3">Feeds</h3>
        {Object.entries(p.feeds ?? {}).map(([name, f]) => (
          <div key={name} className="grid grid-cols-[6rem_1fr_1fr] gap-2 items-center py-1">
            <span className="mono">{name}</span>
            <TextField value={f.key} mono onChange={(v) => cfg.setValue(FILE, ["feeds", name, "key"], v)} />
            <TextField value={f.title_suffix} placeholder="title suffix" onChange={(v) => cfg.setValue(FILE, ["feeds", name, "title_suffix"], v)} />
          </div>
        ))}
      </Section>
    </div>
  );
}
