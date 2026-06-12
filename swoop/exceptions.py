"""Custom exception types for swoop."""


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


# Subset of the canonical gRPC status codes Google has been observed to return
# in the ErrorResponse envelope. Used only to label the exception message.
_GRPC_CODE_NAMES = {
    3: "INVALID_ARGUMENT",
    7: "PERMISSION_DENIED",
    8: "RESOURCE_EXHAUSTED",
    13: "INTERNAL",
    14: "UNAVAILABLE",
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

    def __init__(
        self,
        grpc_code: int | None,
        type_url: str | None = None,
        message: str | None = None,
    ):
        self.grpc_code = grpc_code
        self.type_url = type_url
        if message is None:
            if grpc_code is not None:
                name = _GRPC_CODE_NAMES.get(grpc_code)
                label = f"{grpc_code} {name}" if name else str(grpc_code)
            else:
                label = "unknown"
            message = (
                f"Google Flights rejected the request (gRPC status {label}). "
                "This is an upstream error, not an empty result."
            )
        super().__init__(message)
