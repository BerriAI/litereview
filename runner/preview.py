"""Preview content is agent-authored HTML guided by shared design primitives."""

from pathlib import Path

from pydantic import BaseModel, Field


class PreviewDocument(BaseModel):
    title: str = Field(min_length=1, description="A short title describing the change.")
    summary: str = Field(min_length=1, description="The user-visible change and purpose in a few plain-English sentences.")
    html: str = Field(min_length=1, description="A self-contained HTML body with optional inline CSS. Choose the structure that best explains this PR; there are no required sections. Use native HTML/CSS interactions, not JavaScript. Reference saved media with src=artifact:ID.")
    artifact_ids: list[str] = Field(default_factory=list, description="Saved screenshot/video IDs referenced in html, from this job or its fusion branches.")


DESIGN_CSS = Path(__file__).with_name("preview-design.css").read_text()
PREVIEW_INSTRUCTIONS = """
Create a review companion that helps a teammate understand this PR and decide what to inspect.
Use the full supplied PR context. Explain the actual change and its purpose with concrete before/after
examples. Identify related PRs and review order only when supported by repository or PR evidence.
Distinguish this PR from the larger stack. Do not invent intent or claim access to private discussions.
Use familiar words, active voice, short paragraphs and precise claims. Remove filler, jargon,
dramatic contrasts and narration of your investigation. Keep technical detail that helps a reviewer
understand behavior or make a decision. Link claims to exact code and evidence.

The finish_review preview field accepts title, summary, html and artifact_ids. Design the page for
this particular PR. There is no mandatory layout, section list, component registry or fixed template.
Use the shared design language below as a foundation, then compose your own HTML and CSS.
The service displays the title and summary above your content. Do not repeat them.

Design language:
- Quiet white canvas, near-black text, muted secondary labels, restrained blue for links/actions.
- Whitespace and type establish hierarchy. Avoid borders, nested cards, badges and decorative boxes.
- Use a 4px spacing rhythm; normal text 16px/1.65; line length about 65 characters; clear short headings.
- Optional primitives supply spacing, rows, responsive grids, muted text, before/after columns, and
  subtle surfaces. Use only what helps; custom composition is encouraged. No prescribed page shape.
- Choose the most useful medium: real UI/video for user-facing changes, request/response paths for
  backend changes, or linked PR relationships for stacks. Include actual behavior and review focus.
- Prefer native HTML interactions: details/summary, labeled radio/checkbox controls with CSS for
  scenario/step selection, and video controls. Make controls keyboard accessible, respect reduced
  motion, keep text selectable, and make the narrow layout useful. No external libraries or scripts.
- Explain code-derived flows as code-derived. Mark a path observed only when execution evidence
  supports it. Native interactions should teach cause and effect, not decorate static prose.

For UI work, operate the actual checkout with browser/computer tools and recording start/stop.
Do not record credentials. For backend work, use real requests, responses or meaningful test output
when they clarify behavior. Label fixtures and simulations; diagrams are not execution proof.
If execution is blocked, describe the attempted setup and obstacle. Required failures belong in
limitations. Optional media can be omitted with an honest note in the page.
Reference saved media as <img src="artifact:ID" alt="..."> or
<video controls preload="metadata" src="artifact:ID" aria-label="..."></video>, and include every ID
in artifact_ids. Only media saved by this job or its fusion branches is allowed. External images,
embeds, forms, JavaScript, event handlers and network requests are disabled. Standard semantic HTML,
tables, inline SVG diagrams, style blocks and inline CSS are supported. Do not include html/head/body wrappers.

When branch reports are supplied, synthesize their evidence and reconcile contradictions. Their
artifact IDs can be used directly. Do not claim their checks as your own. Keep the GitHub summary
short; the service adds the stable hosted link. Preview has no numeric score.

Shared CSS, already applied to the page (you can extend it):
""" + DESIGN_CSS
