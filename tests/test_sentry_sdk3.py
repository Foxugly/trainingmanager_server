"""sentry-sdk 3.x configuration, exercised through a real client.

``sentry_options`` is what production passes to ``sentry_sdk.init``; a capture
transport collects the envelopes instead of sending them.
"""

import importlib
import logging
import warnings

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

_sentry = importlib.import_module("django-trainingmanager.settings.sentry")
_scrub = importlib.import_module("django-trainingmanager.settings.sentry_scrub")
before_send, sentry_options = _sentry.before_send, _sentry.sentry_options
FILTERED, scrub_event, scrub_span = _scrub.FILTERED, _scrub.scrub_event, _scrub.scrub_span
scrub_text = _scrub.scrub_text

_SECRET_ROUTE_EXAMPLES = [
    ("/api/v1/public/events/tok/", "/api/v1/public/events/[Filtered]/"),
    ("/api/v1/rsvp-magic/tok/", "/api/v1/rsvp-magic/[Filtered]/"),
    ("/api/v1/team/invitations/lookup/tok/", "/api/v1/team/invitations/lookup/[Filtered]/"),
    ("/api/v1/team/join-magic/tok/", "/api/v1/team/join-magic/[Filtered]/"),
    ("/api/v1/calendar/tok-_x.ics", "/api/v1/calendar/[Filtered]"),
    ("/api/v1/me/calendar-token/rotate/", "/api/v1/me/calendar-token/rotate/"),
]
_OPTIONS = {"dsn": "https://public@o0.ingest.sentry.io/1", "environment": "test", "release": "test"}


class _CaptureTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)

    def items(self, kind):
        return [
            item.payload.json
            for envelope in self.envelopes
            for item in envelope.items
            if item.type == kind
        ]


@pytest.fixture
def sentry(request):
    traces_sample_rate = getattr(request, "param", 0.0)
    transport = _CaptureTransport()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        sentry_sdk.init(
            transport=transport,
            **sentry_options(**_OPTIONS, traces_sample_rate=traces_sample_rate),
        )
    transport.init_warnings = caught
    try:
        yield transport
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)


def test_init_works_and_collects_no_pii(sentry):
    client = sentry_sdk.get_client()
    assert client.is_active()
    resolved = client.options["data_collection"]
    assert resolved["user_info"] is False
    assert resolved["cookies"] == {"mode": "off"}
    assert resolved["http_bodies"] == []
    assert resolved["queues"] is False
    assert resolved["database_query_data"] is False
    assert resolved["gen_ai"] == {"inputs": False, "outputs": False}
    assert resolved["mcp"] == {"inputs": False, "outputs": False}
    assert resolved["stack_frame_variables"]["mode"] == "denylist"
    assert client.options["attach_stacktrace"] is False
    assert "logging" in client.integrations


def test_init_raises_no_deprecation_warning(sentry):
    deprecations = [
        str(w.message)
        for w in sentry.init_warnings
        if issubclass(w.category, (DeprecationWarning, PendingDeprecationWarning, FutureWarning))
    ]
    assert deprecations == []


def test_logger_error_produces_an_event_but_info_ships_nothing(sentry):
    logger = logging.getLogger("sentry3.test")
    logger.info("routine information")
    logger.error("something broke")
    sentry_sdk.get_client().flush(timeout=2)
    events = sentry.items("event")
    assert [e["logentry"]["message"] for e in events] == ["something broke"]
    assert events[0]["level"] == "error"
    # No Sentry Logs (3.0 would ship INFO and up with the integration's default).
    assert sentry.items("log") == []


def _raise_with_secrets():
    # Built at runtime so the values do not appear in the source context lines.
    password = "hunter" + "2"  # noqa: F841 - captured as a frame variable
    api_token = "tok-" + "123"  # noqa: F841
    page = 3  # noqa: F841
    raise ValueError("boom")


def test_before_send_masks_extra_and_local_variables(sentry):
    with sentry_sdk.new_scope() as scope:
        scope.set_extra("token", "tok-" + "456")
        scope.set_extra("page", 2)
        try:
            _raise_with_secrets()
        except ValueError:
            sentry_sdk.capture_exception()
    (event,) = sentry.items("event")
    assert event["extra"]["token"] == FILTERED
    assert event["extra"]["page"] == 2
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]
    variables = next(f["vars"] for f in frames if f["function"] == "_raise_with_secrets")
    assert variables["password"] == FILTERED
    assert variables["api_token"] == FILTERED
    assert variables["page"] == "3"
    text = repr(event)
    assert "hunter2" not in text and "tok-123" not in text and "tok-456" not in text


def test_scrub_event_masks_request_data_headers_user_and_breadcrumbs():
    event = {
        "request": {
            "url": "https://example.test/reset?token=abc&page=2",
            "query_string": "password=pw&page=2",
            "headers": {"Authorization": "Bearer x", "Accept": "*/*"},
            "cookies": {"sessionid": "s"},
            "data": {"password": "hunter2", "nested": {"api_key": "k"}, "title": "ok"},
        },
        "extra": {"secret": "s", "page": 3, "note": "Bearer abc.def"},
        "user": {"id": "7", "ip_address": "203.0.113.9"},
        "breadcrumbs": {"values": [{"data": {"token": "abc", "n": 1}}]},
        "exception": {
            "values": [{"stacktrace": {"frames": [{"vars": {"passwd": "p", "i": "1"}}]}}]
        },
    }
    out = scrub_event(event, {})
    request = out["request"]
    assert request["url"] == "https://example.test/reset?token=[Filtered]&page=2"
    assert request["query_string"] == "password=[Filtered]&page=2"
    assert request["headers"] == {"Authorization": FILTERED, "Accept": "*/*"}
    assert request["cookies"] == FILTERED
    assert request["data"]["password"] == FILTERED
    assert request["data"]["nested"]["api_key"] == FILTERED
    assert request["data"]["title"] == "ok"
    assert out["extra"] == {"secret": FILTERED, "page": 3, "note": "Bearer [Filtered]"}
    assert out["user"] == {"id": "7"}
    assert out["breadcrumbs"]["values"][0]["data"] == {"token": FILTERED, "n": 1}
    frame = out["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame["vars"] == {"passwd": FILTERED, "i": "1"}


class _Boom(dict):
    def items(self):
        raise RuntimeError("unexpected")

    def get(self, *args):
        raise RuntimeError("unexpected")


def test_scrub_event_never_raises_and_strips_on_unexpected_shape():
    frame = {"vars": {"password": "x"}}
    event = {
        "extra": _Boom(password="x"),
        "request": {"data": "x"},
        "exception": {"values": [{"stacktrace": {"frames": [frame]}}]},
        "message": "m",
    }
    out = scrub_event(event, {})
    assert set(out) == {"exception", "message"}
    assert frame == {}
    # Even the frames cannot be walked: the stack traces go too.
    out = scrub_event({"extra": _Boom(), "exception": _Boom(), "message": "m"}, {})
    assert out == {"message": "m"}


def test_scrub_span_never_raises():
    out = scrub_span({"name": "GET /?token=abc", "attributes": _Boom(token="abc")}, {})
    assert out == {"name": FILTERED, "attributes": {}}


def test_before_send_span_masks_name_and_attributes():
    span = {
        "name": "GET /api/reset/?token=abc123&page=2",
        "attributes": {
            "http.request.header.authorization": ["Bearer xyz"],
            "url.full": "https://example.test/?password=pw&page=2",
            "db.query.text": "SELECT 1",
            "sentry.op": "http.server",
        },
    }
    out = scrub_span(span, {})
    assert out["name"] == "GET /api/reset/?token=[Filtered]&page=2"
    attributes = out["attributes"]
    assert attributes["http.request.header.authorization"] == FILTERED
    assert attributes["url.full"] == "https://example.test/?password=[Filtered]&page=2"
    assert attributes["db.query.text"] == "SELECT 1"
    assert attributes["sentry.op"] == "http.server"


@pytest.mark.parametrize("sentry", [1.0], indirect=True)
def test_before_send_span_runs_on_real_spans(sentry):
    with sentry_sdk.start_span(name="job ?token=abc", attributes={"api_key": "k", "n": 1}):
        pass
    sentry_sdk.get_client().flush(timeout=2)
    spans = [
        span
        for container in sentry.items("span")
        for span in (container.get("items") if isinstance(container, dict) else None) or [container]
    ]
    assert spans, "no span envelope captured"
    text = repr(spans)
    assert "abc" not in text and "'k'" not in text
    assert "[Filtered]" in text


def test_response_headers_are_not_collected():
    options = sentry_options(**_OPTIONS, traces_sample_rate=0.0)
    headers = options["data_collection"]["http_headers"]
    assert headers["response"] == {"mode": "off"}


def test_scrub_event_masks_contexts_and_embedded_spans():
    event = {
        "contexts": {
            "response": {
                "status_code": 200,
                "headers": {"Set-Cookie": "sessionid=abc", "Authorization": "x", "Vary": "Cookie"},
            },
            "trace": {"trace_id": "0123456789abcdef", "data": {"auth_token": "t", "n": 1}},
        },
        "spans": [
            {
                "description": "GET https://example.test/?token=abc",
                "name": "GET /reset?password=pw",
                "data": {"password": "pw", "url": "https://u:p@example.test/x", "n": 1},
                "attributes": {"http.request.header.cookie": "c"},
            }
        ],
    }
    out = scrub_event(event, {})
    response = out["contexts"]["response"]
    assert response["headers"] == {
        "Set-Cookie": FILTERED,
        "Authorization": FILTERED,
        "Vary": "Cookie",
    }
    assert response["status_code"] == 200
    trace = out["contexts"]["trace"]
    assert trace["data"] == {"auth_token": FILTERED, "n": 1}
    assert trace["trace_id"] == "0123456789abcdef"
    (span,) = out["spans"]
    assert span["description"] == "GET https://example.test/?token=[Filtered]"
    assert span["name"] == "GET /reset?password=[Filtered]"
    assert span["data"] == {"password": FILTERED, "url": "https://example.test/x", "n": 1}
    assert span["attributes"] == {"http.request.header.cookie": FILTERED}


def test_scrub_event_masks_messages():
    event = {
        "logentry": {
            "message": "GET %s failed",
            "params": ["/x?token=abc"],
            "formatted": "GET /x?token=abc failed",
        },
        "message": "broker redis://:pw@127.0.0.1:6379/1 down",
        "exception": {"values": [{"type": "E", "value": "bad https://u:p@example.test/?secret=s"}]},
        "breadcrumbs": {"values": [{"message": "Bearer abc.def"}]},
    }
    out = scrub_event(event, {})
    assert out["logentry"]["params"] == ["/x?token=[Filtered]"]
    assert out["logentry"]["formatted"] == "GET /x?token=[Filtered] failed"
    assert out["message"] == "broker redis://127.0.0.1:6379/1 down"
    assert out["exception"]["values"][0]["value"] == "bad https://example.test/?secret=[Filtered]"
    assert out["breadcrumbs"]["values"][0]["message"] == "Bearer [Filtered]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://user:pass@example.test/a?b=1", "https://example.test/a?b=1"),
        ("redis://:secretpw@127.0.0.1:6379/2", "redis://127.0.0.1:6379/2"),
        ("postgres://app@db.internal:5432/app", "postgres://db.internal:5432/app"),
        ("https://example.test:99999999/x@y", "https://[Filtered]/x@y"),  # bad port: fail closed
        ("https://u:p@[::1/x", "https://[Filtered]/x"),
        ("https://user:pass/x", "https://[Filtered]/x"),
        ("https://user:pass@host?q=1", "https://host?q=1"),
        ("https://user@host#frag", "https://host#[Filtered]"),
        ("https://[::1]:8000/x", "https://[::1]:8000/x"),
        ("see https://example.test/plain", "see https://example.test/plain"),
    ],
)
def test_scrub_text_drops_url_userinfo(text, expected):
    assert scrub_text(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/api/v1/x/AbCdEf0123456789XyZ_-abcdef/", "/api/v1/x/[Filtered]/"),
        ("GET /x/q1w2e3r4t5y6u7i8o9p0a1s2d3?n=1", "GET /x/[Filtered]?n=1"),
        ("/api/v1/course/42/sections/", "/api/v1/course/42/sections/"),
        (
            "/api/v1/notification-preferences-overview/",
            "/api/v1/notification-preferences-overview/",
        ),
        ("/api/v1/reset/<str:token>/", "/api/v1/reset/<str:token>/"),
    ],
)
def test_scrub_text_masks_long_opaque_path_segments(text, expected):
    assert scrub_text(text) == expected


@pytest.mark.parametrize(("text", "expected"), _SECRET_ROUTE_EXAMPLES)
def test_scrub_text_masks_secret_route_segments(text, expected):
    assert scrub_text(text) == expected


def test_scrub_event_masks_exception_and_thread_values():
    event = {
        "exception": {"values": [{"value": "GET https://example.test/?api_key=k failed"}]},
        "threads": {"values": [{"value": "token=abc"}]},
    }
    out = scrub_event(event, {})
    value = out["exception"]["values"][0]["value"]
    assert value == "GET https://example.test/?api_key=[Filtered] failed"
    assert out["threads"]["values"][0]["value"] == "token=[Filtered]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "https://example.test/cb?state=abcdefghijklmnopqrstuv&page=2",
            "https://example.test/cb?state=[Filtered]&page=2",
        ),
        (
            "GET /cb?X-Access=k&x-Auth-Token=t&lang=fr",
            "GET /cb?X-Access=k&x-Auth-Token=[Filtered]&lang=fr",
        ),
        ("/cb?api%5Fkey=v", "/cb?api%5Fkey=[Filtered]"),
        ("https://example.test/app#access_token=abc", "https://example.test/app#[Filtered]"),
        ("'/reset#k'", "'/reset#[Filtered]'"),
        ("/x?page=2&sort=name", "/x?page=2&sort=name"),
        ("issue #12 on /x", "issue #12 on /x"),
    ],
)
def test_scrub_text_masks_query_values_and_fragments(text, expected):
    assert scrub_text(text) == expected


def test_scrub_event_masks_opaque_query_string_values():
    event = {"request": {"query_string": "state=abcdefghijklmnopqrstuv&page=2&code=c"}}
    out = scrub_event(event, {})
    assert out["request"]["query_string"] == "state=[Filtered]&page=2&code=[Filtered]"


def test_scrub_event_fails_closed_on_tuples_sets_and_depth():
    deep = {"n": 1}
    for _ in range(20):
        deep = {"child": deep}
    event = {
        "extra": {
            "pair": ("token=abc", {"password": "p"}),
            "tags": {"secret=s", "ok"},
            "frozen": frozenset({"Bearer x"}),
            "deep": deep,
        }
    }
    extra = scrub_event(event, {})["extra"]
    assert extra["pair"] == ("token=[Filtered]", {"password": FILTERED})
    assert extra["tags"] == {"secret=[Filtered]", "ok"}
    assert extra["frozen"] == frozenset({"Bearer [Filtered]"})
    node = extra["deep"]
    while isinstance(node, dict):
        node = node["child"]
    assert node == FILTERED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/x?abcdefghij0123456789&page=2", "/x?[Filtered]&page=2"),
        ("/x?abcdefghijklmnop0123=1&page=2", "/x?[Filtered]&page=2"),
        ("/x?token&page=2", "/x?[Filtered]&page=2"),
        ("/x?t%6Fken=abc&page=2", "/x?t%6Fken=[Filtered]&page=2"),
        ("/x?next=%2Freset%3Ftoken%3Dabc&page=2", "/x?next=[Filtered]&page=2"),
        ("/x?q=Bearer+abc&page=2", "/x?q=[Filtered]&page=2"),
        ("/x?utm_campaign_name=spring&page=2", "/x?utm_campaign_name=spring&page=2"),
    ],
)
def test_scrub_query_masks_opaque_names_valueless_chunks_and_encoded_secrets(text, expected):
    assert scrub_text(text) == expected


def test_scrub_event_never_skips_a_value_because_of_its_type():
    class Opaque:
        def __repr__(self):
            return "token=abc"

    event = {
        "request": {
            "query_string": b"token=abc&page=2",
            "url": ["https://u:p@example.test/?secret=s"],
        },
        "extra": {
            "raw": b"Bearer abc",
            "obj": Opaque(),
            "pairs": [("token", "abc"), ("page", "2")],
            "pair_list": ["api_key", "k"],
            "when": __import__("datetime").datetime(2026, 11, 23),
        },
        "exception": {"values": [{"value": ["password=pw"]}]},
        "message": b"secret=s",
    }
    out = scrub_event(event, {})
    assert out["request"]["query_string"] == "token=[Filtered]&page=2"
    assert out["request"]["url"] == ["https://example.test/?secret=[Filtered]"]
    extra = out["extra"]
    assert extra["raw"] == "Bearer [Filtered]"
    assert extra["obj"] == FILTERED
    assert extra["pairs"] == [("token", FILTERED), ("page", "2")]
    assert extra["pair_list"] == ["api_key", FILTERED]
    assert extra["when"].year == 2026
    assert out["exception"]["values"][0]["value"] == ["password=[Filtered]"]
    assert out["message"] == "secret=[Filtered]"


def test_scrub_span_scrubs_non_string_fields():
    span = {"name": b"GET /x?token=abc", "attributes": {"tags": ("secret=s",), "n": 3}}
    out = scrub_span(span, {})
    assert out["name"] == "GET /x?token=[Filtered]"
    assert out["attributes"] == {"tags": ("secret=[Filtered]",), "n": 3}


def test_before_send_scrubs_then_drops_disallowed_host():
    from django.core.exceptions import DisallowedHost

    exc = DisallowedHost("Invalid HTTP_HOST header: ''.")
    assert before_send({}, {"exc_info": (DisallowedHost, exc, None)}) is None
    assert before_send({"logger": "django.security.DisallowedHost"}, {}) is None
    event = before_send({"extra": {"secret": "s", "n": 1}}, {})
    assert event["extra"] == {"secret": FILTERED, "n": 1}
