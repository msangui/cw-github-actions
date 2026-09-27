"use client";

import { useConfig } from "@/components/ConfigProvider";
import { Field, NumberField, Section, TextField, Toggle } from "@/components/fields";

const VOICES = "config/voices.yaml";
const STITCH = "config/stitch.yaml";

interface Voice {
  voice_id?: string;
  stability?: number;
  similarity_boost?: number;
  style?: number;
  use_speaker_boost?: boolean;
}
interface Voices {
  model_id?: string;
  gap_between_calls_ms?: number;
  retry_delays_seconds?: number[];
  voices?: Record<string, Voice>;
}
interface Stitch {
  tts_delay_ms?: number;
  duck_start_s?: number;
  duck_end_s?: number;
  intro_volume_floor?: number;
  loudnorm_i?: number;
  loudnorm_tp?: number;
  loudnorm_lra?: number;
}

export default function VoicesPage() {
  const cfg = useConfig();
  const v = cfg.parsed<Voices>(VOICES);
  const st = cfg.parsed<Stitch>(STITCH);
  if (!v || !st) return <div className="text-muted">voices.yaml / stitch.yaml not loaded.</div>;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {Object.entries(v.voices ?? {}).map(([speaker, voice]) => (
        <Section key={speaker} title={`${speaker} voice`} description="ElevenLabs voice settings. Repo variables CLAIRE_VOICE_ID / FLINT_VOICE_ID override the voice_id at run time.">
          <Field label="Voice ID">
            <TextField value={voice.voice_id ?? ""} mono onChange={(x) => cfg.setValue(VOICES, ["voices", speaker, "voice_id"], x)} />
          </Field>
          <Pct label="Stability" hint="low = more expressive, high = more consistent" value={voice.stability ?? 0.5} onChange={(x) => cfg.setValue(VOICES, ["voices", speaker, "stability"], x)} />
          <Pct label="Similarity boost" value={voice.similarity_boost ?? 0.75} onChange={(x) => cfg.setValue(VOICES, ["voices", speaker, "similarity_boost"], x)} />
          <Pct label="Style" hint="exaggeration of the voice's style; costs stability" value={voice.style ?? 0} onChange={(x) => cfg.setValue(VOICES, ["voices", speaker, "style"], x)} />
          <div className="py-1.5">
            <Toggle checked={voice.use_speaker_boost ?? true} onChange={(x) => cfg.setValue(VOICES, ["voices", speaker, "use_speaker_boost"], x)} label="Speaker boost" />
          </div>
        </Section>
      ))}
      <Section title="TTS engine" description="Model and pacing between ElevenLabs calls.">
        <Field label="ElevenLabs model" inline>
          <TextField value={v.model_id ?? ""} mono onChange={(x) => cfg.setValue(VOICES, ["model_id"], x)} />
        </Field>
        <Field label="Gap between calls (ms)" inline>
          <NumberField value={v.gap_between_calls_ms} min={0} max={5000} step={50} onChange={(x) => cfg.setValue(VOICES, ["gap_between_calls_ms"], Math.round(x))} />
        </Field>
      </Section>
      <Section title="Mix (ffmpeg)" description="Intro jingle duck curve and loudness normalization. Change deliberately; these are editorial.">
        <Field label="Voice enters at (ms)" inline>
          <NumberField value={st.tts_delay_ms} min={0} max={60000} step={500} onChange={(x) => cfg.setValue(STITCH, ["tts_delay_ms"], Math.round(x))} />
        </Field>
        <Field label="Duck starts (s)" inline>
          <NumberField value={st.duck_start_s} min={0} max={60} step={0.5} onChange={(x) => cfg.setValue(STITCH, ["duck_start_s"], x)} />
        </Field>
        <Field label="Duck reaches floor (s)" inline>
          <NumberField value={st.duck_end_s} min={0} max={60} step={0.5} onChange={(x) => cfg.setValue(STITCH, ["duck_end_s"], x)} />
        </Field>
        <Pct label="Intro volume floor" hint="jingle level under the voices" value={st.intro_volume_floor ?? 0.25} onChange={(x) => cfg.setValue(STITCH, ["intro_volume_floor"], x)} />
        <Field label="Loudness target (LUFS)" inline>
          <NumberField value={st.loudnorm_i} min={-30} max={-8} step={0.5} onChange={(x) => cfg.setValue(STITCH, ["loudnorm_i"], x)} />
        </Field>
        <Field label="True peak (dBTP)" inline>
          <NumberField value={st.loudnorm_tp} min={-6} max={0} step={0.1} onChange={(x) => cfg.setValue(STITCH, ["loudnorm_tp"], x)} />
        </Field>
        <Field label="Loudness range" inline>
          <NumberField value={st.loudnorm_lra} min={1} max={30} step={1} onChange={(x) => cfg.setValue(STITCH, ["loudnorm_lra"], x)} />
        </Field>
      </Section>
    </div>
  );
}

function Pct({ label, hint, value, onChange }: { label: string; hint?: string; value: number; onChange: (v: number) => void }) {
  return (
    <div className="py-1.5">
      <div className="flex justify-between text-[13px]">
        <span className="font-medium">
          {label}
          {hint && <span className="text-muted font-normal"> · {hint}</span>}
        </span>
        <span className="mono text-muted">{value.toFixed(2)}</span>
      </div>
      <input type="range" min={0} max={1} step={0.01} defaultValue={value} key={value} onMouseUp={(e) => onChange(parseFloat((e.target as HTMLInputElement).value))} onTouchEnd={(e) => onChange(parseFloat((e.target as HTMLInputElement).value))} onKeyUp={(e) => onChange(parseFloat((e.target as HTMLInputElement).value))} />
    </div>
  );
}
