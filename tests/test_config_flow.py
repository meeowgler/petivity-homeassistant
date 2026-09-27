"""Config flow tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.petivity.api import PetivityAuthError, PetivityConnectionError
from custom_components.petivity.const import CONF_ID_TOKEN, CONF_REFRESH_TOKEN, DOMAIN

from .conftest import HOUSEHOLD

USER_INPUT = {CONF_ID_TOKEN: "id.tok.en", CONF_REFRESH_TOKEN: "refresh-token"}


async def test_user_flow_creates_entry(hass: HomeAssistant, mock_client: MagicMock) -> None:
    mock_client.id_token = "renewed.id.token"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"]["login_url"].startswith("https://")

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == HOUSEHOLD["id"]
    # The renewed token from validation is what gets stored.
    assert result["data"] == {CONF_ID_TOKEN: "renewed.id.token", CONF_REFRESH_TOKEN: "refresh-token"}


async def test_user_flow_invalid_auth(hass: HomeAssistant, mock_client: MagicMock) -> None:
    mock_client.async_renew.side_effect = PetivityAuthError("no")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant, mock_client: MagicMock) -> None:
    mock_client.async_get_household.side_effect = PetivityConnectionError("down")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_entry(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    new = {CONF_ID_TOKEN: "new.id.token", CONF_REFRESH_TOKEN: "new-refresh"}
    mock_client.id_token, mock_client.refresh_token = new[CONF_ID_TOKEN], new[CONF_REFRESH_TOKEN]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data == new
    # Reauth reloads the entry; unload it so its poll timer does not linger.
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
