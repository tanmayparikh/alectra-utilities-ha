"""Config flow for Alectra Utilities."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .alectra_api import AlectraAuthError, AlectraApiError, AlectraClient

_LOGGER = logging.getLogger(__name__)
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_CUSTOMER_NUMBER,
    CONF_METER_NUMBER,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_ACCOUNT_NUMBER): str,
    }
)


class AlectraConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_ACCOUNT_NUMBER])
            self._abort_if_unique_id_configured()

            client = AlectraClient(
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                account_number=user_input[CONF_ACCOUNT_NUMBER],
            )
            try:
                await client.login()
                discovered = await client.discover_account()
            except AlectraAuthError as exc:
                _LOGGER.error("Alectra auth error during setup: %s", exc)
                errors["base"] = "invalid_auth"
            except AlectraApiError as exc:
                _LOGGER.error("Alectra API error during setup: %s", exc)
                errors["base"] = "cannot_connect"
            except Exception as exc:  # noqa: BLE001
                _LOGGER.exception("Alectra unexpected error during setup: %s", exc)
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"Alectra {user_input[CONF_ACCOUNT_NUMBER]}",
                    data={
                        **user_input,
                        CONF_CUSTOMER_NUMBER: discovered["customer_number"],
                        CONF_METER_NUMBER: discovered["meter_number"],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
