"""Binäre Telemetrie (lädt, geparkt …) + Push-Verbindung (Diagnose)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PanoptesConfigEntry, PanoptesCoordinator
from .entity import PanoptesEntity


def _klasse(text: Any) -> BinarySensorDeviceClass | None:
    try:
        return BinarySensorDeviceClass(str(text))
    except ValueError:
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanoptesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    bekannt: set[str] = set()

    @callback
    def neue_felder() -> None:
        neu: list[BinarySensorEntity] = []
        for feld in coordinator.data.zustand.get("felder") or []:
            if not isinstance(feld, dict) or feld.get("typ") != "binary":
                continue
            key = str(feld.get("key") or "")
            if key and key not in bekannt:
                bekannt.add(key)
                neu.append(TelemetrieBinaer(coordinator, feld))
        if neu:
            async_add_entities(neu)

    async_add_entities([PushVerbindung(coordinator)])
    neue_felder()
    entry.async_on_unload(coordinator.async_add_listener(neue_felder))


class TelemetrieBinaer(PanoptesEntity, BinarySensorEntity):
    def __init__(self, coordinator: PanoptesCoordinator, feld: dict[str, Any]) -> None:
        self._key = str(feld["key"])
        super().__init__(coordinator, f"t_{self._key}")
        self._attr_name = str(feld.get("titel") or self._key)
        self._attr_device_class = _klasse(feld.get("deviceClass"))
        self._attr_icon = feld.get("icon") or None
        self._attr_entity_registry_enabled_default = not feld.get("erweitert", False)

    @property
    def available(self) -> bool:
        return super().available and self._key in self.coordinator.data.telemetrie

    @property
    def is_on(self) -> bool | None:
        wert = self.coordinator.data.telemetrie.get(self._key)
        if wert is None:
            return None
        if isinstance(wert, str):
            return wert.lower() in ("1", "true", "on", "an", "ja")
        return bool(wert)


class PushVerbindung(PanoptesEntity, BinarySensorEntity):
    """Direkt: läuft Push (SSE)? Webhook: hat sich das Auto in den letzten Minuten gemeldet?"""

    _attr_translation_key = "push"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PanoptesCoordinator) -> None:
        super().__init__(coordinator, "push")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.verbunden
