"""Schalter: Wächter an/aus (Panoptes-Funktion) + boolesche Panoptes-Einstellungen.

Regel A: Kein Schalter steuert das Fahrzeug selbst (Klima, Schloss, Licht, Hupe, Fenster).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import PanoptesFehler
from .const import DOMAIN
from .coordinator import PanoptesConfigEntry, PanoptesCoordinator
from .entity import PanoptesEntity, einstellungs_name, felder, wert_von


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanoptesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    bekannt: set[str] = set()

    @callback
    def neue_felder() -> None:
        if not coordinator.einstellungen_an:
            return
        neu = []
        for gruppe, feld in felder(coordinator.data.schema, {"bool"}):
            if feld["key"] not in bekannt:
                bekannt.add(feld["key"])
                neu.append(EinstellungsSchalter(coordinator, gruppe, feld))
        if neu:
            async_add_entities(neu)

    async_add_entities([WaechterSchalter(coordinator)])
    neue_felder()
    entry.async_on_unload(coordinator.async_add_listener(neue_felder))


class WaechterSchalter(PanoptesEntity, SwitchEntity):
    _attr_translation_key = "waechter_schalter"
    _attr_icon = "mdi:shield-car"

    def __init__(self, coordinator: PanoptesCoordinator) -> None:
        super().__init__(coordinator, "waechter_schalter")

    @property
    def is_on(self) -> bool | None:
        zustand = (self.coordinator.data.zustand.get("waechter") or {}).get("zustand")
        if zustand is None:
            return None
        return zustand in ("scharf", "startet")

    async def _setze(self, an: bool) -> None:
        try:
            await self.coordinator.aktion("waechter", an=an)
        except PanoptesFehler as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="aktion_fehlgeschlagen",
                translation_placeholders={"grund": err.grund},
            ) from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._setze(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._setze(False)


class EinstellungsSchalter(PanoptesEntity, SwitchEntity):
    """Eine gespeicherte bool-Einstellung aus dem Schema."""

    def __init__(self, coordinator: PanoptesCoordinator, gruppe: dict, feld: dict) -> None:
        self._feld = feld
        super().__init__(coordinator, f"e_{feld['key']}")
        self._attr_name = einstellungs_name(gruppe, feld)
        self._attr_entity_registry_enabled_default = not feld.get("erweitert", False)
        self._attr_icon = "mdi:cog-outline"

    @property
    def is_on(self) -> bool | None:
        wert = wert_von(self.coordinator.data.werte, self._feld)
        return None if wert is None else bool(wert)

    async def _setze(self, an: bool) -> None:
        try:
            await self.coordinator.einstellung_setzen(self._feld["key"], an)
        except PanoptesFehler as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="einstellung_abgelehnt",
                translation_placeholders={"grund": err.grund},
            ) from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._setze(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._setze(False)
