"""Sensoren: Telemetrie generisch aus dem Feld-Katalog + Wächter, Gesundheit, F1-Stufe."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import F1_STUFEN, GESUNDHEIT_ZUSTAENDE, WAECHTER_ZUSTAENDE
from .coordinator import PanoptesConfigEntry, PanoptesCoordinator
from .entity import PanoptesEntity

# Ortsdaten nie als Sensor – Position gibt es nur als device_tracker mit doppelter Zustimmung.
NIE_ALS_SENSOR = frozenset({"lat", "lon", "elevation", "heading", "vin"})


def _klasse(text: Any) -> SensorDeviceClass | None:
    try:
        klasse = SensorDeviceClass(str(text))
    except ValueError:
        return None
    return None if klasse == SensorDeviceClass.ENUM else klasse


def _zustandsklasse(text: Any) -> SensorStateClass | None:
    try:
        return SensorStateClass(str(text))
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
        neu: list[SensorEntity] = []
        for feld in coordinator.data.zustand.get("felder") or []:
            if not isinstance(feld, dict) or feld.get("typ") != "sensor":
                continue
            key = str(feld.get("key") or "")
            if not key or key in NIE_ALS_SENSOR or key in bekannt:
                continue
            bekannt.add(key)
            neu.append(TelemetrieSensor(coordinator, feld))
        if neu:
            async_add_entities(neu)

    async_add_entities(
        [
            ZustandsSensor(coordinator, "waechter", WAECHTER_ZUSTAENDE, "mdi:shield-car"),
            ZustandsSensor(coordinator, "gesundheit", GESUNDHEIT_ZUSTAENDE, "mdi:heart-pulse"),
            ZustandsSensor(coordinator, "f1", F1_STUFEN, "mdi:map-marker-distance", feld="stufe"),
            AppVersionSensor(coordinator),
        ]
    )
    neue_felder()
    entry.async_on_unload(coordinator.async_add_listener(neue_felder))


class TelemetrieSensor(PanoptesEntity, SensorEntity):
    """Ein Wert aus der Telemetrie (gleiche Schlüssel wie MQTT)."""

    def __init__(self, coordinator: PanoptesCoordinator, feld: dict[str, Any]) -> None:
        self._key = str(feld["key"])
        super().__init__(coordinator, f"t_{self._key}")
        self._attr_name = str(feld.get("titel") or self._key)
        self._attr_device_class = _klasse(feld.get("deviceClass"))
        self._attr_state_class = _zustandsklasse(feld.get("stateClass"))
        self._attr_native_unit_of_measurement = feld.get("einheit") or None
        self._attr_icon = feld.get("icon") or None
        self._attr_entity_registry_enabled_default = not feld.get("erweitert", False)

    @property
    def available(self) -> bool:
        return super().available and self._key in self.coordinator.data.telemetrie

    @property
    def native_value(self) -> Any:
        wert = self.coordinator.data.telemetrie.get(self._key)
        if isinstance(wert, (dict, list)):
            return None
        if isinstance(wert, bool) and self._attr_state_class is not None:
            return int(wert)
        return wert


class ZustandsSensor(PanoptesEntity, SensorEntity):
    """Wächter / Gesundheit / F1 als Auswahl-Sensor (übersetzbar)."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(
        self,
        coordinator: PanoptesCoordinator,
        bereich: str,
        optionen: list[str],
        icon: str,
        feld: str = "zustand",
    ) -> None:
        super().__init__(coordinator, bereich)
        self._bereich = bereich
        self._feld = feld
        self._attr_translation_key = bereich
        self._attr_options = optionen
        self._attr_icon = icon

    def _teil(self) -> dict[str, Any]:
        teil = self.coordinator.data.zustand.get(self._bereich)
        return teil if isinstance(teil, dict) else {}

    @property
    def native_value(self) -> str | None:
        wert = self._teil().get(self._feld)
        return wert if wert in (self._attr_options or []) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        teil = self._teil()
        attribute: dict[str, Any] = {}
        if teil.get("grund") is not None:
            attribute["grund"] = teil["grund"]
        if self._bereich == "gesundheit" and isinstance(teil.get("punkte"), list):
            attribute["punkte"] = {
                str(p.get("titel") or p.get("id")): bool(p.get("ok"))
                for p in teil["punkte"]
                if isinstance(p, dict)
            }
        return attribute


class AppVersionSensor(PanoptesEntity, SensorEntity):
    _attr_translation_key = "app_version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information-outline"

    def __init__(self, coordinator: PanoptesCoordinator) -> None:
        super().__init__(coordinator, "app_version")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.geraet.get("appVersion")
