"""Tests for the one part of scripts/preflight.py that can be tested offline.

Everything else in that script makes real calls on purpose and belongs
nowhere near pytest. The stale-server detector is different: it is pure
comparison of two OpenAPI documents, and it is the single check the rest of
the harness leans on -- if it silently passes, every other green result
might be describing a process running last week's code.

It has already failed that way once. The first version walked `app.routes`,
which in this FastAPI version contains `_IncludedRouter` objects rather than
the flattened child routes, so it compared exactly one path (`/health`) and
reported "ok" against a server missing `/memory/graph` entirely. These tests
exist so that cannot happen again unnoticed.
"""
import copy

import pytest

from scripts.preflight import Report, check_served_code_is_this_checkout
from app.main import app


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Stands in for httpx.AsyncClient, serving one canned OpenAPI document."""

    def __init__(self, payload):
        self._payload = payload

    async def get(self, path):
        assert path == "/openapi.json"
        return _FakeResponse(self._payload)


@pytest.fixture
def current_spec():
    return app.openapi()


async def test_matching_spec_passes(current_spec):
    report = Report()

    await check_served_code_is_this_checkout(_FakeClient(current_spec), report)

    assert (report.passed, report.failed) == (1, 0)


async def test_a_route_this_checkout_defines_but_the_server_lacks_is_caught(current_spec):
    """The Phase 3 failure mode: the fix is written, the process is old."""
    stale = copy.deepcopy(current_spec)
    stale["paths"].pop("/memory/graph")

    report = Report()
    await check_served_code_is_this_checkout(_FakeClient(stale), report)

    assert report.failed == 1


async def test_a_response_schema_a_revision_behind_is_caught(current_spec):
    """The Phase 4 failure mode: the path exists, the response shape does not.

    Route names alone would pass this, which is why the check compares
    schemas too.
    """
    stale = copy.deepcopy(current_spec)
    stale["components"]["schemas"]["MemoryGraphResponse"]["properties"].pop("adaptive")

    report = Report()
    await check_served_code_is_this_checkout(_FakeClient(stale), report)

    assert report.failed == 1


async def test_a_route_the_server_has_but_this_checkout_does_not_is_caught(current_spec):
    """Staleness runs both ways -- a second, older uvicorn holding the port,
    or a checkout rolled back beneath a still-running process.
    """
    stale = copy.deepcopy(current_spec)
    stale["paths"]["/ghost"] = {}

    report = Report()
    await check_served_code_is_this_checkout(_FakeClient(stale), report)

    assert report.failed == 1


async def test_the_local_spec_actually_contains_the_real_routes():
    """Guards the specific bug that made the original check useless: if the
    locally derived path set collapses to just /health again, every
    comparison above becomes vacuous and would still pass.
    """
    paths = set(app.openapi()["paths"])

    assert {"/chat/message", "/memory", "/memory/graph", "/models", "/personas"} <= paths
    assert len(paths) > 5


async def test_an_unreadable_spec_fails_rather_than_passing_quietly():
    class Broken(_FakeClient):
        async def get(self, path):
            raise ValueError("not json")

    report = Report()
    await check_served_code_is_this_checkout(Broken(None), report)

    assert report.failed == 1
