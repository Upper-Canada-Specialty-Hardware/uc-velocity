"""Render deployment-owned email bodies from validated Clerk metadata."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape


# Resolve templates from this module so rendering never depends on process CWD.
_TEMPLATE_DIRECTORY = Path(__file__).parent / "templates" / "emails"
# Strict variables expose template mistakes instead of silently sending blanks.
_ENVIRONMENT = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIRECTORY),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
)


def render_verification_code_email(code: str) -> tuple[str, str]:
    """Render UC Velocity's HTML and text verification-code bodies.

    Args:
        code: Validated Clerk-generated verification code.

    Returns:
        The rendered HTML body followed by the rendered plain-text body.

    Raises:
        jinja2.TemplateError: If a required template cannot be loaded or
            rendered.
    """
    # Webhook content is data passed into fixed local templates, never template source.
    html_body = _ENVIRONMENT.get_template("verification_code.html").render(
        code=code
    )
    text_body = _ENVIRONMENT.get_template("verification_code.txt").render(
        code=code
    )
    return html_body, text_body
