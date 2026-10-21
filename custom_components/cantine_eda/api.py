"""Client HTTP pour le portail Moneweb (espace « clients »)."""

from __future__ import annotations

import asyncio
import base64
import binascii
from datetime import date
import json
import logging
import secrets
import string
from typing import Any
from urllib.parse import urljoin

import aiohttp

from .const import API_DASHBOARD, API_SIGN_IN, API_TICKET, CLIENT_PATH
from .parser import PhotoRef, find_photo

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)
JSON_HEADERS = {
    "Content-Type": "application/json;charset=utf-8",
    "Accept": "application/json, text/plain, */*",
}
# Symboles généralement acceptés par les formulaires web (pas de guillemets,
# antislash ni espace, qui posent souvent problème).
PASSWORD_SYMBOLS = "!#$%&()*+,-.:;=?@[]_{}"
MAX_PHOTO_BYTES = 8 * 1024 * 1024


def _parse_date(value: Any) -> date | None:
    """« 2027-01-18 » ou « 2027-01-18T00:00:00 » -> date."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class MonewebError(Exception):
    """Erreur générique Moneweb."""


class MonewebConnectionError(MonewebError):
    """Le site est injoignable."""


class MonewebSSLError(MonewebConnectionError):
    """Le certificat du site n'a pas pu être vérifié."""


class MonewebAuthError(MonewebError):
    """Identifiant ou mot de passe refusé."""


def normalize_base_url(host: str) -> str:
    """`eda-arcueil.moneweb.fr` -> `https://eda-arcueil.moneweb.fr`."""
    host = host.strip().rstrip("/")
    if host.endswith("/clients"):
        host = host[: -len("/clients")]
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    return host


def generate_password(length: int = 16, symbols: bool = True) -> str:
    """Mot de passe aléatoire contenant au moins une minuscule, majuscule, chiffre (et symbole)."""
    length = max(8, min(length, 64))
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits]
    if symbols:
        pools.append(PASSWORD_SYMBOLS)
    alphabet = "".join(pools)
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if all(any(c in pool for c in candidate) for pool in pools):
            return candidate


def render_payload(template: str, **values: str) -> Any:
    """Remplace {old}/{new}/{id} dans un gabarit JSON, sans casser l'échappement."""
    try:
        parsed = json.loads(template)
    except json.JSONDecodeError as err:
        raise MonewebError(f"Gabarit JSON invalide : {err}") from err

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if isinstance(node, str):
            for key, value in values.items():
                node = node.replace("{" + key + "}", value)
            return node
        return node

    return _walk(parsed)


class MonewebClient:
    """Un compte Moneweb (une session et un jeu de cookies dédiés)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
    ) -> None:
        """Initialise le client."""
        self._session = session
        self.base_url = normalize_base_url(host)
        self.username = username
        self.password = password
        self._ssl: bool | None = None if verify_ssl else False
        self._logged_in = False
        self._lock = asyncio.Lock()
        # Renseignés à chaque connexion réussie
        self.password_expires: date | None = None
        self.must_change_password = False

    def url(self, path: str) -> str:
        """URL absolue à partir d'un chemin relatif au site."""
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(self.base_url + "/", path.lstrip("/"))

    @property
    def client_url(self) -> str:
        """Page d'accueil de l'espace client."""
        return self.url(CLIENT_PATH)

    async def _request(
        self, method: str, path: str, payload: Any = None, *, raw: bool = False
    ) -> aiohttp.ClientResponse | tuple[int, Any]:
        try:
            resp = await self._session.request(
                method,
                self.url(path),
                json=payload,
                headers=JSON_HEADERS if payload is not None else None,
                timeout=TIMEOUT,
                ssl=self._ssl,
            )
        except aiohttp.ClientConnectorCertificateError as err:
            raise MonewebSSLError(str(err)) from err
        except aiohttp.ClientSSLError as err:
            raise MonewebSSLError(str(err)) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MonewebConnectionError(str(err)) from err
        if raw:
            return resp
        async with resp:
            text = await resp.text()
            try:
                body: Any = json.loads(text) if text else None
            except ValueError:
                body = text
            return resp.status, body

    async def _sign_in(self, password: str) -> None:
        status, body = await self._request(
            "POST",
            API_SIGN_IN,
            {"ID": self.username, "Password": password, "RememberMe": True},
        )
        _LOGGER.debug("sign/in -> HTTP %s", status)
        if status in (400, 401, 403):
            raise MonewebAuthError(f"Connexion refusée (HTTP {status})")
        if status >= 400:
            raise MonewebConnectionError(f"Erreur du site (HTTP {status})")
        if isinstance(body, dict):
            lowered = {str(k).lower(): v for k, v in body.items()}
            for key in ("success", "succes", "isauthenticated", "authenticated", "ok"):
                if lowered.get(key) is False:
                    raise MonewebAuthError("Identifiant ou mot de passe incorrect")
            # Réponse observée : {"ChangePassword": false, "Token": null,
            #                     "PasswordExpireDate": "2027-01-18"}
            self.must_change_password = bool(lowered.get("changepassword"))
            expire = lowered.get("passwordexpiredate")
            self.password_expires = _parse_date(expire)
        self._logged_in = True

    async def async_login(self) -> None:
        """Se connecte avec le mot de passe courant."""
        async with self._lock:
            await self._sign_in(self.password)

    async def async_check_password(self, password: str) -> bool:
        """Teste un mot de passe (la session reste ouverte s'il est bon)."""
        async with self._lock:
            try:
                await self._sign_in(password)
            except MonewebAuthError:
                self._logged_in = False
                return False
            return True

    async def _authenticated_post(self, path: str, payload: Any) -> tuple[int, Any]:
        return await self._authenticated("POST", path, payload)

    async def _authenticated(
        self, method: str, path: str, payload: Any = None
    ) -> tuple[int, Any]:
        if not self._logged_in:
            await self.async_login()
        status, body = await self._request(method, path, payload)
        if status in (401, 403, 419, 440):
            _LOGGER.debug("Session expirée (HTTP %s), nouvelle connexion", status)
            self._logged_in = False
            await self.async_login()
            status, body = await self._request(method, path, payload)
            if status in (401, 403):
                raise MonewebAuthError(f"Accès refusé après reconnexion (HTTP {status})")
        if status >= 400:
            raise MonewebConnectionError(f"Erreur du site sur {path} (HTTP {status})")
        return status, body

    async def async_get_dashboard(self) -> dict[str, Any]:
        """Tableau de bord du compte (solde, tickets…)."""
        _, body = await self._authenticated_post(
            API_DASHBOARD, {"LastTicketInstant": None, "LastVersementInstant": None}
        )
        if not isinstance(body, dict):
            # Une page HTML au lieu du JSON = session perdue
            self._logged_in = False
            await self.async_login()
            _, body = await self._authenticated_post(
                API_DASHBOARD, {"LastTicketInstant": None, "LastVersementInstant": None}
            )
        if not isinstance(body, dict):
            raise MonewebConnectionError("Réponse inattendue du tableau de bord")
        return body

    async def async_get_ticket_html(self, ticket_id: str) -> str | None:
        """Ticket de caisse (HTML) d'un passage."""
        _, body = await self._authenticated("GET", API_TICKET.format(ticket_id=ticket_id))
        if isinstance(body, dict) and isinstance(body.get("html"), str):
            return body["html"]
        return None

    async def async_fetch_photo(
        self, ref: PhotoRef, url_template: str | None
    ) -> tuple[bytes, str] | None:
        """Récupère les octets de la photo du plateau."""
        if ref.kind == "base64":
            data = ref.value
            content_type = "image/jpeg"
            if data.startswith("data:"):
                header, _, data = data.partition(",")
                content_type = header[5:].split(";")[0] or content_type
            try:
                image = base64.b64decode(data, validate=False)
            except (binascii.Error, ValueError):
                _LOGGER.debug("Champ %s : base64 illisible", ref.key)
                return None
            if image.startswith(b"\x89PNG"):
                content_type = "image/png"
            return image, content_type

        if ref.kind == "url":
            path = ref.value
        elif url_template:
            path = url_template.format(id=ref.value, ticket_id=ref.ticket_id or ref.value)
        else:
            _LOGGER.debug(
                "Photo référencée par l'identifiant %s (champ %s) : renseignez le "
                "modèle d'URL de la photo dans les options",
                ref.value,
                ref.key,
            )
            return None

        if not self._logged_in:
            await self.async_login()
        for attempt in range(2):
            resp = await self._request("GET", path, raw=True)
            async with resp:
                if resp.status in (401, 403) and attempt == 0:
                    self._logged_in = False
                    await self.async_login()
                    continue
                if resp.status >= 400:
                    _LOGGER.debug("Photo %s : HTTP %s", path, resp.status)
                    return None
                content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
                data = await resp.content.read(MAX_PHOTO_BYTES)
            if content_type.startswith("application/json") or data[:1] in (b"{", b'"'):
                # Certaines API renvoient la photo en base64 dans un JSON
                try:
                    decoded = json.loads(data)
                except ValueError:
                    return None
                if isinstance(decoded, dict):
                    inner = find_photo(decoded)
                    if inner is None:
                        inner = next(
                            (
                                PhotoRef("base64", v, k)
                                for k, v in decoded.items()
                                if isinstance(v, str) and len(v) > 512
                            ),
                            None,
                        )
                    if inner and inner.kind == "base64":
                        return await self.async_fetch_photo(inner, None)
                if isinstance(decoded, str):
                    return await self.async_fetch_photo(PhotoRef("base64", decoded, "json"), None)
                return None
            if not content_type.startswith("image/"):
                return None
            return data, content_type
        return None

    async def async_change_password(
        self, old: str, new: str, endpoint: str, payload_template: str
    ) -> None:
        """Appelle l'API de changement de mot de passe du site."""
        if not endpoint:
            raise MonewebError("Endpoint de changement de mot de passe non configuré")
        payload = render_payload(payload_template, old=old, new=new, id=self.username)
        async with self._lock:
            if not self._logged_in:
                await self._sign_in(old)
            status, body = await self._request("POST", endpoint, payload)
        _LOGGER.debug("Changement de mot de passe -> HTTP %s", status)
        if status >= 400:
            message = body if isinstance(body, str) else json.dumps(body)[:200]
            raise MonewebError(f"Le site a refusé le changement (HTTP {status}) : {message}")
        if isinstance(body, dict):
            lowered = {str(k).lower(): v for k, v in body.items()}
            for key in ("success", "succes", "ok"):
                if lowered.get(key) is False:
                    raise MonewebError(f"Le site a refusé le changement : {body}")
