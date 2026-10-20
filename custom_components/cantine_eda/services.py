"""Actions (services) : récupérer, renouveler ou définir le mot de passe."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    CONF_PASSWORD,
    CONF_PENDING_PASSWORD,
    CONF_PREVIOUS_PASSWORD,
    DOMAIN,
    SERVICE_GET_PASSWORD,
    SERVICE_RENEW_PASSWORD,
    SERVICE_SET_PASSWORD,
)

if TYPE_CHECKING:
    from .coordinator import MonewebCoordinator

ATTR_PASSWORD = "password"

ENTRY_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})
RENEW_SCHEMA = ENTRY_SCHEMA.extend({vol.Optional(ATTR_PASSWORD): cv.string})
SET_SCHEMA = ENTRY_SCHEMA.extend({vol.Required(ATTR_PASSWORD): cv.string})


def get_coordinator(hass: HomeAssistant, entry_id: str) -> MonewebCoordinator:
    """Coordinateur d'un compte chargé."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"account": entry.title},
        )
    return entry.runtime_data


async def _async_require_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """Les mots de passe ne sont lisibles que par un administrateur (ou une automatisation)."""
    if call.context.user_id is None:
        return
    user = await hass.auth.async_get_user(call.context.user_id)
    if user is None or not user.is_admin:
        raise Unauthorized(context=call.context)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Enregistre les actions."""

    async def get_password(call: ServiceCall) -> ServiceResponse:
        await _async_require_admin(hass, call)
        coordinator = get_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        data = coordinator.config_entry.data
        return {
            "identifiant": coordinator.client.username,
            "mot_de_passe": data.get(CONF_PASSWORD),
            "mot_de_passe_precedent": data.get(CONF_PREVIOUS_PASSWORD),
            "mot_de_passe_en_cours_de_changement": data.get(CONF_PENDING_PASSWORD),
            "change_le": coordinator.password_changed_at.isoformat()
            if coordinator.password_changed_at
            else None,
            "url_rechargement": coordinator.recharge_url,
        }

    async def renew_password(call: ServiceCall) -> ServiceResponse:
        await _async_require_admin(hass, call)
        coordinator = get_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        new = await coordinator.async_renew_password(call.data.get(ATTR_PASSWORD))
        if call.return_response:
            return {"identifiant": coordinator.client.username, "mot_de_passe": new}
        return None

    async def set_password(call: ServiceCall) -> None:
        await _async_require_admin(hass, call)
        coordinator = get_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        await coordinator.async_set_password(call.data[ATTR_PASSWORD])
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PASSWORD,
        get_password,
        schema=ENTRY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RENEW_PASSWORD,
        renew_password,
        schema=RENEW_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PASSWORD, set_password, schema=SET_SCHEMA
    )
