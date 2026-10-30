"""Schlanker Client für die lokale Panoptes-API (Port 8180).

Vertrag: HA_VERTRAG.md (Koordination) + KOPPLUNG_ANFRAGE.md + EINSTELLUNGEN_SCHEMA.md.
Regel A: Dieser Client kennt keinen einzigen Fahrzeug-Endpunkt (/api/vehicle/* u. a.).
Er spricht nur Kopplung, Zustand, Ereignisse, Einstellungen und die Erlaubnisliste in
``ERLAUBTE_AKTIONEN``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .const import ERLAUBTE_AKTIONEN, KOPPLUNG_ART, STANDARD_PORT

_LOGGER = logging.getLogger(__name__)

ZEITLIMIT = aiohttp.ClientTimeout(total=15)


class PanoptesFehler(Exception):
    """Allgemeiner Fehler; ``grund`` stammt aus der Server-Antwort, falls vorhanden."""

    def __init__(self, grund: str = "unbekannt", status: int | None = None) -> None:
        super().__init__(grund)
        self.grund = grund
        self.status = status


class PanoptesNichtErreichbar(PanoptesFehler):
    """Netzwerkfehler oder Zeitüberschreitung."""


class PanoptesAnmeldungFehlt(PanoptesFehler):
    """401: Token abgelaufen oder widerrufen → Neu-Kopplung (reauth)."""


class PanoptesGesperrt(PanoptesFehler):
    """403: Server verweigert (z. B. nurLesendExtern, fahrzeugaktion)."""


class PanoptesNichtVorhanden(PanoptesFehler):
    """404: Endpunkt gibt es in dieser Panoptes-Version (noch) nicht."""


class PanoptesZuOft(PanoptesFehler):
    """429: Ratenlimit oder zu viele offene Anfragen."""


def normalisiere_url(eingabe: str) -> str:
    """'100.64.0.10' → 'http://100.64.0.10:8180'. Pfad, Query und Fragment fallen weg."""
    text = eingabe.strip()
    if not text:
        raise ValueError("leer")
    if "://" not in text:
        text = f"http://{text}"
    teile = urlsplit(text)
    if teile.scheme not in ("http", "https") or not teile.hostname:
        raise ValueError("ungueltig")
    host = teile.hostname
    if ":" in host:  # IPv6
        host = f"[{host}]"
    port = teile.port or (STANDARD_PORT if teile.scheme == "http" else 443)
    return urlunsplit((teile.scheme, f"{host}:{port}", "", "", ""))


@dataclass(slots=True)
class KopplungsAnfrage:
    """Antwort auf POST /auth/kopplung/anfrage. ``id`` ist das Abhol-Geheimnis (nie loggen)."""

    id: str
    gueltig_bis: int | None
    abfrage_alle_s: float


@dataclass(slots=True)
class KopplungsErgebnis:
    """Endstatus des Abholens."""

    status: str  # erlaubt | abgelehnt | abgelaufen | abgeholt | unbekannt
    jwt: str | None = None
    kopplung_id: str | None = None


class PanoptesApi:
    """Ein Panoptes-Auto."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str | None = None) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self.token = token

    # ------------------------------------------------------------------ Grundlage
    def _kopf(self) -> dict[str, str]:
        kopf = {"Accept": "application/json"}
        if self.token:
            kopf["Authorization"] = f"Bearer {self.token}"
        return kopf

    async def _anfrage(
        self,
        methode: str,
        pfad: str,
        *,
        json_body: Any = None,
        params: dict[str, str] | None = None,
        erlaubte_status: tuple[int, ...] = (),
    ) -> tuple[int, dict[str, Any]]:
        try:
            async with self._session.request(
                methode,
                f"{self.url}{pfad}",
                json=json_body,
                params=params,
                headers=self._kopf(),
                timeout=ZEITLIMIT,
            ) as antwort:
                try:
                    daten = await antwort.json(content_type=None)
                except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError):
                    daten = None
                if not isinstance(daten, dict):
                    daten = {}
                status = antwort.status
        except (TimeoutError, aiohttp.ClientError) as err:
            raise PanoptesNichtErreichbar("nicht_erreichbar") from err

        if status < 400 or status in erlaubte_status:
            return status, daten
        grund = str(daten.get("grund") or daten.get("status") or daten.get("error") or status)
        if status == 401:
            raise PanoptesAnmeldungFehlt(grund, status)
        if status == 403:
            raise PanoptesGesperrt(grund, status)
        if status == 404:
            raise PanoptesNichtVorhanden(grund, status)
        if status == 429:
            raise PanoptesZuOft(grund, status)
        raise PanoptesFehler(grund, status)

    # ------------------------------------------------------------------ Kopplung
    async def kopplung_anfragen(self, geraet: str, pruefzahl: str) -> KopplungsAnfrage:
        """Kopplungs-Anfrage stellen (ohne Login). Panoptes meldet sie per Telegram/Web/Display."""
        _, daten = await self._anfrage(
            "POST",
            "/auth/kopplung/anfrage",
            json_body={"geraet": geraet[:40], "pruefzahl": pruefzahl, "art": KOPPLUNG_ART},
        )
        if not daten.get("ok") or not daten.get("id"):
            raise PanoptesFehler(str(daten.get("grund", "antwort")))
        return KopplungsAnfrage(
            id=str(daten["id"]),
            gueltig_bis=daten.get("gueltigBis"),
            abfrage_alle_s=max(1.0, float(daten.get("abfrageAlleMs") or 2000) / 1000),
        )

    async def kopplung_abholen(self, anfrage_id: str) -> KopplungsErgebnis:
        """Einmal abfragen. 'wartet' → None-Status 'wartet'."""
        _, daten = await self._anfrage(
            "GET", f"/auth/kopplung/anfrage/{anfrage_id}", erlaubte_status=(404,)
        )
        status = str(daten.get("status") or "unbekannt")
        if status == "erlaubt":
            return KopplungsErgebnis("erlaubt", daten.get("jwt"), daten.get("kopplungId"))
        return KopplungsErgebnis(status)

    async def sitzung_erneuern(self) -> str:
        """Gleitende Laufzeit: neuer JWT für dieselbe Kopplung."""
        _, daten = await self._anfrage("POST", "/api/sitzung/erneuern")
        jwt = daten.get("jwt")
        if not jwt:
            raise PanoptesFehler("kein_jwt")
        self.token = str(jwt)
        return self.token

    async def selbst_widerrufen(self) -> bool:
        """Eigene Kopplung widerrufen (POST /api/sitzung/abmelden, nur eigene kid).

        True, wenn sie danach sicher ungültig ist. 404 (alte Version oder Kopplung unbekannt) und
        400 keine_kopplung gelten nicht als Erfolg → Aufrufer weist auf Handentfernen hin.
        """
        try:
            await self._anfrage("POST", "/api/sitzung/abmelden", json_body={})
        except PanoptesAnmeldungFehlt:
            return True  # Token war schon ungültig = Ziel erreicht
        return True

    # ------------------------------------------------------------------ Lesen
    async def zustand(self, *, position: bool = False, sprache: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if position:
            params["position"] = "1"
        if sprache:
            params["sprache"] = sprache
        _, daten = await self._anfrage("GET", "/api/ha/v1/zustand", params=params or None)
        return daten

    async def regeln(self) -> list[dict[str, Any]]:
        _, daten = await self._anfrage("GET", "/api/ha/v1/regeln")
        regeln = daten.get("regeln")
        return [r for r in regeln if isinstance(r, dict)] if isinstance(regeln, list) else []

    async def einstellungen_schema(self, sprache: str | None = None) -> dict[str, Any]:
        params = {"sprache": sprache} if sprache else None
        _, daten = await self._anfrage("GET", "/api/einstellungen/schema", params=params)
        return daten

    async def einstellungen(self) -> dict[str, Any]:
        _, daten = await self._anfrage("GET", "/api/einstellungen")
        return daten

    # ------------------------------------------------------------------ Schreiben
    async def einstellung_setzen(self, etag: str | None, key: str, wert: Any) -> dict[str, Any]:
        """Ein Feld speichern. Bei veraltetem etag einmal mit frischem etag wiederholen."""
        for _ in range(2):
            status, daten = await self._anfrage(
                "PUT",
                "/api/einstellungen",
                json_body={"etag": etag, "werte": {key: wert}},
                erlaubte_status=(409,),
            )
            if status == 409:
                etag = daten.get("etag")
                continue
            fehler = daten.get("fehler") or {}
            if key in fehler:
                grund = str(fehler[key])
                if grund == "nurLesendExtern":
                    raise PanoptesGesperrt(grund, 403)
                raise PanoptesFehler(grund, status)
            return daten
        raise PanoptesFehler("veraltet", 409)

    async def aktion(self, aktion: str, /, **werte: Any) -> dict[str, Any]:
        """Panoptes-eigene Aktion. Alles außerhalb der Erlaubnisliste bricht HIER ab (Regel A)."""
        if aktion not in ERLAUBTE_AKTIONEN:
            raise PanoptesGesperrt("nicht_erlaubt")
        _, daten = await self._anfrage(
            "POST", "/api/ha/v1/aktion", json_body={**werte, "aktion": aktion}
        )
        if daten.get("ok") is False:
            raise PanoptesFehler(str(daten.get("grund", "abgelehnt")))
        return daten

    # ------------------------------------------------------------------ Push
    async def ereignisse(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Server-Sent Events lesen. Endet, wenn der Server den Stream schließt."""
        kopf = self._kopf() | {"Accept": "text/event-stream"}
        try:
            antwort = await self._session.get(
                f"{self.url}/api/ha/v1/ereignisse",
                headers=kopf,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=90),
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            raise PanoptesNichtErreichbar("nicht_erreichbar") from err
        async with antwort:
            if antwort.status == 401:
                raise PanoptesAnmeldungFehlt("401", 401)
            if antwort.status == 404:
                raise PanoptesNichtVorhanden("404", 404)
            if antwort.status >= 400:
                raise PanoptesFehler(str(antwort.status), antwort.status)
            async for art, daten in lies_sse(antwort.content):
                yield art, daten


async def lies_sse(inhalt: aiohttp.StreamReader) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Minimaler SSE-Parser (event/data, Kommentare = Ping)."""
    art = "message"
    zeilen: list[str] = []
    try:
        async for roh in inhalt:
            zeile = roh.decode("utf-8", errors="replace").rstrip("\r\n")
            if not zeile:
                if zeilen:
                    try:
                        daten = json.loads("\n".join(zeilen))
                    except json.JSONDecodeError:
                        daten = None
                    if isinstance(daten, dict):
                        yield art, daten
                art, zeilen = "message", []
                continue
            if zeile.startswith(":"):
                continue
            feld, _, wert = zeile.partition(":")
            wert = wert.removeprefix(" ")
            if feld == "event":
                art = wert or "message"
            elif feld == "data":
                zeilen.append(wert)
    except (TimeoutError, aiohttp.ClientError) as err:
        raise PanoptesNichtErreichbar("stream") from err
