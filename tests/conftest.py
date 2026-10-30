"""Nachgebautes Panoptes (aiohttp auf 127.0.0.1) nach HA_VERTRAG.md – kein echtes Auto."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

from aiohttp import web
import pytest

from homeassistant.core import HomeAssistant

from custom_components.panoptes.const import (
    CONF_AUTO_ID,
    CONF_KOPPLUNG_ID,
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
    OPT_EINSTELLUNGEN,
    OPT_POSITION,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

TOKEN = "jwt-ha-1"
AUTO_ID = "a-7f3c"

ZUSTAND: dict[str, Any] = {
    "version": 1,
    "geraet": {"autoId": AUTO_ID, "name": "Dolphin Surf", "modell": "Dolphin Surf", "appVersion": "0.1.7", "dilink": 3},
    "telemetrie": {"soc": 81.0, "ev_range_km": 260, "volt_12v": 13.1, "is_charging": False, "is_parked": True},
    "felder": [
        {"key": "soc", "titel": "Ladestand", "typ": "sensor", "deviceClass": "battery", "stateClass": "measurement", "einheit": "%", "icon": "mdi:battery"},
        {"key": "ev_range_km", "titel": "Reichweite", "typ": "sensor", "deviceClass": "distance", "einheit": "km"},
        {"key": "volt_12v", "titel": "12-V-Batterie", "typ": "sensor", "deviceClass": "voltage", "stateClass": "measurement", "einheit": "V"},
        {"key": "lat", "titel": "Breite", "typ": "sensor"},
        {"key": "is_charging", "titel": "Lädt", "typ": "binary", "deviceClass": "battery_charging"},
        {"key": "is_parked", "titel": "Geparkt", "typ": "binary"},
    ],
    "waechter": {"zustand": "scharf", "grund": None, "seit": 1761040000000},
    "gesundheit": {"zustand": "ok", "punkte": [{"id": "kamera", "ok": True, "titel": "Kameras"}]},
    "f1": {"stufe": "reicht", "grund": None},
    "ruhezeit": {"aktiv": False, "bis": None},
    "position": None,
}

SCHEMA: dict[str, Any] = {
    "version": 1,
    "etag": "s-1",
    "gruppen": [
        {
            "id": "system",
            "titel": "System",
            "felder": [
                {"key": "planner.band", "typ": "bool", "titel": "F1-Band", "standard": True, "nurLesendExtern": False, "neustartNoetig": False, "sichtbar": True},
                {"key": "planner.reserve", "typ": "int", "titel": "Reserve", "standard": 0, "min": 0, "max": 50, "schritt": 1, "einheit": "%", "nurLesendExtern": False, "neustartNoetig": False, "sichtbar": True},
                {"key": "remote.tailscale.enabled", "typ": "bool", "titel": "Tailscale", "standard": True, "nurLesendExtern": True, "neustartNoetig": False, "sichtbar": True},
                {"key": "planner.ohneAngabe", "typ": "bool", "titel": "Ohne Angabe", "standard": True, "neustartNoetig": False, "sichtbar": True},
            ],
        },
        {
            "id": "waechter",
            "titel": "Wächter",
            "felder": [
                {"key": "deterrent.light", "typ": "bool", "titel": "Licht", "standard": False, "nurLesendExtern": False, "gefahr": True, "neustartNoetig": False, "sichtbar": True, "alias": "surveillance.deterrentAction"},
                {"key": "deterrent.horn", "typ": "bool", "titel": "Hupe", "standard": False, "nurLesendExtern": False, "neustartNoetig": False, "sichtbar": True},
                {"key": "surveillance.triggerMode", "typ": "auswahl", "titel": "Auslöser", "standard": None, "nurLesendExtern": False, "neustartNoetig": False, "sichtbar": True, "bestaetigung": "guardLegal.trigger",
                 "optionen": [{"wert": "shock", "titel": "Erschütterung"}]},
                {"key": "surveillance.sensitivity", "typ": "auswahl", "titel": "Empfindlichkeit", "standard": "normal", "nurLesendExtern": False, "neustartNoetig": False, "sichtbar": True,
                 "optionen": [{"wert": "niedrig", "titel": "Niedrig"}, {"wert": "normal", "titel": "Normal"}, {"wert": "hoch", "titel": "Hoch", "gesperrt": True, "gesperrtGrund": "ch"}]},
                {"key": "surveillance.retentionHours", "typ": "int", "titel": "Behalten", "standard": 72, "min": 1, "max": 720, "einheit": "h", "nurLesendExtern": False, "neustartNoetig": False, "sichtbar": True},
            ],
        },
    ],
}

REGELN = [
    {"id": "r-garage", "name": "Garage", "ausloesbar": True, "grund": None},
    {"id": "r-klima", "name": "Vorklimatisieren", "ausloesbar": False, "grund": "fahrzeugaktion"},
]

VERBOTENE_PRAEFIXE = (
    "/api/vehicle", "/api/quick-controls", "/api/keymap", "/api/automations", "/api/start",
    "/api/stop", "/api/stream", "/api/kopplung/", "/api/kopplungen", "/api/system/tailscale",
    "/api/surveillance/enable", "/api/surveillance/disable", "/auth/token",
)


class FakePanoptes:
    """Zustand + Protokoll aller Aufrufe."""

    def __init__(self) -> None:
        self.aufrufe: list[tuple[str, str, Any]] = []
        self.zustand = copy.deepcopy(ZUSTAND)
        self.schema = copy.deepcopy(SCHEMA)
        self.regeln = copy.deepcopy(REGELN)
        self.werte: dict[str, Any] = {"planner.band": {"wert": True, "gesetzt": True}, "planner.reserve": {"wert": 10, "gesetzt": True}}
        self.etag = 1
        self.anfrage_status = "erlaubt"  # was beim Abholen kommt (nach 1× wartet)
        self.abgefragt = 0
        self.gueltige_tokens = {TOKEN}
        self.mit_sse = True
        self.mit_ha_api = True
        self.ereignisse: asyncio.Queue[str | None] = asyncio.Queue()
        self.sse_offen = 0
        self.widerrufen: list[str] = []
        self.url = ""

    def _auth(self, request: web.Request) -> str | None:
        kopf = request.headers.get("Authorization", "")
        token = kopf.removeprefix("Bearer ")
        return token if token in self.gueltige_tokens else None

    async def _body(self, request: web.Request) -> Any:
        if request.can_read_body:
            try:
                return await request.json()
            except json.JSONDecodeError:
                return None
        return None

    @web.middleware
    async def protokoll(self, request: web.Request, handler):
        self.aufrufe.append((request.method, request.path, await self._body(request)))
        return await handler(request)

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self.protokoll])
        r = app.router
        r.add_post("/auth/kopplung/anfrage", self.anfrage)
        r.add_get("/auth/kopplung/anfrage/{id}", self.abholen)
        r.add_post("/api/sitzung/abmelden", self.selbst_widerrufen)
        r.add_post("/api/sitzung/erneuern", self.erneuern)
        r.add_get("/api/ha/v1/zustand", self.get_zustand)
        r.add_get("/api/ha/v1/ereignisse", self.sse)
        r.add_get("/api/ha/v1/regeln", self.get_regeln)
        r.add_post("/api/ha/v1/aktion", self.aktion)
        r.add_get("/api/einstellungen/schema", self.get_schema)
        r.add_get("/api/einstellungen", self.get_werte)
        r.add_put("/api/einstellungen", self.put_werte)
        return app

    # --- Kopplung
    async def anfrage(self, request: web.Request) -> web.Response:
        body = await request.json()
        if len(str(body.get("pruefzahl", ""))) != 4 or not str(body["pruefzahl"]).isdigit():
            return web.json_response({"ok": False, "grund": "pruefzahl"}, status=400)
        return web.json_response({"ok": True, "id": "0" * 32, "gueltigBis": 0, "abfrageAlleMs": 2000})

    async def abholen(self, request: web.Request) -> web.Response:
        self.abgefragt += 1
        if self.abgefragt == 1:
            return web.json_response({"status": "wartet", "gueltigBis": 0})
        if self.anfrage_status == "erlaubt":
            self.anfrage_status = "abgeholt"
            return web.json_response({"status": "erlaubt", "jwt": TOKEN, "kopplungId": "k-1", "expiresIn": 2592000})
        return web.json_response({"status": self.anfrage_status})

    async def selbst_widerrufen(self, request: web.Request) -> web.Response:
        token = self._auth(request)
        if not token:
            return web.json_response({"ok": False}, status=401)
        self.gueltige_tokens.discard(token)
        self.widerrufen.append(token)
        return web.json_response({"ok": True})

    async def erneuern(self, request: web.Request) -> web.Response:
        if not self._auth(request):
            return web.json_response({}, status=401)
        self.gueltige_tokens.add("jwt-ha-2")
        return web.json_response({"success": True, "jwt": "jwt-ha-2", "expiresIn": 2592000})

    # --- Lesen
    async def get_zustand(self, request: web.Request) -> web.Response:
        if not self.mit_ha_api:
            return web.json_response({}, status=404)
        if not self._auth(request):
            return web.json_response({"grund": "token"}, status=401)
        daten = copy.deepcopy(self.zustand)
        if request.query.get("position") == "1":
            daten["position"] = {"lat": 48.2, "lon": 16.37, "genauigkeitM": 8, "zeit": 1}
        return web.json_response(daten)

    async def sse(self, request: web.Request) -> web.StreamResponse:
        if not self.mit_sse:
            return web.json_response({}, status=404)
        if not self._auth(request):
            return web.json_response({}, status=401)
        antwort = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await antwort.prepare(request)
        self.sse_offen += 1
        try:
            await antwort.write(b": hallo\n\n")
            while True:
                text = await self.ereignisse.get()
                if text is None:
                    break
                await antwort.write(text.encode())
        finally:
            self.sse_offen -= 1
        return antwort

    def sende(self, art: str, daten: dict) -> None:
        self.ereignisse.put_nowait(f"event: {art}\ndata: {json.dumps(daten)}\n\n")

    async def get_regeln(self, request: web.Request) -> web.Response:
        if not self._auth(request):
            return web.json_response({}, status=401)
        return web.json_response({"regeln": self.regeln})

    async def get_schema(self, request: web.Request) -> web.Response:
        if not self._auth(request):
            return web.json_response({}, status=401)
        return web.json_response(self.schema)

    async def get_werte(self, request: web.Request) -> web.Response:
        if not self._auth(request):
            return web.json_response({}, status=401)
        return web.json_response({"etag": f"w-{self.etag}", "werte": self.werte})

    async def put_werte(self, request: web.Request) -> web.Response:
        if not self._auth(request):
            return web.json_response({}, status=401)
        body = await request.json()
        if body.get("etag") != f"w-{self.etag}":
            return web.json_response({"ok": False, "grund": "veraltet", "etag": f"w-{self.etag}"}, status=409)
        gespeichert, fehler = {}, {}
        felder = {f["key"]: f for g in self.schema["gruppen"] for f in g["felder"]}
        for key, wert in body["werte"].items():
            feld = felder.get(key)
            if feld is None:
                fehler[key] = "unbekannt"
            elif feld.get("nurLesendExtern") is not False:
                fehler[key] = "nurLesendExtern"
            elif "max" in feld and wert > feld["max"]:
                fehler[key] = f"max {feld['max']}"
            else:
                self.werte[key] = {"wert": wert, "gesetzt": True}
                gespeichert[key] = wert
        if gespeichert:
            self.etag += 1
        return web.json_response({"ok": not fehler, "etag": f"w-{self.etag}", "gespeichert": gespeichert, "fehler": fehler})

    async def aktion(self, request: web.Request) -> web.Response:
        if not self._auth(request):
            return web.json_response({}, status=401)
        body = await request.json()
        name = body.get("aktion")
        if name == "waechter":
            self.zustand["waechter"]["zustand"] = "scharf" if body.get("an") else "aus"
        elif name == "regel":
            regel = next((r for r in self.regeln if r["id"] == body.get("id")), None)
            if not regel or not regel["ausloesbar"]:
                return web.json_response({"ok": False, "grund": "fahrzeugaktion"}, status=403)
        elif name == "ziel":  # wie Panoptes 0.1.32
            return web.json_response({"ok": False, "grund": "nicht_verfuegbar"})
        elif name != "ruhezeit":
            return web.json_response({"ok": False, "grund": "unbekannt"}, status=400)
        return web.json_response({"ok": True})


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def schnell_warten():
    async def kurz(_s: float) -> None:
        await asyncio.sleep(0)

    with patch("custom_components.panoptes.config_flow._schlafen", kurz):
        yield


@pytest.fixture
async def panoptes(socket_enabled) -> AsyncIterator[FakePanoptes]:
    fake = FakePanoptes()
    runner = web.AppRunner(fake.app(), shutdown_timeout=0.5)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    fake.url = f"http://127.0.0.1:{port}"
    yield fake
    # Regel A: Die Integration hat nie einen Fahrzeug- oder Zugangs-Endpunkt aufgerufen.
    verboten = [a for a in fake.aufrufe if a[1].startswith(VERBOTENE_PRAEFIXE)]
    assert verboten == [], verboten
    for _ in range(3):
        fake.ereignisse.put_nowait(None)  # offene SSE-Streams beenden
    await runner.cleanup()


@pytest.fixture
def eintrag(panoptes: FakePanoptes) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Dolphin Surf",
        unique_id=AUTO_ID,
        data={CONF_URL: panoptes.url, CONF_TOKEN: TOKEN, CONF_KOPPLUNG_ID: "k-1", CONF_AUTO_ID: AUTO_ID},
        options={OPT_POSITION: False, OPT_EINSTELLUNGEN: True},
    )


async def einrichten(hass: HomeAssistant, eintrag: MockConfigEntry) -> None:
    eintrag.add_to_hass(hass)
    assert await hass.config_entries.async_setup(eintrag.entry_id)
    await hass.async_block_till_done()
