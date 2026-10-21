"""Capteurs du compte cantine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import MonewebCoordinator
from .entity import MonewebEntity

if TYPE_CHECKING:
    from . import MonewebConfigEntry

MAX_TICKETS_ATTR = 15
MAX_PAYMENTS_ATTR = 5


@dataclass(frozen=True, kw_only=True)
class MonewebSensorDescription(SensorEntityDescription):
    """Description d'un capteur."""

    value_fn: Callable[[MonewebCoordinator], Any]


def _truncate(value: Any) -> str | None:
    return str(value)[:255] if value else None


def _last_ticket_attr(coordinator: MonewebCoordinator, attr: str) -> Any:
    ticket = coordinator.data.last_ticket
    return getattr(ticket, attr) if ticket else None


SENSORS: tuple[MonewebSensorDescription, ...] = (
    MonewebSensorDescription(
        key="balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda c: c.data.balance,
    ),
    MonewebSensorDescription(
        key="last_passage",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.data.last_passage,
    ),
    MonewebSensorDescription(
        key="last_meal",
        value_fn=lambda c: _truncate(_last_ticket_attr(c, "meal") or _last_ticket_attr(c, "label")),
    ),
    MonewebSensorDescription(
        key="last_activity",
        value_fn=lambda c: _last_ticket_attr(c, "label"),
    ),
    MonewebSensorDescription(
        key="last_ticket_amount",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=lambda c: _last_ticket_attr(c, "amount"),
    ),
    MonewebSensorDescription(
        key="month_spending",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=lambda c: c.data.month_spending(dt_util.utcnow()),
    ),
    MonewebSensorDescription(
        key="month_passages",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.data.month_passages(dt_util.utcnow()),
    ),
    MonewebSensorDescription(
        key="last_payment",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=lambda c: c.data.last_payment.amount if c.data.last_payment else None,
    ),
    MonewebSensorDescription(
        key="password_expires",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda c: c.password_expires,
    ),
    MonewebSensorDescription(
        key="password_changed_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.password_changed_at,
    ),
    MonewebSensorDescription(
        key="next_password_renewal",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.next_renewal,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MonewebConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crée les capteurs."""
    coordinator = entry.runtime_data
    async_add_entities(MonewebSensor(coordinator, desc) for desc in SENSORS)


class MonewebSensor(MonewebEntity, SensorEntity):
    """Capteur générique."""

    entity_description: MonewebSensorDescription
    # Gros attributs inutiles dans l'historique
    _unrecorded_attributes = frozenset(
        {"tickets", "versements", "lignes", "produits", "participations"}
    )

    def __init__(
        self, coordinator: MonewebCoordinator, description: MonewebSensorDescription
    ) -> None:
        """Initialise le capteur."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Valeur du capteur."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Attributs utilisés par la carte."""
        key = self.entity_description.key
        data = self.coordinator.data
        if key == "balance":
            return {
                "identifiant": self.coordinator.client.username,
                "url_rechargement": self.coordinator.recharge_url,
                "seuil_solde_bas": self.coordinator.low_balance_threshold,
                "dernier_passage": _iso(data.last_passage),
                "expiration_mot_de_passe": self.coordinator.password_expires.isoformat()
                if self.coordinator.password_expires
                else None,
                "entite_photo": self._sibling("image", "tray_photo"),
                "entite_renouvellement": self._sibling("button", "renew_password")
                if self.coordinator.can_renew
                else None,
                "tickets": [t.as_dict() for t in data.tickets[:MAX_TICKETS_ATTR]],
                "versements": [p.as_dict() for p in data.payments[:MAX_PAYMENTS_ATTR]],
            }
        if key in ("last_meal", "last_activity", "last_ticket_amount") and data.last_ticket:
            ticket = data.last_ticket
            attrs: dict[str, Any] = {
                "date": _iso(ticket.date),
                "activite": ticket.activity,
                "solde_apres": ticket.balance_after,
            }
            if key == "last_meal" and ticket.detail:
                attrs |= ticket.detail.as_dict()
            return attrs
        if key == "password_expires":
            return {"renouvellement_exige": self.coordinator.client.must_change_password}
        if key == "last_payment" and data.last_payment:
            return {"date": _iso(data.last_payment.date)}
        return None

    def _sibling(self, domain: str, key: str) -> str | None:
        return er.async_get(self.hass).async_get_entity_id(
            domain, DOMAIN, f"{self.coordinator.config_entry.entry_id}_{key}"
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
