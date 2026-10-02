"""Compile the writer system prompt from config/show.yaml.

config/show.yaml describes the hosts (bio, traits, quirks, catchphrases, voice notes and
0-100 "manner dials") and the show-level dynamics (joke density, banter, ...). Each dial is
mapped to one of five language bands whose wording also lives in show.yaml. This module
renders that into a text block and substitutes it for the ``{{SHOW}}`` placeholder in an
agent's ``system_prompt`` (writer and aisle_writer). Script rules, the verbatim sign-off
lines and the ``# SECTION:READ_THESE`` marker stay in the agent YAML and are untouched.

The admin panel ships a TypeScript port of ``render_show_block`` for its live preview;
``tests/fixtures/compiled_writer_prompt.txt`` is the shared golden output that keeps the two
in sync. Change both together.
"""

from __future__ import annotations

from typing import Any

from pipeline.config import Settings

SHOW_PLACEHOLDER = "{{SHOW}}"
DEFAULT_BAND_EDGES = [20, 40, 60, 80]
HOST_DIALS = ["humor", "straightforwardness", "warmth", "skepticism", "verbosity", "energy", "technical_depth"]
DYNAMICS_DIALS = ["joke_density", "banter", "disagreement", "interruptions", "tangents", "callbacks", "audience_address", "pace"]

_LABELS = {
    "humor": "Humor",
    "straightforwardness": "Straightforwardness",
    "warmth": "Warmth",
    "skepticism": "Skepticism",
    "verbosity": "Verbosity",
    "energy": "Energy",
    "technical_depth": "Technical depth",
    "joke_density": "Joke density",
    "banter": "Banter",
    "disagreement": "Disagreement",
    "interruptions": "Interruptions",
    "tangents": "Tangents",
    "callbacks": "Callbacks",
    "audience_address": "Audience address",
    "pace": "Pace",
}


def clamp_dial(value: Any) -> int:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        v = 50
    return max(0, min(100, v))


def band_index(value: Any, edges: list[int] | None = None) -> int:
    """0..len(edges) — which band a 0-100 dial value falls into."""
    v = clamp_dial(value)
    for i, edge in enumerate(edges or DEFAULT_BAND_EDGES):
        if v < edge:
            return i
    return len(edges or DEFAULT_BAND_EDGES)


def band_text(show: dict[str, Any], group: str, dial: str, value: Any) -> str:
    bands = ((show.get("bands") or {}).get(group) or {}).get(dial) or []
    if not bands:
        return f"{_LABELS.get(dial, dial)}: {clamp_dial(value)}/100"
    idx = min(band_index(value, show.get("band_edges")), len(bands) - 1)
    return str(bands[idx])


def _label(dial: str) -> str:
    return _LABELS.get(dial, dial.replace("_", " ").capitalize())


def _list(items: Any) -> list[str]:
    if not items:
        return []
    if isinstance(items, str):
        return [items]
    return [str(x) for x in items if str(x).strip()]


def render_host(show: dict[str, Any], key: str, host: dict[str, Any]) -> list[str]:
    head = f"{key} — {host.get('role') or 'Host'}."
    if host.get("bio"):
        head += f" {str(host['bio']).strip()}"
    lines = [head]
    traits = _list(host.get("traits"))
    if traits:
        lines.append("  Traits: " + "; ".join(traits) + ".")
    quirks = _list(host.get("quirks"))
    if quirks:
        lines.append("  Quirks: " + "; ".join(quirks) + ".")
    phrases = _list(host.get("catchphrases"))
    if phrases:
        lines.append("  Catchphrases (use sparingly): " + "; ".join(f'"{p}"' for p in phrases) + ".")
    if host.get("voice_notes"):
        lines.append(f"  Voice: {str(host['voice_notes']).strip()}")
    dials = host.get("dials") or {}
    manner = [f"{_label(d)} — {band_text(show, 'host', d, dials.get(d, 50))}" for d in HOST_DIALS]
    lines.append("  Manner:")
    lines.extend(f"    - {m}" for m in manner)
    return lines


def render_show_block(show: dict[str, Any]) -> str:
    """Render show.yaml into the text that replaces {{SHOW}}."""
    out: list[str] = []
    meta = show.get("show") or {}
    if meta.get("premise") or meta.get("audience"):
        out.append("SHOW:")
        if meta.get("premise"):
            out.append(f"{str(meta['premise']).strip()}")
        if meta.get("audience"):
            out.append(f"Audience: {str(meta['audience']).strip()}")
        out.append("")
    out.append("HOSTS:")
    for key, host in (show.get("hosts") or {}).items():
        out.extend(render_host(show, str(key), host or {}))
    dynamics = show.get("dynamics") or {}
    if dynamics or (show.get("bands") or {}).get("dynamics"):
        out.append("")
        out.append("DYNAMICS (how the hosts interact):")
        for d in DYNAMICS_DIALS:
            out.append(f"- {_label(d)}: {band_text(show, 'dynamics', d, dynamics.get(d, 50))}")
    return "\n".join(out).rstrip() + "\n"


def compile_system_prompt(template: str, show: dict[str, Any] | None) -> str:
    """Substitute {{SHOW}} in an agent prompt. No placeholder → template returned verbatim."""
    if SHOW_PLACEHOLDER not in template:
        return template
    block = render_show_block(show) if show else ""
    # Preserve the placeholder line's indentation for every rendered line.
    lines = template.split("\n")
    result: list[str] = []
    for line in lines:
        if SHOW_PLACEHOLDER in line:
            indent = line[: len(line) - len(line.lstrip())]
            for bl in block.rstrip("\n").split("\n"):
                result.append((indent + bl) if bl else "")
        else:
            result.append(line)
    return "\n".join(result)


def compile_agent_prompt(settings: Settings, agent_key: str) -> str:
    """Final system prompt for an agent: its YAML system_prompt with show.yaml rendered in."""
    cfg = settings.agent(agent_key)
    template = str(cfg.get("system_prompt", ""))
    show = settings.load_yaml("show")
    return compile_system_prompt(template, show or None)
