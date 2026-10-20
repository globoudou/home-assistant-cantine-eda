"""Lecture tolérante des réponses Moneweb.

Seuls quelques champs du tableau de bord sont connus avec certitude
(`Solde`, `DateDernierPassage`, `Tickets`). Le reste est détecté par
noms de clés candidats, sans tenir compte de la casse, pour que
l'intégration continue de fonctionner si le format varie légèrement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any

from homeassistant.util import dt as dt_util

DATE_KEYS = (
    "Instant",
    "Date",
    "DateHeure",
    "DatePassage",
    "DateTicket",
    "Horodatage",
    "DateCreation",
)
AMOUNT_KEYS = ("Montant", "MontantTotal", "Total", "Prix", "Valeur", "Amount")
LABEL_KEYS = ("Libelle", "Designation", "Description", "Nom", "Intitule", "Label")
LINES_KEYS = ("Lignes", "Articles", "Details", "Produits", "Items", "Composants")
ID_KEYS = ("Id", "ID", "IdTicket", "TicketId", "Numero", "Guid")
TICKET_LIST_KEYS = ("Tickets",)
PAYMENT_LIST_KEYS = ("Versements", "Rechargements", "Paiements")
PHOTO_KEY_RE = re.compile(r"photo|image|plateau|picture|img|clich", re.IGNORECASE)

_NET_DATE_RE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")
_B64_PREFIXES = ("/9j/", "iVBOR", "R0lGOD", "UklGR", "data:image")


def find_key(obj: Any, candidates: tuple[str, ...]) -> Any:
    """Retourne la première valeur dont la clé correspond (insensible à la casse)."""
    if not isinstance(obj, dict):
        return None
    lowered = {str(k).lower(): v for k, v in obj.items()}
    for cand in candidates:
        value = lowered.get(cand.lower())
        if value not in (None, ""):
            return value
    return None


def parse_datetime(value: Any) -> datetime | None:
    """Convertit une date Moneweb (ISO, /Date(ms)/ ou epoch) en datetime aware."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str):
        if match := _NET_DATE_RE.search(value):
            return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=UTC)
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return None
        if parsed.tzinfo is None:
            # Le site est en France : heure locale de Home Assistant
            parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
        return parsed
    return None


def parse_amount(value: Any) -> float | None:
    """Convertit un montant (nombre ou texte « 3,45 € ») en float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, str):
        cleaned = value.replace("€", "").replace("\xa0", "").replace(" ", "")
        cleaned = cleaned.replace(",", ".")
        try:
            return round(float(cleaned), 2)
        except ValueError:
            return None
    return None


@dataclass(slots=True)
class PhotoRef:
    """Référence vers une photo trouvée dans la réponse."""

    kind: str  # "base64" | "url" | "id"
    value: str
    key: str
    ticket_id: str | None = None

    @property
    def fingerprint(self) -> str:
        """Identifiant stable pour savoir si la photo a changé."""
        if self.kind == "base64":
            return f"b64:{len(self.value)}:{self.value[-32:]}"
        return f"{self.kind}:{self.value}"


def _classify_photo(value: Any) -> tuple[str, str] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return ("id", str(int(value)))
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith(_B64_PREFIXES) or (len(text) > 512 and " " not in text):
        return ("base64", text)
    if text.startswith(("http://", "https://", "/")) or "." in text.rsplit("/", 1)[-1]:
        return ("url", text)
    return ("id", text)


def find_photo(obj: Any, ticket_id: str | None = None, depth: int = 0) -> PhotoRef | None:
    """Cherche récursivement un champ ressemblant à une photo de plateau."""
    if depth > 4:
        return None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if PHOTO_KEY_RE.search(str(key)) and not isinstance(value, (dict, list)):
                if classified := _classify_photo(value):
                    return PhotoRef(classified[0], classified[1], str(key), ticket_id)
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                if PHOTO_KEY_RE.search(str(key)) and isinstance(value, dict):
                    # ex. {"Photo": {"Url": "..."}} ou {"Photo": {"Id": 12}}
                    inner = find_key(value, ("Url", "Uri", "Href", "Data", "Base64", "Contenu"))
                    if inner is None:
                        inner = find_key(value, ID_KEYS)
                    if classified := _classify_photo(inner):
                        return PhotoRef(classified[0], classified[1], str(key), ticket_id)
                if found := find_photo(value, ticket_id, depth + 1):
                    return found
    elif isinstance(obj, list):
        for item in obj:
            if found := find_photo(item, ticket_id, depth + 1):
                return found
    return None


@dataclass(slots=True)
class Ticket:
    """Un passage à la cantine."""

    date: datetime | None
    amount: float | None
    label: str | None
    ticket_id: str | None
    lines: list[dict[str, Any]] = field(default_factory=list)
    photo: PhotoRef | None = None

    def as_dict(self) -> dict[str, Any]:
        """Représentation sérialisable (attributs / carte)."""
        return {
            "date": self.date.isoformat() if self.date else None,
            "montant": self.amount,
            "libelle": self.label,
            "id": self.ticket_id,
            "lignes": self.lines,
            "photo": self.photo is not None,
        }


def _parse_lines(raw: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return lines
    for item in raw:
        if isinstance(item, str):
            lines.append({"libelle": item, "montant": None})
        elif isinstance(item, dict):
            label = find_key(item, LABEL_KEYS)
            amount = parse_amount(find_key(item, AMOUNT_KEYS))
            qty = find_key(item, ("Quantite", "Qte", "Quantity"))
            if label is None and amount is None:
                continue
            line: dict[str, Any] = {"libelle": label, "montant": amount}
            if qty not in (None, 1, "1"):
                line["quantite"] = qty
            lines.append(line)
    return lines


def parse_ticket(raw: Any) -> Ticket | None:
    """Normalise un ticket brut."""
    if not isinstance(raw, dict):
        return None
    ticket_id = find_key(raw, ID_KEYS)
    ticket_id = str(ticket_id) if ticket_id is not None else None
    lines = _parse_lines(find_key(raw, LINES_KEYS))
    label = find_key(raw, LABEL_KEYS)
    if label is None and lines:
        label = ", ".join(str(line["libelle"]) for line in lines if line.get("libelle"))
    amount = parse_amount(find_key(raw, AMOUNT_KEYS))
    if amount is None and lines and all(line.get("montant") is not None for line in lines):
        amount = round(sum(line["montant"] for line in lines), 2)
    return Ticket(
        date=parse_datetime(find_key(raw, DATE_KEYS)),
        amount=amount,
        label=str(label) if label is not None else None,
        ticket_id=ticket_id,
        lines=lines,
        photo=find_photo(raw, ticket_id),
    )


@dataclass(slots=True)
class Payment:
    """Un rechargement du compte."""

    date: datetime | None
    amount: float | None
    label: str | None

    def as_dict(self) -> dict[str, Any]:
        """Représentation sérialisable."""
        return {
            "date": self.date.isoformat() if self.date else None,
            "montant": self.amount,
            "libelle": self.label,
        }


@dataclass(slots=True)
class CantineData:
    """Données normalisées d'un compte."""

    balance: float | None
    last_passage: datetime | None
    tickets: list[Ticket]
    payments: list[Payment]
    photo: PhotoRef | None
    raw: dict[str, Any]

    @property
    def last_ticket(self) -> Ticket | None:
        """Ticket le plus récent."""
        return self.tickets[0] if self.tickets else None

    @property
    def last_payment(self) -> Payment | None:
        """Rechargement le plus récent."""
        return self.payments[0] if self.payments else None

    def month_spending(self, now: datetime) -> float | None:
        """Total dépensé sur le mois en cours (d'après les tickets reçus)."""
        dated = [t for t in self.tickets if t.date and t.amount is not None]
        if not dated:
            return None
        local_now = dt_util.as_local(now)
        total = sum(
            t.amount
            for t in dated
            if (d := dt_util.as_local(t.date)).year == local_now.year
            and d.month == local_now.month
        )
        return round(abs(total), 2)

    def month_passages(self, now: datetime) -> int | None:
        """Nombre de passages sur le mois en cours."""
        dated = [t for t in self.tickets if t.date]
        if not dated:
            return None
        local_now = dt_util.as_local(now)
        return sum(
            1
            for t in dated
            if (d := dt_util.as_local(t.date)).year == local_now.year
            and d.month == local_now.month
        )


def _sort_key(item: Ticket | Payment) -> float:
    return item.date.timestamp() if item.date else float("-inf")


def parse_dashboard(raw: dict[str, Any]) -> CantineData:
    """Normalise la réponse de /compte/dashboard."""
    tickets_raw = find_key(raw, TICKET_LIST_KEYS)
    if not isinstance(tickets_raw, list):
        tickets_raw = []
    tickets = [t for t in map(parse_ticket, tickets_raw) if t is not None]
    tickets.sort(key=_sort_key, reverse=True)

    payments_raw = find_key(raw, PAYMENT_LIST_KEYS)
    if not isinstance(payments_raw, list):
        payments_raw = []
    payments: list[Payment] = []
    for item in payments_raw:
        if isinstance(item, dict):
            payments.append(
                Payment(
                    date=parse_datetime(find_key(item, DATE_KEYS)),
                    amount=parse_amount(find_key(item, AMOUNT_KEYS)),
                    label=find_key(item, LABEL_KEYS),
                )
            )
    payments.sort(key=_sort_key, reverse=True)

    last_passage = parse_datetime(find_key(raw, ("DateDernierPassage",)))
    if last_passage is None and tickets:
        last_passage = tickets[0].date

    # Photo : d'abord dans le dernier ticket, sinon ailleurs dans la réponse
    photo = next((t.photo for t in tickets if t.photo), None)
    if photo is None:
        photo = find_photo({k: v for k, v in raw.items() if k not in ("Tickets",)})

    return CantineData(
        balance=parse_amount(find_key(raw, ("Solde", "Balance"))),
        last_passage=last_passage,
        tickets=tickets,
        payments=payments,
        photo=photo,
        raw=raw,
    )
