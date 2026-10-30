"""Home-Assistant-Integration für Panoptes (BYD DiLink 3/4/5).

Zwei Wege (Vertrag docs/HA_VERTRAG.md):
- „Direkt“: HA spricht die Auto-API über Tailscale oder das Heim-WLAN (Push per SSE).
- „Über das Internet“ (Webhook): Das Auto sendet ausgehend an dieses HA. Ausgeblendet, bis Panoptes das kann.
Panoptes → HA: Telemetrie, Wächter, Gesundheit, F1, Ankunft/Abfahrt.
HA → Panoptes: nur gespeicherte Einstellungen und Panoptes-eigene Funktionen, nie Fahrzeugbefehle.
Entfernen trennt die Verbindung auch in Panoptes.
"""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
import homeassistant.helpers.config_validation as cv

from .api import PanoptesApi, PanoptesFehler
from .const import (
    CONF_MODUS,
    CONF_TOKEN,
    CONF_URL,
    CONF_WEBHOOK_ID,
    DOMAIN,
    MODUS_DIREKT,
    MODUS_WEBHOOK,
    SITZUNG_ERNEUERN,
)
from .coordinator import PanoptesBasis, PanoptesConfigEntry, PanoptesCoordinator
from .services import async_registriere_dienste
from .webhook import Grabsteine, WebhookCoordinator

_LOGGER = logging.getLogger(__name__)

PLATTFORMEN: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
SCHLUESSEL_GRABSTEINE = f"{DOMAIN}_grabsteine"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_registriere_dienste(hass)
    grabsteine = Grabsteine(hass)
    await grabsteine.laden()
    hass.data[SCHLUESSEL_GRABSTEINE] = grabsteine
    return True


def modus(entry: PanoptesConfigEntry) -> str:
    return entry.data.get(CONF_MODUS, MODUS_DIREKT)


async def async_setup_entry(hass: HomeAssistant, entry: PanoptesConfigEntry) -> bool:
    coordinator: PanoptesBasis
    if modus(entry) == MODUS_WEBHOOK:
        coordinator = WebhookCoordinator(hass, entry)
        coordinator.registrieren()
        entry.async_on_unload(coordinator.abmelden)

        @callback
        def lebenszeichen(_jetzt) -> None:
            # „Live-Verbindung“ neu bewerten, auch wenn das Auto schweigt.
            coordinator.async_update_listeners()

        entry.async_on_unload(async_track_time_interval(hass, lebenszeichen, SITZUNG_ERNEUERN / 1440))
    else:
        api = PanoptesApi(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
        coordinator = PanoptesCoordinator(hass, entry, api)
        await coordinator.async_config_entry_first_refresh()
        entry.async_create_background_task(hass, coordinator.push_schleife(), f"{DOMAIN}_push_{entry.entry_id}")
        entry.async_on_unload(async_track_time_interval(hass, coordinator.sitzung_erneuern, SITZUNG_ERNEUERN))

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATTFORMEN)
    # Doppelte Entitäten vermeiden: Solange HA verbunden ist, meldet Panoptes nicht zusätzlich per MQTT-Discovery.
    entry.async_create_background_task(
        hass, coordinator.mqtt_discovery_abschalten(True), f"{DOMAIN}_mqtt_{entry.entry_id}"
    )

    optionen = dict(entry.options)

    async def bei_aenderung(hass: HomeAssistant, geaendert: PanoptesConfigEntry) -> None:
        # Token-Erneuerung ändert nur data → kein Neuladen. Optionen → neu laden.
        if dict(geaendert.options) != optionen:
            await hass.config_entries.async_reload(geaendert.entry_id)

    entry.async_on_unload(entry.add_update_listener(bei_aenderung))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PanoptesConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATTFORMEN)


async def async_remove_entry(hass: HomeAssistant, entry: PanoptesConfigEntry) -> None:
    """Integration entfernt → Verbindung auch in Panoptes trennen."""
    if modus(entry) == MODUS_WEBHOOK:
        # Das Auto ist nicht erreichbar; es erfährt es beim nächsten Senden über den Grabstein
        # (der setzt auch die MQTT-Discovery wieder an).
        grabsteine: Grabsteine = hass.data[SCHLUESSEL_GRABSTEINE]
        await grabsteine.hinzufuegen(entry.data[CONF_WEBHOOK_ID])
        return

    api = PanoptesApi(async_get_clientsession(hass), entry.data[CONF_URL], entry.data.get(CONF_TOKEN))
    try:
        antwort = await api.einstellungen()
        if "ha.mqttDiscoveryAus" in (antwort.get("werte") or {}):
            await api.einstellung_setzen(antwort.get("etag"), "ha.mqttDiscoveryAus", False)
    except PanoptesFehler:
        pass
    try:
        await api.selbst_widerrufen()
    except PanoptesFehler as err:
        _LOGGER.warning(
            "Kopplung in Panoptes konnte nicht widerrufen werden (%s). Bitte in Panoptes unter "
            "„Gekoppelte Geräte“ den Eintrag „Home Assistant“ entfernen",
            err.grund,
        )
