"""HTML sanitization for Note.content via bleach.

Whitelist tags suitable for the rich text editor used on the
frontend (PrimeNG / Quill style). All disallowed tags and attributes
are stripped on save.
"""

import html

import bleach

ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "ul",
    "ol",
    "li",
    "blockquote",
    "code",
    "pre",
    "h1",
    "h2",
    "h3",
    "h4",
    "a",
    "span",
]

ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "span": ["class"],
}

ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def sanitize_html(html):
    """Sanitize HTML coming from a rich text editor.

    Returns the cleaned HTML, or '' for None / empty input.
    """
    if not html:
        return ""
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )


def strip_html(value):
    """Return the plain-text of an HTML string (all tags removed).

    Strips every tag (``bleach.clean`` with no allowed tags) and then
    unescapes HTML entities so the result is human-readable plain text.
    Suitable for plain-text contexts (iCal DESCRIPTION, AI prompts) where
    the rich-text HTML must not leak as markup. None / '' -> ''.
    """
    if not value:
        return ""
    return html.unescape(bleach.clean(value, tags=[], strip=True))
