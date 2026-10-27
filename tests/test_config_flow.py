"""Config flow tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import aiohttp
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import aiohttp_client
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.petivity.api import PetivityAuthError, PetivityConnectionError
from custom_components.petivity.const import (
    CONF_DPOP_KEY,
    CONF_EMAIL,
    CONF_ID_TOKEN,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

from .conftest import HOUSEHOLD

USER_INPUT = {CONF_EMAIL: "cat@example.com", CONF_PASSWORD: "hunter2"}


async def test_user_flow_creates_entry(
    hass: HomeAssistant, mock_auth: MagicMock, mock_client: MagicMock
) -> None:
    mock_auth.id_token = "fresh.id.token"
    mock_auth.refresh_token = "fresh-refresh"
    mock_auth.dpop_key_pem = "fresh-pem"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == HOUSEHOLD["id"]
    assert result["data"] == {
        CONF_EMAIL: "cat@example.com",
        CONF_PASSWORD: "hunter2",
        CONF_ID_TOKEN: "fresh.id.token",
        CONF_REFRESH_TOKEN: "fresh-refresh",
        CONF_DPOP_KEY: "fresh-pem",
    }
    mock_auth.async_login.assert_awaited_once()


async def test_validation_uses_own_detached_session(
    hass: HomeAssistant, mock_auth: MagicMock, mock_client: MagicMock
) -> None:
    """Validation runs on its own session with an empty cookie jar, detached afterwards."""
    created: list[tuple[aiohttp.ClientSession, dict]] = []

    def capture(hass: HomeAssistant, **kwargs):
        session = aiohttp_client.async_create_clientsession(hass, **kwargs)
        created.append((session, kwargs))
        return session

    with (
        patch(
            "custom_components.petivity.config_flow.async_create_clientsession",
            side_effect=capture,
        ),
        patch(
            "custom_components.petivity.config_flow.PurinaAuth", return_value=mock_auth
        ) as auth_cls,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert len(created) == 1
    session, kwargs = created[0]
    assert isinstance(kwargs["cookie_jar"], aiohttp.DummyCookieJar)
    assert kwargs["auto_cleanup"] is False
    assert auth_cls.call_args.args[1] is session
    assert session.closed  # detach() puts the session in the closed state


async def test_user_flow_invalid_auth(
    hass: HomeAssistant, mock_auth: MagicMock, mock_client: MagicMock
) -> None:
    mock_auth.async_login.side_effect = PetivityAuthError("no")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, mock_auth: MagicMock, mock_client: MagicMock
) -> None:
    mock_client.async_get_household.side_effect = PetivityConnectionError("down")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(
    hass: HomeAssistant,
    mock_auth: MagicMock,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_entry(
    hass: HomeAssistant,
    mock_auth: MagicMock,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    mock_auth.id_token = "new.id.token"
    mock_auth.refresh_token = "new-refresh"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_ID_TOKEN] == "new.id.token"
    assert config_entry.data[CONF_PASSWORD] == "hunter2"
    # Reauth reloads the entry; unload it so its poll timer does not linger.
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
