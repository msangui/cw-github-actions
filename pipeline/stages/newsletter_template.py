"""Morning-Brew-style HTML newsletter renderer. Pure Python, email-safe inline CSS."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class StoryBlock:
    title: str
    url: str
    source: str
    summary: str
    claire_take: str
    is_deep_dive: bool = False


@dataclass
class NewsletterData:
    episode_date: str
    episode_title: str
    cold_open_hook: str
    stories: list[StoryBlock] = field(default_factory=list)
    flint_prediction: str = ""
    listen_url: str = "#"


_BG, _CONTAINER, _HEADER_BG, _HEADER_TEXT = "#F5F5F5", "#FFFFFF", "#0D0D0D", "#FFFFFF"
_ACCENT, _BODY_TEXT, _SECONDARY_TEXT, _DIVIDER = "#6B7CFF", "#1A1A1A", "#666666", "#E8E8E8"
_DEEP_DIVE_BG, _FOOTER_BG, _FOOTER_TEXT = "#F0F2FF", "#F5F5F5", "#999999"


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def format_date(date_str: str) -> str:
    try:
        d = date.fromisoformat(date_str)
        return f"{d.strftime('%A, %B')} {d.day}, {d.year}"
    except Exception:
        return date_str


def render_newsletter(data: NewsletterData) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(data.episode_title)}</title>
</head>
<body style="margin:0;padding:0;background-color:{_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{_BG};">
  <tr><td align="center" style="padding:24px 16px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background-color:{_CONTAINER};border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
      <tr><td style="background-color:{_HEADER_BG};padding:32px 40px 28px;">
        <p style="margin:0;font-size:11px;font-weight:700;letter-spacing:3px;color:{_ACCENT};text-transform:uppercase;">Daily Briefing</p>
        <h1 style="margin:8px 0 4px;font-size:28px;font-weight:800;color:{_HEADER_TEXT};letter-spacing:-0.5px;line-height:1.2;">Context Window</h1>
        <p style="margin:0;font-size:13px;color:#888888;">{format_date(data.episode_date)}</p>
      </td></tr>
      <tr><td style="background-color:{_HEADER_BG};padding:0 40px 32px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
          <td style="border-left:3px solid {_ACCENT};padding-left:16px;">
            <p style="margin:0;font-size:14px;line-height:1.6;color:#CCCCCC;font-style:italic;">{esc(data.cold_open_hook)}</p>
          </td></tr></table>
      </td></tr>
      <tr><td style="padding:28px 40px 4px;">
        <p style="margin:0;font-size:14px;color:{_SECONDARY_TEXT};line-height:1.6;">Here's what Flint and Claire covered in today's <strong style="color:{_BODY_TEXT};">{len(data.stories)}-story episode</strong>.</p>
      </td></tr>
      {_render_stories(data.stories)}
      {_render_deep_dives(data.stories)}
      {_render_prediction(data.flint_prediction)}
      <tr><td style="padding:32px 40px;">
        <table role="presentation" cellpadding="0" cellspacing="0"><tr>
          <td style="background-color:{_ACCENT};border-radius:6px;">
            <a href="{esc(data.listen_url)}" style="display:inline-block;padding:14px 32px;font-size:14px;font-weight:700;color:#FFFFFF;text-decoration:none;letter-spacing:0.3px;">🎧 Listen to Today's Episode →</a>
          </td></tr></table>
      </td></tr>
      <tr><td style="padding:0 40px;"><hr style="border:none;border-top:1px solid {_DIVIDER};margin:0;"></td></tr>
      <tr><td style="padding:24px 40px 8px;">
        <p style="margin:0;font-size:13px;color:{_SECONDARY_TEXT};line-height:1.6;">
          <strong>FLINT:</strong> <em>"Links are in your Telegram. You know what to do. Sorry for my gibberish, it happens every now and then"</em><br>
          <strong>CLAIRE:</strong> <em>"We'll be here tomorrow."</em><br>
          <strong>FLINT:</strong> <em>"Unfortunately."</em>
        </p>
      </td></tr>
      <tr><td style="background-color:{_FOOTER_BG};padding:24px 40px;border-top:1px solid {_DIVIDER};">
        <p style="margin:0;font-size:11px;color:{_FOOTER_TEXT};line-height:1.6;text-align:center;">Context Window — AI &amp; Agentic Engineering Daily<br>Generated {format_date(data.episode_date)}</p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def _render_stories(stories: list[StoryBlock]) -> str:
    blocks = []
    for i, s in enumerate(stories, 1):
        claire = (
            f'<tr><td style="padding:10px 0 0;"><p style="margin:0;font-size:12px;color:{_SECONDARY_TEXT};font-style:italic;line-height:1.5;border-left:2px solid {_DIVIDER};padding-left:12px;">— {esc(s.claire_take)}</p></td></tr>'
            if s.claire_take
            else ""
        )
        badge = (
            f'<span style="display:inline-block;margin-left:8px;padding:1px 7px;font-size:10px;font-weight:700;letter-spacing:0.5px;color:{_ACCENT};background-color:{_DEEP_DIVE_BG};border-radius:3px;">DEEP DIVE</span>'
            if s.is_deep_dive
            else ""
        )
        blocks.append(
            f"""<tr><td style="padding:20px 40px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr><td style="padding-bottom:12px;">
            <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1px;color:{_ACCENT};text-transform:uppercase;">{i:02d} &nbsp;·&nbsp; {esc(s.source)}</p>
            <h2 style="margin:0 0 8px;font-size:16px;font-weight:700;color:{_BODY_TEXT};line-height:1.3;"><a href="{esc(s.url)}" style="color:{_BODY_TEXT};text-decoration:none;">{esc(s.title)}</a>{badge}</h2>
            <p style="margin:0;font-size:14px;color:{_BODY_TEXT};line-height:1.6;">{esc(s.summary)}</p>
            {claire}
          </td></tr>
          <tr><td><hr style="border:none;border-top:1px solid {_DIVIDER};margin:0;"></td></tr>
        </table>
      </td></tr>"""
        )
    return "\n".join(blocks)


def _render_deep_dives(stories: list[StoryBlock]) -> str:
    picks = [s for s in stories if s.is_deep_dive]
    if not picks:
        return ""
    items = "\n".join(
        f'<tr><td style="padding:6px 0;"><p style="margin:0;font-size:13px;color:{_BODY_TEXT};line-height:1.5;"><a href="{esc(s.url)}" style="color:{_ACCENT};font-weight:600;text-decoration:none;">{esc(s.title)}</a><span style="color:{_SECONDARY_TEXT};"> — {esc(s.source)}</span></p></td></tr>'
        for s in picks
    )
    return f"""<tr><td style="padding:24px 40px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{_DEEP_DIVE_BG};border-left:3px solid {_ACCENT};border-radius:0 6px 6px 0;padding:20px 24px;">
          <tr><td>
            <p style="margin:0 0 12px;font-size:11px;font-weight:700;letter-spacing:2px;color:{_ACCENT};text-transform:uppercase;">📚 Read These</p>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{items}</table>
          </td></tr>
        </table>
      </td></tr>"""


def _render_prediction(prediction: str) -> str:
    if not prediction:
        return ""
    return f"""<tr><td style="padding:24px 40px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#1A1A1A;border-radius:6px;padding:20px 24px;">
          <tr><td>
            <p style="margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:2px;color:{_ACCENT};text-transform:uppercase;">🎲 Flint's Bold Prediction</p>
            <p style="margin:0;font-size:14px;color:#EEEEEE;line-height:1.6;font-style:italic;">"{esc(prediction)}"</p>
            <p style="margin:8px 0 0;font-size:12px;color:#888888;"><em>— "I stand by it." / "You always do."</em></p>
          </td></tr>
        </table>
      </td></tr>"""
