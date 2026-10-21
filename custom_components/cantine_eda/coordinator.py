"""Coordinateur : rafraîchissement du compte et renouvellement du mot de passe."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    MonewebAuthError,
    MonewebClient,
    MonewebError,
    MonewebSSLError,
    generate_password,
)
from .const import (
    CLIENT_PATH,
    CONF_PASSWORD,
    CONF_PASSWORD_CHANGED_AT,
    CONF_PENDING_PASSWORD,
    CONF_PREVIOUS_PASSWORD,
    DEFAULT_CHANGE_ENDPOINT,
    DEFAULT_CHANGE_PAYLOAD,
    DEFAULT_LOW_BALANCE,
    DEFAULT_PASSWORD_LENGTH,
    DEFAULT_PASSWORD_SYMBOLS,
    DEFAULT_RENEW_BEFORE_EXPIRY,
    DEFAULT_RENEW_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_RENEW_FAILED,
    OPT_CHANGE_ENDPOINT,
    OPT_CHANGE_PAYLOAD,
    OPT_LOW_BALANCE,
    OPT_PASSWORD_LENGTH,
    OPT_PASSWORD_SYMBOLS,
    OPT_PHOTO_URL_TEMPLATE,
    OPT_RECHARGE_URL,
    OPT_RENEW_BEFORE_EXPIRY,
    OPT_RENEW_DAYS,
    OPT_SCAN_INTERVAL,
    TICKET_DETAILS_COUNT,
)
from .parser import CantineData, TicketDetail, parse_dashboard, parse_ticket_html

if TYPE_CHECKING:
    from . import MonewebConfigEntry

_LOGGER = logging.getLogger(__name__)

EVENT_PASSWORD_RENEWED = f"{DOMAIN}_password_renewed"
RETRY_AFTER_FAILURE = timedelta(hours=24)


def _anonymize_ticket(html: str | None) -> list[str]:
    """Lignes du ticket à partir de « Ancien solde » (le nom figure avant)."""
    if not html:
        return []
    from .parser import _ticket_lines  # noqa: PLC0415

    lines = _ticket_lines(html)
    start = next((i for i, l in enumerate(lines) if "ANCIEN SOLDE" in l.upper()), len(lines))
    return lines[start : start + 40]


class MonewebCoordinator(DataUpdateCoordinator[CantineData]):
    """Gère un compte cantine."""

    config_entry: MonewebConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: MonewebConfigEntry, client: MonewebClient
    ) -> None:
        """Initialise le coordinateur."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.username}",
            update_interval=timedelta(
                minutes=entry.options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.photo: bytes | None = None
        self.photo_content_type = "image/jpeg"
        self.photo_fingerprint: str | None = None
        self.photo_updated: datetime | None = None
        self.ticket_details: dict[str, TicketDetail | None] = {}
        # Tickets non reconnus, gardés (anonymisés) pour les diagnostics
        self.unparsed_tickets: dict[str, list[str]] = {}
        self.renew_lock = asyncio.Lock()
        self.last_renew_attempt: datetime | None = None
        self.last_renew_error: str | None = None
        # Instantané des options : on ne recharge l'entrée que si elles changent
        self.options_snapshot = dict(entry.options)

    # ------------------------------------------------------------------ options
    def opt(self, key: str, default: Any) -> Any:
        """Lecture d'une option avec valeur par défaut."""
        value = self.config_entry.options.get(key)
        return default if value in (None, "") else value

    @property
    def recharge_url(self) -> str:
        """Page à ouvrir pour recharger le compte."""
        return self.opt(OPT_RECHARGE_URL, "") or self.client.url(CLIENT_PATH)

    @property
    def low_balance_threshold(self) -> float:
        """Seuil de solde bas."""
        return float(self.opt(OPT_LOW_BALANCE, DEFAULT_LOW_BALANCE))

    @property
    def can_renew(self) -> bool:
        """Le renouvellement est-il configuré ?"""
        return bool(self.opt(OPT_CHANGE_ENDPOINT, DEFAULT_CHANGE_ENDPOINT))

    @property
    def password_changed_at(self) -> datetime | None:
        """Date du dernier changement de mot de passe connu."""
        value = self.config_entry.data.get(CONF_PASSWORD_CHANGED_AT)
        return dt_util.parse_datetime(value) if value else None

    @property
    def password_expires(self) -> date | None:
        """Date d'expiration annoncée par le site à la dernière connexion."""
        return self.client.password_expires

    @property
    def next_renewal(self) -> datetime | None:
        """Date du prochain renouvellement automatique (None = désactivé)."""
        if not self.can_renew:
            return None
        candidates: list[datetime] = []
        if self.client.must_change_password:
            candidates.append(dt_util.utcnow())
        before = int(self.opt(OPT_RENEW_BEFORE_EXPIRY, DEFAULT_RENEW_BEFORE_EXPIRY))
        if before > 0 and self.password_expires:
            # Le matin, heure locale, N jours avant l'expiration
            due_day = self.password_expires - timedelta(days=before)
            candidates.append(
                datetime.combine(due_day, time(9, 0), tzinfo=dt_util.get_default_time_zone())
            )
        days = int(self.opt(OPT_RENEW_DAYS, DEFAULT_RENEW_DAYS))
        if days > 0:
            base = self.password_changed_at or dt_util.utcnow()
            candidates.append(base + timedelta(days=days))
        if not candidates:
            return None
        due = min(candidates)
        if self.last_renew_error and self.last_renew_attempt:
            due = max(due, self.last_renew_attempt + RETRY_AFTER_FAILURE)
        return due

    # ------------------------------------------------------------------ données
    def _update_entry_data(self, changes: dict[str, Any], remove: tuple[str, ...] = ()) -> None:
        data = {**self.config_entry.data, **changes}
        for key in remove:
            data.pop(key, None)
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)

    async def _async_recover_password(self) -> bool:
        """Essaie les mots de passe connus (en cours / précédent) après un refus."""
        data = self.config_entry.data
        current = data.get(CONF_PASSWORD)
        for key in (CONF_PENDING_PASSWORD, CONF_PREVIOUS_PASSWORD):
            candidate = data.get(key)
            if not candidate or candidate == current:
                continue
            if await self.client.async_check_password(candidate):
                _LOGGER.warning(
                    "Compte %s : le mot de passe enregistré est refusé, mais le "
                    "mot de passe « %s » fonctionne ; il devient le mot de passe courant",
                    self.client.username,
                    "en cours de renouvellement" if key == CONF_PENDING_PASSWORD else "précédent",
                )
                self.client.password = candidate
                changes = {CONF_PASSWORD: candidate}
                if key == CONF_PENDING_PASSWORD:
                    changes[CONF_PREVIOUS_PASSWORD] = current
                    changes[CONF_PASSWORD_CHANGED_AT] = dt_util.utcnow().isoformat()
                self._update_entry_data(changes, remove=(CONF_PENDING_PASSWORD,))
                return True
        return False

    async def _async_update_data(self) -> CantineData:
        try:
            try:
                raw = await self.client.async_get_dashboard()
            except MonewebAuthError as err:
                if not await self._async_recover_password():
                    raise ConfigEntryAuthFailed(
                        translation_domain=DOMAIN, translation_key="invalid_auth"
                    ) from err
                raw = await self.client.async_get_dashboard()
        except MonewebSSLError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="ssl_error",
                translation_placeholders={"error": str(err)},
            ) from err
        except MonewebAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except MonewebError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        data = parse_dashboard(raw)
        await self._async_update_details(data)
        await self._async_update_photo(data)
        return data

    async def _async_update_details(self, data: CantineData) -> None:
        """Lit le ticket de caisse des derniers passages (une seule fois par ticket)."""
        wanted = list(data.tickets[:TICKET_DETAILS_COUNT])
        if data.photo_ticket and data.photo_ticket not in wanted:
            wanted.append(data.photo_ticket)  # pour la légende de la photo
        for ticket in wanted:
            if not ticket.ticket_id:
                continue
            if ticket.ticket_id not in self.ticket_details:
                try:
                    html = await self.client.async_get_ticket_html(ticket.ticket_id)
                except MonewebError as err:
                    _LOGGER.debug("Ticket %s illisible : %s", ticket.ticket_id, err)
                    continue
                detail = parse_ticket_html(html) if html else None
                if detail is None or not detail.products:
                    _LOGGER.debug("Ticket %s : aucun produit reconnu", ticket.ticket_id)
                    self.unparsed_tickets[ticket.ticket_id] = _anonymize_ticket(html)
                self.ticket_details[ticket.ticket_id] = detail
            ticket.detail = self.ticket_details.get(ticket.ticket_id)
        # Le cache ne garde que les tickets encore affichés
        keep = {t.ticket_id for t in [*data.tickets[: TICKET_DETAILS_COUNT * 2], *wanted]}
        for key in [k for k in self.ticket_details if k not in keep]:
            del self.ticket_details[key]

    async def _async_update_photo(self, data: CantineData) -> None:
        ref = data.photo
        if ref is None or ref.fingerprint == self.photo_fingerprint:
            return
        try:
            result = await self.client.async_fetch_photo(
                ref, self.opt(OPT_PHOTO_URL_TEMPLATE, "") or None
            )
        except MonewebError as err:
            _LOGGER.debug("Photo du plateau indisponible : %s", err)
            return
        if result is None:
            return
        self.photo, self.photo_content_type = result
        self.photo_fingerprint = ref.fingerprint
        ticket = data.photo_ticket
        self.photo_updated = (ticket.date if ticket else None) or dt_util.utcnow()

    # --------------------------------------------------------- mot de passe
    async def async_set_password(self, password: str) -> None:
        """Enregistre un mot de passe changé à la main (après vérification)."""
        if not await self.client.async_check_password(password):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="password_rejected"
            )
        old = self.client.password
        self.client.password = password
        self._update_entry_data(
            {
                CONF_PASSWORD: password,
                CONF_PREVIOUS_PASSWORD: old,
                CONF_PASSWORD_CHANGED_AT: dt_util.utcnow().isoformat(),
            },
            remove=(CONF_PENDING_PASSWORD,),
        )
        ir.async_delete_issue(self.hass, DOMAIN, f"{ISSUE_RENEW_FAILED}_{self.config_entry.entry_id}")

    async def async_renew_password(self, new_password: str | None = None) -> str:
        """Change le mot de passe sur le site, sans jamais perdre l'ancien."""
        if not self.can_renew:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="renew_not_configured"
            )
        async with self.renew_lock:
            self.last_renew_attempt = dt_util.utcnow()
            try:
                new = await self._async_do_renew(new_password)
            except HomeAssistantError as err:
                self.last_renew_error = str(err)
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"{ISSUE_RENEW_FAILED}_{self.config_entry.entry_id}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_RENEW_FAILED,
                    translation_placeholders={
                        "account": self.config_entry.title,
                        "error": str(err),
                    },
                )
                raise
            self.last_renew_error = None
            ir.async_delete_issue(
                self.hass, DOMAIN, f"{ISSUE_RENEW_FAILED}_{self.config_entry.entry_id}"
            )
            self.hass.bus.async_fire(
                EVENT_PASSWORD_RENEWED,
                {"config_entry_id": self.config_entry.entry_id, "account": self.client.username},
            )
            self.async_update_listeners()
            return new

    async def _async_do_renew(self, new_password: str | None) -> str:
        old = self.client.password
        new = new_password or generate_password(
            int(self.opt(OPT_PASSWORD_LENGTH, DEFAULT_PASSWORD_LENGTH)),
            bool(self.config_entry.options.get(OPT_PASSWORD_SYMBOLS, DEFAULT_PASSWORD_SYMBOLS)),
        )
        # 1) Le nouveau mot de passe est sauvegardé AVANT d'appeler le site
        self._update_entry_data({CONF_PENDING_PASSWORD: new})

        change_error: MonewebError | None = None
        try:
            await self.client.async_login()
            await self.client.async_change_password(
                old,
                new,
                self.opt(OPT_CHANGE_ENDPOINT, DEFAULT_CHANGE_ENDPOINT),
                self.opt(OPT_CHANGE_PAYLOAD, DEFAULT_CHANGE_PAYLOAD),
            )
        except MonewebError as err:
            change_error = err

        # 2) Vérification réelle : on se reconnecte avec le nouveau mot de passe
        try:
            new_ok = await self.client.async_check_password(new)
        except MonewebError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="renew_unverified",
                translation_placeholders={"error": str(err)},
            ) from err

        if new_ok:
            self.client.password = new
            self._update_entry_data(
                {
                    CONF_PASSWORD: new,
                    CONF_PREVIOUS_PASSWORD: old,
                    CONF_PASSWORD_CHANGED_AT: dt_util.utcnow().isoformat(),
                },
                remove=(CONF_PENDING_PASSWORD,),
            )
            _LOGGER.info("Compte %s : mot de passe renouvelé", self.client.username)
            return new

        # 3) Échec : l'ancien mot de passe fonctionne-t-il toujours ?
        old_ok = await self.client.async_check_password(old)
        if old_ok:
            self._update_entry_data({}, remove=(CONF_PENDING_PASSWORD,))
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="renew_failed",
                translation_placeholders={
                    "error": str(change_error) if change_error else "mot de passe inchangé"
                },
            )
        # Aucun des deux ne marche : on garde tout pour pouvoir les récupérer
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="renew_unverified",
            translation_placeholders={
                "error": str(change_error) if change_error else "aucun mot de passe accepté"
            },
        )

    async def async_check_auto_renew(self, _now: datetime | None = None) -> None:
        """Appelé toutes les heures : renouvelle le mot de passe s'il est temps."""
        if self.renew_lock.locked() or not self.can_renew:
            return
        days = int(self.opt(OPT_RENEW_DAYS, DEFAULT_RENEW_DAYS))
        if days > 0 and self.password_changed_at is None:
            # Point de départ du compte à rebours périodique
            self._update_entry_data({CONF_PASSWORD_CHANGED_AT: dt_util.utcnow().isoformat()})
        due = self.next_renewal
        if due is None or dt_util.utcnow() < due:
            return
        try:
            await self.async_renew_password()
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Renouvellement automatique du mot de passe de %s impossible : %s",
                self.client.username,
                err,
            )
