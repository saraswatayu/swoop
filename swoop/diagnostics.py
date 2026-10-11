"""Opt-in, request-owned transport measurements without payloads or credentials.

One recorder may span multiple calls and caller-controlled failover attempts.
Snapshots remain available after empty responses and exceptions. No recorder is
created implicitly and no process-global request context is used.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import wraps
import inspect
import re
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Iterator, Literal, Optional, TypeVar, cast


@dataclass(frozen=True)
class HTTPAttempt:
    """One actual HTTP dispatch; bytes describe decoded UTF-8, not wire bytes."""

    query_id: Optional[int]
    operation: Literal["shopping", "booking", "calendar", "other"]
    transport: Literal["rpc", "page"]
    method: Literal["POST", "GET"]
    retry_index: int
    status_code: Optional[int]
    network_ms: float
    response_bytes: Optional[int]
    error_type: Optional[str]


@dataclass(frozen=True)
class QueryBounds:
    origin_airports: tuple[str, ...]
    destination_airports: tuple[str, ...]
    date: str


@dataclass(frozen=True)
class QueryDiagnostics:
    query_id: int
    operation: Literal["shopping", "booking"]
    bounds: tuple[QueryBounds, ...]
    bounds_available: bool
    cabin: Optional[str]
    outcome: Literal["pending", "success", "empty", "error"]
    raw_result_count: Optional[int]
    decoded_result_count: Optional[int]
    result_scope: Optional[Literal["all", "default", "limited"]]
    decode_ms: float
    error_type: Optional[str]


@dataclass(frozen=True)
class TransportDiagnostics:
    """Immutable snapshot; aggregate counts remain exact when samples are bounded."""

    http_attempts: int
    rpc_attempts: int
    page_attempts: int
    retry_attempts: int
    elapsed_ms: float
    network_ms: float
    decode_ms: float
    retry_wait_ms: float
    response_bytes: int
    responses_with_unknown_bytes: int
    attempts: tuple[HTTPAttempt, ...]
    queries: tuple[QueryDiagnostics, ...]
    omitted_attempts: int
    omitted_queries: int


@dataclass
class _Query:
    query_id: int
    operation: Literal["shopping", "booking"]
    bounds: tuple[QueryBounds, ...]
    bounds_available: bool
    cabin: Optional[str]
    outcome: Literal["pending", "success", "empty", "error"] = "pending"
    raw_result_count: Optional[int] = None
    decoded_result_count: Optional[int] = None
    result_scope: Optional[Literal["all", "default", "limited"]] = None
    decode_ms: float = 0.0
    error_type: Optional[str] = None


class DiagnosticsRecorder:
    """Explicit request-scoped recorder, safe to share among that request's workers.

    Create a fresh instance for each independent request and pass it through
    ``TransportConfig(diagnostics=recorder)``. Reuse that instance for external
    retries to retain failed attempts. It never stores URLs, selectors, booking
    tokens, headers, bodies, proxies, cookies, or exception messages.
    """

    def __init__(self, *, max_attempts: int = 1024, max_queries: int = 256):
        if max_attempts < 0 or max_queries < 0:
            raise ValueError("Diagnostic sample bounds must be nonnegative")
        self._max_attempts, self._max_queries = max_attempts, max_queries
        self._lock = Lock()
        self._attempts: list[HTTPAttempt] = []
        self._queries: dict[int, _Query] = {}
        self._query_count = 0
        self._http_count = self._rpc_count = self._page_count = self._retry_count = 0
        self._network_ms = self._decode_ms = self._retry_wait_ms = 0.0
        self._first_started: Optional[float] = None
        self._last_finished: Optional[float] = None
        self._response_bytes = self._unknown_bytes = 0

    def snapshot(self) -> TransportDiagnostics:
        with self._lock:
            return TransportDiagnostics(
                self._http_count, self._rpc_count, self._page_count, self._retry_count,
                ((self._last_finished - self._first_started) * 1000
                 if self._first_started is not None and self._last_finished is not None else 0.0),
                self._network_ms, self._decode_ms, self._retry_wait_ms,
                self._response_bytes, self._unknown_bytes, tuple(self._attempts),
                tuple(QueryDiagnostics(**vars(query)) for query in self._queries.values()),
                self._http_count - len(self._attempts), self._query_count - len(self._queries),
            )

    def _begin_query(self, operation: Literal["shopping", "booking"],
                     bounds: tuple[QueryBounds, ...], available: bool,
                     cabin: Optional[str]) -> int:
        with self._lock:
            self._query_count += 1
            query_id = self._query_count
            if len(self._queries) < self._max_queries:
                self._queries[query_id] = _Query(query_id, operation, bounds, available, cabin)
            return query_id

    def _finish_query(self, query_id: int, result: Any, error: Optional[BaseException]) -> None:
        with self._lock:
            query = self._queries.get(query_id)
            if query is None:
                return
            if error is not None:
                query.outcome, query.error_type = "error", type(error).__name__
                return
            if query.operation == "booking":
                query.decoded_result_count = len(result) if isinstance(result, list) else None
            elif result is not None:
                query.decoded_result_count = len(result.best) + len(result.other)
                raw_count = getattr(result, "_raw_result_count", None)
                query.raw_result_count = raw_count if isinstance(raw_count, int) else None
                scope = getattr(result, "_result_scope", None)
                if scope in ("all", "default", "limited"):
                    query.result_scope = scope
            query.outcome = "empty" if result is None or query.decoded_result_count == 0 else "success"

    def _interval(self, started: float, finished: float) -> None:
        self._first_started = started if self._first_started is None else min(self._first_started, started)
        self._last_finished = finished if self._last_finished is None else max(self._last_finished, finished)

    def _attempt(self, attempt: HTTPAttempt, started: float, finished: float) -> None:
        with self._lock:
            self._interval(started, finished)
            self._http_count += 1
            self._rpc_count += attempt.transport == "rpc"
            self._page_count += attempt.transport == "page"
            self._retry_count += attempt.retry_index > 0
            self._network_ms += attempt.network_ms
            if attempt.response_bytes is None:
                self._unknown_bytes += 1
            else:
                self._response_bytes += attempt.response_bytes
            if len(self._attempts) < self._max_attempts:
                self._attempts.append(attempt)

    def _decoded(self, query_id: Optional[int], started: float, finished: float) -> None:
        with self._lock:
            self._interval(started, finished)
            milliseconds = (finished - started) * 1000
            self._decode_ms += milliseconds
            query = self._queries.get(query_id) if query_id is not None else None
            if query is not None:
                query.decode_ms += milliseconds

    def _waited(self, milliseconds: float) -> None:
        with self._lock:
            self._retry_wait_ms += milliseconds


@dataclass
class _HTTPMeasurement:
    enabled: bool = False
    status_code: Optional[int] = None
    response_bytes: Optional[int] = None

    def body(self, text: str) -> None:
        if self.enabled:
            self.response_bytes = len(text.encode("utf-8", errors="replace"))


@contextmanager
def measure_http(transport: Any, operation: Literal["shopping", "booking", "calendar", "other"],
                 phase: Literal["rpc", "page"], method: Literal["POST", "GET"],
                 retry_index: int = 0) -> Iterator[_HTTPMeasurement]:
    recorder = transport.diagnostics
    measurement = _HTTPMeasurement(enabled=recorder is not None)
    if recorder is None:
        yield measurement
        return
    started = perf_counter()
    error_type = None
    try:
        yield measurement
    except BaseException as error:
        error_type = type(error).__name__
        raise
    finally:
        finished = perf_counter()
        recorder._attempt(HTTPAttempt(transport._diagnostics_query_id, operation, phase, method,
            retry_index, measurement.status_code, (finished - started) * 1000,
            measurement.response_bytes, error_type), started, finished)


@contextmanager
def measure_decode(transport: Any) -> Iterator[None]:
    recorder = transport.diagnostics
    if recorder is None:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        recorder._decoded(transport._diagnostics_query_id, started, perf_counter())


def measure_retry_wait(transport: Any, wait: Callable[[], None]) -> None:
    if transport.diagnostics is None:
        wait()
        return
    started = perf_counter()
    try:
        wait()
    finally:
        transport.diagnostics._waited((perf_counter() - started) * 1000)


def rpc_operation(url: str) -> Literal["shopping", "booking", "calendar", "other"]:
    endpoint = url.split("?")[0].rsplit("/", 1)[-1]
    if endpoint == "GetShoppingResults":
        return "shopping"
    if endpoint == "GetBookingResults":
        return "booking"
    if endpoint == "GetCalendarPicker":
        return "calendar"
    return "other"


def _bounds(arguments: dict[str, Any]) -> tuple[tuple[QueryBounds, ...], bool]:
    legs = arguments.get("legs")
    if legs is None:
        itinerary = arguments.get("itinerary_or_token")
        date = arguments.get("date")
        departure_date = getattr(itinerary, "departure_date", None)
        if not date and departure_date:
            date = "-".join(str(value).zfill(4 if index == 0 else 2)
                            for index, value in enumerate(departure_date))
        legs = [{"origin": arguments.get("origin") or getattr(itinerary, "departure_airport_code", ""),
                 "destination": arguments.get("destination") or getattr(itinerary, "arrival_airport_code", ""),
                 "date": date}]
    if not isinstance(legs, list) or not legs or len(legs) > 32:
        return (), False
    bounds = []
    for leg in legs:
        if not isinstance(leg, dict):
            return (), False
        codes = []
        for key in ("origin", "destination"):
            value = leg.get(key)
            airports = (value,) if isinstance(value, str) else tuple(value) if isinstance(value, list) else ()
            if not airports or len(airports) > 32 or any(not isinstance(code, str)
                    or re.fullmatch(r"[A-Z]{3}", code) is None for code in airports):
                return (), False
            codes.append(airports)
        date = leg.get("date")
        if not isinstance(date, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) is None:
            return (), False
        bounds.append(QueryBounds(codes[0], codes[1], date))
    return tuple(bounds), True


F = TypeVar("F", bound=Callable[..., Any])


def observe_query(operation: Literal["shopping", "booking"]) -> Callable[[F], F]:
    """Keep exact request correlation explicit in the derived transport config."""
    def decorate(function: F) -> F:
        signature = inspect.signature(function)

        @wraps(function)
        def measured(*args: Any, **kwargs: Any) -> Any:
            transport = kwargs.get("transport")
            if transport is None or transport.diagnostics is None:
                return function(*args, **kwargs)
            arguments = signature.bind(*args, **kwargs).arguments
            bounds, available = _bounds(dict(arguments))
            cabin = arguments.get("cabin", "economy")
            if cabin not in ("economy", "premium-economy", "business", "first"):
                cabin = None
            recorder = transport.diagnostics
            query_id = recorder._begin_query(operation, bounds, available, cabin)
            try:
                result = function(*args, **{**kwargs, "transport": replace(transport, _diagnostics_query_id=query_id)})
            except BaseException as error:
                recorder._finish_query(query_id, None, error)
                # Typed provider errors expose the same snapshot without altering their message/cause.
                from .exceptions import SwoopError
                if isinstance(error, SwoopError):
                    error.diagnostics = recorder.snapshot()
                raise
            recorder._finish_query(query_id, result, None)
            return result

        return cast(F, measured)

    return decorate
