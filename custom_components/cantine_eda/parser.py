"""Lecture tolérante des réponses Moneweb.

Seuls quelques champs du tableau de bord sont connus avec certitude
(`Solde`, `DateDernierPassage`, `Tickets`). Le reste est détecté par
noms de clés candidats, sans tenir compte de la casse, pour que
l'intégration continue de fonctionner si le format varie légèrement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
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
PHOTO_KEY_RE = re.compile(r"photo|image|picture|img|clich", re.IGNORECASE)
# Clés qui ressemblent à une photo mais contiennent un montant (ex. TotalPlateau)
NOT_PHOTO_KEY_RE = re.compile(r"total|montant|prix|solde|nombre|nb", re.IGNORECASE)

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
    if isinstance(value, float):
        return None
    if isinstance(value, int):
        return ("id", str(value))
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
            if (
                PHOTO_KEY_RE.search(str(key))
                and not NOT_PHOTO_KEY_RE.search(str(key))
                and not isinstance(value, (dict, list))
            ):
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


ACTIVITY_LABELS = {
    "SELF": "Self",
    "BAR&FOYER": "Bar & foyer",
    "RESTAURATION RAPIDE": "Restauration rapide",
    "E-RECHARGEMENT": "Rechargement en ligne",
}


def activity_label(activity: Any) -> str | None:
    """« RESTAURATION RAPIDE » -> « Restauration rapide »."""
    if activity in (None, ""):
        return None
    text = str(activity).strip()
    return ACTIVITY_LABELS.get(text.upper(), text.capitalize())


@dataclass(slots=True)
class Ticket:
    """Un passage à la cantine (ou un rechargement)."""

    date: datetime | None
    amount: float | None  # montant débité (positif), négatif pour un remboursement
    label: str | None
    ticket_id: str | None
    order_id: str | None = None
    activity: str | None = None
    balance_after: float | None = None
    is_reload: bool = False
    lines: list[dict[str, Any]] = field(default_factory=list)
    photo: PhotoRef | None = None
    detail: TicketDetail | None = None

    @property
    def meal(self) -> str | None:
        """Ce qui a été consommé, d'après le ticket de caisse."""
        return self.detail.summary if self.detail else None

    def as_dict(self) -> dict[str, Any]:
        """Représentation sérialisable (attributs / carte)."""
        return {
            "date": self.date.isoformat() if self.date else None,
            "montant": self.amount,
            "libelle": self.label,
            "activite": self.activity,
            "repas": self.meal,
            "solde_apres": self.balance_after,
            "id": self.ticket_id,
            "lignes": self.detail.products if self.detail else self.lines,
            "prix_complet": self.detail.full_price if self.detail else None,
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
            if label is None and amount is None:
                continue
            lines.append({"libelle": label, "montant": amount})
    return lines


def parse_ticket(raw: Any, pictures: dict[str, str] | None = None) -> Ticket | None:
    """Normalise un ticket Moneweb.

    Format observé : IdTicket, IdCommande, DateTicket, Activite, AncienSolde,
    NouveauSolde, TotalFinancier (rechargement), TotalPlateau (repas).
    """
    if not isinstance(raw, dict):
        return None
    ticket_id = find_key(raw, ("IdTicket", *ID_KEYS))
    order_id = find_key(raw, ("IdCommande",))
    activity = find_key(raw, ("Activite",))
    old = parse_amount(find_key(raw, ("AncienSolde",)))
    new = parse_amount(find_key(raw, ("NouveauSolde",)))
    financier = parse_amount(find_key(raw, ("TotalFinancier",))) or 0.0
    tray = parse_amount(find_key(raw, ("TotalPlateau",)))

    if old is not None and new is not None:
        amount: float | None = round(old - new, 2)
    elif tray is not None:
        amount = round(tray - financier, 2)
    else:
        amount = parse_amount(find_key(raw, AMOUNT_KEYS))
        if amount is not None:
            amount = abs(amount)

    is_reload = bool(
        (activity and "RECHARG" in str(activity).upper())
        or (financier > 0 and not tray)
    )
    lines = _parse_lines(find_key(raw, LINES_KEYS))
    label = activity_label(activity) or find_key(raw, LABEL_KEYS)
    if label is None and lines:
        label = ", ".join(str(line["libelle"]) for line in lines if line.get("libelle"))

    photo: PhotoRef | None = None
    if pictures:
        for key in (order_id, ticket_id):
            if key is not None and (path := pictures.get(str(key))):
                photo = PhotoRef("url", "/" + str(path).lstrip("/"), "Asa.Pictures", str(key))
                break
    if photo is None and not pictures:
        photo = find_photo(raw, str(ticket_id) if ticket_id is not None else None)

    return Ticket(
        date=parse_datetime(find_key(raw, ("DateTicket", *DATE_KEYS))),
        amount=amount,
        label=str(label) if label is not None else None,
        ticket_id=str(ticket_id) if ticket_id is not None else None,
        order_id=str(order_id) if order_id is not None else None,
        activity=str(activity) if activity is not None else None,
        balance_after=new,
        is_reload=is_reload,
        lines=lines,
        photo=photo,
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
    tickets: list[Ticket]  # passages uniquement (sans les rechargements), du plus récent au plus ancien
    payments: list[Payment]
    photo: PhotoRef | None
    raw: dict[str, Any]

    @property
    def last_ticket(self) -> Ticket | None:
        """Passage le plus récent."""
        return self.tickets[0] if self.tickets else None

    @property
    def photo_ticket(self) -> Ticket | None:
        """Passage correspondant à la photo affichée."""
        return next((t for t in self.tickets if t.photo is not None), None)

    @property
    def last_payment(self) -> Payment | None:
        """Rechargement le plus récent."""
        return self.payments[0] if self.payments else None

    def _this_month(self, now: datetime) -> list[Ticket]:
        local_now = dt_util.as_local(now)
        return [
            t
            for t in self.tickets
            if t.date
            and (d := dt_util.as_local(t.date)).year == local_now.year
            and d.month == local_now.month
        ]

    def month_spending(self, now: datetime) -> float | None:
        """Total dépensé sur le mois en cours."""
        if not self.tickets:
            return None
        return round(sum(t.amount or 0 for t in self._this_month(now)), 2)

    def month_passages(self, now: datetime) -> int | None:
        """Nombre de passages sur le mois en cours."""
        if not self.tickets:
            return None
        return len(self._this_month(now))


def _sort_key(item: Ticket | Payment) -> float:
    return item.date.timestamp() if item.date else float("-inf")


def _as_list(value: Any) -> list[Any]:
    """Moneweb renvoie {"Delta": bool, "List": [...]} ; on accepte aussi une liste."""
    if isinstance(value, dict):
        value = find_key(value, ("List", "Items", "Liste"))
    return value if isinstance(value, list) else []


def _pictures(raw: dict[str, Any]) -> dict[str, str]:
    """SubModels.Asa.Pictures : {IdCommande: "clients/api/acquisition/<id>"}."""
    node: Any = raw
    for key in ("SubModels", "Asa", "Pictures"):
        node = find_key(node, (key,))
        if node is None:
            return {}
    if not isinstance(node, dict):
        return {}
    return {str(k): str(v) for k, v in node.items() if isinstance(v, str) and v}


def parse_dashboard(raw: dict[str, Any]) -> CantineData:
    """Normalise la réponse de /clients/api/compte/dashboard."""
    pictures = _pictures(raw)
    all_tickets = [
        t
        for t in (parse_ticket(item, pictures) for item in _as_list(find_key(raw, TICKET_LIST_KEYS)))
        if t is not None
    ]
    all_tickets.sort(key=_sort_key, reverse=True)
    tickets = [t for t in all_tickets if not t.is_reload]

    payments: list[Payment] = []
    for item in _as_list(find_key(raw, PAYMENT_LIST_KEYS)):
        if not isinstance(item, dict) or find_key(item, ("Enregistre",)) is False:
            continue
        payments.append(
            Payment(
                date=parse_datetime(find_key(item, ("DateReponse", "DateCreation", *DATE_KEYS))),
                amount=parse_amount(find_key(item, AMOUNT_KEYS)),
                label=find_key(item, LABEL_KEYS) or "Rechargement en ligne",
            )
        )
    if not payments:
        payments = [
            Payment(t.date, abs(t.amount) if t.amount is not None else None, t.label)
            for t in all_tickets
            if t.is_reload
        ]
    payments.sort(key=_sort_key, reverse=True)

    last_passage = tickets[0].date if tickets and tickets[0].date else None
    if last_passage is None:
        last_passage = parse_datetime(find_key(raw, ("DateDernierPassage",)))

    photo_ticket = next((t for t in tickets if t.photo is not None), None)
    photo = photo_ticket.photo if photo_ticket else None

    return CantineData(
        balance=parse_amount(find_key(raw, ("Solde", "Balance"))),
        last_passage=last_passage,
        tickets=tickets,
        payments=payments,
        photo=photo,
        raw=raw,
    )


# --------------------------------------------------------------------------
# Ticket de caisse : GET /clients/api/ticket/display/{IdTicket}
# Réponse : {"html": "<pre>…</pre><pre>…</pre>", "id": …, "number": …, "date": …}
# Le HTML est l'impression du ticket, une ligne par <pre>, en colonnes :
#   "  Qté Produit                     PU       TTC  "
#   "D   1 Repas de       25,00 P   4,640 €    4,64 €"
#   "      service                                   "   <- suite du libellé
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_PRODUCTS_HEADER_RE = re.compile(r"Qt[ée]\s+Produit", re.IGNORECASE)
_ITEM_RE = re.compile(
    r"^\s*(?:[A-Z]\s+)?(?P<qty>\d+(?:,\d+)?)\s+(?P<name>\S.*?)\s{2,}"
    r"(?:(?P<pts>-?[\d\s]+,\d+)\s*P\s+)?"
    r"(?P<pu>-?[\d\s]+,\d+)\s*€\s+(?P<ttc>-?[\d\s]+,\d+)\s*€\s*$"
)
_AMOUNT_LINE_RE = re.compile(r"^\s*(?:\d+\s+)?(?P<label>\S.*?)\s{2,}(?P<amount>-?[\d\s]+,\d+)\s*€\s*$")
_POINTS_SUFFIX_RE = re.compile(r"\s+\d+\s*pts?$", re.IGNORECASE)


def _quantity(text: str) -> int | float | None:
    value = parse_amount(text)
    return int(value) if value is not None and value.is_integer() else value


def _ticket_lines(html_text: str) -> list[str]:
    parts = re.split(r"</pre\s*>", html_text, flags=re.IGNORECASE)
    return [unescape(_TAG_RE.sub("", part)).replace("\xa0", " ").rstrip() for part in parts]


@dataclass(slots=True)
class TicketDetail:
    """Contenu d'un ticket de caisse."""

    products: list[dict[str, Any]]
    total: float | None  # payé par le compte
    full_price: float | None  # prix TTC avant participations
    subsidies: list[dict[str, Any]]
    sections: list[str]

    @property
    def summary(self) -> str | None:
        """« Repas de service, Yaourt aux fruits et lait entier »."""
        names = [p["libelle"] for p in self.products if p.get("libelle")]
        return ", ".join(names) if names else None

    def as_dict(self) -> dict[str, Any]:
        """Représentation sérialisable."""
        return {
            "produits": self.products,
            "total": self.total,
            "prix_complet": self.full_price,
            "participations": self.subsidies,
        }


def parse_ticket_html(html_text: str) -> TicketDetail | None:
    """Extrait les produits, le total et les participations d'un ticket."""
    if not isinstance(html_text, str) or not html_text:
        return None
    lines = _ticket_lines(html_text)
    products: list[dict[str, Any]] = []
    subsidies: list[dict[str, Any]] = []
    sections: list[str] = []
    total = full_price = None
    in_products = False
    current: dict[str, Any] | None = None

    for index, line in enumerate(lines):
        stripped = line.strip()
        if _PRODUCTS_HEADER_RE.search(line):
            in_products = True
            current = None
            # Le titre centré juste au-dessus est l'activité (SELF, BAR&FOYER…)
            if index and lines[index - 1].strip():
                sections.append(lines[index - 1].strip())
            continue
        if in_products:
            if not stripped or stripped.upper().startswith("SOUS TOTAL"):
                in_products = False
                current = None
            elif match := _ITEM_RE.match(line):
                current = {
                    "libelle": match.group("name").strip(),
                    "quantite": _quantity(match.group("qty")),
                    "montant": parse_amount(match.group("ttc").replace(" ", "")),
                }
                products.append(current)
                continue
            elif current is not None and "€" not in line and line.startswith("    "):
                current["libelle"] = f"{current['libelle']} {stripped}"
                continue
        upper = stripped.upper()
        if upper.startswith("SOUS TOTAL"):
            amounts = re.findall(r"-?[\d\s]+,\d+\s*€", stripped)
            if amounts:
                full_price = parse_amount(amounts[-1].replace(" ", ""))
        elif upper.startswith("TOTAL TICKET"):
            amounts = re.findall(r"-?[\d\s]+,\d+\s*€", stripped)
            if amounts:
                total = parse_amount(amounts[-1].replace(" ", ""))
        elif upper.lstrip("0123456789 ").startswith("PARTICIPATION"):
            if match := _AMOUNT_LINE_RE.match(line):
                subsidies.append(
                    {
                        "libelle": match.group("label").strip(),
                        "montant": parse_amount(match.group("amount").replace(" ", "")),
                    }
                )

    for product in products:
        product["libelle"] = _POINTS_SUFFIX_RE.sub("", " ".join(product["libelle"].split()))
    if not products and total is None:
        return None
    return TicketDetail(products, total, full_price, subsidies, sections)
