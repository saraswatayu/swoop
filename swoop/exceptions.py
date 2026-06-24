"""Custom exception types for swoop.

Error-handling contract
=======================
swoop talks to an undocumented, sometimes-flaky upstream, so every public entry
point follows one rule: **a failure the caller must act on is observable — as a
typed exception or in the return value — never as a silent empty result or a log
line.** The specifics:

1. Single-shot calls raise. A structured upstream rejection (an HTTP 200 whose
   body carries a ``travel.frontend.flights.ErrorResponse`` envelope with a gRPC
   status code) on a one-fetch call — ``search``, ``search_legs``,
   ``check_price``, ``price_selector``, ``price_legs``, ``deals``, ``explore``,
   ``get_booking_results`` — raises :class:`SwoopUpstreamError`. A genuinely
   empty result (no flights) is *not* an error: it returns an empty result.

2. Aggregate / fan-out calls degrade, but not silently. The multi-city beam in
   ``search`` keeps the chains it found and raises only if *every* branch was
   rejected upstream. ``price_explore_all`` prices what it can and raises only
   if *every* destination was rejected upstream; a partial outage leaves the
   affected slots ``None`` (logged).

3. Confirmed vs estimated price is observable. ``PriceResult.is_estimate`` is
   ``True`` when the price is the search-derived shopping figure rather than a
   confirmed bookable fare.

4. Retry is the caller's policy. ``transport.retries`` governs HTTP 429 backoff;
   upstream ErrorResponse codes are surfaced (with ``grpc_code``) so a caller
   can apply its own retry rather than swoop hard-coding one — a default that
   retried during a broad outage would turn a fleet of clients into a retry
   storm and deepen the upstream's gating of everyone.

``SwoopUpstreamError.grpc_code`` carries the status; ``_GRPC_CODE_NAMES`` labels
the known codes.
"""


class SwoopError(Exception):
    """Base exception for all swoop errors."""


class SwoopHTTPError(SwoopError):
    """Raised when Google Flights returns a non-200 HTTP response.

    Attributes:
        status_code: The HTTP status code returned.
    """

    def __init__(self, status_code: int, message: str | None = None):
        self.status_code = status_code
        if message is None:
            message = f"Google Flights returned HTTP {status_code}"
        super().__init__(message)


class SwoopRateLimitError(SwoopHTTPError):
    """Raised when Google Flights returns HTTP 429 (Too Many Requests)."""

    def __init__(self) -> None:
        super().__init__(
            429,
            "Google Flights rate limit hit (HTTP 429). "
            "Wait a few minutes before retrying.",
        )


class SwoopParseError(SwoopError):
    """Raised when the response from Google Flights cannot be parsed."""


# The canonical gRPC status codes (0-16). Google's ErrorResponse envelope
# carries one of these; the table is used only to label the exception message.
# Listing all of them (not just the observed subset) keeps an unexpected code
# like 4 DEADLINE_EXCEEDED or 2 UNKNOWN from rendering as a bare number.
_GRPC_CODE_NAMES = {
    0: "OK",
    1: "CANCELLED",
    2: "UNKNOWN",
    3: "INVALID_ARGUMENT",
    4: "DEADLINE_EXCEEDED",
    5: "NOT_FOUND",
    6: "ALREADY_EXISTS",
    7: "PERMISSION_DENIED",
    8: "RESOURCE_EXHAUSTED",
    9: "FAILED_PRECONDITION",
    10: "ABORTED",
    11: "OUT_OF_RANGE",
    12: "UNIMPLEMENTED",
    13: "INTERNAL",
    14: "UNAVAILABLE",
    15: "DATA_LOSS",
    16: "UNAUTHENTICATED",
}


class SwoopUpstreamError(SwoopError):
    """Raised when Google Flights rejects a request with a structured error.

    Google answers with HTTP 200 but, instead of a result payload, returns a
    ``travel.frontend.flights.ErrorResponse`` envelope carrying a gRPC status
    code. swoop surfaces this as an explicit error so callers can tell
    "Google rejected the request" apart from "there are genuinely no flights"
    (the latter decodes to an empty result, not an exception).

    Attributes:
        grpc_code: The gRPC status code from the error envelope (e.g. 13 =
            INTERNAL). ``None`` if it could not be extracted.
        type_url: The protobuf type URL Google attached, when present.
    """

    def __init__(self, grpc_code: int | None, type_url: str | None = None):
        self.grpc_code = grpc_code
        self.type_url = type_url
        if grpc_code is None:
            label = "unknown"
        else:
            name = _GRPC_CODE_NAMES.get(grpc_code)
            label = f"{grpc_code} {name}" if name else str(grpc_code)
        super().__init__(
            f"Google Flights rejected the request (gRPC status {label}). "
            "This is an upstream error, not an empty result."
        )
