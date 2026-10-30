"""Entitäten, Push, Schreiben, Regel A/B, Widerruf beim Entfernen."""

from __future__ import annotations

import asyncio

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.panoptes.api import PanoptesGesperrt
from custom_components.panoptes.const import CONF_TOKEN, DOMAIN

from .conftest import TOKEN, FakePanoptes, einrichten


async def _warte(bedingung, versuche: int = 100) -> None:
    for _ in range(versuche):
        if bedingung():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("Bedingung nicht erfüllt")


async def test_entitaeten(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    assert eintrag.state is ConfigEntryState.LOADED

    assert hass.states.get("sensor.dolphin_surf_ladestand").state == "81.0"
    assert hass.states.get("sensor.dolphin_surf_reichweite").state == "260"
    assert hass.states.get("sensor.dolphin_surf_12_v_batterie").state == "13.1"
    assert hass.states.get("binary_sensor.dolphin_surf_ladt").state == "off"
    assert hass.states.get("binary_sensor.dolphin_surf_geparkt").state == "on"
    assert hass.states.get("sensor.dolphin_surf_guardian").state == "scharf"
    assert hass.states.get("sensor.dolphin_surf_health").state == "ok"
    assert hass.states.get("sensor.dolphin_surf_will_it_make_it").state == "reicht"
    # Position nie als Sensor, device_tracker nur mit Option.
    assert hass.states.get("sensor.dolphin_surf_breite") is None
    assert hass.states.get("device_tracker.dolphin_surf_position") is None
    assert not any(a[1] == "/api/ha/v1/zustand" and a[2] for a in panoptes.aufrufe)
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_einstellungen_nur_erlaubte(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    reg = er.async_get(hass)
    ids = {e.unique_id for e in er.async_entries_for_config_entry(reg, eintrag.entry_id)}
    basis = eintrag.unique_id
    assert f"{basis}_e_planner.band" in ids
    assert f"{basis}_e_planner.reserve" in ids
    assert f"{basis}_e_surveillance.sensitivity" in ids
    assert f"{basis}_e_surveillance.retentionHours" in ids
    # Regel B: Zugang nur lesend; fehlendes nurLesendExtern = vorsichtshalber nicht schreibbar.
    assert f"{basis}_e_remote.tailscale.enabled" not in ids
    assert f"{basis}_e_planner.ohneAngabe" not in ids
    # Regel A: Abschreckung (Licht/Hupe) und bestätigungspflichtige Felder nie aus HA.
    assert f"{basis}_e_deterrent.light" not in ids
    assert f"{basis}_e_deterrent.horn" not in ids
    assert f"{basis}_e_surveillance.triggerMode" not in ids

    auswahl = hass.states.get("select.dolphin_surf_empfindlichkeit")
    assert auswahl.attributes["options"] == ["Niedrig", "Normal"]  # „Hoch“ ist gesperrt
    assert auswahl.state == "Normal"
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_einstellung_schreiben(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.dolphin_surf_reserve", "value": 20}, blocking=True
    )
    assert panoptes.werte["planner.reserve"]["wert"] == 20
    put = [a for a in panoptes.aufrufe if a[0] == "PUT"]
    assert put[-1][2] == {"etag": "w-1", "werte": {"planner.reserve": 20}}
    assert hass.states.get("number.dolphin_surf_reserve").state == "20.0"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.dolphin_surf_f1_band"}, blocking=True
    )
    assert panoptes.werte["planner.band"]["wert"] is False

    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.dolphin_surf_empfindlichkeit", "option": "Niedrig"}, blocking=True
    )
    assert panoptes.werte["surveillance.sensitivity"]["wert"] == "niedrig"
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_einstellung_veralteter_etag_wird_wiederholt(
    hass: HomeAssistant, panoptes: FakePanoptes, eintrag
) -> None:
    await einrichten(hass, eintrag)
    panoptes.etag = 7  # jemand anderes hat inzwischen gespeichert
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.dolphin_surf_behalten", "value": 48}, blocking=True
    )
    assert panoptes.werte["surveillance.retentionHours"]["wert"] == 48
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_einstellung_vom_server_abgelehnt(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    # Server macht das Feld nachträglich nur lesend → HA meldet Fehler, speichert nichts.
    panoptes.schema["gruppen"][0]["felder"][1]["nurLesendExtern"] = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": "number.dolphin_surf_reserve", "value": 30}, blocking=True
        )
    assert panoptes.werte["planner.reserve"]["wert"] == 10
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_waechter_schalter(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    assert hass.states.get("switch.dolphin_surf_guardian").state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.dolphin_surf_guardian"}, blocking=True)
    aktion = [a for a in panoptes.aufrufe if a[1] == "/api/ha/v1/aktion"][-1]
    assert aktion[2] == {"aktion": "waechter", "an": False}
    await hass.async_block_till_done()
    assert hass.states.get("switch.dolphin_surf_guardian").state == "off"
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_regeln_nur_ohne_fahrzeugaktion(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    assert hass.states.get("button.dolphin_surf_rule_garage") is not None
    assert hass.states.get("button.dolphin_surf_rule_vorklimatisieren") is None

    await hass.services.async_call("button", "press", {"entity_id": "button.dolphin_surf_rule_garage"}, blocking=True)
    assert [a for a in panoptes.aufrufe if a[1] == "/api/ha/v1/aktion"][-1][2] == {"aktion": "regel", "id": "r-garage"}

    anzahl = len(panoptes.aufrufe)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "regel_ausfuehren", {"config_entry_id": eintrag.entry_id, "regel_id": "r-klima"}, blocking=True
        )
    assert len(panoptes.aufrufe) == anzahl  # gar nicht erst gesendet
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_dienste(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    await hass.services.async_call(
        DOMAIN, "ruhezeit", {"config_entry_id": eintrag.entry_id, "minuten": 90}, blocking=True
    )
    aktionen = [a[2] for a in panoptes.aufrufe if a[1] == "/api/ha/v1/aktion"]
    assert {"aktion": "ruhezeit", "minuten": 90} in aktionen
    # Panoptes 0.1.32 hat kein Planer-Postfach für Ziele: Der Dienst fehlt, statt immer zu scheitern.
    assert not hass.services.has_service(DOMAIN, "ziel_senden")
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_api_kennt_keine_fahrzeugbefehle(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    api = eintrag.runtime_data.api
    for name in ("klima", "lock", "unlock", "hupe", "licht", "fenster", "find_car"):
        with pytest.raises(PanoptesGesperrt):
            await api.aktion(name)
    assert not any(a[1] == "/api/ha/v1/aktion" for a in panoptes.aufrufe)
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_push(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    await _warte(lambda: panoptes.sse_offen == 1)
    panoptes.sende("zustand", {"telemetrie": {"soc": 79.5}, "position": {"lat": 1, "lon": 2}})
    await _warte(lambda: hass.states.get("sensor.dolphin_surf_ladestand").state == "79.5")
    await _warte(lambda: hass.states.get("binary_sensor.dolphin_surf_live_connection").state == "on")
    # Übrige Telemetrie bleibt erhalten; Position ohne Option verworfen.
    assert hass.states.get("sensor.dolphin_surf_reichweite").state == "260"
    assert eintrag.runtime_data.data.zustand.get("position") is None

    ankuenfte = []
    hass.bus.async_listen("panoptes_ankunft", lambda e: ankuenfte.append(e.data))
    panoptes.sende("ankunft", {"ort": "Zuhause", "zeit": 123})
    await _warte(lambda: bool(ankuenfte))
    assert ankuenfte[0]["ort"] == "Zuhause"
    await _warte(lambda: hass.states.get("event.dolphin_surf_arrival_departure").attributes.get("event_type") == "ankunft")

    panoptes.sende("waechter", {"zustand": "startet"})
    await _warte(lambda: hass.states.get("sensor.dolphin_surf_guardian").state == "startet")

    panoptes.sende("ereignis", {"art": "ereignis", "was": "person", "zeit": 5})
    await _warte(lambda: hass.states.get("event.dolphin_surf_guardian_event").attributes.get("event_type") == "person")
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_zeitstempel_als_text(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    """Panoptes liefert Zeitstempel als Text; ohne Umwandlung legt HA den Sensor gar nicht an."""
    feld = {"titel": "Zeit", "typ": "sensor", "deviceClass": "timestamp", "stateClass": None,
            "einheit": None, "icon": None, "erweitert": False}
    panoptes.zustand["felder"] += [{**feld, "key": "lock_changed_at"}, {**feld, "key": "kaputt_at", "titel": "Kaputt"}]
    panoptes.zustand["telemetrie"]["lock_changed_at"] = "2026-10-30T17:00:27+01:00"
    panoptes.zustand["telemetrie"]["kaputt_at"] = "keine Zeit"
    await einrichten(hass, eintrag)
    assert hass.states.get("sensor.dolphin_surf_zeit").state == "2026-10-30T16:00:27+00:00"
    assert hass.states.get("sensor.dolphin_surf_kaputt").state == "unknown"
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_ereignis_wie_panoptes_0132(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    """Das Auto schickt die Art in "art" und ab 0.1.30 die ereignisId; sie muss in HA ankommen."""
    await einrichten(hass, eintrag)
    await _warte(lambda: panoptes.sse_offen == 1)
    ereignisse = []
    hass.bus.async_listen("panoptes_waechter_ereignis", lambda e: ereignisse.append(e.data))
    kennung = "a" * 32
    panoptes.sende("ereignis", {"art": "bewegung", "zeit": 7, "ereignisId": kennung})
    await _warte(lambda: bool(ereignisse))
    assert ereignisse[0]["art"] == "bewegung"
    assert ereignisse[0]["ereignis_id"] == kennung
    await _warte(lambda: hass.states.get("event.dolphin_surf_guardian_event").attributes.get("ereignis_id") == kennung)
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_sse_loescht_die_position_nicht(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    """Der erste SSE-Zustand trägt position:null; die abgefragte Position muss bleiben."""
    eintrag.add_to_hass(hass)
    hass.config_entries.async_update_entry(eintrag, options={**eintrag.options, "position": True})
    assert await hass.config_entries.async_setup(eintrag.entry_id)
    await hass.async_block_till_done()
    await _warte(lambda: panoptes.sse_offen == 1)
    assert eintrag.runtime_data.data.zustand["position"]["lat"] == 48.2
    panoptes.sende("zustand", {"telemetrie": {"soc": 78.5}, "position": None})
    await _warte(lambda: hass.states.get("sensor.dolphin_surf_ladestand").state == "78.5")
    assert eintrag.runtime_data.data.zustand["position"]["lat"] == 48.2
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_ohne_push_polling(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    panoptes.mit_sse = False
    await einrichten(hass, eintrag)
    c = eintrag.runtime_data
    await _warte(lambda: c.push_unterstuetzt is False)
    assert c.update_interval.total_seconds() <= 60
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_position_mit_option(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    eintrag.add_to_hass(hass)
    hass.config_entries.async_update_entry(eintrag, options={"position": True, "einstellungen": False})
    assert await hass.config_entries.async_setup(eintrag.entry_id)
    await hass.async_block_till_done()
    zustand = hass.states.get("device_tracker.dolphin_surf_position")
    assert zustand.attributes["latitude"] == 48.2
    assert any(a[1] == "/api/ha/v1/zustand" for a in panoptes.aufrufe)
    # Einstellungen aus → keine Einstellungs-Entitäten, kein Abruf.
    assert hass.states.get("number.dolphin_surf_reserve") is None
    assert not any(a[1] == "/api/einstellungen" for a in panoptes.aufrufe)
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_widerruf_in_panoptes_startet_reauth(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    panoptes.gueltige_tokens.clear()
    await eintrag.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_sitzung_erneuern(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    await eintrag.runtime_data.sitzung_erneuern()
    assert eintrag.data[CONF_TOKEN] == "jwt-ha-2"
    assert eintrag.state is ConfigEntryState.LOADED  # Token-Wechsel lädt nicht neu
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_entfernen_widerruft_kopplung(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    assert await hass.config_entries.async_remove(eintrag.entry_id)
    await hass.async_block_till_done()
    assert panoptes.widerrufen == [TOKEN]
    assert TOKEN not in panoptes.gueltige_tokens


async def test_entfernen_offline_bricht_nicht_ab(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    hass.config_entries.async_update_entry(eintrag, data={**eintrag.data, "url": "http://127.0.0.1:9"})
    await hass.async_block_till_done()
    assert await hass.config_entries.async_remove(eintrag.entry_id)
    assert not hass.config_entries.async_entries(DOMAIN)
