"""Newsletter: extract structured content from the approved script, render HTML."""

from __future__ import annotations

from typing import Any

from pipeline.config import Settings
from pipeline.llm import LLM
from pipeline.log import get_logger
from pipeline.stages.newsletter_template import NewsletterData, StoryBlock, esc, render_newsletter

log = get_logger(stage="newsletter")

NEWSLETTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "episode_title": {"type": "string"},
        "cold_open_hook": {"type": "string"},
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "source": {"type": "string"},
                    "summary": {"type": "string"},
                    "claire_take": {"type": "string"},
                    "is_deep_dive": {"type": "boolean"},
                },
                "required": ["title", "url", "source", "summary", "claire_take", "is_deep_dive"],
                "additionalProperties": False,
            },
        },
        "flint_prediction": {"type": "string"},
    },
    "required": ["episode_title", "cold_open_hook", "stories", "flint_prediction"],
    "additionalProperties": False,
}


def stub_html(episode_date: str, stories: list[dict[str, Any]], listen_url: str = "#") -> str:
    items = "\n".join(f'<li><a href="{esc(s.get("url", ""))}">{esc(s["title"])}</a> — {esc(s.get("source_name", ""))}</li>' for s in stories)
    return f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px;">
  <h1>Context Window — {esc(episode_date)}</h1>
  <p>Today's AI and agentic engineering news:</p>
  <ul>{items}</ul>
  <p><a href="{esc(listen_url)}"><strong>Listen now.</strong></a></p>
</body></html>"""


def generate_newsletter(settings: Settings, llm: LLM, script: str, stories: list[dict[str, Any]], episode_date: str, listen_url: str = "#") -> dict[str, Any]:
    if not llm.available:
        return {"html": stub_html(episode_date, stories, listen_url), "episode_title": f"Context Window — {episode_date}", "token_usage": {}, "model": None}

    stories_text = "\n".join(f"- {s['title']} ({s.get('source_name', '')}) {s.get('url', '')}" for s in stories[:14])
    user = f"Episode date: {episode_date}\n\nStories from the curator brief:\n{stories_text}\n\nFull script:\n{script[:16000]}\n\nReturn only the JSON."
    try:
        result = llm.call_json("newsletter", user, schema=NEWSLETTER_SCHEMA)
    except Exception as e:
        log.warning("Newsletter extraction failed, using stub", error=str(e)[:200])
        return {"html": stub_html(episode_date, stories, listen_url), "episode_title": f"Context Window — {episode_date}", "token_usage": {}, "model": None}

    d = result.data
    blocks = [
        StoryBlock(
            title=s.get("title", ""),
            url=s.get("url", ""),
            source=s.get("source", ""),
            summary=s.get("summary", ""),
            claire_take=s.get("claire_take", ""),
            is_deep_dive=bool(s.get("is_deep_dive", False)),
        )
        for s in d.get("stories", [])
    ]
    data = NewsletterData(
        episode_date=episode_date,
        episode_title=d.get("episode_title") or f"Context Window — {episode_date}",
        cold_open_hook=d.get("cold_open_hook", ""),
        stories=blocks,
        flint_prediction=d.get("flint_prediction", ""),
        listen_url=listen_url,
    )
    log.info("Newsletter rendered", stories=len(blocks))
    return {"html": render_newsletter(data), "episode_title": data.episode_title, "token_usage": result.token_usage, "model": result.model}
