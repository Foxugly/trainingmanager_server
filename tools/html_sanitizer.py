"""HTML sanitization for rich-text fields (Note.content, messages, AI HTML).

Backed by nh3 (Rust/ammonia) — a maintained, fast, safe-by-default sanitizer.
Whitelist tags suit the frontend rich-text editor (PrimeNG / Quill style); all
disallowed tags/attributes are stripped on save. Every ``<a>`` is forced to
``rel="noopener noreferrer"`` (nh3 ``link_rel``) — anti reverse-tabnabbing.
"""

import html

import nh3

ALLOWED_TAGS = {
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
}

# nh3 manages <a rel> via link_rel below, so 'rel' is intentionally NOT here:
# any author-supplied rel is dropped and replaced with noopener noreferrer.
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "span": {"class"},
}

ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def sanitize_html(value):
    """Sanitize HTML coming from a rich text editor.

    Returns the cleaned HTML, or '' for None / empty input. Every ``<a>`` is
    forced to ``rel="noopener noreferrer"`` (anti reverse-tabnabbing).
    """
    if not value:
        return ""
    return nh3.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )


def strip_html(value):
    """Return the plain-text of an HTML string (all tags removed).

    Strips every tag (``nh3.clean`` with an empty tag allow-list) and then
    unescapes HTML entities so the result is human-readable plain text.
    Suitable for plain-text contexts (iCal DESCRIPTION, AI prompts, titles)
    where the rich-text HTML must not leak as markup. None / '' -> ''.
    """
    if not value:
        return ""
    return html.unescape(nh3.clean(value, tags=set()))
