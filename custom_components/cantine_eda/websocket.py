"""Commande websocket utilisée par la carte pour copier les identifiants."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_PASSWORD, DOMAIN, OPT_ALLOW_NON_ADMIN

WS_CREDENTIALS = f"{DOMAIN}/credentials"


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Enregistre la commande."""
    websocket_api.async_register_command(hass, ws_credentials)


@websocket_api.websocket_command(
    {vol.Required("type"): WS_CREDENTIALS, vol.Required("entity_id"): str}
)
@websocket_api.async_response
async def ws_credentials(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Renvoie identifiant + mot de passe du compte lié à une entité."""
    entity = er.async_get(hass).async_get(msg["entity_id"])
    entry = (
        hass.config_entries.async_get_entry(entity.config_entry_id)
        if entity and entity.config_entry_id
        else None
    )
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Compte introuvable")
        return

    allowed = connection.user.is_admin or entry.options.get(OPT_ALLOW_NON_ADMIN, False)
    if not allowed:
        connection.send_error(
            msg["id"],
            websocket_api.ERR_UNAUTHORIZED,
            "Seul un administrateur peut récupérer le mot de passe",
        )
        return

    coordinator = getattr(entry, "runtime_data", None)
    connection.send_result(
        msg["id"],
        {
            "username": coordinator.client.username if coordinator else None,
            "password": entry.data.get(CONF_PASSWORD),
            "recharge_url": coordinator.recharge_url if coordinator else None,
        },
    )
