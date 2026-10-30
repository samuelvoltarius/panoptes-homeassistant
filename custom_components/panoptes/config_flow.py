"""Einrichtung: Adresse (oder mDNS) → Kopplungs-Anfrage → ein Tipp auf „Erlauben“ in Panoptes.

Kein Code zum Abtippen. HA zeigt nur die 4-stellige Prüfzahl, der Nutzer vergleicht sie in
Telegram / Panoptes-Web / Auto-Display und tippt „Erlauben“. Danach holt HA einmalig den
Kopplungs-Token (scope "kopplung").
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import secrets
import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    KopplungsAnfrage,
    KopplungsErgebnis,
    PanoptesApi,
    PanoptesFehler,
    PanoptesNichtErreichbar,
    PanoptesNichtVorhanden,
    PanoptesZuOft,
    normalisiere_url,
)
from .const import (
    CONF_ADRESSEN,
    CONF_AUTO_ID,
    CONF_GEHEIMNIS,
    CONF_MODUS,
    CONF_WEBHOOK_ID,
    MODUS_DIREKT,
    MODUS_WEBHOOK,
    CONF_KOPPLUNG_ID,
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
    KOPPLUNG_HOECHSTENS_S,
    OPT_EINSTELLUNGEN,
    OPT_POSITION,
    WEBHOOK_VERFUEGBAR,
)

from .webhook import Einrichtung, ermittle_adressen, gueltig_bis_text, telegram_link

_LOGGER = logging.getLogger(__name__)

# Netzaussetzer während des Wartens tolerieren (Auto im Funkloch, WLAN-Wechsel).
MAX_NETZFEHLER = 5

_EINRICHTUNG_WARTEN_S = 600

# Eigener Name, damit Tests das Warten verkürzen können, ohne asyncio global zu patchen.
_schlafen = asyncio.sleep


class PanoptesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Einrichtungsassistent."""

    VERSION = 1

    def __init__(self) -> None:
        self._url: str | None = None
        self._name: str | None = None
        self._api: PanoptesApi | None = None
        self._anfrage: KopplungsAnfrage | None = None
        self._pruefzahl: str = ""
        self._warte_task: asyncio.Task[KopplungsErgebnis] | None = None
        self._ergebnis: KopplungsErgebnis | None = None
        self._adressen: list[str] = []
        self._einrichtung: Einrichtung | None = None
        self._hallo: dict[str, Any] | None = None
        self._fertig_begonnen = False
        self._reauth_webhook = False

    # ------------------------------------------------------------------ Einstiege
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Einstieg (auch über den My-Home-Assistant-Link aus Panoptes): Weg wählen."""
        if not WEBHOOK_VERFUEGBAR:
            return await self.async_step_direkt(user_input)
        return self.async_show_menu(step_id="user", menu_options=["internet", "direkt"])

    async def async_step_direkt(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Direkt: gleiches WLAN oder Tailscale, Adresse eingeben."""
        fehler: dict[str, str] = {}
        if user_input is not None:
            try:
                self._url = normalisiere_url(user_input[CONF_URL])
            except ValueError:
                fehler[CONF_URL] = "ungueltige_adresse"
            else:
                return await self._starte_kopplung(fehler, step_id="direkt")
        return self.async_show_form(
            step_id="direkt",
            data_schema=vol.Schema(
                {vol.Required(CONF_URL, default=self._url or ""): str}
            ),
            errors=fehler,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Panoptes kündigt sich im LAN als _panoptes._tcp an."""
        auto_id = str(discovery_info.properties.get("id") or "").strip()
        if not auto_id:
            return self.async_abort(reason="keine_kennung")
        host = discovery_info.host
        if ":" in host:
            host = f"[{host}]"
        self._url = f"http://{host}:{discovery_info.port or 8180}"
        await self.async_set_unique_id(auto_id)
        # Bekanntes Auto mit neuer IP: nur die Adresse nachziehen, keine neue Kopplung.
        self._abort_if_unique_id_configured(updates={CONF_URL: self._url})
        self._name = discovery_info.name.split(".", 1)[0] or "Panoptes"
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        fehler: dict[str, str] = {}
        if user_input is not None:
            return await self._starte_kopplung(fehler, step_id="zeroconf_confirm")
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._name or "Panoptes", "url": self._url or ""},
            errors=fehler,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Kopplung widerrufen/abgelaufen bzw. in Panoptes getrennt → neu verbinden, gleicher Weg."""
        self._reauth_webhook = entry_data.get(CONF_MODUS) == MODUS_WEBHOOK
        self._url = entry_data.get(CONF_URL)
        # Erst fragen: Ein Neu-Verbinden legt einen Einrichtungs-Webhook an, das nie unaufgefordert.
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        fehler: dict[str, str] = {}
        if user_input is not None:
            if self._reauth_webhook:
                return await self.async_step_internet()
            return await self._starte_kopplung(fehler, step_id="reauth_confirm")
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"url": self._url or "Panoptes"},
            errors=fehler,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nur die Adresse ändern (z. B. Tailscale statt WLAN); die Kopplung bleibt."""
        eintrag = self._get_reconfigure_entry()
        if eintrag.data.get(CONF_MODUS) == MODUS_WEBHOOK:
            # Neue HA-Adressen muss das Auto kennen → neu verbinden (3 Tipps).
            return await self.async_step_internet()
        fehler: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalisiere_url(user_input[CONF_URL])
            except ValueError:
                fehler[CONF_URL] = "ungueltige_adresse"
            else:
                api = PanoptesApi(
                    async_get_clientsession(self.hass), url, eintrag.data[CONF_TOKEN]
                )
                try:
                    zustand = await api.zustand()
                except PanoptesNichtErreichbar:
                    fehler["base"] = "nicht_erreichbar"
                except PanoptesFehler:
                    fehler["base"] = "anderes_auto"
                else:
                    geraet = zustand.get("geraet") or {}
                    auto_id = geraet.get("haId") or geraet.get("autoId")
                    if auto_id and auto_id != eintrag.unique_id:
                        fehler["base"] = "anderes_auto"
                    else:
                        return self.async_update_reload_and_abort(
                            eintrag, data_updates={CONF_URL: url}
                        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_URL, default=eintrag.data[CONF_URL]): str}
            ),
            errors=fehler,
        )

    # ------------------------------------------------------------------ Kopplung
    def _geraetename(self) -> str:
        ort = (self.hass.config.location_name or "").strip()
        name = f"Home Assistant {ort}".strip() if ort and ort != "Home" else "Home Assistant"
        return name[:40]

    async def _starte_kopplung(
        self, fehler: dict[str, str], step_id: str = "direkt"
    ) -> ConfigFlowResult:
        assert self._url
        self._api = PanoptesApi(async_get_clientsession(self.hass), self._url)
        self._pruefzahl = f"{secrets.randbelow(10000):04d}"
        try:
            self._anfrage = await self._api.kopplung_anfragen(self._geraetename(), self._pruefzahl)
        except PanoptesNichtErreichbar:
            fehler["base"] = "nicht_erreichbar"
        except PanoptesZuOft:
            fehler["base"] = "zu_viele_anfragen"
        except PanoptesNichtVorhanden:
            fehler["base"] = "panoptes_zu_alt"
        except PanoptesFehler:
            fehler["base"] = "unbekannt"
        if fehler:
            if step_id == "direkt":
                return self.async_show_form(
                    step_id="direkt",
                    data_schema=vol.Schema({vol.Required(CONF_URL, default=self._url): str}),
                    errors=fehler,
                )
            return self.async_show_form(
                step_id=step_id,
                description_placeholders={"name": self._name or "Panoptes", "url": self._url},
                errors=fehler,
            )
        self._warte_task = None
        return await self.async_step_warten()

    async def async_step_warten(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Fortschrittsanzeige mit Prüfzahl, bis in Panoptes erlaubt/abgelehnt wurde."""
        if self._warte_task is None:
            self._warte_task = self.hass.async_create_task(
                self._warte_auf_freigabe(), eager_start=False
            )
        if not self._warte_task.done():
            return self.async_show_progress(
                step_id="warten",
                progress_action="freigabe",
                description_placeholders={
                    "pruefzahl": self._pruefzahl,
                    "geraet": self._geraetename(),
                },
                progress_task=self._warte_task,
            )
        try:
            self._ergebnis = self._warte_task.result()
        except Exception:  # noqa: BLE001 - Fehler im Hintergrund → sauber abbrechen
            _LOGGER.exception("Kopplung: unerwarteter Fehler beim Warten")
            self._ergebnis = KopplungsErgebnis("unbekannt")
        if self._ergebnis.status == "erlaubt" and self._ergebnis.jwt:
            return self.async_show_progress_done(next_step_id="fertig")
        return self.async_show_progress_done(next_step_id="fehlgeschlagen")

    async def _warte_auf_freigabe(self) -> KopplungsErgebnis:
        assert self._api and self._anfrage
        ende = time.monotonic() + KOPPLUNG_HOECHSTENS_S
        netzfehler = 0
        while time.monotonic() < ende:
            await _schlafen(self._anfrage.abfrage_alle_s)
            try:
                ergebnis = await self._api.kopplung_abholen(self._anfrage.id)
            except PanoptesNichtErreichbar:
                netzfehler += 1
                if netzfehler > MAX_NETZFEHLER:
                    return KopplungsErgebnis("nicht_erreichbar")
                continue
            if ergebnis.status != "wartet":
                return ergebnis
        return KopplungsErgebnis("abgelaufen")

    async def async_step_fehlgeschlagen(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        status = self._ergebnis.status if self._ergebnis else "unbekannt"
        grund = {
            "abgelehnt": "abgelehnt",
            "abgelaufen": "abgelaufen",
            "abgeholt": "schon_abgeholt",
            "nicht_erreichbar": "nicht_erreichbar",
        }.get(status, "unbekannt")
        return self.async_abort(reason=grund)

    async def async_step_fertig(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._api and self._ergebnis and self._ergebnis.jwt and self._url
        self._api.token = self._ergebnis.jwt
        try:
            zustand = await self._api.zustand()
        except PanoptesNichtVorhanden:
            # Gekoppelt, aber dieses Panoptes kennt die HA-Schnittstelle noch nicht.
            await self._raeume_auf()
            return self.async_abort(reason="panoptes_zu_alt")
        except PanoptesFehler:
            await self._raeume_auf()
            return self.async_abort(reason="nicht_erreichbar")

        geraet = zustand.get("geraet") or {}
        # Vertrag: haId (= mDNS-id, nicht verknüpfbar). Panoptes 0.1.32 liefert noch die Planer-autoId.
        auto_id = str(geraet.get("haId") or geraet.get("autoId") or self._ergebnis.kopplung_id or "")
        daten = {
            CONF_MODUS: MODUS_DIREKT,
            CONF_URL: self._url,
            CONF_TOKEN: self._ergebnis.jwt,
            CONF_KOPPLUNG_ID: self._ergebnis.kopplung_id,
            CONF_AUTO_ID: auto_id,
        }

        if self.source == SOURCE_REAUTH:
            eintrag = self._get_reauth_entry()
            if eintrag.unique_id and auto_id and eintrag.unique_id != auto_id:
                await self._raeume_auf()
                return self.async_abort(reason="anderes_auto")
            return self.async_update_reload_and_abort(eintrag, data_updates=daten)

        await self.async_set_unique_id(auto_id, raise_on_progress=False)
        if self._async_current_entries() and any(
            e.unique_id == auto_id for e in self._async_current_entries()
        ):
            # Schon eingerichtet: die eben erzeugte zweite Kopplung nicht liegen lassen.
            await self._raeume_auf()
            return self.async_abort(reason="already_configured")
        return self.async_create_entry(
            title=str(geraet.get("name") or "Panoptes"),
            data=daten,
            options={OPT_POSITION: False, OPT_EINSTELLUNGEN: True},
        )

    async def _raeume_auf(self) -> None:
        """Gerade erhaltene Kopplung widerrufen (best effort)."""
        if not self._api:
            return
        try:
            await self._api.selbst_widerrufen()
        except PanoptesFehler:
            _LOGGER.warning(
                "Kopplung konnte nicht selbst widerrufen werden – bitte in Panoptes unter "
                "„Gekoppelte Geräte“ entfernen"
            )

    # ------------------------------------------------------------------ Über das Internet (Webhook, Standard)
    async def async_step_internet(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Adressen dieses HA ermitteln; ohne externe Adresse ehrlich warnen."""
        adressen, extern = ermittle_adressen(self.hass)
        if not adressen:
            return self.async_abort(reason="keine_adresse")
        self._adressen = adressen
        if not extern:
            return await self.async_step_nur_lokal()
        return await self._starte_einrichtung()

    async def async_step_nur_lokal(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._starte_einrichtung()
        return self.async_show_form(
            step_id="nur_lokal", description_placeholders={"adresse": self._adressen[0]}
        )

    async def _starte_einrichtung(self) -> ConfigFlowResult:
        self._einrichtung = Einrichtung(self.hass, self._adressen, self._geraetename())
        self._einrichtung.starten()
        self._warte_task = None
        return await self.async_step_warten_auto()

    async def async_step_warten_auto(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Text an den eigenen Panoptes-Bot schicken, dort Erlauben – bis zu 10 min."""
        e = self._einrichtung
        assert e is not None
        if self._warte_task is None:
            self._warte_task = self.hass.async_create_task(self._warte_auf_auto(), eager_start=False)
            # Der Nutzer ist gerade in Telegram, nicht in HA: Ohne offenes Fenster bliebe der Flow bei
            # „Fortschritt fertig“ stehen, und das Auto hätte Zugangsdaten ohne Gegenstelle.
            self._warte_task.add_done_callback(
                lambda _t: self.hass.async_create_task(self._weiter_ohne_browser())
            )
        if not self._warte_task.done():
            return self.async_show_progress(
                step_id="warten_auto",
                progress_action="auto",
                description_placeholders={
                    "link": telegram_link(e.text),
                    "text": e.text,
                    "pruefzahl": e.pruefzahl,
                    "bis": gueltig_bis_text(e),
                },
                progress_task=self._warte_task,
            )
        self._hallo = self._warte_task.result()
        if self._hallo is None:
            return self.async_show_progress_done(next_step_id="auto_abgelaufen")
        return self.async_show_progress_done(next_step_id="fertig_internet")

    async def _warte_auf_auto(self) -> dict[str, Any] | None:
        assert self._einrichtung
        try:
            return await asyncio.wait_for(asyncio.shield(self._einrichtung.ergebnis), _EINRICHTUNG_WARTEN_S)
        except (TimeoutError, asyncio.CancelledError):
            return None
        finally:
            self._einrichtung.beenden_ohne_abbruch()

    async def _weiter_ohne_browser(self) -> None:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if self.cur_step and self.cur_step.get("type") is FlowResultType.SHOW_PROGRESS_DONE:
                break
        else:
            return
        with suppress(UnknownFlow):
            await self.hass.config_entries.flow.async_configure(self.flow_id)

    async def async_step_auto_abgelaufen(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason="abgelaufen")

    async def async_step_fertig_internet(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._hallo is not None
        if self._fertig_begonnen:  # Browser und Server-Fortsetzung gleichzeitig
            return self.async_abort(reason="already_in_progress")
        self._fertig_begonnen = True
        geraet = self._hallo["geraet"]
        ha_id = str(geraet["haId"])
        daten = {
            CONF_MODUS: MODUS_WEBHOOK,
            CONF_WEBHOOK_ID: self._hallo["webhook"],
            CONF_GEHEIMNIS: self._hallo["geheimnis"],
            CONF_ADRESSEN: self._adressen,
            CONF_AUTO_ID: ha_id,
        }
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            eintrag = (
                self._get_reauth_entry() if self.source == SOURCE_REAUTH else self._get_reconfigure_entry()
            )
            if eintrag.unique_id and eintrag.unique_id != ha_id:
                return self.async_abort(reason="anderes_auto")
            return self.async_update_reload_and_abort(eintrag, data=daten)
        await self.async_set_unique_id(ha_id, raise_on_progress=False)
        # Gleiches Auto neu verbunden: Das Auto nutzt ab jetzt die neuen Zugangsdaten → Eintrag aktualisieren.
        self._abort_if_unique_id_configured(updates=daten)
        return self.async_create_entry(
            title=str(geraet.get("name") or "Panoptes"),
            data=daten,
            options={OPT_POSITION: False, OPT_EINSTELLUNGEN: True},
        )

    @callback
    def async_remove(self) -> None:
        """Flow abgebrochen/geschlossen: Einrichtungs-Webhook sofort entfernen."""
        if self._einrichtung is not None:
            self._einrichtung.beenden()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PanoptesOptionsFlow()


class PanoptesOptionsFlow(OptionsFlow):
    """Position (device_tracker) und Einstellungs-Entitäten ein-/ausschalten."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        optionen = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_POSITION, default=optionen.get(OPT_POSITION, False)
                    ): bool,
                    vol.Required(
                        OPT_EINSTELLUNGEN, default=optionen.get(OPT_EINSTELLUNGEN, True)
                    ): bool,
                }
            ),
        )
