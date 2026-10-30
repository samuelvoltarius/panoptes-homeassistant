"""Datenhaltung für beide Wege.

- ``PanoptesBasis``: gemeinsamer Teil (Daten, Ereignisse, Optionen) – Vertrag Teil A.
- ``PanoptesCoordinator``: Modus „Direkt“ (Tailscale/Heim-WLAN), Push per SSE, Polling 30 s als Rückfall – Teil C.
- ``webhook.WebhookCoordinator``: Standardweg, das Auto sendet selbst an HA – Teil B.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
import random
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PanoptesAnmeldungFehlt,
    PanoptesApi,
    PanoptesFehler,
    PanoptesGesperrt,
    PanoptesNichtVorhanden,
)
from .const import (
    CONF_TOKEN,
    DOMAIN,
    ERLAUBTE_AKTIONEN,
    EVENT_ABFAHRT,
    EVENT_ANKUNFT,
    EVENT_WAECHTER,
    MQTT_DISCOVERY_AUS,
    OPT_EINSTELLUNGEN,
    OPT_POSITION,
    POLL_MIT_PUSH,
    POLL_OHNE_PUSH,
    REGELN_NEU_LADEN,
    SIGNAL_EREIGNIS,
)

_LOGGER = logging.getLogger(__name__)

type PanoptesConfigEntry = ConfigEntry[PanoptesBasis]


@dataclass
class PanoptesDaten:
    """Alles, was die Entitäten brauchen."""

    zustand: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, Any] | None = None
    werte: dict[str, Any] = field(default_factory=dict)
    werte_etag: str | None = None
    regeln: list[dict[str, Any]] = field(default_factory=list)

    @property
    def telemetrie(self) -> dict[str, Any]:
        t = self.zustand.get("telemetrie")
        return t if isinstance(t, dict) else {}

    @property
    def geraet(self) -> dict[str, Any]:
        g = self.zustand.get("geraet")
        return g if isinstance(g, dict) else {}


def mische(alt: dict[str, Any], neu: dict[str, Any]) -> dict[str, Any]:
    """Teil-Update einarbeiten (verschachtelte Objekte zusammenführen)."""
    ergebnis = dict(alt)
    for key, wert in neu.items():
        if isinstance(wert, dict) and isinstance(ergebnis.get(key), dict):
            ergebnis[key] = mische(ergebnis[key], wert)
        else:
            ergebnis[key] = wert
    return ergebnis


class PanoptesBasis(DataUpdateCoordinator[PanoptesDaten]):
    """Gemeinsamer Teil beider Wege."""

    config_entry: PanoptesConfigEntry
    modus = "basis"

    def __init__(self, hass: HomeAssistant, entry: PanoptesConfigEntry, update_interval: timedelta | None) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=update_interval,
        )
        self.data = PanoptesDaten()

    @property
    def position_an(self) -> bool:
        return bool(self.config_entry.options.get(OPT_POSITION, False))

    @property
    def einstellungen_an(self) -> bool:
        return bool(self.config_entry.options.get(OPT_EINSTELLUNGEN, True))

    @property
    def verbunden(self) -> bool:
        """Für die Diagnose-Entität „Live-Verbindung“."""
        return False

    # ------------------------------------------------------------------ Schreiben (je Weg)
    async def aktion(self, typ: str, /, **werte: Any) -> None:
        raise NotImplementedError

    async def einstellung_setzen(self, key: str, wert: Any) -> None:
        raise NotImplementedError

    async def mqtt_discovery_abschalten(self, aus: bool) -> None:
        """Doppelte Entitäten vermeiden (Koordination 21.10.): Discovery aus, solange HA verbunden ist."""

    # ------------------------------------------------------------------ Lesen
    def _ohne_position(self, daten: dict[str, Any]) -> dict[str, Any]:
        if self.position_an:
            return daten
        return {k: v for k, v in daten.items() if k != "position"}

    @callback
    def zustand_einarbeiten(self, teil: dict[str, Any]) -> None:
        self.async_set_updated_data(
            replace(self.data, zustand=mische(self.data.zustand, self._ohne_position(teil)))
        )

    @callback
    def ereignis(self, art: str, daten: dict[str, Any]) -> None:
        """Vertrag A2: ankunft/abfahrt (nur Zonenname), ereignis (nur Art + Zeit), waechter."""
        eintrag_id = self.config_entry.entry_id
        if art == "waechter":
            self.zustand_einarbeiten({"waechter": {k: v for k, v in daten.items() if k != "art"}})
            return
        if art in ("ankunft", "abfahrt"):
            nutzlast = {"ort": daten.get("ort"), "zeit": daten.get("zeit")}
            self.hass.bus.async_fire(
                EVENT_ANKUNFT if art == "ankunft" else EVENT_ABFAHRT,
                {"config_entry_id": eintrag_id, **nutzlast},
            )
            async_dispatcher_send(self.hass, SIGNAL_EREIGNIS.format(eintrag_id), "fahrt", art, nutzlast)
            return
        if art == "ereignis":
            # Vertrag: "was"; Panoptes 0.1.32 schickt die Art per SSE noch in "art".
            was = str(daten.get("was") or (daten.get("art") if daten.get("art") != "ereignis" else "") or "")
            # ereignisId (ab 0.1.30): Schlüssel fürs Standbild, nur mit Recht standbild oder kamera abrufbar.
            nutzlast = {"art": was, "zeit": daten.get("zeit"), "ereignis_id": daten.get("ereignisId")}
            self.hass.bus.async_fire(EVENT_WAECHTER, {"config_entry_id": eintrag_id, **nutzlast})
            async_dispatcher_send(self.hass, SIGNAL_EREIGNIS.format(eintrag_id), "waechter", was, nutzlast)


class PanoptesCoordinator(PanoptesBasis):
    """Modus „Direkt“: HA spricht die Auto-API (Teil C)."""

    modus = "direkt"

    def __init__(self, hass: HomeAssistant, entry: PanoptesConfigEntry, api: PanoptesApi) -> None:
        super().__init__(hass, entry, POLL_OHNE_PUSH)
        self.api = api
        self.push_aktiv = False
        self.push_unterstuetzt = True
        self._regeln_geladen: datetime | None = None
        self._sprache = hass.config.language

    @property
    def verbunden(self) -> bool:
        return self.push_aktiv

    async def _async_update_data(self) -> PanoptesDaten:
        alt = self.data
        try:
            zustand = await self.api.zustand(position=self.position_an, sprache=self._sprache)
            schema, werte, etag = alt.schema, alt.werte, alt.werte_etag
            if self.einstellungen_an:
                if schema is None:
                    schema = await self.api.einstellungen_schema(self._sprache)
                antwort = await self.api.einstellungen()
                werte = antwort.get("werte") or {}
                if antwort.get("etag") != etag and alt.schema is not None:
                    # Sichtbarkeit (sichtbar/nurWenn) kann sich nach Änderungen verschieben.
                    schema = await self.api.einstellungen_schema(self._sprache)
                etag = antwort.get("etag")
            regeln = alt.regeln
            jetzt = datetime.now()
            if self._regeln_geladen is None or jetzt - self._regeln_geladen > REGELN_NEU_LADEN:
                try:
                    regeln = await self.api.regeln()
                except PanoptesNichtVorhanden:
                    regeln = []
                self._regeln_geladen = jetzt
        except PanoptesAnmeldungFehlt as err:
            raise ConfigEntryAuthFailed("Kopplung widerrufen oder abgelaufen") from err
        except PanoptesFehler as err:
            raise UpdateFailed(f"Panoptes nicht erreichbar: {err.grund}") from err
        return PanoptesDaten(zustand=zustand, schema=schema, werte=werte, werte_etag=etag, regeln=regeln)

    # ------------------------------------------------------------------ Push
    async def push_schleife(self) -> None:
        """Läuft als Hintergrund-Task, solange der Eintrag geladen ist."""
        wartezeit = 5.0
        while True:
            try:
                async for art, daten in self.api.ereignisse():
                    if not self.push_aktiv:
                        self._setze_push(True)
                        wartezeit = 5.0
                    if art == "zustand":
                        # SSE trägt nie eine Position: Das null darf den abgefragten Wert nicht löschen.
                        self.zustand_einarbeiten({k: v for k, v in daten.items() if k != "position"})
                    else:
                        self.ereignis(art, daten)
            except PanoptesNichtVorhanden:
                _LOGGER.info("Panoptes ohne Push (SSE) – bleibe bei Polling alle %s", POLL_OHNE_PUSH)
                self.push_unterstuetzt = False
                self._setze_push(False)
                return
            except PanoptesAnmeldungFehlt:
                self._setze_push(False)
                self.config_entry.async_start_reauth(self.hass)
                return
            except PanoptesFehler as err:
                _LOGGER.debug("Push unterbrochen: %s", err.grund)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Push darf HA nie mitreißen
                _LOGGER.exception("Push: unerwarteter Fehler")
            self._setze_push(False)
            await asyncio.sleep(wartezeit + random.uniform(0, 2))
            wartezeit = min(wartezeit * 2, 300.0)

    @callback
    def _setze_push(self, aktiv: bool) -> None:
        if aktiv == self.push_aktiv:
            return
        self.push_aktiv = aktiv
        self.update_interval = POLL_MIT_PUSH if aktiv else POLL_OHNE_PUSH
        if not aktiv:
            self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------ Token
    async def sitzung_erneuern(self, _jetzt: Any = None) -> None:
        """Täglich: gleitende 30-Tage-Laufzeit der Kopplung erhalten."""
        try:
            token = await self.api.sitzung_erneuern()
        except PanoptesAnmeldungFehlt:
            self.config_entry.async_start_reauth(self.hass)
            return
        except PanoptesFehler as err:
            _LOGGER.debug("Sitzung nicht erneuert (%s), nächster Versuch morgen", err.grund)
            return
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_TOKEN: token}
        )

    # ------------------------------------------------------------------ Schreiben
    async def aktion(self, typ: str, /, **werte: Any) -> None:
        await self.api.aktion(typ, **werte)
        await self.async_request_refresh()

    async def einstellung_setzen(self, key: str, wert: Any) -> None:
        try:
            antwort = await self.api.einstellung_setzen(self.data.werte_etag, key, wert)
        except PanoptesAnmeldungFehlt as err:
            raise ConfigEntryAuthFailed from err
        neu_werte = dict(self.data.werte)
        gespeichert = antwort.get("gespeichert") or {}
        if key in gespeichert:
            neu_werte[key] = {**(neu_werte.get(key) or {}), "wert": gespeichert[key], "gesetzt": True}
        self.async_set_updated_data(
            replace(self.data, werte=neu_werte, werte_etag=antwort.get("etag") or self.data.werte_etag)
        )
        # Vertrag: danach per GET zurücklesen.
        await self.async_request_refresh()

    async def mqtt_discovery_abschalten(self, aus: bool) -> None:
        try:
            antwort = await self.api.einstellungen()
            if MQTT_DISCOVERY_AUS not in (antwort.get("werte") or {}):
                return  # ältere Panoptes-Version ohne den Schalter
            await self.api.einstellung_setzen(antwort.get("etag"), MQTT_DISCOVERY_AUS, aus)
        except (PanoptesFehler, PanoptesGesperrt) as err:
            _LOGGER.debug("MQTT-Discovery-Schalter nicht gesetzt: %s", err.grund)


def pruefe_aktion(typ: str) -> None:
    """Regel A, gemeinsam für beide Wege."""
    if typ not in ERLAUBTE_AKTIONEN:
        raise PanoptesGesperrt("nicht_erlaubt")
