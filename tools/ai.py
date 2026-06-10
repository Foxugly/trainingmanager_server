"""Low-level wrapper around the Anthropic Claude API.

Higher-level features (program generation, training generation, etc.)
should sit in domain-specific modules and call into this one.
"""

import logging

from anthropic import Anthropic, APIError, APITimeoutError, AuthenticationError
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)


_PROMPT_LOG_MAX_CHARS = 500


def truncate_for_log(text, max_chars=_PROMPT_LOG_MAX_CHARS):
    """Trim long prompts in log lines. The full prompt is persisted on
    Program.ai_prompt / Event.ai_prompt — logs only need a snippet for
    operational debugging, not the full copy."""
    if text is None:
        return ""
    s = str(text)
    if len(s) <= max_chars:
        return repr(s)
    return f"{s[:max_chars]!r}…[+{len(s) - max_chars} chars]"


class AIServiceError(APIException):
    status_code = 502
    default_detail = _("AI service unavailable. Please retry later.")
    default_code = "ai_service_error"


class AIConfigurationError(APIException):
    status_code = 500
    default_detail = _("AI service is not configured.")
    default_code = "ai_configuration_error"


def _get_client() -> Anthropic:
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise AIConfigurationError(_("ANTHROPIC_API_KEY is not set. Configure it in .env."))
    return Anthropic(
        api_key=api_key,
        timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
    )


def _create_message(client, **kwargs):
    """Call ``client.messages.create`` and map the Anthropic exception ladder to
    our AIConfigurationError (500) / AIServiceError (502). Shared by every
    Claude entry point so the mapping stays in one place."""
    try:
        return client.messages.create(**kwargs)
    except AuthenticationError as e:
        logger.error("Anthropic authentication failed: %s", e)
        raise AIConfigurationError(_("AI authentication failed. Check API key."))
    except APITimeoutError as e:
        logger.error("Anthropic API timeout: %s", e)
        raise AIServiceError(_("AI request timed out."))
    except APIError as e:
        logger.error("Anthropic API error: %s", e)
        raise AIServiceError(_("AI request failed."))
    except Exception:
        logger.exception("Unexpected error calling Anthropic")
        raise AIServiceError(_("Unexpected AI error."))


def call_claude(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    track_kwargs: dict | None = None,
) -> dict:
    """Send a single user prompt to Claude and return the response payload.

    If `track_kwargs` is provided (a dict with keys `team`, `user`,
    `endpoint`), the call is recorded into AIUsage. Tracking failures
    are logged but never raised.
    """
    client = _get_client()

    kwargs = {
        "model": model or settings.ANTHROPIC_MODEL_DEFAULT,
        "max_tokens": max_tokens or settings.ANTHROPIC_MAX_TOKENS_DEFAULT,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    response = _create_message(client, **kwargs)

    if track_kwargs is not None:
        from tools.ai_tracking import track_ai_usage

        track_ai_usage(response, model_used=response.model, **track_kwargs)

    text_content = ""
    for block in response.content:
        if hasattr(block, "text"):
            text_content += block.text

    return {
        "text": text_content,
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "stop_reason": response.stop_reason,
    }


def call_claude_with_tool(
    prompt: str,
    *,
    tool: dict,
    system: str | None = None,
    cached_prefix: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    track_kwargs: dict | None = None,
) -> dict:
    """Call Claude with a forced tool, guaranteeing a structured JSON payload.

    ``cached_prefix`` is stable, reusable context (e.g. the modality/energysegment
    catalogs and generic rules, identical across sessions of the same team). It is
    sent as the FIRST user content block with a cache breakpoint so the cached
    prefix becomes tools + system + prefix — large enough to clear the model's
    minimum cacheable size, which tools+system alone usually miss. The variable
    ``prompt`` (this session's specifics) follows, uncached.

    If `track_kwargs` is provided, the call is recorded into AIUsage
    even when the tool block is missing (the API call still consumed
    tokens). Tracking is best-effort and never raises.
    """
    client = _get_client()

    # Prompt caching: tool schema, system prompt and the stable catalog prefix
    # are identical across calls of the same generator (only the trailing user
    # prompt varies), so mark them cacheable. Below the model's minimum cacheable
    # size the markers are silently ignored — harmless.
    if cached_prefix:
        user_content = [
            {"type": "text", "text": cached_prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": prompt},
        ]
    else:
        user_content = prompt
    kwargs = {
        "model": model or settings.ANTHROPIC_MODEL_DEFAULT,
        "max_tokens": max_tokens or settings.ANTHROPIC_MAX_TOKENS_DEFAULT,
        "messages": [{"role": "user", "content": user_content}],
        "tools": [{**tool, "cache_control": {"type": "ephemeral"}}],
        "tool_choice": {"type": "tool", "name": tool["name"]},
    }
    if system:
        kwargs["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]

    logger.info(
        "Anthropic call_with_tool: model=%s max_tokens=%s tool=%r "
        "prefix_chars=%s prompt_chars=%s",
        kwargs["model"],
        kwargs["max_tokens"],
        tool["name"],
        len(cached_prefix or ""),
        len(prompt),
    )

    response = _create_message(client, **kwargs)

    cache_creation = getattr(response.usage, "cache_creation_input_tokens", None)
    cache_read = getattr(response.usage, "cache_read_input_tokens", None)
    logger.info(
        "Anthropic call_with_tool returned: model=%s stop_reason=%s "
        "tokens(in/out)=%s/%s cache(create/read)=%s/%s block_types=%s",
        response.model,
        response.stop_reason,
        response.usage.input_tokens,
        response.usage.output_tokens,
        cache_creation,
        cache_read,
        [getattr(b, "type", "?") for b in response.content],
    )

    if track_kwargs is not None:
        from tools.ai_tracking import track_ai_usage

        track_ai_usage(response, model_used=response.model, **track_kwargs)

    tool_use_block = None
    for block in response.content:
        if hasattr(block, "type") and block.type == "tool_use":
            tool_use_block = block
            break

    if tool_use_block is None:
        logger.warning(
            "AI did not call the expected tool %r. stop_reason=%r block_types=%s",
            tool["name"],
            response.stop_reason,
            [getattr(b, "type", "?") for b in response.content],
        )
        raise AIServiceError(_("AI did not call the expected tool."))

    return {
        "tool_input": tool_use_block.input,
        "tool_name": tool_use_block.name,
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_tokens": cache_creation or 0,
        "cache_read_tokens": cache_read or 0,
        "stop_reason": response.stop_reason,
    }
