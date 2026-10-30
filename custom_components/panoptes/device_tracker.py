"""Position nur, wenn sie in Panoptes UND in den HA-Optionen eingeschaltet ist."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PanoptesConfigEntry, PanoptesCoordinator
from .entity import PanoptesEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanoptesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if coordinator.position_an:
        async_add_entities([PanoptesPosition(coordinator)])


class PanoptesPosition(PanoptesEntity, TrackerEntity):
    _attr_translation_key = "position"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: PanoptesCoordinator) -> None:
        super().__init__(coordinator, "position")

    def _position(self) -> dict:
        pos = self.coordinator.data.zustand.get("position")
        return pos if isinstance(pos, dict) else {}

    @property
    def available(self) -> bool:
        pos = self._position()
        return super().available and pos.get("lat") is not None and pos.get("lon") is not None

    @property
    def latitude(self) -> float | None:
        return self._position().get("lat")

    @property
    def longitude(self) -> float | None:
        return self._position().get("lon")

    @property
    def location_accuracy(self) -> float:
        return float(self._position().get("genauigkeitM") or 0)
