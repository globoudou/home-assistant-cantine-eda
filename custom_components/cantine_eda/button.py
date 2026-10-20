"""Boutons : actualiser et renouveler le mot de passe."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
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
    """Crée les boutons."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MonewebRefreshButton(coordinator, "refresh"),
            MonewebRenewButton(coordinator, "renew_password"),
        ]
    )


class MonewebRefreshButton(MonewebEntity, ButtonEntity):
    """Force la mise à jour du compte."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Actualise."""
        await self.coordinator.async_request_refresh()


class MonewebRenewButton(MonewebEntity, ButtonEntity):
    """Génère un nouveau mot de passe et le change sur le site."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        """Disponible seulement si l'endpoint de changement est configuré."""
        return super().available and self.coordinator.can_renew

    async def async_press(self) -> None:
        """Renouvelle."""
        await self.coordinator.async_renew_password()
