"""Tests für den Pulse-API-Client."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
from multidict import CIMultiDict
import pytest
from yarl import URL

from custom_components.pulse.api import (
    PulseApiClient,
    PulseApiError,
    PulseAuthError,
    PulseConnectionError,
)


@pytest.mark.asyncio
async def test_get_resources_paginates_250_resources() -> None:
    resources = [{"id": f"res-{idx}"} for idx in range(250)]
    session = FakeSession(
        {
            "https://pulse.example/api/resources?page=1&limit=100": FakeResponse(
                payload={"data": resources[:100], "meta": {"total": 250}}
            ),
            "https://pulse.example/api/resources?page=2&limit=100": FakeResponse(
                payload={"data": resources[100:200], "meta": {"total": 250}}
            ),
            "https://pulse.example/api/resources?page=3&limit=100": FakeResponse(
                payload={"data": resources[200:], "meta": {"total": 250}}
            ),
        }
    )
    client = PulseApiClient(session, "https://pulse.example", "secret-token")

    result = await client.async_get_resources()

    assert len(result) == 250
    assert result[0] == {"id": "res-0"}
    assert result[-1] == {"id": "res-249"}
    assert len(session.calls) == 3


@pytest.mark.asyncio
async def test_auth_error_has_no_token_in_text(caplog: pytest.LogCaptureFixture) -> None:
    session = FakeSession({"https://pulse.example/api/state": FakeResponse(status=401)})
    client = PulseApiClient(session, "https://pulse.example", "secret-token")

    with pytest.raises(PulseAuthError) as exc:
        await client.async_get_state()

    assert "secret-token" not in str(exc.value)
    assert "secret-token" not in caplog.text


@pytest.mark.asyncio
async def test_client_error_exception_chain_does_not_expose_token(caplog: pytest.LogCaptureFixture) -> None:
    token = "secret-token-in-request-info"
    request_info = aiohttp.RequestInfo(
        url=URL("https://pulse.example/api/state"),
        method="GET",
        headers=CIMultiDict({"X-API-Token": token}),
        real_url=URL("https://pulse.example/api/state"),
    )
    session = FakeSession(
        {"https://pulse.example/api/state": aiohttp.ClientResponseError(request_info, (), status=500)}
    )
    client = PulseApiClient(session, "https://pulse.example", token)

    with pytest.raises(PulseConnectionError) as exc:
        await client.async_get_state()

    serialized_chain = " ".join(str(item) for item in _exception_chain(exc.value))
    assert token not in serialized_chain
    assert token not in caplog.text


@pytest.mark.asyncio
async def test_insufficient_scope_is_marked() -> None:
    session = FakeSession(
        {
            "https://pulse.example/api/state/summary": FakeResponse(
                status=403,
                text="missing_scope: monitoring:read",
            )
        }
    )
    client = PulseApiClient(session, "https://pulse.example", "secret-token")

    with pytest.raises(PulseAuthError) as exc:
        await client.async_get_summary()

    assert exc.value.insufficient_scope is True


@pytest.mark.asyncio
async def test_feature_unavailable_returns_false_without_exception() -> None:
    session = FakeSession({"https://pulse.example/api/recovery/rollups": FakeResponse(status=402)})
    client = PulseApiClient(session, "https://pulse.example", "secret-token")

    result = await client.async_get_feature_payload("/api/recovery/rollups", "recovery")

    assert result is None
    assert client.feature_available["recovery"] is False


@pytest.mark.asyncio
async def test_server_error_raises_api_error() -> None:
    session = FakeSession({"https://pulse.example/api/state": FakeResponse(status=500)})
    client = PulseApiClient(session, "https://pulse.example", "secret-token")

    with pytest.raises(PulseApiError):
        await client.async_get_state()


@pytest.mark.asyncio
async def test_timeout_raises_connection_error() -> None:
    session = FakeSession({"https://pulse.example/api/state": asyncio.TimeoutError()})
    client = PulseApiClient(session, "https://pulse.example", "secret-token")

    with pytest.raises(PulseConnectionError):
        await client.async_get_state()


@pytest.mark.asyncio
async def test_redirect_is_rejected_and_not_followed() -> None:
    session = FakeSession({"https://pulse.example/api/state": FakeResponse(status=302)})
    client = PulseApiClient(session, "https://pulse.example", "secret-token")

    with pytest.raises(PulseApiError):
        await client.async_get_state()

    assert session.calls[0]["allow_redirects"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("auth", PulseAuthError),
        ("redirect", PulseApiError),
        ("bad_json", PulseApiError),
    ],
)
async def test_error_paths_do_not_log_token(
    case: str,
    expected_error: type[Exception],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    token = "secret-token-debug-log"
    response = {
        "auth": FakeResponse(status=401),
        "redirect": FakeResponse(status=302),
        "bad_json": FakeResponse(raw_json="not-json"),
    }[case]
    session = FakeSession({"https://pulse.example/api/state": response})
    client = PulseApiClient(session, "https://pulse.example", token)

    with pytest.raises(expected_error):
        await client.async_get_state()

    assert token not in caplog.text


@pytest.mark.asyncio
async def test_timeout_does_not_log_token(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    token = "secret-token-timeout-log"
    session = FakeSession({"https://pulse.example/api/state": asyncio.TimeoutError()})
    client = PulseApiClient(session, "https://pulse.example", token)

    with pytest.raises(PulseConnectionError):
        await client.async_get_state()

    assert token not in caplog.text


class FakeSession:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, **kwargs: Any):
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses[url]
        if isinstance(response, BaseException):
            raise response
        return response


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        payload: dict[str, Any] | None = None,
        text: str = "",
        raw_json: str | None = None,
    ) -> None:
        self.status = status
        self._payload = payload if payload is not None else {}
        self._text = text
        self._raw_json = raw_json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def text(self) -> str:
        return self._text

    async def json(self, *, content_type=None) -> dict[str, Any]:
        if self._raw_json is not None:
            return json.loads(self._raw_json)
        return self._payload


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain
