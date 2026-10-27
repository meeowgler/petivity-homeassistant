"""Tests for the Purina Auth0 sign-in."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import re
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.petivity.api import PetivityAuthError
from custom_components.petivity.purina_auth import PurinaAuth

AUTHORIZE = re.compile(r"auth\.purina\.com/authorize")
IDENTIFIER = re.compile(r"/u/login/identifier")
PASSWORD = re.compile(r"/u/login/password")
TOKEN = re.compile(r"/oauth/token")
CODE_REDIRECT = {"Location": "arbuckle://auth0-login?code=THECODE&state=s1"}


def _jwt(exp: float) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp)})}.sig"


def _page(state: str = "s1") -> str:
    """A login page carrying the Auth0 universal_login_context the code reads."""
    ctx = base64.b64encode(json.dumps({"transaction": {"state": state}}).encode()).decode()
    return (
        "<html><script>window.universal_login_context="
        'JSON.parse(new TextDecoder("utf-8").decode(Uint8Array.from(atob("'
        + ctx
        + '"), c => c.charCodeAt(0))));</script></html>'
    )


def _key_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _mock_sign_in_pages(aioclient_mock: AiohttpClientMocker) -> None:
    """The browser steps up to the callback that carries the auth code."""
    aioclient_mock.get(AUTHORIZE, text=_page())
    aioclient_mock.post(
        IDENTIFIER,
        status=302,
        headers={"Location": "https://auth.purina.com/u/login/password?state=s1"},
    )
    aioclient_mock.get(PASSWORD, text=_page())
    aioclient_mock.post(PASSWORD, status=302, headers=CODE_REDIRECT)


def _responses(*replies: dict):
    queue = list(replies)

    async def side_effect(method, url, data):
        return AiohttpClientMockResponse(method=method, url=url, **queue.pop(0))

    return side_effect


def _auth(hass: HomeAssistant, **kwargs) -> PurinaAuth:
    return PurinaAuth(
        hass, async_get_clientsession(hass), "cat@example.com", "hunter2", **kwargs
    )


async def test_login_returns_tokens(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    idt = _jwt(time.time() + 86400)
    _mock_sign_in_pages(aioclient_mock)
    aioclient_mock.post(TOKEN, json={"id_token": idt, "refresh_token": "rt-1"})
    auth = _auth(hass)
    await auth.async_login()
    assert auth.id_token == idt
    assert auth.refresh_token == "rt-1"
    assert auth.dpop_key_pem is not None
    # The password was POSTed to the password step.
    pw_call = next(c for c in aioclient_mock.mock_calls if "password" in str(c[1]) and c[0] == "POST")
    assert "password=hunter2" in pw_call[2].decode()


async def test_login_rejects_bad_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(AUTHORIZE, text=_page())
    aioclient_mock.post(
        IDENTIFIER,
        status=302,
        headers={"Location": "https://auth.purina.com/u/login/password?state=s1"},
    )
    aioclient_mock.get(PASSWORD, text=_page())
    # A wrong password re-renders the password page instead of redirecting.
    aioclient_mock.post(PASSWORD, text=_page())
    with pytest.raises(PetivityAuthError):
        await _auth(hass).async_login()


async def test_dpop_nonce_is_retried(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    idt = _jwt(time.time() + 86400)
    _mock_sign_in_pages(aioclient_mock)
    aioclient_mock.post(
        TOKEN,
        side_effect=_responses(
            {"status": 400, "json": {"error": "use_dpop_nonce"}, "headers": {"DPoP-Nonce": "n1"}},
            {"json": {"id_token": idt, "refresh_token": "rt"}},
        ),
    )
    auth = _auth(hass)
    await auth.async_login()
    assert auth.id_token == idt


async def test_id_token_is_refreshed_when_stale(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    fresh = _jwt(time.time() + 86400)
    aioclient_mock.post(TOKEN, json={"id_token": fresh, "refresh_token": "rt-2"})
    auth = _auth(
        hass, id_token=_jwt(time.time() - 60), refresh_token="rt-1", dpop_key_pem=_key_pem()
    )
    assert await auth.async_id_token() == fresh
    # The rotated refresh token replaces the old one.
    assert auth.refresh_token == "rt-2"


async def test_fresh_token_is_used_without_a_network_call(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    good = _jwt(time.time() + 86400)
    auth = _auth(hass, id_token=good, refresh_token="rt", dpop_key_pem=_key_pem())
    assert await auth.async_id_token() == good
    assert aioclient_mock.call_count == 0


async def test_rejected_refresh_falls_back_to_login(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    idt = _jwt(time.time() + 86400)
    _mock_sign_in_pages(aioclient_mock)
    aioclient_mock.post(
        TOKEN,
        side_effect=_responses(
            {"status": 403, "json": {"error": "invalid_grant"}},  # refresh rejected
            {"json": {"id_token": idt, "refresh_token": "rt-new"}},  # sign-in exchange
        ),
    )
    auth = _auth(
        hass, id_token=_jwt(time.time() - 60), refresh_token="stale", dpop_key_pem=_key_pem()
    )
    assert await auth.async_id_token() == idt
    assert auth.refresh_token == "rt-new"
