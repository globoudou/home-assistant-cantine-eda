"""Faux serveur Moneweb pour les tests."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
import pytest

from custom_components.cantine_eda.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
)

# PNG 1x1
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Active les intégrations personnalisées."""


@dataclass
class FakeMoneweb:
    """État du faux site."""

    username: str = "ELEVE"
    password: str = "ancien-mdp"
    change_status: int = 200
    change_applies: bool = True
    logins: list[str] = field(default_factory=list)
    change_payloads: list[dict[str, Any]] = field(default_factory=list)
    url: str = ""

    def dashboard(self) -> dict[str, Any]:
        return {
            "Solde": 3.5,
            "DateDernierPassage": "2026-10-19T12:05:00",
            "Tickets": [
                {
                    "Id": 41,
                    "Instant": "2026-10-16T12:01:00",
                    "Montant": -3.45,
                    "Libelle": "Repas",
                },
                {
                    "Id": 42,
                    "Instant": "2026-10-19T12:05:00",
                    "Montant": -3.45,
                    "Lignes": [
                        {"Libelle": "Carottes râpées", "Montant": 0.8},
                        {"Libelle": "Poulet basquaise", "Montant": 2.65},
                    ],
                    "PhotoPlateau": "/clients/api/ticket/42/photo",
                },
            ],
            "Versements": [{"Instant": "2026-10-01T09:00:00", "Montant": 30}],
        }


@pytest.fixture
async def moneweb(socket_enabled: Any) -> FakeMoneweb:
    """Démarre un faux site Moneweb sur 127.0.0.1."""
    state = FakeMoneweb()

    def authed(request: web.Request) -> bool:
        return request.cookies.get("session") == "ok"

    async def sign_in(request: web.Request) -> web.Response:
        body = await request.json()
        state.logins.append(body["Password"])
        if body["ID"] != state.username or body["Password"] != state.password:
            return web.json_response({"Message": "Identifiants invalides"}, status=401)
        resp = web.json_response({"Nom": "Test"})
        resp.set_cookie("session", "ok")
        return resp

    async def dashboard(request: web.Request) -> web.Response:
        if not authed(request):
            return web.json_response({}, status=401)
        return web.json_response(state.dashboard())

    async def photo(request: web.Request) -> web.Response:
        if not authed(request):
            return web.Response(status=401)
        return web.Response(body=PNG, content_type="image/png")

    async def change(request: web.Request) -> web.Response:
        if not authed(request):
            return web.json_response({}, status=401)
        body = await request.json()
        state.change_payloads.append(body)
        if state.change_status >= 400:
            return web.json_response({"Message": "Erreur"}, status=state.change_status)
        if body.get("OldPassword") != state.password:
            return web.json_response({"Message": "Ancien mot de passe faux"}, status=400)
        if state.change_applies:
            state.password = body["NewPassword"]
        return web.json_response({"Success": True})

    app = web.Application()
    app.router.add_post("/clients/api/sign/in", sign_in)
    app.router.add_post("/clients/api/compte/dashboard", dashboard)
    app.router.add_get("/clients/api/ticket/42/photo", photo)
    app.router.add_post("/clients/api/compte/password", change)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    state.url = f"http://127.0.0.1:{port}"
    yield state
    await runner.cleanup()


@pytest.fixture
def entry_data(moneweb: FakeMoneweb) -> dict[str, Any]:
    """Données d'entrée valides."""
    return {
        CONF_HOST: moneweb.url,
        CONF_USERNAME: moneweb.username,
        CONF_PASSWORD: moneweb.password,
        CONF_VERIFY_SSL: True,
    }


__all__ = ["DOMAIN", "PNG", "FakeMoneweb"]
