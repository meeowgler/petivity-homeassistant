"""API client tests."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.petivity import api
from custom_components.petivity.api import (
    GRAPHQL_URL,
    USER_AGENT,
    PetivityClient,
    PetivityConnectionError,
    jwt_expiry,
    utc_iso,
)


def _jwt(exp: float) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp)})}.sig"


class _StubAuth:
    """Stand in for PurinaAuth: hand out a token and record forced renewals."""

    def __init__(self) -> None:
        self.id_token = "id.tok.en"
        self.forced: list[bool] = []

    async def async_id_token(self, *, force: bool = False) -> str:
        self.forced.append(force)
        return self.id_token


def _client(hass: HomeAssistant) -> tuple[PetivityClient, _StubAuth]:
    auth = _StubAuth()
    return PetivityClient(async_get_clientsession(hass), auth), auth  # type: ignore[arg-type]


def _household(**extra: object) -> dict:
    return {
        "data": {"authenticate": {"myHousehold": {"id": "H", "machines": [], "cats": [], **extra}}}
    }


def _responses(*replies: dict):
    """Answer successive requests with each reply in turn."""
    queue = list(replies)

    async def side_effect(method, url, data):
        return AiohttpClientMockResponse(method=method, url=url, **queue.pop(0))

    return side_effect


def test_jwt_expiry() -> None:
    assert jwt_expiry(_jwt(1_800_000_000)) == 1_800_000_000
    assert jwt_expiry("not-a-jwt") == 0.0


def test_utc_iso() -> None:
    local = datetime(2026, 9, 27, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert utc_iso(local) == "2026-09-27T04:00:00.000Z"


def test_user_agent_has_no_url() -> None:
    # Akamai in front of the API blocks agents that contain a URL.
    assert "http" not in USER_AGENT and "/" in USER_AGENT


async def test_query_sends_token_and_no_cookie(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(GRAPHQL_URL, json=_household())
    client, auth = _client(hass)
    household = await client.async_get_household()
    assert household["id"] == "H"
    headers = aioclient_mock.mock_calls[0][3]
    assert "Cookie" not in headers
    assert headers["User-Agent"] == USER_AGENT
    assert aioclient_mock.mock_calls[0][2]["variables"]["jwt"] == "id.tok.en"
    assert auth.forced == [False]


async def test_null_authenticate_retries_with_a_forced_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        GRAPHQL_URL,
        side_effect=_responses({"json": {"data": {"authenticate": None}}}, {"json": _household()}),
    )
    client, auth = _client(hass)
    household = await client.async_get_household()
    assert household["id"] == "H"
    # First with the cached token, then once more forcing a fresh one.
    assert auth.forced == [False, True]


async def test_null_authenticate_twice_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        GRAPHQL_URL, json={"data": {"authenticate": None}, "errors": [{"message": "nope"}]}
    )
    client, _ = _client(hass)
    with pytest.raises(PetivityConnectionError, match="nope"):
        await client.async_get_household()


async def test_empty_household_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(GRAPHQL_URL, json={"data": {"authenticate": {"myHousehold": None}}})
    client, _ = _client(hass)
    with pytest.raises(PetivityConnectionError, match="household"):
        await client.async_get_household()


@pytest.fixture
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_RETRY_DELAY", 0)


@pytest.mark.usefixtures("no_retry_delay")
@pytest.mark.parametrize(
    "first",
    [
        {"status": 504},
        {"status": 502},
        {"exc": aiohttp.ClientConnectionError()},
        {"exc": TimeoutError()},
    ],
)
async def test_blip_is_retried_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, first: dict
) -> None:
    aioclient_mock.post(GRAPHQL_URL, side_effect=_responses(first, {"json": _household()}))
    client, _ = _client(hass)
    assert (await client.async_get_household())["id"] == "H"
    assert aioclient_mock.call_count == 2


@pytest.mark.usefixtures("no_retry_delay")
async def test_outage_still_fails_after_the_retry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(GRAPHQL_URL, side_effect=_responses({"status": 504}, {"status": 504}))
    client, _ = _client(hass)
    with pytest.raises(PetivityConnectionError, match="HTTP 504"):
        await client.async_get_household()
    assert aioclient_mock.call_count == 2
