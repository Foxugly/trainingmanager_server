"""HTML sanitization for Note.content via bleach.

Whitelist tags suitable for the rich text editor used on the
frontend (PrimeNG / Quill style). All disallowed tags and attributes
are stripped on save.
"""

import html

import bleach
from bleach.html5lib_shim import Filter

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


class _ForceNoopenerFilter(Filter):
    """Force ``rel="noopener noreferrer"`` on any ``<a>`` that carries a
    ``target`` attribute.

    bleach allows ``target`` (so links can open a new tab) but does not inject
    ``rel`` — a ``target="_blank"`` link without it lets the opened page reach
    back via ``window.opener`` (reverse tabnabbing). This post-sanitize filter
    closes that on every anchor that opens a new context.
    """

    def __iter__(self):
        for token in super().__iter__():
            if token.get("type") in ("StartTag", "EmptyTag") and token.get("name") == "a":
                data = token.get("data") or {}
                if any(name == "target" for _ns, name in data):
                    rel_key = next(
                        (k for k in data if k[1] == "rel"), (None, "rel")
                    )
                    data[rel_key] = "noopener noreferrer"
                    token["data"] = data
            yield token


_CLEANER = bleach.sanitizer.Cleaner(
    tags=ALLOWED_TAGS,
    attributes=ALLOWED_ATTRIBUTES,
    protocols=ALLOWED_PROTOCOLS,
    strip=True,
    filters=[_ForceNoopenerFilter],
)


def sanitize_html(html):
    """Sanitize HTML coming from a rich text editor.

    Returns the cleaned HTML, or '' for None / empty input. Any ``<a target>``
    is forced to ``rel="noopener noreferrer"`` (anti reverse-tabnabbing).
    """
    if not html:
        return ""
    return _CLEANER.clean(html)


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
