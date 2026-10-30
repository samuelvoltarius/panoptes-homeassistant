"""Diagnose-Download: ohne Token, ohne Position, ohne Kennungen."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_AUTO_ID, CONF_KOPPLUNG_ID, CONF_TOKEN, CONF_URL
from .coordinator import PanoptesConfigEntry

SCHWAERZEN = {CONF_TOKEN, "geheimnis", "webhook_id", "adressen", "haId", CONF_KOPPLUNG_ID, CONF_AUTO_ID, CONF_URL, "autoId", "position", "lat", "lon"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PanoptesConfigEntry
) -> dict[str, Any]:
    c = entry.runtime_data
    return {
        "eintrag": async_redact_data(dict(entry.data), SCHWAERZEN),
        "optionen": dict(entry.options),
        "modus": c.modus,
        "verbunden": c.verbunden,
        "push_unterstuetzt": getattr(c, "push_unterstuetzt", None),
        "offene_auftraege": [a.get("typ") for a in getattr(c, "auftraege", {}).values()],
        "zustand": async_redact_data(c.data.zustand, SCHWAERZEN),
        "einstellungen_anzahl": len(c.data.werte),
        "regeln": [
            {"ausloesbar": r.get("ausloesbar"), "grund": r.get("grund")} for r in c.data.regeln
        ],
    }
