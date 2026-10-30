"""Auswahl-Einstellungen aus dem Schema. Gesperrte Optionen (z. B. CH) bietet HA nicht an."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
        for gruppe, feld in felder(coordinator.data.schema, {"auswahl"}):
            if feld["key"] not in bekannt and feld.get("optionen"):
                bekannt.add(feld["key"])
                neu.append(EinstellungsAuswahl(coordinator, gruppe, feld))
        if neu:
            async_add_entities(neu)

    neue_felder()
    entry.async_on_unload(coordinator.async_add_listener(neue_felder))


class EinstellungsAuswahl(PanoptesEntity, SelectEntity):
    def __init__(self, coordinator: PanoptesCoordinator, gruppe: dict, feld: dict) -> None:
        self._key = feld["key"]
        super().__init__(coordinator, f"e_{self._key}")
        self._attr_name = einstellungs_name(gruppe, feld)
        self._attr_entity_registry_enabled_default = not feld.get("erweitert", False)
        self._attr_icon = "mdi:form-select"

    def _feld(self) -> dict:
        """Aktuelles Feld aus dem (ggf. neu geladenen) Schema."""
        for _, feld in felder(self.coordinator.data.schema, {"auswahl"}):
            if feld["key"] == self._key:
                return feld
        return {}

    def _optionen(self) -> dict[str, str]:
        """Anzeigetext → Wert, nur nicht gesperrte Optionen."""
        return {
            str(o.get("titel") or o["wert"]): str(o["wert"])
            for o in self._feld().get("optionen") or []
            if isinstance(o, dict) and "wert" in o and not o.get("gesperrt")
        }

    @property
    def available(self) -> bool:
        return super().available and bool(self._feld())

    @property
    def options(self) -> list[str]:
        return list(self._optionen())

    @property
    def current_option(self) -> str | None:
        wert = wert_von(self.coordinator.data.werte, self._feld() or {"key": self._key})
        for titel, w in self._optionen().items():
            if w == str(wert):
                return titel
        return None

    async def async_select_option(self, option: str) -> None:
        wert = self._optionen().get(option)
        if wert is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="einstellung_abgelehnt",
                translation_placeholders={"grund": "option"},
            )
        try:
            await self.coordinator.einstellung_setzen(self._key, wert)
        except PanoptesFehler as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="einstellung_abgelehnt",
                translation_placeholders={"grund": err.grund},
            ) from err
