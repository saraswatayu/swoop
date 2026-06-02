"""Shared pytest fixtures for swoop tests."""
from __future__ import annotations

import sys
import types

import pytest

from tests.factories import FakeHTTPResponse


def pytest_addoption(parser):
    parser.addoption(
        "--run-benchmarks",
        action="store_true",
        default=False,
        help="run tests marked as benchmark",
    )


def pytest_collection_modifyitems(config, items):
    benchmark_only = bool(getattr(config.option, "benchmark_only", False))
    if config.getoption("--run-benchmarks") or benchmark_only:
        return

    skip_benchmark = pytest.mark.skip(reason="use --run-benchmarks to run benchmark tests")
    for item in items:
        if "benchmark" in item.keywords:
            item.add_marker(skip_benchmark)


@pytest.fixture
def fake_primp(monkeypatch):
    """Patch primp.Client to return canned responses.

    Usage:
        def test_something(fake_primp):
            fake_primp(200, "response body")
            # now primp.Client().post() returns FakeHTTPResponse(200, "response body")
    """
    import swoop.rpc as _rpc

    def _install(status_code=200, text=""):
        response = FakeHTTPResponse(status_code, text)
        class FakeClient:
            def __init__(self, **_kw):
                pass
            def post(self, *_a, **_kw):
                return response
        # Clear the connection cache so the fake client is actually used
        _rpc._clients.clear()
        monkeypatch.setitem(sys.modules, "primp", types.SimpleNamespace(Client=FakeClient))
        return response
    return _install


@pytest.fixture(autouse=True)
def _isolate_explore_session_cache():
    """Clear the module-global explore session-param cache around every test.

    _explore caches bl/f.sid (and per-key locks) process-wide. Tests that seed
    or populate it would otherwise leak that state into later tests — even in
    other modules and live runs — making behavior order-dependent.
    """
    from swoop import _explore
    _explore._browser_params_cache.clear()
    _explore._browser_params_key_locks.clear()
    yield
    _explore._browser_params_cache.clear()
    _explore._browser_params_key_locks.clear()
