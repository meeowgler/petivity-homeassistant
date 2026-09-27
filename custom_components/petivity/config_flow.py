"""Config flow for Petivity."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PetivityAuthError, PetivityClient, PetivityError
from .const import CONF_ID_TOKEN, CONF_REFRESH_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID_TOKEN): str,
        vol.Required(CONF_REFRESH_TOKEN): str,
    }
)


class PetivityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Petivity."""

    VERSION = 1

    async def _validate(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, str], str | None, dict[str, str]]:
        """Return (errors, household id, session to store)."""
        client = PetivityClient(
            async_get_clientsession(self.hass),
            user_input[CONF_ID_TOKEN],
            user_input[CONF_REFRESH_TOKEN],
        )
        try:
            await client.async_renew()
            household = await client.async_get_household()
        except PetivityAuthError:
            return {"base": "invalid_auth"}, None, {}
        except PetivityError:
            _LOGGER.exception("Could not reach Petivity")
            return {"base": "cannot_connect"}, None, {}
        session = {CONF_ID_TOKEN: client.id_token, CONF_REFRESH_TOKEN: client.refresh_token}
        return {}, household["id"], session

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, household_id, session = await self._validate(user_input)
            if not errors:
                await self.async_set_unique_id(household_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Petivity", data=session)
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, household_id, session = await self._validate(user_input)
            if not errors:
                await self.async_set_unique_id(household_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(self._get_reauth_entry(), data=session)
        return self.async_show_form(step_id="reauth_confirm", data_schema=SCHEMA, errors=errors)
