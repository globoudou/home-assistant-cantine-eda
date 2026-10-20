"""Intégration Moneweb Cantine : solde, passages et photo du plateau."""

from __future__ import annotations

from datetime import timedelta
import logging
from pathlib import Path

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import MonewebClient
from .const import (
    CARD_FILENAME,
    CARD_URL,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
    RENEW_CHECK_INTERVAL_MINUTES,
)
from .coordinator import MonewebCoordinator
from .services import async_setup_services
from .websocket import async_setup_websocket

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.IMAGE, Platform.SENSOR]
FIRST_PLATFORMS = [Platform.BUTTON, Platform.IMAGE]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type MonewebConfigEntry = ConfigEntry[MonewebCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services, websocket et carte Lovelace (une seule fois pour tous les comptes)."""
    async_setup_services(hass)
    async_setup_websocket(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Sert la carte JS et la charge automatiquement dans le frontend."""
    if hass.http is None or "frontend_extra_module_url" not in hass.data:
        _LOGGER.debug("Interface web absente : carte non enregistrée")
        return
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    card_path = Path(__file__).parent / "frontend" / CARD_FILENAME
    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(card_path), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")


async def async_setup_entry(hass: HomeAssistant, entry: MonewebConfigEntry) -> bool:
    """Configure un compte."""
    session = async_create_clientsession(
        hass,
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, True),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    client = MonewebClient(
        session,
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_VERIFY_SSL, True),
    )
    coordinator = MonewebCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Image et boutons d'abord : le capteur Solde publie leurs entity_id pour la carte
    await hass.config_entries.async_forward_entry_setups(entry, FIRST_PLATFORMS)
    await hass.config_entries.async_forward_entry_setups(
        entry, [p for p in PLATFORMS if p not in FIRST_PLATFORMS]
    )

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            coordinator.async_check_auto_renew,
            timedelta(minutes=RENEW_CHECK_INTERVAL_MINUTES),
            name=f"{DOMAIN} renouvellement {entry.title}",
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: MonewebConfigEntry) -> None:
    """Recharge seulement si les options ont changé (pas lors d'un changement de mot de passe)."""
    coordinator = entry.runtime_data
    if dict(entry.options) != coordinator.options_snapshot:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MonewebConfigEntry) -> bool:
    """Décharge un compte."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
