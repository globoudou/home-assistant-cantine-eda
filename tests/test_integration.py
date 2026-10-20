"""Tests de bout en bout contre le faux site."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cantine_eda.const import (
    CONF_PASSWORD,
    CONF_PENDING_PASSWORD,
    CONF_PREVIOUS_PASSWORD,
    DOMAIN,
    OPT_CHANGE_ENDPOINT,
)

from .conftest import PNG, FakeMoneweb


async def _setup(hass: HomeAssistant, data: dict[str, Any], options: dict | None = None):
    entry = MockConfigEntry(domain=DOMAIN, title=data["username"], data=data, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant, moneweb: FakeMoneweb, entry_data) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    bad = {**entry_data, CONF_PASSWORD: "faux"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], entry_data)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ELEVE"
    await hass.async_block_till_done()

    # Deuxième ajout du même compte -> refusé ; un autre compte serait accepté
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], entry_data)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_entities(hass: HomeAssistant, moneweb: FakeMoneweb, entry_data, hass_client) -> None:
    await _setup(hass, entry_data)

    balance = hass.states.get("sensor.cantine_eleve_solde") or hass.states.get(
        "sensor.cantine_eleve_balance"
    )
    assert balance is not None, [s.entity_id for s in hass.states.async_all()]
    assert float(balance.state) == 3.5
    attrs = balance.attributes
    assert attrs["identifiant"] == "ELEVE"
    assert attrs["tickets"][0]["id"] == "42"  # le plus récent d'abord
    assert attrs["tickets"][0]["libelle"] == "Carottes râpées, Poulet basquaise"
    assert attrs["entite_photo"].startswith("image.")
    assert attrs["url_rechargement"].endswith("/clients/")

    low = [s for s in hass.states.async_all("binary_sensor")][0]
    assert low.state == "on"  # 3,5 € < 5 €

    image_id = attrs["entite_photo"]
    image = hass.states.get(image_id)
    assert image.state not in ("unavailable", "unknown")
    client = await hass_client()
    resp = await client.get(f"/api/image_proxy/{image_id}")
    assert resp.status == 200
    assert await resp.read() == PNG

    errors = [s.entity_id for s in hass.states.async_all() if s.state == "unavailable" and "renew" not in s.entity_id]
    assert not errors


async def test_get_password_service(hass: HomeAssistant, moneweb: FakeMoneweb, entry_data) -> None:
    entry = await _setup(hass, entry_data)
    resp = await hass.services.async_call(
        DOMAIN, "get_password", {"config_entry_id": entry.entry_id},
        blocking=True, return_response=True,
    )
    assert resp["identifiant"] == "ELEVE"
    assert resp["mot_de_passe"] == "ancien-mdp"


async def test_renew_password(hass: HomeAssistant, moneweb: FakeMoneweb, entry_data) -> None:
    entry = await _setup(
        hass, entry_data, {OPT_CHANGE_ENDPOINT: "/clients/api/compte/password"}
    )
    resp = await hass.services.async_call(
        DOMAIN, "renew_password", {"config_entry_id": entry.entry_id},
        blocking=True, return_response=True,
    )
    new = resp["mot_de_passe"]
    assert new != "ancien-mdp" and len(new) == 16
    assert moneweb.password == new
    assert entry.data[CONF_PASSWORD] == new
    assert entry.data[CONF_PREVIOUS_PASSWORD] == "ancien-mdp"
    assert CONF_PENDING_PASSWORD not in entry.data
    # Toujours chargé (pas de rechargement intempestif) et fonctionnel
    assert entry.state is config_entries.ConfigEntryState.LOADED
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.last_update_success


async def test_renew_failure_keeps_old(hass: HomeAssistant, moneweb: FakeMoneweb, entry_data) -> None:
    entry = await _setup(
        hass, entry_data, {OPT_CHANGE_ENDPOINT: "/clients/api/compte/password"}
    )
    moneweb.change_status = 500
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "renew_password", {"config_entry_id": entry.entry_id}, blocking=True
        )
    assert entry.data[CONF_PASSWORD] == "ancien-mdp"
    assert CONF_PENDING_PASSWORD not in entry.data
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"renew_failed_{entry.entry_id}")


async def test_recovers_pending_password(hass: HomeAssistant, moneweb: FakeMoneweb, entry_data) -> None:
    """HA s'est arrêté entre le changement sur le site et l'enregistrement."""
    data = {**entry_data, CONF_PENDING_PASSWORD: "nouveau-mdp"}
    moneweb.password = "nouveau-mdp"
    entry = await _setup(hass, data)
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert entry.data[CONF_PASSWORD] == "nouveau-mdp"
    assert entry.data[CONF_PREVIOUS_PASSWORD] == "ancien-mdp"


async def test_bad_password_triggers_reauth(hass: HomeAssistant, moneweb: FakeMoneweb, entry_data) -> None:
    moneweb.password = "change-ailleurs"
    entry = MockConfigEntry(domain=DOMAIN, title="ELEVE", data=entry_data)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == "reauth"


async def test_websocket_credentials(hass: HomeAssistant, moneweb: FakeMoneweb, entry_data, hass_ws_client) -> None:
    await _setup(hass, entry_data)
    balance = next(
        s for s in hass.states.async_all("sensor") if "identifiant" in s.attributes
    )
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "cantine_eda/credentials", "entity_id": balance.entity_id})
    msg = await ws.receive_json()
    assert msg["success"], msg
    assert msg["result"]["password"] == "ancien-mdp"


async def test_options_flow(hass: HomeAssistant, moneweb: FakeMoneweb, entry_data) -> None:
    entry = await _setup(hass, entry_data)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "general": {"scan_interval": 15, "low_balance_threshold": 2, "allow_non_admin": False},
            "renewal": {
                "renew_interval_days": 90,
                "password_length": 20,
                "password_symbols": False,
                "password_change_endpoint": "/clients/api/compte/password",
                "password_change_payload": '{"OldPassword": "{old}", "NewPassword": "{new}"}',
            },
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["renew_interval_days"] == 90
    assert entry.state is config_entries.ConfigEntryState.LOADED
    low = [s for s in hass.states.async_all("binary_sensor")][0]
    assert low.state == "off"  # 3,5 € > 2 €
