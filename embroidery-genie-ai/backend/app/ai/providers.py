"""Provider adapters.

One job: turn a prompt plus normalised artwork into text, token counts and a
request id. No retry logic, no caching, no budget awareness — those belong to
the gateway, and keeping them out of here is what stops a retry from escaping
the meter.

**SDK auto-retry is switched off deliberately.** Both SDKs retry 429s and 5xx
by default (twice each), which means a single ``create()`` can silently become
three billed requests that the ledger never sees. ``max_retries=0`` moves that
decision up to the gateway, where every attempt is counted, priced and subject
to the budget — which is the whole point of requirement "every retry is an
additional AI request and cost".
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

from app.ai.payload import NormalizedArtwork
from app.ai.routing import Provider

log = logging.getLogger(__name__)


@dataclass
class ProviderResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    #: True when the counts came from the provider rather than our estimate.
    measured: bool = False
    request_id: str | None = None
    stop_reason: str | None = None


class ProviderError(Exception):
    """A provider call failed.

    ``retryable`` is the only thing the gateway asks about. Invalid requests,
    auth failures and token-limit rejections are permanent: retrying them burns
    money to arrive at the same error.
    """

    def __init__(self, message: str, *, code: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ProviderUnavailable(ProviderError):
    def __init__(self, message: str):
        super().__init__(message, code="provider_unavailable", retryable=False)


# --------------------------------------------------------------- anthropic
def _classify_anthropic(exc: Exception) -> ProviderError:
    import anthropic

    name = type(exc).__name__
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError(str(exc), code="rate_limited", retryable=True)
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return ProviderError(str(exc), code="connection", retryable=True)
    if isinstance(exc, anthropic.InternalServerError):
        return ProviderError(str(exc), code="server_error", retryable=True)
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError(str(exc), code="auth_failed", retryable=False)
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderError(str(exc), code="permission_denied", retryable=False)
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError(str(exc), code="model_not_found", retryable=False)
    if isinstance(exc, anthropic.BadRequestError):
        # Includes "prompt is too long". Sending it again cannot help.
        return ProviderError(str(exc), code="invalid_request", retryable=False)
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", 0) or 0
        return ProviderError(str(exc), code=f"http_{status}", retryable=status >= 500)
    return ProviderError(f"{name}: {exc}", code="unknown", retryable=False)


def call_anthropic(
    *,
    api_key: str,
    model: str,
    prompt: str,
    artwork: NormalizedArtwork,
    max_output_tokens: int,
    timeout_seconds: int,
) -> ProviderResponse:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ProviderUnavailable("The anthropic SDK is not installed.") from exc

    client = anthropic.Anthropic(
        api_key=api_key,
        timeout=timeout_seconds,
        max_retries=0,  # see module docstring — the gateway owns retries
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_output_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": artwork.media_type,
                                "data": base64.b64encode(artwork.data).decode(),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - re-raised as a classified error
        raise _classify_anthropic(exc) from exc

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        raise ProviderError(
            "The model declined to describe this artwork.",
            code="refusal",
            retryable=False,
        )

    usage = getattr(response, "usage", None)
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    return ProviderResponse(
        text=text,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        measured=usage is not None,
        request_id=getattr(response, "_request_id", None),
        stop_reason=stop_reason,
    )


# ------------------------------------------------------------------ openai
def _classify_openai(exc: Exception) -> ProviderError:
    import openai

    if isinstance(exc, openai.RateLimitError):
        return ProviderError(str(exc), code="rate_limited", retryable=True)
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return ProviderError(str(exc), code="connection", retryable=True)
    if isinstance(exc, openai.InternalServerError):
        return ProviderError(str(exc), code="server_error", retryable=True)
    if isinstance(exc, openai.AuthenticationError):
        return ProviderError(str(exc), code="auth_failed", retryable=False)
    if isinstance(exc, openai.PermissionDeniedError):
        return ProviderError(str(exc), code="permission_denied", retryable=False)
    if isinstance(exc, openai.NotFoundError):
        return ProviderError(str(exc), code="model_not_found", retryable=False)
    if isinstance(exc, openai.BadRequestError):
        return ProviderError(str(exc), code="invalid_request", retryable=False)
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", 0) or 0
        return ProviderError(str(exc), code=f"http_{status}", retryable=status >= 500)
    return ProviderError(f"{type(exc).__name__}: {exc}", code="unknown", retryable=False)


def call_openai(
    *,
    api_key: str,
    model: str,
    prompt: str,
    artwork: NormalizedArtwork,
    max_output_tokens: int,
    timeout_seconds: int,
) -> ProviderResponse:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ProviderUnavailable("The openai SDK is not installed.") from exc

    client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
    data_uri = f"data:{artwork.media_type};base64,{base64.b64encode(artwork.data).decode()}"
    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_output_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - re-raised as a classified error
        raise _classify_openai(exc) from exc

    usage = getattr(response, "usage", None)
    choice = response.choices[0] if response.choices else None
    return ProviderResponse(
        text=(choice.message.content if choice else "") or "",
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        measured=usage is not None,
        request_id=getattr(response, "id", None),
        stop_reason=getattr(choice, "finish_reason", None) if choice else None,
    )


DISPATCH = {
    Provider.anthropic: call_anthropic,
    Provider.openai: call_openai,
}


def api_key_for(provider: Provider) -> str:
    from app.core.config import settings

    return (
        settings.anthropic_api_key
        if provider is Provider.anthropic
        else settings.openai_api_key
    )
