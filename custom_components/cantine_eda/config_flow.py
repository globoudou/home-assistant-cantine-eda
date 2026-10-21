"""Configuration : ajout de comptes, réauthentification, reconfiguration, options."""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    MonewebAuthError,
    MonewebClient,
    MonewebError,
    MonewebSSLError,
    normalize_base_url,
)
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PASSWORD_CHANGED_AT,
    CONF_PREVIOUS_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_CHANGE_ENDPOINT,
    DEFAULT_CHANGE_PAYLOAD,
    DEFAULT_HOST,
    DEFAULT_LOW_BALANCE,
    DEFAULT_PASSWORD_LENGTH,
    DEFAULT_PASSWORD_SYMBOLS,
    DEFAULT_RENEW_BEFORE_EXPIRY,
    DEFAULT_RENEW_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OPT_ALLOW_NON_ADMIN,
    OPT_CHANGE_ENDPOINT,
    OPT_CHANGE_PAYLOAD,
    OPT_LOW_BALANCE,
    OPT_PASSWORD_LENGTH,
    OPT_PASSWORD_SYMBOLS,
    OPT_PHOTO_URL_TEMPLATE,
    OPT_RECHARGE_URL,
    OPT_RENEW_BEFORE_EXPIRY,
    OPT_RENEW_DAYS,
    OPT_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
SECTION_GENERAL = "general"
SECTION_RENEWAL = "renewal"


async def validate_account(hass: HomeAssistant, data: Mapping[str, Any]) -> None:
    """Se connecte et lit le tableau de bord ; lève une MonewebError sinon."""
    session = async_create_clientsession(
        hass,
        verify_ssl=data.get(CONF_VERIFY_SSL, True),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    # Session dédiée (cookies propres à ce compte) ; Home Assistant la libère
    # lui-même, il ne faut pas la fermer.
    client = MonewebClient(
        session,
        data[CONF_HOST],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        data.get(CONF_VERIFY_SSL, True),
    )
    await client.async_login()
    await client.async_get_dashboard()


def _error_key(err: Exception) -> str:
    if isinstance(err, MonewebAuthError):
        return "invalid_auth"
    if isinstance(err, MonewebSSLError):
        return "ssl_error"
    if isinstance(err, MonewebError):
        return "cannot_connect"
    _LOGGER.exception("Erreur inattendue lors de la validation du compte")
    return "unknown"


def _unique_id(host: str, username: str) -> str:
    return f"{normalize_base_url(host).removeprefix('https://')}|{username.strip().lower()}"


class CantineConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ajout d'un compte cantine (on peut en ajouter plusieurs)."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CantineOptionsFlow:
        """Options."""
        return CantineOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Formulaire principal."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_USERNAME] = user_input[CONF_USERNAME].strip()
            user_input[CONF_HOST] = user_input[CONF_HOST].strip()
            await self.async_set_unique_id(
                _unique_id(user_input[CONF_HOST], user_input[CONF_USERNAME])
            )
            self._abort_if_unique_id_configured()
            try:
                await validate_account(self.hass, user_input)
            except Exception as err:  # noqa: BLE001
                errors["base"] = _error_key(err)
            else:
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data=user_input
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                vol.Required(CONF_VERIFY_SSL, default=True): bool,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Le site refuse le mot de passe."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Saisie du nouveau mot de passe."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await validate_account(self.hass, data)
            except Exception as err:  # noqa: BLE001
                errors["base"] = _error_key(err)
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates=_password_updates(entry, user_input[CONF_PASSWORD])
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"account": entry.title},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Modifier l'adresse du site, le mot de passe ou la vérification SSL."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            try:
                await validate_account(self.hass, data)
            except Exception as err:  # noqa: BLE001
                errors["base"] = _error_key(err)
            else:
                updates = {
                    CONF_HOST: user_input[CONF_HOST].strip(),
                    CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                }
                if user_input[CONF_PASSWORD] != entry.data[CONF_PASSWORD]:
                    updates |= _password_updates(entry, user_input[CONF_PASSWORD])
                return self.async_update_reload_and_abort(entry, data_updates=updates)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                vol.Required(CONF_VERIFY_SSL): bool,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or dict(entry.data)
            ),
            description_placeholders={"account": entry.title},
            errors=errors,
        )


def _password_updates(entry: ConfigEntry, new_password: str) -> dict[str, Any]:
    from homeassistant.util import dt as dt_util  # noqa: PLC0415

    return {
        CONF_PASSWORD: new_password,
        CONF_PREVIOUS_PASSWORD: entry.data.get(CONF_PASSWORD),
        CONF_PASSWORD_CHANGED_AT: dt_util.utcnow().isoformat(),
    }


class CantineOptionsFlow(OptionsFlow):
    """Options d'un compte."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Formulaire d'options en deux sections."""
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = {
                **user_input.get(SECTION_GENERAL, {}),
                **user_input.get(SECTION_RENEWAL, {}),
            }
            payload = flat.get(OPT_CHANGE_PAYLOAD) or DEFAULT_CHANGE_PAYLOAD
            try:
                json.loads(payload)
            except ValueError:
                errors["base"] = "invalid_payload"
            else:
                if "{new}" not in payload:
                    errors["base"] = "payload_missing_new"
            if not errors:
                for key in (
                    OPT_SCAN_INTERVAL,
                    OPT_RENEW_DAYS,
                    OPT_RENEW_BEFORE_EXPIRY,
                    OPT_PASSWORD_LENGTH,
                ):
                    if key in flat:
                        flat[key] = int(flat[key])
                return self.async_create_entry(data=flat)

        current = {**self.config_entry.options}
        defaults = {
            OPT_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            OPT_LOW_BALANCE: DEFAULT_LOW_BALANCE,
            OPT_ALLOW_NON_ADMIN: False,
            OPT_RENEW_DAYS: DEFAULT_RENEW_DAYS,
            OPT_RENEW_BEFORE_EXPIRY: DEFAULT_RENEW_BEFORE_EXPIRY,
            OPT_PASSWORD_LENGTH: DEFAULT_PASSWORD_LENGTH,
            OPT_PASSWORD_SYMBOLS: DEFAULT_PASSWORD_SYMBOLS,
            OPT_CHANGE_ENDPOINT: DEFAULT_CHANGE_ENDPOINT,
            OPT_CHANGE_PAYLOAD: DEFAULT_CHANGE_PAYLOAD,
        }
        values = {**defaults, **current}

        general = vol.Schema(
            {
                vol.Required(OPT_SCAN_INTERVAL, default=values[OPT_SCAN_INTERVAL]): NumberSelector(
                    NumberSelectorConfig(
                        min=5, max=1440, step=1, mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Required(OPT_LOW_BALANCE, default=values[OPT_LOW_BALANCE]): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=500, step=0.5, mode=NumberSelectorMode.BOX,
                        unit_of_measurement="€",
                    )
                ),
                vol.Optional(
                    OPT_RECHARGE_URL,
                    description={"suggested_value": values.get(OPT_RECHARGE_URL)},
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                vol.Optional(
                    OPT_PHOTO_URL_TEMPLATE,
                    description={"suggested_value": values.get(OPT_PHOTO_URL_TEMPLATE)},
                ): str,
                vol.Required(OPT_ALLOW_NON_ADMIN, default=values[OPT_ALLOW_NON_ADMIN]): BooleanSelector(),
            }
        )
        renewal = vol.Schema(
            {
                vol.Required(
                    OPT_RENEW_BEFORE_EXPIRY, default=values[OPT_RENEW_BEFORE_EXPIRY]
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=60, step=1, mode=NumberSelectorMode.BOX,
                        unit_of_measurement="j",
                    )
                ),
                vol.Required(OPT_RENEW_DAYS, default=values[OPT_RENEW_DAYS]): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=365, step=1, mode=NumberSelectorMode.BOX,
                        unit_of_measurement="j",
                    )
                ),
                vol.Required(OPT_PASSWORD_LENGTH, default=values[OPT_PASSWORD_LENGTH]): NumberSelector(
                    NumberSelectorConfig(min=8, max=64, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(OPT_PASSWORD_SYMBOLS, default=values[OPT_PASSWORD_SYMBOLS]): BooleanSelector(),
                vol.Required(
                    OPT_CHANGE_ENDPOINT, default=values[OPT_CHANGE_ENDPOINT]
                ): str,
                vol.Required(OPT_CHANGE_PAYLOAD, default=values[OPT_CHANGE_PAYLOAD]): TextSelector(
                    TextSelectorConfig(multiline=True)
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(SECTION_GENERAL): section(general, {"collapsed": False}),
                    vol.Required(SECTION_RENEWAL): section(renewal, {"collapsed": True}),
                }
            ),
            errors=errors,
        )
