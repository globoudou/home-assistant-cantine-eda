"""Diagnostics : réponse brute de l'API (anonymisée) pour affiner la détection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_PASSWORD,
    CONF_PENDING_PASSWORD,
    CONF_PREVIOUS_PASSWORD,
    CONF_USERNAME,
)

if TYPE_CHECKING:
    from . import MonewebConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_PREVIOUS_PASSWORD,
    CONF_PENDING_PASSWORD,
    CONF_USERNAME,
    "Nom",
    "Prenom",
    "NomPrenom",
    "Email",
    "Mail",
    "Adresse",
    "Telephone",
    "Tel",
    "Portable",
    "DateNaissance",
    "Iban",
    "IBAN",
}


def _shorten(node: Any) -> Any:
    """Tronque les longues chaînes (photos en base64) en gardant le début."""
    if isinstance(node, dict):
        return {k: _shorten(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_shorten(v) for v in node[:10]] + (
            [f"... {len(node) - 10} éléments de plus"] if len(node) > 10 else []
        )
    if isinstance(node, str) and len(node) > 200:
        return f"{node[:60]}... ({len(node)} caractères)"
    return node


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MonewebConfigEntry
) -> dict[str, Any]:
    """Diagnostics d'un compte."""
    coordinator = entry.runtime_data
    data = coordinator.data
    photo = data.photo if data else None
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "detection": {
            "solde": data.balance if data else None,
            "nb_tickets": len(data.tickets) if data else 0,
            "nb_versements": len(data.payments) if data else 0,
            "dernier_ticket": data.last_ticket.as_dict() if data and data.last_ticket else None,
            "photo_trouvee": (
                {"type": photo.kind, "champ": photo.key, "valeur": _shorten(photo.value)}
                if photo
                else None
            ),
            "photo_recuperee": coordinator.photo is not None,
            "renouvellement_configure": coordinator.can_renew,
            "derniere_erreur_renouvellement": coordinator.last_renew_error,
        },
        "tickets_lus": {
            ticket_id: (
                {"produits": d.products, "total": d.total, "lignes": d.raw_lines} if d else None
            )
            for ticket_id, d in coordinator.ticket_details.items()
        },
        "tickets_non_reconnus": coordinator.unparsed_tickets,
        "dashboard_brut": async_redact_data(_shorten(data.raw), TO_REDACT) if data else None,
    }
