"""Entité de base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import MonewebCoordinator


class MonewebEntity(CoordinatorEntity[MonewebCoordinator]):
    """Entité rattachée à un compte cantine."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MonewebCoordinator, key: str) -> None:
        """Initialise l'entité."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Cantine {entry.title}",
            manufacturer=MANUFACTURER,
            model=coordinator.client.base_url.removeprefix("https://"),
            configuration_url=coordinator.client.client_url,
            entry_type=DeviceEntryType.SERVICE,
        )
