"""Dienste: Ruhezeit, Regel ausführen.

Regel A: Es gibt bewusst KEINEN Dienst für Klima, Schloss, Fenster, Hupe oder Licht.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .api import PanoptesFehler
from .const import DOMAIN
from .coordinator import PanoptesCoordinator

ATTR_EINTRAG = "config_entry_id"

SCHEMA_RUHEZEIT = vol.Schema(
    {
        vol.Required(ATTR_EINTRAG): cv.string,
        vol.Required("minuten"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
    }
)
SCHEMA_REGEL = vol.Schema(
    {vol.Required(ATTR_EINTRAG): cv.string, vol.Required("regel_id"): cv.string}
)


def _coordinator(hass: HomeAssistant, eintrag_id: str) -> PanoptesCoordinator:
    eintrag = hass.config_entries.async_get_entry(eintrag_id)
    if eintrag is None or eintrag.domain != DOMAIN:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="eintrag_unbekannt")
    if eintrag.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="eintrag_nicht_geladen")
    return eintrag.runtime_data


async def _ausfuehren(coordinator: PanoptesCoordinator, aktion: str, /, **werte: Any) -> None:
    try:
        await coordinator.aktion(aktion, **werte)
    except PanoptesFehler as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="aktion_fehlgeschlagen",
            translation_placeholders={"grund": err.grund},
        ) from err


def async_registriere_dienste(hass: HomeAssistant) -> None:
    async def ruhezeit(call: ServiceCall) -> None:
        c = _coordinator(hass, call.data[ATTR_EINTRAG])
        await _ausfuehren(c, "ruhezeit", minuten=call.data["minuten"])
        await c.async_request_refresh()

    async def regel_ausfuehren(call: ServiceCall) -> None:
        c = _coordinator(hass, call.data[ATTR_EINTRAG])
        regel_id = call.data["regel_id"]
        regel = next((r for r in c.data.regeln if str(r.get("id")) == regel_id), None)
        if regel is None or regel.get("ausloesbar") is not True:
            # Regel A: Regeln mit Fahrzeugaktion lösen wir nie aus, auch nicht per Dienst.
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="regel_nicht_ausloesbar"
            )
        await _ausfuehren(c, "regel", id=regel_id)

    hass.services.async_register(DOMAIN, "ruhezeit", ruhezeit, schema=SCHEMA_RUHEZEIT)
    hass.services.async_register(DOMAIN, "regel_ausfuehren", regel_ausfuehren, schema=SCHEMA_REGEL)
