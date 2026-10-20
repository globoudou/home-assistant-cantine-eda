"""Intégration Moneweb Cantine : solde, passages et photo du plateau."""

from __future__ import annotations

from datetime import timedelta
import logging
from pathlib import Path

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant
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
    """Sert la carte JS et l'injecte dans tous les tableaux de bord."""
    if hass.http is None:
        _LOGGER.warning("Serveur web indisponible : carte Cantine EDA non enregistrée")
        return
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    card_path = Path(__file__).parent / "frontend" / CARD_FILENAME
    try:
        version = (await async_get_integration(hass, DOMAIN)).version
        if not card_path.is_file():
            raise FileNotFoundError(card_path)
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(card_path), cache_headers=False)]
        )
        add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    except Exception:
        _LOGGER.exception("La carte Cantine EDA n'a pas pu être enregistrée")
        return
    _LOGGER.debug("Carte Cantine EDA servie sur %s (v%s)", CARD_URL, version)

    async def _resource(_event: Event | None = None) -> None:
        await _async_ensure_dashboard_resource(hass, f"{CARD_URL}?v={version}")

    if hass.is_running:
        hass.async_create_task(_resource())
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _resource)


async def _async_ensure_dashboard_resource(hass: HomeAssistant, url: str) -> None:
    """Ajoute (ou met à jour) la carte dans les ressources des tableaux de bord.

    L'appli mobile ne charge pas toujours les modules injectés par
    add_extra_js_url ; une ressource Lovelace est chargée partout. Seul le mode
    « stockage » (le mode par défaut) est modifiable.
    """
    try:
        from homeassistant.components.lovelace.const import (  # noqa: PLC0415
            LOVELACE_DATA,
        )

        data = hass.data.get(LOVELACE_DATA)
        resources = getattr(data, "resources", None)
        if resources is None or getattr(data, "resource_mode", "") != "storage":
            return
        if not resources.loaded:
            await resources.async_load()
            resources.loaded = True
        matches = [
            r
            for r in resources.async_items()
            if str(r.get("url", "")).split("?")[0] == CARD_URL
        ]
        if not matches:
            await resources.async_create_item({"res_type": "module", "url": url})
            _LOGGER.info("Ressource de tableau de bord ajoutée : %s", url)
            return
        first, *duplicates = matches
        if first.get("url") != url or first.get("type") != "module":
            await resources.async_update_item(
                first["id"], {"res_type": "module", "url": url}
            )
        for dup in duplicates:
            await resources.async_delete_item(dup["id"])
    except Exception:
        _LOGGER.exception("Impossible d'ajouter la carte aux ressources des tableaux de bord")


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
