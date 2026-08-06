"""Rate-limit handling for a Korean broker REST API.

Extracted from a collector that pulled several gigabytes of minute bars. It is here
because of one behaviour that cannot be derived from any specification and was only
found by running against the real endpoint:

**The API signals rate-limiting inside a 200 OK response.**

There is no 429 status, no `Retry-After` header, and no error field that a generic HTTP
client would notice. The body comes back with a non-zero `return_code` and a Korean
message containing 허용된 요청 개수 — "the permitted number of requests". A client that
checks `response.status` sees success, parses the body, gets no rows, and moves on. The
collector then writes a gap into the panel that looks exactly like a quiet trading day.

That failure mode is the reason this module exists rather than a `requests` call: a
silent partial collection is worse than a crash, because the resulting hole is
indistinguishable from real data downstream.

The fix is to normalise it — detect the condition and re-raise it as the 429 the
protocol should have sent, so it flows into the same backoff path as a real one. That
is what `raise_for_api_error` does, and it is deliberately pure so it can be tested
without a network.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError

RATE_LIMIT_MARKER = "허용된 요청 개수"
"""Substring the broker returns in a 200 OK body when the caller is being throttled.

Matched on the message rather than on a code, because the code varies by endpoint while
this phrase has been stable across every endpoint observed.
"""

OK_RETURN_CODES = frozenset({"0", "000000", ""})
"""Return codes that mean success. The API uses several representations of zero."""


class _BodyAsResponse:
    """Minimal file-like wrapper so a synthesised HTTPError carries the original body.

    Without this the re-raised error loses the broker's own message, and a maintainer
    reading the log sees a 429 with no evidence of where it came from.
    """

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> bytes:
        return self._text.encode("utf-8")

    def close(self) -> None:  # pragma: no cover - interface completeness
        pass


def raise_for_api_error(payload: dict[str, Any]) -> None:
    """Inspect a decoded 200 OK body and raise if it is actually an error.

    :param payload: the parsed JSON body of a response the transport considered
        successful.
    :raise HTTPError: with ``code == 429`` when the body carries the rate-limit marker,
        so callers can treat it identically to a real 429 and back off.
    :raise RuntimeError: for any other non-zero return code, carrying the broker's
        message. Deliberately not swallowed — an unrecognised API error must not
        degrade into an empty result set.
    """
    return_code = str(payload.get("return_code", "0"))
    if return_code in OK_RETURN_CODES:
        return

    message = payload.get("return_msg") or payload.get("msg") or payload
    if RATE_LIMIT_MARKER in str(message):
        raise HTTPError(
            url="",
            code=429,
            msg="broker rate limit, reported inside a 200 OK body",
            hdrs={},  # type: ignore[arg-type]
            fp=_BodyAsResponse(json.dumps(payload, ensure_ascii=False)),
        )

    raise RuntimeError(f"broker API error {return_code}: {message}")


def backoff_seconds(attempt: int, base: float = 65.0, cap: float = 300.0) -> float:
    """Seconds to wait before retry number `attempt`.

    The base is deliberately long. The published limit is per-minute, so a short retry
    simply re-enters the same window and burns another request against it — which is how
    a naive exponential backoff starting at one second turns one throttled call into a
    dozen.

    :param attempt: zero-based retry index.
    :return: seconds to sleep, capped.
    """
    return min(base * (attempt + 1), cap)


def should_retry(status_code: int, attempt: int, max_retries: int) -> bool:
    """Whether a failed request should be retried.

    :param status_code: HTTP status, after normalisation by `raise_for_api_error`.
    :return: True only for rate limiting, and only while retries remain. Other 4xx
        responses are the caller's fault and retrying them wastes the quota that the
        rate limit is protecting.
    """
    return status_code == 429 and attempt < max_retries
