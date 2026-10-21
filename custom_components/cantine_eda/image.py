"""Photo du dernier plateau."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MonewebCoordinator
from .entity import MonewebEntity

if TYPE_CHECKING:
    from . import MonewebConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MonewebConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crée l'entité image."""
    async_add_entities([MonewebTrayPhoto(entry.runtime_data)])


class MonewebTrayPhoto(MonewebEntity, ImageEntity):
    """Dernière photo de plateau trouvée sur le compte."""

    def __init__(self, coordinator: MonewebCoordinator) -> None:
        """Initialise l'image."""
        MonewebEntity.__init__(self, coordinator, "tray_photo")
        ImageEntity.__init__(self, coordinator.hass)
        self._fingerprint: str | None = None
        self._sync()

    @callback
    def _sync(self) -> None:
        if self.coordinator.photo_fingerprint != self._fingerprint:
            self._fingerprint = self.coordinator.photo_fingerprint
            self._attr_content_type = self.coordinator.photo_content_type
            self._attr_image_last_updated = self.coordinator.photo_updated
            self._cached_image = None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Disponible dès qu'une photo a été récupérée."""
        return super().available and self.coordinator.photo is not None

    async def async_image(self) -> bytes | None:
        """Octets de la photo."""
        return self.coordinator.photo

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Ticket correspondant."""
        ticket = self.coordinator.data.photo_ticket if self.coordinator.data else None
        if ticket is None:
            return None
        return {
            "date": ticket.date.isoformat() if ticket.date else None,
            "libelle": ticket.label,
            "montant": ticket.amount,
        }
