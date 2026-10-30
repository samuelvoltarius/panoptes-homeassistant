"""Zahlen-Einstellungen (int/zahl) aus dem Schema, mit Grenzen vom Server."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
        for gruppe, feld in felder(coordinator.data.schema, {"int", "zahl"}):
            if feld.get("min") is None or feld.get("max") is None:
                continue  # ohne Grenzen keine Eingabe aus HA
            if feld["key"] not in bekannt:
                bekannt.add(feld["key"])
                neu.append(EinstellungsZahl(coordinator, gruppe, feld))
        if neu:
            async_add_entities(neu)

    neue_felder()
    entry.async_on_unload(coordinator.async_add_listener(neue_felder))


class EinstellungsZahl(PanoptesEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: PanoptesCoordinator, gruppe: dict, feld: dict) -> None:
        self._feld = feld
        super().__init__(coordinator, f"e_{feld['key']}")
        self._attr_name = einstellungs_name(gruppe, feld)
        self._attr_native_min_value = float(feld["min"])
        self._attr_native_max_value = float(feld["max"])
        self._attr_native_step = float(feld.get("schritt") or (1 if feld["typ"] == "int" else 0.1))
        self._attr_native_unit_of_measurement = feld.get("einheit") or None
        self._attr_entity_registry_enabled_default = not feld.get("erweitert", False)
        self._attr_icon = "mdi:tune-variant"

    @property
    def native_value(self) -> float | None:
        wert = wert_von(self.coordinator.data.werte, self._feld)
        try:
            return None if wert is None else float(wert)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        wert: float | int = int(round(value)) if self._feld["typ"] == "int" else value
        try:
            await self.coordinator.einstellung_setzen(self._feld["key"], wert)
        except PanoptesFehler as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="einstellung_abgelehnt",
                translation_placeholders={"grund": err.grund},
            ) from err
