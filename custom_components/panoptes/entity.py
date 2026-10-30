"""Gemeinsame Basis der Entitäten + Filter für schreibbare Einstellungen."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, GESPERRTE_EINSTELLUNGS_PRAEFIXE, HERSTELLER
from .coordinator import PanoptesCoordinator


class PanoptesEntity(CoordinatorEntity[PanoptesCoordinator]):
    """Ein Gerät je Auto."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PanoptesCoordinator, schluessel: str) -> None:
        super().__init__(coordinator)
        eintrag = coordinator.config_entry
        basis = eintrag.unique_id or eintrag.entry_id
        self._attr_unique_id = f"{basis}_{schluessel}"
        geraet = coordinator.data.geraet if coordinator.data else {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, basis)},
            name=str(geraet.get("name") or eintrag.title),
            manufacturer=HERSTELLER,
            model=geraet.get("modell"),
            sw_version=geraet.get("appVersion"),
            configuration_url=eintrag.data.get("url"),
        )


def ist_schreibbar(feld: dict[str, Any]) -> bool:
    """Regel A/B auf HA-Seite: nur harmlose, gespeicherte Panoptes-Einstellungen."""
    key = str(feld.get("key") or "")
    if not key or feld.get("alias"):
        return False
    if any(key.startswith(p) for p in GESPERRTE_EINSTELLUNGS_PRAEFIXE):
        return False
    if feld.get("nurLesendExtern") is not False:  # fehlt das Feld → vorsichtshalber nur lesen
        return False
    if feld.get("gefahr") or feld.get("bestaetigung") or feld.get("neustartNoetig"):
        return False
    if feld.get("typ") == "geheim" or feld.get("sichtbar") is False:
        return False
    return True


def felder(schema: dict[str, Any] | None, typen: set[str]) -> Iterator[tuple[dict, dict]]:
    """(gruppe, feld) für alle schreibbaren Felder der gewünschten Typen."""
    if not schema:
        return
    for gruppe in schema.get("gruppen") or []:
        if not isinstance(gruppe, dict):
            continue
        for feld in gruppe.get("felder") or []:
            if isinstance(feld, dict) and feld.get("typ") in typen and ist_schreibbar(feld):
                yield gruppe, feld


def wert_von(werte: dict[str, Any], feld: dict[str, Any]) -> Any:
    eintrag = werte.get(feld["key"]) or {}
    if "wert" in eintrag and eintrag["wert"] is not None:
        return eintrag["wert"]
    return feld.get("standard")


def einstellungs_name(gruppe: dict[str, Any], feld: dict[str, Any]) -> str:
    return str(feld.get("titel") or feld["key"])
