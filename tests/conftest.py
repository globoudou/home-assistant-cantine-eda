"""Faux serveur Moneweb pour les tests."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import date, timedelta
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


def ticket_html(section: str, items: list[tuple[str, str, str]], total: str) -> str:
    """Ticket de caisse au format Moneweb (une ligne par <pre>), contenu inventé."""
    rows = [
        '<pre style="text-align : center;"><span style="font-size: 1.5em;">ARCUEIL</span></pre>',
        "<pre> </pre>",
        '<pre style="text-align : left;"><span style="font-size: 1.5em;">PRENOM  </span>'
        '<span style="font-size: 1.5em;">NOM</span></pre>',
        "<pre>D   1 D admission                         9,87 €</pre>",
        "<pre> </pre>",
        f'<pre style="text-align : center;"><span style="text-decoration : underline;">{section}</span></pre>',
        "<pre>  Qté Produit                     PU       TTC  </pre>",
    ]
    for first, rest, price in items:
        rows.append(f'<pre style="text-align : left;">D   1 {first:<14}25,00 P   {price}0 €    {price} €</pre>')
        for part in rest.split("|"):
            if part:
                rows.append(f'<pre style="text-align : left;">      {part:<42}</pre>')
    rows += [
        "<pre> </pre>",
        '<pre><span style="text-decoration : underline;">SOUS TOTAL              </span>'
        '<span style="text-decoration : underline;">  13,70 € </span><span>(HT) </span>'
        '<span style="text-decoration : underline;">  15,07 €</span></pre>',
        "<pre>    1 Participation sur admission         9,87 €</pre>",
        "<pre>    1 Participation point alimentaire     1,54 €</pre>",
        "<pre> </pre>",
        '<pre><span style="font-size: 1.5em;text-decoration : underline;">TOTAL TICKET           </span>'
        f'<span style="font-size: 1.5em;text-decoration : underline;">   {total} €</span></pre>',
        '<pre style="text-align : center;">Bon app&eacute;tit</pre>',
    ]
    return "".join(rows)


TICKETS_HTML = {
    "2010000000000043": ticket_html(
        "SELF", [("Repas de", "service|25pts", "4,64"), ("Yaourt aux", "fruits et|lait entier", "0,56")], "3,45"
    ),
    "2010000000000045": ticket_html("BAR&FOYER", [("Café", "", "1,20"), ("Croissant", "", "2,30")], "3,50"),
}


@dataclass
class FakeMoneweb:
    """État du faux site (format relevé sur eda-arcueil.moneweb.fr)."""

    username: str = "ELEVE"
    password: str = "ancien-mdp"
    change_status: int = 200
    change_applies: bool = True
    expire: str = "2027-01-18"
    must_change: bool = False
    logins: list[str] = field(default_factory=list)
    change_payloads: list[dict[str, Any]] = field(default_factory=list)
    ticket_requests: list[str] = field(default_factory=list)
    url: str = ""

    def dashboard(self) -> dict[str, Any]:
        def day(n: int, hour: str = "12:05:00") -> str:
            return (date.today() - timedelta(days=n)).isoformat() + "T" + hour

        tickets = [
            # Ordre volontairement mélangé, comme sur le vrai site
            {"IdTicket": 410079, "IdCommande": 410088, "IdCompte": 0, "DateTicket": day(20, "09:13:25"),
             "Activite": "E-RECHARGEMENT", "AncienSolde": 1.0, "NouveauSolde": 31.0,
             "TotalFinancier": 30.0, "TotalTTC": 0.0, "TotalPlateau": 0.0},
            {"IdTicket": 2010000000000041, "IdCommande": 2010000000000040, "IdCompte": 0,
             "DateTicket": day(3), "Activite": "SELF", "AncienSolde": 13.9, "NouveauSolde": 10.45,
             "TotalFinancier": 0.0, "TotalTTC": 0.0, "TotalPlateau": 3.45},
            {"IdTicket": 2010000000000043, "IdCommande": 2010000000000042, "IdCompte": 0,
             "DateTicket": day(1), "Activite": "SELF", "AncienSolde": 6.95, "NouveauSolde": 3.5,
             "TotalFinancier": 0.0, "TotalTTC": 0.0, "TotalPlateau": 3.45},
            {"IdTicket": 2010000000000045, "IdCommande": 2010000000000044, "IdCompte": 0,
             "DateTicket": day(2, "08:04:36"), "Activite": "BAR&FOYER", "AncienSolde": 10.45,
             "NouveauSolde": 6.95, "TotalFinancier": 0.0, "TotalTTC": 0.0, "TotalPlateau": 3.5},
        ]
        return {
            "Solde": 3.5,
            "DateDernierPassage": date.today().isoformat(),
            "Tickets": {"Delta": False, "List": tickets},
            "CGVValidees": False,
            "Versements": {"Delta": False, "List": [
                {"Id": 89270, "DateCreation": day(20, "09:12:53"), "DateReponse": day(20, "09:13:25"),
                 "Montant": 30.0, "EtatTransaction": 2, "Enregistre": True,
                 "ConfirmationEnAttente": False, "CleConfirmation": None},
            ]},
            "Messages": [],
            "ImmediateShowMessage": False,
            "SubModels": {
                "Asa": {"Pictures": {
                    "2010000000000040": "clients/api/acquisition/2010000000000301",
                    "2010000000000042": "clients/api/acquisition/2010000000000302",
                }},
                "Reservation": {"NombreReservations": 0},
            },
            "Id": 1,
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
        resp = web.json_response(
            {"ChangePassword": state.must_change, "Token": None, "PasswordExpireDate": state.expire}
        )
        resp.set_cookie("session", "ok")
        return resp

    async def dashboard(request: web.Request) -> web.Response:
        if not authed(request):
            return web.json_response({}, status=401)
        return web.json_response(state.dashboard())

    async def photo(request: web.Request) -> web.Response:
        if not authed(request):
            return web.Response(status=401)
        return web.Response(body=PNG, content_type="image/jpeg")

    async def change(request: web.Request) -> web.Response:
        if not authed(request):
            return web.json_response({}, status=401)
        body = await request.json()
        state.change_payloads.append(body)
        if state.change_status >= 400:
            return web.json_response({"Message": "Erreur"}, status=state.change_status)
        if body.get("OldPassword") != state.password or body.get("NewPassword") != body.get("ConfirmPassword"):
            return web.json_response({"Message": "Refusé"}, status=400)
        if state.change_applies:
            state.password = body["NewPassword"]
            state.expire = (date.today() + timedelta(days=89)).isoformat()
            state.must_change = False
        # Le vrai site répond 200 avec un corps vide (text/plain)
        return web.Response(status=200, content_type="text/plain")

    async def ticket(request: web.Request) -> web.Response:
        if not authed(request):
            return web.json_response({}, status=401)
        ticket_id = request.match_info["id"]
        state.ticket_requests.append(ticket_id)
        html = TICKETS_HTML.get(ticket_id, ticket_html("SELF", [("Repas", "", "3,45")], "3,45"))
        return web.json_response({"html": html, "id": int(ticket_id), "number": 1, "date": "x"})

    app = web.Application()
    app.router.add_get("/clients/api/ticket/display/{id}", ticket)
    app.router.add_post("/clients/api/sign/in", sign_in)
    app.router.add_post("/clients/api/compte/dashboard", dashboard)
    app.router.add_get("/clients/api/acquisition/{id}", photo)
    app.router.add_post("/clients/api/password", change)
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
