"""Panoptes 0.1.43.21: /api/ha/v1/daten (Ladekosten, Ersparnis als Schätzung, Clips, Meldungen, BYD-Summen),
Gang P/R/N/D, Kilometerstand als Gesamtzähler, unplausibler 50-km-Verbrauch."""

from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.panoptes.const import EVENT_MELDUNG, FELD_VERBRAUCH_50KM

from .conftest import AUTO_ID, JAN_2026, MAI_2026, FakePanoptes, einrichten


def _id(hass: HomeAssistant, plattform: str, schluessel: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(plattform, "panoptes", f"{AUTO_ID}_{schluessel}")


def _zustand(hass: HomeAssistant, schluessel: str, plattform: str = "sensor"):
    entity_id = _id(hass, plattform, schluessel)
    assert entity_id, schluessel
    return hass.states.get(entity_id)


async def _neu_lesen(hass: HomeAssistant, eintrag) -> None:
    c = eintrag.runtime_data
    c._daten_geladen = None  # noqa: SLF001 - 60-s-Takt im Test überspringen
    await c.async_refresh()
    await hass.async_block_till_done()


async def test_ladekosten(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    letzte = _zustand(hass, "d_ladekosten_letzte")
    assert float(letzte.state) == 6.6
    assert letzte.attributes["unit_of_measurement"] == "EUR"
    assert letzte.attributes["device_class"] == SensorDeviceClass.MONETARY
    assert letzte.attributes["kwh_abgerechnet"] == 22.0
    assert "state_class" not in letzte.attributes

    monat = _zustand(hass, "d_ladekosten_monat")
    assert float(monat.state) == 21.5
    assert monat.attributes["state_class"] == SensorStateClass.TOTAL
    assert monat.attributes["vollstaendig"] is False
    assert monat.attributes["ladungen_ohne_preis"] == 1
    sensor = hass.data["entity_components"]["sensor"].get_entity(_id(hass, "sensor", "d_ladekosten_monat"))
    assert sensor.last_reset == datetime.fromtimestamp(MAI_2026 / 1000, tz=timezone.utc)

    # Jahr ohne eine Ladung mit Preis: unbekannt, nie 0 €
    jahr = _zustand(hass, "d_ladekosten_jahr")
    assert jahr.state == STATE_UNKNOWN
    sensor = hass.data["entity_components"]["sensor"].get_entity(_id(hass, "sensor", "d_ladekosten_jahr"))
    assert sensor.last_reset == datetime.fromtimestamp(JAN_2026 / 1000, tz=timezone.utc)

    geladen = _zustand(hass, "d_geladen_monat")
    assert float(geladen.state) == 80.0
    assert geladen.attributes["unit_of_measurement"] == "kWh"
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_ersparnis_immer_als_schaetzung(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    monat = _zustand(hass, "d_ersparnis_monat")
    assert float(monat.state) == 42.3
    assert monat.attributes["schaetzung"] is True
    assert monat.attributes["hinweis"].startswith("Schätzung")
    assert "(estimate)" in monat.attributes["friendly_name"]
    assert monat.attributes["vergleich_kraftstoff"] == "benzin"
    assert monat.attributes["vergleich_preis_richtwert"] is True

    jahr = _zustand(hass, "d_ersparnis_jahr")
    assert jahr.state == STATE_UNKNOWN
    assert jahr.attributes["grund"] == "zu_wenig_km"
    assert jahr.attributes["schaetzung"] is True
    gesamt = _zustand(hass, "d_ersparnis_gesamt")  # Block-Teil null
    assert gesamt.state == STATE_UNKNOWN
    assert gesamt.attributes["schaetzung"] is True

    # Plug-in-Hybrid: verfuegbar=false → keine Zahl, auch wenn eine im Block stünde
    panoptes.daten["ersparnis"]["verfuegbar"] = False
    await _neu_lesen(hass, eintrag)
    assert _zustand(hass, "d_ersparnis_monat").state == STATE_UNKNOWN
    assert _zustand(hass, "d_ersparnis_monat").attributes["schaetzung"] is True
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_clips_nur_zaehler(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    assert _zustand(hass, "d_clips_gesamt").state == "12"
    assert _zustand(hass, "d_clips_heute").state == "2"
    # „behalten“ ist per Voreinstellung deaktiviert
    eintrag_reg = er.async_get(hass).async_get(_id(hass, "sensor", "d_clips_behalten"))
    assert eintrag_reg.disabled_by is not None
    letzter = _zustand(hass, "d_clip_letzter")
    assert letzter.state == "2026-05-17T06:23:20+00:00"
    assert letzter.attributes["grund"] == "stoss"
    assert letzter.attributes["dauer_s"] == 30
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_meldungen(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    m = _zustand(hass, "d_meldungen")
    assert m.state == "1"
    assert m.attributes["meldungen"][0]["schluessel"] == "reifendruck"
    assert m.attributes["meldungen"][0]["text"] == "Reifendruck prüfen"

    # Auto gibt keine Meldungen heraus → unbekannt, nicht „0 = alles ok“
    panoptes.daten["meldungen"] = {"verfuegbar": False, "anzahl": 0, "liste": [], "verlauf": [], "stand": None}
    await _neu_lesen(hass, eintrag)
    m = _zustand(hass, "d_meldungen")
    assert m.state == STATE_UNKNOWN
    assert "meldungen" not in m.attributes
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_meldung_ereignis_kommt_geht(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    alt = {"zeit": 1778000000000, "code": 7, "schluessel": "alt", "text": "Alt", "ereignis": "kommt"}
    panoptes.daten["meldungen"]["verlauf"] = [alt]
    gefeuert: list[dict] = []
    hass.bus.async_listen(EVENT_MELDUNG, lambda e: gefeuert.append(dict(e.data)))
    await einrichten(hass, eintrag)
    # Beim ersten Lesen schon Vorhandenes löst nichts aus
    assert gefeuert == []

    kommt = {"zeit": 1778990000000, "code": 13, "schluessel": "reifendruck", "text": "Reifendruck prüfen", "ereignis": "kommt"}
    geht = {**kommt, "zeit": 1778999999000, "ereignis": "geht"}
    panoptes.daten["meldungen"]["verlauf"] = [geht, kommt, alt]  # neueste zuerst
    await _neu_lesen(hass, eintrag)
    assert [g["ereignis"] for g in gefeuert] == ["kommt", "geht"]
    assert gefeuert[0]["schluessel"] == "reifendruck"
    assert gefeuert[0]["code"] == 13
    zustand = _zustand(hass, "ereignis_meldung", "event")
    assert zustand.attributes["event_type"] == "geht"
    assert zustand.attributes["text"] == "Reifendruck prüfen"

    # Kein zweites Mal für dieselben Einträge
    await _neu_lesen(hass, eintrag)
    assert len(gefeuert) == 2
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_byd_verbrauch(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    monat = _zustand(hass, "d_byd_verbrauch_monat")
    assert float(monat.state) == 15.0
    assert monat.attributes["unit_of_measurement"] == "kWh/100 km"
    assert monat.attributes["fahrten"] == 31
    assert _zustand(hass, "d_byd_verbrauch_jahr").state == STATE_UNKNOWN  # unter 1 km: null

    panoptes.daten["byd_verbrauch"] = {"verfuegbar": False, "monat": None, "jahr": None}
    await _neu_lesen(hass, eintrag)
    assert _zustand(hass, "d_byd_verbrauch_monat").state == STATE_UNKNOWN
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_block_null_heisst_unbekannt(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    panoptes.daten["laden"] = None
    panoptes.daten["clips"] = None
    await _neu_lesen(hass, eintrag)
    assert _zustand(hass, "d_ladekosten_monat").state == STATE_UNKNOWN
    assert _zustand(hass, "d_clips_gesamt").state == STATE_UNKNOWN
    # Die anderen Blöcke bleiben
    assert float(_zustand(hass, "d_ersparnis_monat").state) == 42.3

    # Fehler beim Abruf: Zustand läuft weiter, letzter Stand von /daten bleibt
    panoptes.daten_status = 500
    await _neu_lesen(hass, eintrag)
    assert hass.states.get("sensor.dolphin_surf_ladestand").state == "81.0"
    assert float(_zustand(hass, "d_ersparnis_monat").state) == 42.3
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_panoptes_vor_014321(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    panoptes.daten = None  # 404
    await einrichten(hass, eintrag)
    assert hass.states.get("sensor.dolphin_surf_ladestand").state == "81.0"
    assert _id(hass, "sensor", "d_ladekosten_monat") is None
    abrufe = sum(1 for a in panoptes.aufrufe if a[1] == "/api/ha/v1/daten")
    await eintrag.runtime_data.async_refresh()
    await hass.async_block_till_done()
    # Nach 404 nicht bei jedem Abgleich erneut fragen (erst nach einer Stunde)
    assert sum(1 for a in panoptes.aufrufe if a[1] == "/api/ha/v1/daten") == abrufe
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_daten_nur_get(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    aufrufe = [a for a in panoptes.aufrufe if a[1] == "/api/ha/v1/daten"]
    assert aufrufe
    assert all(a[0] == "GET" for a in aufrufe)
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_gang_kilometer_verbrauch(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    panoptes.zustand["felder"] += [
        {"key": "gear", "titel": "Gang", "typ": "sensor", "icon": "mdi:car-shift-pattern"},
        {"key": "odometer", "titel": "Kilometerstand", "typ": "sensor", "deviceClass": "distance",
         "stateClass": "measurement", "einheit": "km"},
        {"key": FELD_VERBRAUCH_50KM, "titel": "Verbrauch 50 km", "typ": "sensor", "stateClass": "measurement",
         "einheit": "kWh/100 km"},
    ]
    panoptes.zustand["telemetrie"].update({"gear": "D", "odometer": 12345.6, FELD_VERBRAUCH_50KM: 309.6})
    await einrichten(hass, eintrag)

    gang = hass.states.get("sensor.dolphin_surf_gang")
    assert gang.state == "D"
    assert gang.attributes["device_class"] == SensorDeviceClass.ENUM
    assert gang.attributes["options"] == ["P", "R", "N", "D"]
    km = hass.states.get("sensor.dolphin_surf_kilometerstand")
    assert km.state == "12345.6"
    assert km.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING
    assert hass.states.get("sensor.dolphin_surf_verbrauch_50_km").state == STATE_UNKNOWN

    panoptes.zustand["telemetrie"].update({"gear": "UNKNOWN", FELD_VERBRAUCH_50KM: 15.2})
    await eintrag.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dolphin_surf_gang").state == STATE_UNKNOWN
    assert hass.states.get("sensor.dolphin_surf_verbrauch_50_km").state == "15.2"

    panoptes.zustand["telemetrie"]["gear"] = None
    await eintrag.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dolphin_surf_gang").state == STATE_UNKNOWN
    await hass.config_entries.async_unload(eintrag.entry_id)
