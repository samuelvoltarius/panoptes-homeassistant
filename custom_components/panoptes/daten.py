"""Sensoren aus ``GET /api/ha/v1/daten`` (ab Panoptes 0.1.43.21, nur Modus „Direkt“).

Ladekosten, Ersparnis gegenüber Benzin/Diesel (immer als Schätzung gekennzeichnet), Dashcam-Clips (nur Zähler und
Zeitpunkte, nie Inhalt), Meldungen des Autos und Verbrauchssummen aus dem BYD-Fahrtenbuch. Alles nur lesend.
Fehlt ein Block (``null``), ist die Entität unbekannt, nicht dauerhaft „nicht verfügbar“. Kein Wert wird erfunden.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.util import dt as dt_util

from .coordinator import PanoptesCoordinator
from .entity import PanoptesEntity

Block = dict[str, Any]


def zeit_aus_ms(ms: Any) -> datetime | None:
    """Panoptes-Zeit (ms seit 1970, UTC) → Zeit mit Zone; alles andere → None."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return dt_util.utc_from_timestamp(ms / 1000)


def teil(block: Any, *pfad: str) -> Block | None:
    for name in pfad:
        if not isinstance(block, dict):
            return None
        block = block.get(name)
    return block if isinstance(block, dict) else None


def zahl(wert: Any) -> float | int | None:
    return wert if isinstance(wert, (int, float)) and not isinstance(wert, bool) else None


def ohne_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# ---------------------------------------------------------------------------------------------- Attribute


def _ladekosten_attr(z: Block) -> dict[str, Any]:
    return ohne_none(
        {
            "ladungen": z.get("ladungen"),
            "kwh": z.get("kwh"),
            "kwh_abgerechnet": z.get("kwh_abgerechnet"),
            "preis_kwh": z.get("preis_kwh"),
            "ladungen_ohne_preis": z.get("ladungen_ohne_preis"),
            "vollstaendig": z.get("vollstaendig"),
            "von": zeit_aus_ms(z.get("von")),
            "bis": zeit_aus_ms(z.get("bis")),
        }
    )


def _letzte_attr(z: Block) -> dict[str, Any]:
    return ohne_none(
        {
            "start": zeit_aus_ms(z.get("start")),
            "ende": zeit_aus_ms(z.get("ende")),
            "dc": z.get("dc"),
            "kwh": z.get("kwh"),
            "kwh_abgerechnet": z.get("kwh_abgerechnet"),
            "preis_kwh": z.get("preis_kwh"),
        }
    )


def ersparnis_attr(block: Block, z: Block | None) -> dict[str, Any]:
    """Immer als Schätzung kennzeichnen, mit dem Hinweissatz aus Panoptes."""
    auto = teil(block, "vergleichsauto") or {}
    attr: dict[str, Any] = ohne_none(
        {
            "hinweis": block.get("hinweis"),
            "vergleich_kraftstoff": auto.get("kraftstoff"),
            "vergleich_verbrauch_l_100km": auto.get("verbrauch_l_100km"),
            "vergleich_preis_l": auto.get("preis_l"),
            "vergleich_preis_richtwert": auto.get("preis_richtwert"),
        }
    )
    if z:
        attr.update(
            ohne_none(
                {
                    "km": z.get("km"),
                    "strom_kosten": z.get("strom_kosten"),
                    "sprit_kosten": z.get("sprit_kosten"),
                    "vollstaendig": z.get("vollstaendig"),
                    "grund": z.get("grund"),
                }
            )
        )
    return {"schaetzung": True, **attr}


def ersparnis_wert(block: Block, z: Block | None) -> Any:
    if block.get("verfuegbar") is False or not z:
        return None  # Plug-in-Hybrid oder ohne Daten: keine erfundene Zahl
    return zahl(z.get("ersparnis"))


def _byd_wert(block: Block, z: Block | None) -> Any:
    if block.get("verfuegbar") is False or not z:
        return None
    return zahl(z.get("kwh_je_100km"))


def meldungen_attr(block: Block) -> dict[str, Any]:
    liste = [
        ohne_none(
            {
                "seit": zeit_aus_ms(m.get("zeit")),
                "code": m.get("code"),
                "schluessel": m.get("schluessel"),
                "text": m.get("text"),
            }
        )
        for m in block.get("liste") or []
        if isinstance(m, dict)
    ]
    return ohne_none({"meldungen": liste, "stand": zeit_aus_ms(block.get("stand"))})


# ---------------------------------------------------------------------------------------------- Beschreibungen


@dataclass(frozen=True, kw_only=True)
class DatenBeschreibung:
    """Ein Sensor aus einem Block von /daten."""

    schluessel: str
    block: str
    wert: Callable[[Block], Any]
    attribute: Callable[[Block], dict[str, Any]] = lambda _b: {}
    icon: str | None = None
    geld: bool = False
    einheit: str | None = None
    klasse: SensorDeviceClass | None = None
    zustand: SensorStateClass | None = None
    # Zeitraum mit Beginn ``von`` (ms) → last_reset (state_class total, Monat/Jahr beginnen neu).
    zeitraum: str | None = None
    aktiv: bool = True


def _ladekosten(name: str) -> DatenBeschreibung:
    return DatenBeschreibung(
        schluessel=f"ladekosten_{name}",
        block="laden",
        # kosten null = keine Ladung mit Preis → unbekannt, nie 0 €
        wert=lambda b: zahl((teil(b, name) or {}).get("kosten")),
        attribute=lambda b: _ladekosten_attr(teil(b, name) or {}),
        icon="mdi:cash-multiple",
        geld=True,
        zustand=SensorStateClass.TOTAL,
        zeitraum=name,
    )


def _geladen(name: str) -> DatenBeschreibung:
    return DatenBeschreibung(
        schluessel=f"geladen_{name}",
        block="laden",
        wert=lambda b: zahl((teil(b, name) or {}).get("kwh")),
        attribute=lambda b: ohne_none({"ladungen": (teil(b, name) or {}).get("ladungen")}),
        icon="mdi:battery-charging",
        einheit=UnitOfEnergy.KILO_WATT_HOUR,
        klasse=SensorDeviceClass.ENERGY,
        zustand=SensorStateClass.TOTAL,
        zeitraum=name,
    )


def _ersparnis(name: str) -> DatenBeschreibung:
    return DatenBeschreibung(
        schluessel=f"ersparnis_{name}",
        block="ersparnis",
        wert=lambda b: ersparnis_wert(b, teil(b, name)),
        attribute=lambda b: ersparnis_attr(b, teil(b, name)),
        icon="mdi:piggy-bank-outline",
        geld=True,
    )


def _byd(name: str) -> DatenBeschreibung:
    return DatenBeschreibung(
        schluessel=f"byd_verbrauch_{name}",
        block="byd_verbrauch",
        wert=lambda b: _byd_wert(b, teil(b, name)),
        attribute=lambda b: (
            ohne_none({k: (teil(b, name) or {}).get(k) for k in ("fahrten", "km", "kwh")})
            if b.get("verfuegbar") is not False
            else {}
        ),
        icon="mdi:lightning-bolt-outline",
        einheit="kWh/100 km",
        zustand=SensorStateClass.MEASUREMENT,
    )


def _clips(schluessel: str, feld: str, icon: str, aktiv: bool = True) -> DatenBeschreibung:
    return DatenBeschreibung(
        schluessel=schluessel,
        block="clips",
        wert=lambda b: zahl(b.get(feld)),
        icon=icon,
        zustand=SensorStateClass.MEASUREMENT,
        aktiv=aktiv,
    )


DATEN_SENSOREN: tuple[DatenBeschreibung, ...] = (
    DatenBeschreibung(
        schluessel="ladekosten_letzte",
        block="laden",
        wert=lambda b: zahl((teil(b, "letzte") or {}).get("kosten")),
        attribute=lambda b: _letzte_attr(teil(b, "letzte") or {}),
        icon="mdi:ev-station",
        geld=True,
    ),
    _ladekosten("monat"),
    _ladekosten("jahr"),
    _geladen("monat"),
    _geladen("jahr"),
    _ersparnis("monat"),
    _ersparnis("jahr"),
    _ersparnis("gesamt"),
    _byd("monat"),
    _byd("jahr"),
    _clips("clips_gesamt", "anzahl", "mdi:filmstrip-box-multiple"),
    _clips("clips_heute", "heute", "mdi:filmstrip"),
    _clips("clips_behalten", "behalten", "mdi:filmstrip-box", aktiv=False),
    DatenBeschreibung(
        schluessel="clip_letzter",
        block="clips",
        wert=lambda b: zeit_aus_ms((teil(b, "letzter") or {}).get("zeit")),
        attribute=lambda b: ohne_none({k: (teil(b, "letzter") or {}).get(k) for k in ("grund", "dauer_s", "behalten")}),
        icon="mdi:cctv",
        klasse=SensorDeviceClass.TIMESTAMP,
    ),
    DatenBeschreibung(
        schluessel="meldungen",
        block="meldungen",
        # verfuegbar=false: Das Auto gibt keine Meldungen heraus → unbekannt, nie „0 = alles ok“.
        wert=lambda b: zahl(b.get("anzahl")) if b.get("verfuegbar") is True else None,
        attribute=lambda b: meldungen_attr(b) if b.get("verfuegbar") is True else {},
        icon="mdi:car-info",
        zustand=SensorStateClass.MEASUREMENT,
    ),
)


class DatenSensor(PanoptesEntity, SensorEntity):
    """Ein Wert aus /api/ha/v1/daten. Block null → unbekannt."""

    def __init__(self, coordinator: PanoptesCoordinator, beschreibung: DatenBeschreibung) -> None:
        super().__init__(coordinator, f"d_{beschreibung.schluessel}")
        self._b = beschreibung
        self._attr_translation_key = beschreibung.schluessel
        self._attr_icon = beschreibung.icon
        self._attr_device_class = SensorDeviceClass.MONETARY if beschreibung.geld else beschreibung.klasse
        self._attr_state_class = beschreibung.zustand
        self._attr_entity_registry_enabled_default = beschreibung.aktiv
        if beschreibung.geld:
            self._attr_suggested_display_precision = 2

    def _block(self) -> Block | None:
        return self.coordinator.data.block(self._b.block)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if not self._b.geld:
            return self._b.einheit
        block = self._block() or {}
        z = teil(block, "letzte") if self._b.schluessel == "ladekosten_letzte" else None
        auto = teil(block, "vergleichsauto") or {}
        return (z or {}).get("waehrung") or block.get("waehrung") or auto.get("waehrung") or self.hass.config.currency

    @property
    def native_value(self) -> Any:
        block = self._block()
        return None if block is None else self._b.wert(block)

    @property
    def last_reset(self) -> datetime | None:
        block = self._block()
        if self._b.zeitraum is None or block is None:
            return None
        return zeit_aus_ms((teil(block, self._b.zeitraum) or {}).get("von"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        block = self._block()
        if block is None:
            # Auch ohne Daten bleibt sichtbar, dass die Ersparnis eine Schätzung ist.
            return {"schaetzung": True} if self._b.block == "ersparnis" else {}
        return self._b.attribute(block)
