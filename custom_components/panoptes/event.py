"""Ereignis-Entitäten: Ankunft/Abfahrt (H5) und Wächter-Ereignisse (nur Art + Zeit)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import FAHRT_EREIGNISSE, SIGNAL_EREIGNIS, WAECHTER_EREIGNISSE
from .coordinator import PanoptesConfigEntry, PanoptesCoordinator
from .entity import PanoptesEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanoptesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PanoptesEreignis(coordinator, "fahrt", FAHRT_EREIGNISSE, "mdi:car-arrow-right"),
            PanoptesEreignis(coordinator, "waechter", WAECHTER_EREIGNISSE, "mdi:cctv"),
        ]
    )


class PanoptesEreignis(PanoptesEntity, EventEntity):
    def __init__(
        self, coordinator: PanoptesCoordinator, kanal: str, arten: list[str], icon: str
    ) -> None:
        super().__init__(coordinator, f"ereignis_{kanal}")
        self._kanal = kanal
        self._attr_translation_key = f"ereignis_{kanal}"
        self._attr_event_types = arten
        self._attr_icon = icon

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_EREIGNIS.format(self.coordinator.config_entry.entry_id),
                self._empfange,
            )
        )

    @callback
    def _empfange(self, kanal: str, art: str, daten: dict[str, Any]) -> None:
        if kanal != self._kanal or art not in self.event_types:
            return
        self._trigger_event(art, {k: v for k, v in daten.items() if v is not None})
        self.async_write_ha_state()
