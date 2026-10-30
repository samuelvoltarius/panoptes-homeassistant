"""Knöpfe: Panoptes-Regeln ausführen – nur Regeln OHNE Fahrzeugaktion (Regel A)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import PanoptesFehler
from .const import DOMAIN
from .coordinator import PanoptesConfigEntry, PanoptesCoordinator
from .entity import PanoptesEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanoptesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    bekannt: set[str] = set()

    @callback
    def neue_regeln() -> None:
        neu = []
        for regel in coordinator.data.regeln:
            rid = str(regel.get("id") or "")
            if rid and rid not in bekannt and regel.get("ausloesbar") is True:
                bekannt.add(rid)
                neu.append(RegelKnopf(coordinator, regel))
        if neu:
            async_add_entities(neu)

    neue_regeln()
    entry.async_on_unload(coordinator.async_add_listener(neue_regeln))


class RegelKnopf(PanoptesEntity, ButtonEntity):
    _attr_icon = "mdi:script-text-play-outline"

    def __init__(self, coordinator: PanoptesCoordinator, regel: dict) -> None:
        self._regel_id = str(regel["id"])
        super().__init__(coordinator, f"regel_{self._regel_id}")
        self._attr_translation_key = "regel"
        self._attr_translation_placeholders = {"name": str(regel.get("name") or self._regel_id)}

    def _regel(self) -> dict | None:
        for regel in self.coordinator.data.regeln:
            if str(regel.get("id")) == self._regel_id:
                return regel
        return None

    @property
    def available(self) -> bool:
        regel = self._regel()
        # Wird eine Regel später um eine Fahrzeugaktion erweitert, ist der Knopf sofort weg.
        return super().available and regel is not None and regel.get("ausloesbar") is True

    async def async_press(self) -> None:
        if not self.available:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="aktion_fehlgeschlagen",
                translation_placeholders={"grund": "fahrzeugaktion"},
            )
        try:
            await self.coordinator.aktion("regel", id=self._regel_id)
        except PanoptesFehler as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="aktion_fehlgeschlagen",
                translation_placeholders={"grund": err.grund},
            ) from err
