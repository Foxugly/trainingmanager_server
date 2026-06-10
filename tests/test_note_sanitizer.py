"""Unit tests for the bleach-based HTML sanitizer used on Note.content."""

from tools.html_sanitizer import sanitize_html


def test_strips_script_tags():
    out = sanitize_html('<p>Hello</p><script>alert("xss")</script>')
    assert "<script>" not in out
    assert "<p>Hello</p>" in out


def test_keeps_allowed_tags():
    out = sanitize_html("<p><strong>Bold</strong> <em>italic</em></p>")
    assert "<strong>" in out
    assert "<em>" in out


def test_strips_dangerous_attrs():
    out = sanitize_html('<a href="http://x.com" onclick="bad()">link</a>')
    assert "onclick" not in out
    assert 'href="http://x.com"' in out


def test_empty_input_returns_empty():
    assert sanitize_html("") == ""
    assert sanitize_html(None) == ""


def test_strips_javascript_protocol():
    out = sanitize_html('<a href="javascript:alert(1)">link</a>')
    assert "javascript:" not in out


def test_keeps_lists():
    out = sanitize_html("<ul><li>one</li><li>two</li></ul>")
    assert "<ul>" in out
    assert "<li>one</li>" in out


def test_strips_iframe():
    out = sanitize_html('<p>safe</p><iframe src="evil.com"></iframe>')
    assert "<iframe" not in out
    assert "<p>safe</p>" in out


def test_utf8_content_is_preserved():
    out = sanitize_html("<p>Salut, écoute le café résumé naïveté</p>")
    assert "écoute" in out
    assert "café" in out


def test_target_blank_link_gets_rel_noopener():
    """A link opening a new tab must carry rel=noopener noreferrer
    (anti reverse-tabnabbing) even when the author didn't supply it."""
    out = sanitize_html('<a href="https://x.com" target="_blank">link</a>')
    assert 'target="_blank"' in out
    assert "noopener" in out
    assert "noreferrer" in out


def test_target_blank_link_rel_is_overwritten():
    """An author-supplied rel on a target link is replaced, not trusted."""
    out = sanitize_html('<a href="https://x.com" target="_blank" rel="opener">link</a>')
    assert "noopener noreferrer" in out
    assert ">opener<" not in out  # the bare 'opener' rel value is gone


def test_link_without_target_is_left_alone():
    out = sanitize_html('<a href="https://x.com">link</a>')
    assert "noopener" not in out
