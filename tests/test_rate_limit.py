"""Rate limiting reported inside a 200 OK body.

The behaviour under test is not in any specification. It was found by running a
collector against a Korean broker's REST API for long enough to be throttled, and
noticing that the throttle arrives as a *successful* response whose body says, in
Korean, that the permitted number of requests has been exceeded.

Why it matters more than it sounds: a client that trusts the status code parses the
body, finds no rows, and records a gap. In a minute-bar panel that gap is
indistinguishable from a quiet trading day, so the corruption is silent and survives
every downstream check. Normalising it to a 429 is what turns a silent data hole into a
retry.

No network. The whole point of extracting this function was that it could be tested.
"""

from __future__ import annotations

from urllib.error import HTTPError

import pytest

from src.features.market_data.domain.rate_limit import (
    RATE_LIMIT_MARKER,
    backoff_seconds,
    raise_for_api_error,
    should_retry,
)


def test_rate_limit_inside_a_200_ok_body_becomes_a_429():
    """The behaviour that justifies this module existing at all."""
    payload = {
        "return_code": "8005",
        "return_msg": f"{RATE_LIMIT_MARKER}를 초과하였습니다.",
        "output": [],
    }
    with pytest.raises(HTTPError) as caught:
        raise_for_api_error(payload)
    assert caught.value.code == 429


def test_the_raised_error_carries_the_original_body():
    """A re-raised 429 with no evidence of its origin is unmaintainable.

    Without the body, a maintainer reading the log sees a 429 from an endpoint that
    never sends one and has nothing to go on.
    """
    payload = {"return_code": "8005", "return_msg": f"{RATE_LIMIT_MARKER} 초과"}
    with pytest.raises(HTTPError) as caught:
        raise_for_api_error(payload)
    body = caught.value.fp.read().decode("utf-8")
    assert RATE_LIMIT_MARKER in body


@pytest.mark.parametrize("code", ["0", "000000", ""])
def test_success_codes_pass_through(code):
    """The API uses several representations of zero; all mean success."""
    assert raise_for_api_error({"return_code": code, "output": [1, 2]}) is None


def test_a_missing_return_code_is_treated_as_success():
    """Absent means the endpoint does not use the field, not that it failed."""
    assert raise_for_api_error({"output": []}) is None


def test_an_unrecognised_api_error_raises_rather_than_returning_empty():
    """An unknown error must not degrade into an empty result set.

    This is the same silent-failure shape as the rate limit: a caller that receives
    `[]` cannot tell 'no data' from 'the request failed'.
    """
    with pytest.raises(RuntimeError, match="9999"):
        raise_for_api_error({"return_code": "9999", "return_msg": "unknown"})


def test_backoff_starts_long_because_the_limit_is_per_minute():
    """A one-second retry re-enters the same window and burns another request.

    This is why the base is 65 seconds rather than the exponential-from-one-second
    default that most HTTP clients ship with.
    """
    assert backoff_seconds(0) >= 60
    assert backoff_seconds(1) > backoff_seconds(0)
    assert backoff_seconds(99) <= 300, "capped"


def test_only_rate_limits_are_retried():
    """Retrying a 400 wastes the quota the rate limit exists to protect."""
    assert should_retry(429, attempt=0, max_retries=2)
    assert not should_retry(429, attempt=2, max_retries=2)
    for other in (400, 401, 403, 404, 500):
        assert not should_retry(other, attempt=0, max_retries=2)
