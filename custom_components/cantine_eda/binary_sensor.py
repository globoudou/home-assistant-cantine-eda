"""Alerte solde bas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import MonewebEntity

if TYPE_CHECKING:
    from . import MonewebConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MonewebConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crée le capteur binaire."""
    async_add_entities([MonewebLowBalance(entry.runtime_data, "low_balance")])


class MonewebLowBalance(MonewebEntity, BinarySensorEntity):
    """Allumé quand le solde passe sous le seuil configuré."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool | None:
        """Solde sous le seuil ?"""
        balance = self.coordinator.data.balance
        if balance is None:
            return None
        return balance < self.coordinator.low_balance_threshold

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Seuil utilisé."""
        return {"seuil": self.coordinator.low_balance_threshold}
