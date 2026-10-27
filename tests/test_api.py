"""API client tests."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import time

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
    PetivityAuthError,
    PetivityClient,
    PetivityConnectionError,
    jwt_expiry,
    utc_iso,
)


def _jwt(exp: float) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp)})}.sig"


def test_jwt_expiry() -> None:
    assert jwt_expiry(_jwt(1_800_000_000)) == 1_800_000_000
    assert jwt_expiry("not-a-jwt") == 0.0


def test_utc_iso() -> None:
    local = datetime(2026, 9, 27, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert utc_iso(local) == "2026-09-27T04:00:00.000Z"


def test_user_agent_has_no_url() -> None:
    # Akamai in front of the API blocks agents that contain a URL.
    assert "http" not in USER_AGENT and "/" in USER_AGENT


async def test_expired_token_is_renewed_from_cookies(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    fresh = _jwt(time.time() + 3600)
    aioclient_mock.post(
        GRAPHQL_URL,
        json={"data": {"authenticated": True}},
        headers={"Set-Cookie": f"id_token={fresh}; Path=/; HttpOnly"},
    )
    client = PetivityClient(async_get_clientsession(hass), _jwt(time.time() - 60), "refresh")
    await client.async_renew()
    assert client.id_token == fresh
    assert client.refresh_token == "refresh"
    sent = aioclient_mock.mock_calls[0][3]
    assert sent["Cookie"].startswith("id_token=")
    assert sent["User-Agent"] == USER_AGENT


async def test_rejected_session_raises_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(GRAPHQL_URL, json={"data": {"authenticated": False}})
    client = PetivityClient(async_get_clientsession(hass), "x.y.z", "refresh")
    with pytest.raises(PetivityAuthError):
        await client.async_renew()


async def test_http_error_raises_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(GRAPHQL_URL, status=403)
    client = PetivityClient(async_get_clientsession(hass), "x.y.z", "refresh")
    with pytest.raises(PetivityConnectionError):
        await client.async_renew()
    # A refusal is not a blip, so it is not retried.
    assert aioclient_mock.call_count == 1


async def test_unauthorized_raises_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(GRAPHQL_URL, status=401)
    client = PetivityClient(async_get_clientsession(hass), "x.y.z", "refresh")
    with pytest.raises(PetivityAuthError):
        await client.async_renew()
    assert aioclient_mock.call_count == 1


def _responses(*replies: dict):
    """Answer successive requests with each reply in turn."""
    queue = list(replies)

    async def side_effect(method, url, data):
        return AiohttpClientMockResponse(method=method, url=url, **queue.pop(0))

    return side_effect


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
    aioclient_mock.post(
        GRAPHQL_URL, side_effect=_responses(first, {"json": {"data": {"authenticated": True}}})
    )
    client = PetivityClient(async_get_clientsession(hass), "x.y.z", "refresh")
    await client.async_renew()
    assert aioclient_mock.call_count == 2


@pytest.mark.usefixtures("no_retry_delay")
async def test_outage_still_fails_after_the_retry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(GRAPHQL_URL, side_effect=_responses({"status": 504}, {"status": 504}))
    client = PetivityClient(async_get_clientsession(hass), "x.y.z", "refresh")
    with pytest.raises(PetivityConnectionError, match="HTTP 504"):
        await client.async_renew()
    assert aioclient_mock.call_count == 2
