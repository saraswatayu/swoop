"""Optional raw response boundary, separate from body-free diagnostics.

Observers are caller-owned: the SDK does not retain bodies, infer identities,
or attach them to results/errors. Callers must bound retention and redact at
their storage boundary. No URLs, request bodies, headers or credentials are
included in the event metadata.
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

ResponseOperation = Literal["shopping", "booking", "calendar", "deals", "explore", "other"]


@dataclass(frozen=True)
class ResponseBody:
    operation: ResponseOperation
    transport: Literal["rpc", "page"]
    method: Literal["POST", "GET"]
    retry_index: int
    status_code: int
    body: Optional[str] = field(repr=False)
    body_unavailable: bool = False


def response_operation(url: str) -> ResponseOperation:
    # Only the finite operation label leaves this function, never the URL.
    operations: dict[str, ResponseOperation] = {"GetShoppingResults": "shopping", "GetBookingResults": "booking",
        "GetCalendarGraph": "calendar", "GetFlightDealsStreaming": "deals",
        "GetExploreDestinations": "explore"}
    return operations.get(url.split("?")[0].rsplit("/", 1)[-1], "other")


def read_response_body(response: Any, transport: Any, *,
                       operation: ResponseOperation,
                       phase: Literal["rpc", "page"], method: Literal["POST", "GET"],
                       retry_index: int = 0, required: bool = True) -> str:
    """Read once and notify before decoding, including unreadable-body evidence.

    A failed observer cannot affect a provider result. Reading an HTTP rejection
    body is opt-in and cannot replace its authoritative HTTP failure status.
    """
    observer = transport.response_observer
    text = None
    try:
        text = response.text
        return text
    except Exception:
        if required:
            raise
        return ""
    finally:
        if observer is not None:
            try:
                observer(ResponseBody(operation, phase, method, retry_index,
                    response.status_code, text, body_unavailable=text is None))
            except Exception:
                # Storage/telemetry cannot change the provider's outcome.
                pass
