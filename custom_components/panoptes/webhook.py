"""Weg (e), Standard: Das Auto sendet ausgehend per HTTPS an den Webhook dieses HA (Vertrag Teil B).

Das Muster entspricht der HA-Companion-App. Die Rückrichtung läuft nur über wartende Aufträge in der Antwort
(Erlaubnisliste A3); das Auto öffnet nie einen Eingang. Nichts liegt bei Dritten. Nabu Casa nur über die
E2E-Remote-UI-Adresse, nie über Cloudhooks.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from datetime import datetime
import ipaddress
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import PanoptesGesperrt
from .const import (
    CONF_GEHEIMNIS,
    CONF_WEBHOOK_ID,
    DOMAIN,
    EINRICHTUNG_GUELTIG,
    EINRICHTUNG_PRAEFIX,
    GRABSTEIN_DAUER,
    GRABSTEIN_SPEICHER,
    MAX_AUFTRAEGE,
    MQTT_DISCOVERY_AUS,
    SENDETAKT_S,
    STILLE_BIS_GETRENNT,
    WARTEN_MAX_S,
)
from .coordinator import PanoptesBasis, PanoptesConfigEntry, PanoptesDaten, mische, pruefe_aktion

_LOGGER = logging.getLogger(__name__)

EVENT_AUFTRAG_ABGELEHNT = f"{DOMAIN}_auftrag_abgelehnt"
MAX_GROESSE = 256 * 1024


def _json(daten: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(daten, status=status)


async def _lies(request: web.Request) -> dict[str, Any] | None:
    if request.content_length and request.content_length > MAX_GROESSE:
        return None
    try:
        roh = await request.content.read(MAX_GROESSE + 1)
        if len(roh) > MAX_GROESSE:
            return None
        daten = json.loads(roh)
    except (ValueError, UnicodeDecodeError):
        return None
    return daten if isinstance(daten, dict) and daten.get("v") == 1 else None


def _bearer_passt(request: web.Request, geheimnis: str) -> bool:
    kopf = request.headers.get("Authorization", "")
    if not kopf.startswith("Bearer "):
        return False
    return secrets.compare_digest(kopf.removeprefix("Bearer ").encode(), geheimnis.encode())


# ---------------------------------------------------------------------- Adressen + Einrichtungstext
def ist_privat(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host.endswith(".local") or host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def ermittle_adressen(hass: HomeAssistant) -> tuple[list[str], bool]:
    """(Adressen in Reihenfolge, gibt_es_externe). Remote-UI zuerst, dann eigene externe HTTPS, dann intern."""
    adressen: list[str] = []
    try:
        extern = get_url(hass, allow_internal=False, prefer_cloud=True, require_ssl=True, allow_ip=False)
    except NoURLAvailableError:
        extern = None
    if extern and "hooks.nabu.casa" not in extern:
        adressen.append(extern.rstrip("/"))
    try:
        intern = get_url(hass, allow_external=False, allow_cloud=False)
    except NoURLAvailableError:
        intern = None
    if intern and intern.rstrip("/") not in adressen:
        adressen.append(intern.rstrip("/"))
    return adressen, bool(extern)


def einrichtungstext(name: str, adressen: list[str], webhook_id: str, schluessel: str, pruefzahl: str, bis_ms: int) -> str:
    nutzlast = {"v": 1, "n": name[:40], "u": adressen, "w": webhook_id, "k": schluessel, "p": pruefzahl, "bis": bis_ms}
    roh = json.dumps(nutzlast, separators=(",", ":"), ensure_ascii=True).encode()
    return f"{EINRICHTUNG_PRAEFIX} {base64.urlsafe_b64encode(roh).decode().rstrip('=')}"


def telegram_link(text: str) -> str:
    return f"https://t.me/share/url?url={quote(text, safe='')}"


def lies_einrichtungstext(text: str) -> dict[str, Any]:
    """Gegenstück (für Tests und als Referenz für das Auto)."""
    praefix, _, daten = text.strip().partition(" ")
    if praefix != EINRICHTUNG_PRAEFIX:
        raise ValueError("praefix")
    daten += "=" * (-len(daten) % 4)
    return json.loads(base64.urlsafe_b64decode(daten))


# ---------------------------------------------------------------------- Grabsteine (Entfernen)
class Grabsteine:
    """Entfernte Webhooks antworten 90 Tage mit „widerrufen“; leerer 200 wäre für das Auto kein Widerruf."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, 1, GRABSTEIN_SPEICHER)
        self._eintraege: dict[str, float] = {}

    async def laden(self) -> None:
        daten = await self._store.async_load() or {}
        jetzt = time.time()
        self._eintraege = {w: bis for w, bis in (daten.get("webhooks") or {}).items() if bis > jetzt}
        for webhook_id in self._eintraege:
            self._registriere(webhook_id)

    async def hinzufuegen(self, webhook_id: str) -> None:
        self._eintraege[webhook_id] = time.time() + GRABSTEIN_DAUER.total_seconds()
        self._registriere(webhook_id)
        await self._store.async_save({"webhooks": self._eintraege})

    def _registriere(self, webhook_id: str) -> None:
        try:
            webhook.async_register(
                self.hass, DOMAIN, "Panoptes (entfernt)", webhook_id, _grabstein, allowed_methods=["POST"]
            )
        except ValueError:
            pass  # schon registriert


async def _grabstein(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
    # Letzter Auftrag: MQTT-Discovery wieder an, damit der Nutzer nicht ohne Daten dasteht (Vertrag B3).
    return _json(
        {
            "ok": False,
            "grund": "widerrufen",
            "auftraege": [{"id": "a-ende", "typ": "einstellung", "key": MQTT_DISCOVERY_AUS, "wert": False}],
        }
    )


# ---------------------------------------------------------------------- laufender Betrieb
class WebhookCoordinator(PanoptesBasis):
    """Daten kommen nur vom Auto; HA pollt nie."""

    modus = "webhook"

    def __init__(self, hass: HomeAssistant, entry: PanoptesConfigEntry) -> None:
        super().__init__(hass, entry, None)
        self.webhook_id: str = entry.data[CONF_WEBHOOK_ID]
        self._geheimnis: str = entry.data[CONF_GEHEIMNIS]
        self.auftraege: dict[str, dict[str, Any]] = {}
        self._neuer_auftrag = asyncio.Event()
        self.letzter_kontakt: datetime | None = None
        self._regeln_etag: str | None = None
        self._ausstehend: dict[str, Any] = {}  # Auftrags-id → alter Zustand (für Rücknahme)

    async def _async_update_data(self) -> PanoptesDaten:
        return self.data

    @property
    def verbunden(self) -> bool:
        return self.letzter_kontakt is not None and dt_util.utcnow() - self.letzter_kontakt < STILLE_BIS_GETRENNT

    @callback
    def registrieren(self) -> None:
        webhook.async_register(
            self.hass, DOMAIN, f"Panoptes {self.config_entry.title}", self.webhook_id, self._empfang,
            allowed_methods=["POST"],
        )

    @callback
    def abmelden(self) -> None:
        webhook.async_unregister(self.hass, self.webhook_id)
        self._neuer_auftrag.set()  # wartende lange Abfragen sofort beenden

    # ------------------------------------------------------------------ Empfang
    async def _empfang(self, hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        if not _bearer_passt(request, self._geheimnis):
            return _json({"ok": False, "grund": "geheimnis"}, 401)
        body = await _lies(request)
        if body is None:
            return _json({"ok": False, "grund": "format"}, 400)
        self.letzter_kontakt = dt_util.utcnow()
        art = body.get("art")
        if art == "warte":
            return _json(await self._warte(body))
        if art == "tschuess":
            _LOGGER.info("Panoptes hat die Verbindung zu Home Assistant getrennt")
            self.config_entry.async_start_reauth(self.hass)
            return _json({"ok": True})
        if art != "zustand":
            return _json({"ok": False, "grund": "art"}, 400)
        wuensche = self._verarbeite(body)
        return _json(self._antwort(wuensche))

    def _antwort(self, wuensche: dict[str, bool] | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "auftraege": list(self.auftraege.values()),
            "wuensche": wuensche or {"schema": False, "werte": False, "regeln": False},
            "sendeTaktS": SENDETAKT_S,
        }

    async def _warte(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.auftraege:
            self._neuer_auftrag.clear()
            try:
                max_s = min(float(body.get("maxS") or WARTEN_MAX_S), WARTEN_MAX_S)
            except (TypeError, ValueError):
                max_s = WARTEN_MAX_S
            try:
                await asyncio.wait_for(self._neuer_auftrag.wait(), timeout=max(0.0, max_s))
            except TimeoutError:
                pass
        return self._antwort()

    @callback
    def _verarbeite(self, body: dict[str, Any]) -> dict[str, bool]:
        daten = self.data
        zustand = body.get("zustand")
        if isinstance(zustand, dict):
            daten = replace(daten, zustand=_mische_ohne_position(daten.zustand, zustand, self.position_an))
        if self.einstellungen_an:
            if isinstance(body.get("schema"), dict):
                daten = replace(daten, schema=body["schema"])
            if isinstance(body.get("werte"), dict):
                werte = body["werte"]
                daten = replace(daten, werte=werte.get("werte") or {}, werte_etag=werte.get("etag"))
        if isinstance(body.get("regeln"), list):
            daten = replace(daten, regeln=[r for r in body["regeln"] if isinstance(r, dict)])
            self._regeln_etag = (body.get("etags") or {}).get("regeln")
        for quittung in body.get("quittungen") or []:
            if isinstance(quittung, dict):
                daten = self._quittung(daten, quittung)
        self.async_set_updated_data(daten)
        for ereignis in body.get("ereignisse") or []:
            if isinstance(ereignis, dict) and ereignis.get("art"):
                self.ereignis(str(ereignis["art"]), ereignis)

        etags = body.get("etags") or {}
        schema_etag = (self.data.schema or {}).get("etag")
        return {
            "schema": self.einstellungen_an and (self.data.schema is None or etags.get("schema") != schema_etag),
            "werte": self.einstellungen_an and etags.get("werte") != self.data.werte_etag,
            "regeln": etags.get("regeln") != self._regeln_etag,
        }

    def _quittung(self, daten: PanoptesDaten, quittung: dict[str, Any]) -> PanoptesDaten:
        auftrag = self.auftraege.pop(str(quittung.get("id")), None)
        alt = self._ausstehend.pop(str(quittung.get("id")), None)
        if auftrag is None or quittung.get("ok") is True:
            return daten
        grund = str(quittung.get("grund") or "abgelehnt")
        _LOGGER.warning("Panoptes hat einen Auftrag abgelehnt (%s): %s", auftrag.get("typ"), grund)
        self.hass.bus.async_fire(
            EVENT_AUFTRAG_ABGELEHNT,
            {"config_entry_id": self.config_entry.entry_id, "typ": auftrag.get("typ"), "key": auftrag.get("key"), "grund": grund},
        )
        if alt is not None:  # vorläufige Anzeige zurücknehmen
            bereich, wert = alt
            if bereich == "werte":
                werte = dict(daten.werte)
                werte[auftrag["key"]] = wert
                daten = replace(daten, werte=werte)
            elif bereich == "waechter":
                daten = replace(daten, zustand={**daten.zustand, "waechter": wert})
        return daten

    # ------------------------------------------------------------------ Aufträge (A3)
    def _einreihen(self, auftrag: dict[str, Any]) -> str:
        if auftrag.get("typ") == "einstellung":
            # Neuere Änderung desselben Felds ersetzt die ältere, noch nicht abgeholte.
            for alt_id in [i for i, a in self.auftraege.items() if a.get("typ") == "einstellung" and a.get("key") == auftrag["key"]]:
                self.auftraege.pop(alt_id)
                self._ausstehend.pop(alt_id, None)
        if len(self.auftraege) >= MAX_AUFTRAEGE:
            raise PanoptesGesperrt("zu_viele_auftraege")
        auftrag_id = f"a-{secrets.token_hex(6)}"
        self.auftraege[auftrag_id] = {"id": auftrag_id, **auftrag}
        self._neuer_auftrag.set()
        return auftrag_id

    async def aktion(self, typ: str, /, **werte: Any) -> None:
        pruefe_aktion(typ)
        if typ == "einstellung":
            raise PanoptesGesperrt("nicht_erlaubt")
        auftrag_id = self._einreihen({"typ": typ, **werte})
        if typ == "waechter":
            alt = self.data.zustand.get("waechter")
            self._ausstehend[auftrag_id] = ("waechter", alt)
            neu = {**(alt or {}), "zustand": "scharf" if werte.get("an") else "aus", "ausstehend": True}
            self.async_set_updated_data(replace(self.data, zustand={**self.data.zustand, "waechter": neu}))

    async def einstellung_setzen(self, key: str, wert: Any) -> None:
        # Bei Ablehnung zum letzten BESTÄTIGTEN Wert zurück, nicht zu einem ersetzten Zwischenwert.
        alt = self.data.werte.get(key)
        for alt_id, auftrag in self.auftraege.items():
            if auftrag.get("typ") == "einstellung" and auftrag.get("key") == key and alt_id in self._ausstehend:
                alt = self._ausstehend[alt_id][1]
        auftrag_id = self._einreihen({"typ": "einstellung", "key": key, "wert": wert})
        self._ausstehend[auftrag_id] = ("werte", alt)
        werte = dict(self.data.werte)
        werte[key] = {**(alt or {}), "wert": wert, "ausstehend": True}
        self.async_set_updated_data(replace(self.data, werte=werte))

    async def mqtt_discovery_abschalten(self, aus: bool) -> None:
        # Unbekanntes Feld (ältere Version) quittiert das Auto mit „unbekannt“ – harmlos.
        self._einreihen({"typ": "einstellung", "key": MQTT_DISCOVERY_AUS, "wert": aus})


def _mische_ohne_position(alt: dict[str, Any], neu: dict[str, Any], position_an: bool) -> dict[str, Any]:
    if not position_an:
        neu = {k: v for k, v in neu.items() if k != "position"}
    return mische(alt, neu)


# ---------------------------------------------------------------------- Einrichtung
class Einrichtung:
    """Einrichtungs-Webhook w0 für einen Config-Flow. Nach „hallo“ sofort ungültig (Vertrag B1)."""

    def __init__(self, hass: HomeAssistant, adressen: list[str], name: str) -> None:
        self.hass = hass
        self.adressen = adressen
        self.webhook_id = webhook.async_generate_id()
        self.schluessel = secrets.token_urlsafe(24)
        self.pruefzahl = f"{secrets.randbelow(10000):04d}"
        self.bis_ms = int((time.time() + EINRICHTUNG_GUELTIG.total_seconds()) * 1000)
        self.text = einrichtungstext(name, adressen, self.webhook_id, self.schluessel, self.pruefzahl, self.bis_ms)
        self.ergebnis: asyncio.Future[dict[str, Any]] = hass.loop.create_future()
        self._aktiv = False

    def starten(self) -> None:
        webhook.async_register(
            self.hass, DOMAIN, "Panoptes Einrichtung", self.webhook_id, self._hallo, allowed_methods=["POST"]
        )
        self._aktiv = True

    def beenden(self) -> None:
        if self._aktiv:
            webhook.async_unregister(self.hass, self.webhook_id)
            self._aktiv = False
        if not self.ergebnis.done():
            self.ergebnis.cancel()

    async def _hallo(self, hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        body = await _lies(request)
        if body is None or body.get("art") != "hallo":
            return _json({"ok": False, "grund": "format"}, 400)
        if time.time() * 1000 > self.bis_ms:
            return _json({"ok": False, "grund": "abgelaufen"}, 410)
        if not secrets.compare_digest(str(body.get("k", "")).encode(), self.schluessel.encode()):
            return _json({"ok": False, "grund": "schluessel"}, 403)
        geraet = body.get("geraet") if isinstance(body.get("geraet"), dict) else {}
        if not geraet.get("haId"):
            return _json({"ok": False, "grund": "haId"}, 400)
        neu = {"webhook": webhook.async_generate_id(), "geheimnis": secrets.token_urlsafe(32)}
        # w0 sofort ungültig: Telegram kannte w0 und k, aber nie w1/geheimnis.
        self.beenden_ohne_abbruch()
        if not self.ergebnis.done():
            self.ergebnis.set_result({"geraet": geraet, **neu})
        return _json({"ok": True, **neu, "sendeTaktS": SENDETAKT_S})

    def beenden_ohne_abbruch(self) -> None:
        if self._aktiv:
            webhook.async_unregister(self.hass, self.webhook_id)
            self._aktiv = False


def gueltig_bis_text(einrichtung: Einrichtung) -> str:
    return dt_util.as_local(dt_util.utc_from_timestamp(einrichtung.bis_ms / 1000)).strftime("%H:%M")


__all__ = [
    "EVENT_AUFTRAG_ABGELEHNT",
    "Einrichtung",
    "Grabsteine",
    "WebhookCoordinator",
    "ermittle_adressen",
    "gueltig_bis_text",
    "ist_privat",
    "lies_einrichtungstext",
    "telegram_link",
]

