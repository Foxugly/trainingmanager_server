"""Guard against translation drift in the shipped .po catalogs.

For each locale we assert the catalog is COMPLETE:
  - no untranslated entry (an empty ``msgstr ""`` other than the header), and
  - no ``#, fuzzy`` flagged entry.

The catalogs are currently complete; this test keeps them that way as new
source strings are added. ``polib`` is not a project dependency, so we use a
minimal hand-rolled parser that understands single- and multi-line
msgid/msgstr blocks and msgctxt-qualified entries.
"""

import re
from pathlib import Path

import pytest

LOCALES = ["fr", "nl", "it", "es"]
LOCALE_ROOT = Path(__file__).resolve().parent.parent / "locale"


def _po_path(locale):
    return LOCALE_ROOT / locale / "LC_MESSAGES" / "django.po"


def _parse_po(text):
    """Yield (is_fuzzy, msgid, msgstr) tuples for every entry in a .po file.

    Handles continuation lines (a string spanning several quoted lines) and
    flag comments (``#, fuzzy``). The header entry (empty msgid) is yielded
    too; callers skip it.
    """
    entries = []
    flags = set()
    msgid_parts = None
    msgstr_parts = None
    state = None  # None | "msgid" | "msgstr"

    def _unquote(line):
        m = re.match(r'\s*"(.*)"\s*$', line)
        return m.group(1) if m else ""

    def _flush():
        nonlocal msgid_parts, msgstr_parts, flags, state
        if msgid_parts is not None and msgstr_parts is not None:
            entries.append(
                ("fuzzy" in flags, "".join(msgid_parts), "".join(msgstr_parts))
            )
        msgid_parts = None
        msgstr_parts = None
        flags = set()
        state = None

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("#,"):
            for flag in line[2:].split(","):
                flags.add(flag.strip())
            continue
        if line.startswith("#") or line.strip() == "":
            # Comment or blank line ends the current entry.
            if line.strip() == "":
                _flush()
            continue
        if line.startswith("msgctxt "):
            _flush()
            continue
        if line.startswith("msgid_plural "):
            state = "msgstr_plural"
            continue
        if line.startswith("msgid "):
            _flush()
            msgid_parts = [_unquote(line[len("msgid "):])]
            state = "msgid"
            continue
        if line.startswith("msgstr["):
            # Plural form: treat index 0 as the representative translation.
            idx = line[len("msgstr["):].split("]")[0]
            rest = line.split("]", 1)[1].strip()
            if idx == "0":
                msgstr_parts = [_unquote(rest)]
                state = "msgstr"
            else:
                state = "msgstr_other"
            continue
        if line.startswith("msgstr "):
            msgstr_parts = [_unquote(line[len("msgstr "):])]
            state = "msgstr"
            continue
        if line.startswith('"'):
            if state == "msgid":
                msgid_parts.append(_unquote(line))
            elif state == "msgstr":
                msgstr_parts.append(_unquote(line))
            continue

    _flush()
    return entries


@pytest.mark.parametrize("locale", LOCALES)
def test_po_catalog_is_complete(locale):
    path = _po_path(locale)
    assert path.exists(), f"missing catalog: {path}"
    entries = _parse_po(path.read_text(encoding="utf-8"))

    untranslated = []
    fuzzy = []
    for is_fuzzy, msgid, msgstr in entries:
        if msgid == "":
            continue  # header
        if is_fuzzy:
            fuzzy.append(msgid)
        if msgstr == "":
            untranslated.append(msgid)

    assert not untranslated, (
        f"{locale}: {len(untranslated)} untranslated entr(y/ies): "
        f"{untranslated[:5]}"
    )
    assert not fuzzy, (
        f"{locale}: {len(fuzzy)} fuzzy entr(y/ies): {fuzzy[:5]}"
    )
