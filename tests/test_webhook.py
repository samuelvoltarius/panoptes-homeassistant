"""Weg (e) Webhook: Einrichtung per Text/Telegram, laufender Betrieb, Aufträge, Grabstein (Vertrag Teil B)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from custom_components.panoptes.const import (
    CONF_ADRESSEN,
    CONF_GEHEIMNIS,
    CONF_MODUS,
    CONF_WEBHOOK_ID,
    DOMAIN,
    MODUS_WEBHOOK,
)
from custom_components.panoptes.webhook import lies_einrichtungstext

from .conftest import REGELN, SCHEMA, ZUSTAND

@pytest.fixture(autouse=True)
def webhook_verfuegbar():
    with patch("custom_components.panoptes.config_flow.WEBHOOK_VERFUEGBAR", True):
        yield


GERAET = {"haId": "h-9a1", "name": "Dolphin Surf", "modell": "Dolphin Surf", "appVersion": "0.1.8", "dilink": 3}


@pytest.fixture
async def ha(hass: HomeAssistant, hass_client_no_auth):
    await hass.config.async_update(external_url="https://ha.example.com", internal_url="http://192.168.1.5:8123")
    assert await async_setup_component(hass, "webhook", {})
    return await hass_client_no_auth()


async def _starte(hass: HomeAssistant) -> dict:
    ergebnis = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {"next_step_id": "internet"})


async def _einrichten(hass: HomeAssistant, client) -> tuple[Any, dict]:
    ergebnis = await _starte(hass)
    text = lies_einrichtungstext(ergebnis["description_placeholders"]["text"])
    antwort = await client.post(f"/api/webhook/{text['w']}", json={"v": 1, "art": "hallo", "k": text["k"], "geraet": GERAET})
    zugang = await antwort.json()
    await hass.async_block_till_done()
    eintrag = hass.config_entries.async_entries(DOMAIN)[0]
    return eintrag, zugang


async def _sende(client, zugang: dict, body: dict, geheimnis: str | None = None):
    return await client.post(
        f"/api/webhook/{zugang['webhook']}",
        json={"v": 1, **body},
        headers={"Authorization": f"Bearer {geheimnis or zugang['geheimnis']}"},
    )


async def test_einrichtung_ablauf(hass: HomeAssistant, ha) -> None:
    ergebnis = await _starte(hass)
    assert ergebnis["type"] is FlowResultType.SHOW_PROGRESS
    assert ergebnis["progress_action"] == "auto"
    platz = ergebnis["description_placeholders"]
    assert platz["link"].startswith("https://t.me/share/url?url=PANOPTES-HA1%20")
    text = lies_einrichtungstext(platz["text"])
    assert text["u"] == ["https://ha.example.com", "http://192.168.1.5:8123"]
    assert text["p"] == platz["pruefzahl"] and len(text["p"]) == 4
    assert len(platz["text"]) < 450

    # Falscher Einmal-Schlüssel → nichts passiert.
    antwort = await ha.post(f"/api/webhook/{text['w']}", json={"v": 1, "art": "hallo", "k": "falsch", "geraet": GERAET})
    assert antwort.status == 403
    assert not hass.config_entries.async_entries(DOMAIN)

    antwort = await ha.post(f"/api/webhook/{text['w']}", json={"v": 1, "art": "hallo", "k": text["k"], "geraet": GERAET})
    assert antwort.status == 200
    zugang = await antwort.json()
    assert zugang["ok"] is True and zugang["webhook"] != text["w"] and len(zugang["geheimnis"]) >= 32

    # w0 ist sofort tot (Telegram kannte ihn): HA antwortet für unbekannte Webhooks leer.
    antwort = await ha.post(f"/api/webhook/{text['w']}", json={"v": 1, "art": "hallo", "k": text["k"], "geraet": GERAET})
    assert antwort.status == 200 and await antwort.text() == ""

    await hass.async_block_till_done()  # Flow läuft ohne offenen Browser weiter
    eintraege = hass.config_entries.async_entries(DOMAIN)
    assert len(eintraege) == 1
    eintrag = eintraege[0]
    assert eintrag.unique_id == "h-9a1"
    assert eintrag.data[CONF_MODUS] == MODUS_WEBHOOK
    assert eintrag.data[CONF_WEBHOOK_ID] == zugang["webhook"]
    assert eintrag.data[CONF_GEHEIMNIS] == zugang["geheimnis"]
    assert eintrag.data[CONF_ADRESSEN] == text["u"]
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_ohne_externe_adresse_warnt(hass: HomeAssistant, hass_client_no_auth) -> None:
    await hass.config.async_update(internal_url="http://192.168.1.5:8123")
    assert await async_setup_component(hass, "webhook", {})
    ergebnis = await _starte(hass)
    assert ergebnis["type"] is FlowResultType.FORM
    assert ergebnis["step_id"] == "nur_lokal"
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {})
    assert ergebnis["type"] is FlowResultType.SHOW_PROGRESS
    assert lies_einrichtungstext(ergebnis["description_placeholders"]["text"])["u"] == ["http://192.168.1.5:8123"]
    hass.config_entries.flow.async_abort(ergebnis["flow_id"])


async def test_abgebrochene_einrichtung_entfernt_webhook(hass: HomeAssistant, ha) -> None:
    ergebnis = await _starte(hass)
    text = lies_einrichtungstext(ergebnis["description_placeholders"]["text"])
    hass.config_entries.flow.async_abort(ergebnis["flow_id"])
    await hass.async_block_till_done()
    antwort = await ha.post(f"/api/webhook/{text['w']}", json={"v": 1, "art": "hallo", "k": text["k"], "geraet": GERAET})
    assert await antwort.text() == ""  # nicht mehr registriert
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_betrieb(hass: HomeAssistant, ha) -> None:
    eintrag, zugang = await _einrichten(hass, ha)

    # Falsches Geheimnis → 401, keine Daten.
    antwort = await _sende(ha, zugang, {"art": "zustand", "zustand": {"telemetrie": {"soc": 1}}}, geheimnis="falsch")
    assert antwort.status == 401

    zustand = {k: v for k, v in ZUSTAND.items() if k != "geraet"} | {"geraet": GERAET, "position": {"lat": 1, "lon": 2}}
    antwort = await _sende(ha, zugang, {"art": "zustand", "seq": 1, "zustand": zustand, "etags": {"schema": "s-1", "werte": "w-1", "regeln": "r-1"}})
    daten = await antwort.json()
    assert daten["ok"] is True and daten["sendeTaktS"] == 60
    assert daten["wuensche"] == {"schema": True, "werte": True, "regeln": True}
    # Beim Einrichten: MQTT-Discovery abschalten (doppelte Entitäten vermeiden).
    assert [a for a in daten["auftraege"] if a["key"] == "ha.mqttDiscoveryAus"][0]["wert"] is True
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dolphin_surf_ladestand").state == "81.0"
    assert hass.states.get("binary_sensor.dolphin_surf_live_connection").state == "on"
    assert eintrag.runtime_data.data.zustand.get("position") is None  # Option aus

    werte = {"etag": "w-1", "werte": {"planner.band": {"wert": True}, "planner.reserve": {"wert": 10}}}
    antwort = await _sende(ha, zugang, {
        "art": "zustand", "schema": SCHEMA, "werte": werte, "regeln": REGELN,
        "etags": {"schema": "s-1", "werte": "w-1", "regeln": "r-1"},
        "quittungen": [{"id": a["id"], "ok": True} for a in daten["auftraege"]],
    })
    daten = await antwort.json()
    assert daten["wuensche"] == {"schema": False, "werte": False, "regeln": False}
    assert daten["auftraege"] == []
    await hass.async_block_till_done()
    assert hass.states.get("number.dolphin_surf_reserve").state == "10.0"
    assert hass.states.get("button.dolphin_surf_rule_garage") is not None
    assert hass.states.get("button.dolphin_surf_rule_vorklimatisieren") is None
    assert hass.states.get("switch.dolphin_surf_tailscale") is None

    # HA → Panoptes: Einstellung + Wächter werden Aufträge.
    await hass.services.async_call("number", "set_value", {"entity_id": "number.dolphin_surf_reserve", "value": 25}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": "number.dolphin_surf_reserve", "value": 30}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.dolphin_surf_guardian"}, blocking=True)
    assert hass.states.get("number.dolphin_surf_reserve").state == "30.0"  # vorläufig angezeigt
    assert hass.states.get("switch.dolphin_surf_guardian").state == "off"

    antwort = await _sende(ha, zugang, {"art": "zustand"})
    auftraege = (await antwort.json())["auftraege"]
    assert [(a["typ"], a.get("key"), a.get("wert"), a.get("an")) for a in auftraege] == [
        ("einstellung", "planner.reserve", 30, None),  # 25 wurde durch 30 ersetzt
        ("waechter", None, None, False),
    ]
    # Bis zur Quittung kommt derselbe Auftrag wieder (das Auto führt jede id nur einmal aus).
    antwort = await _sende(ha, zugang, {"art": "zustand"})
    assert [a["id"] for a in (await antwort.json())["auftraege"]] == [a["id"] for a in auftraege]

    abgelehnt: list[dict] = []
    hass.bus.async_listen("panoptes_auftrag_abgelehnt", lambda e: abgelehnt.append(e.data))
    antwort = await _sende(ha, zugang, {"art": "zustand", "quittungen": [
        {"id": auftraege[0]["id"], "ok": False, "grund": "max 50"},
        {"id": auftraege[1]["id"], "ok": True},
    ]})
    assert (await antwort.json())["auftraege"] == []
    await hass.async_block_till_done()
    assert abgelehnt[0]["grund"] == "max 50"
    assert hass.states.get("number.dolphin_surf_reserve").state == "10.0"  # zurückgenommen

    # Ereignisse
    ankuenfte: list[dict] = []
    hass.bus.async_listen("panoptes_ankunft", lambda e: ankuenfte.append(e.data))
    await _sende(ha, zugang, {"art": "zustand", "ereignisse": [
        {"art": "ankunft", "ort": "Zuhause", "zeit": 5},
        {"art": "ereignis", "was": "person", "zeit": 6},
    ]})
    await hass.async_block_till_done()
    assert ankuenfte[0]["ort"] == "Zuhause"
    assert hass.states.get("event.dolphin_surf_guardian_event").attributes["event_type"] == "person"

    # Regel A auch im Webhook-Weg: Regel mit Fahrzeugaktion nie als Auftrag.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "regel_ausfuehren", {"config_entry_id": eintrag.entry_id, "regel_id": "r-klima"}, blocking=True)
    assert eintrag.runtime_data.auftraege == {}
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_lange_abfrage(hass: HomeAssistant, ha) -> None:
    eintrag, zugang = await _einrichten(hass, ha)
    eintrag.runtime_data.auftraege.clear()

    warte = asyncio.create_task(_sende(ha, zugang, {"art": "warte", "maxS": 20}))
    await asyncio.sleep(0.1)
    assert not warte.done()
    await hass.services.async_call(DOMAIN, "ruhezeit", {"config_entry_id": eintrag.entry_id, "minuten": 60}, blocking=True)
    antwort = await asyncio.wait_for(warte, 5)
    auftraege = (await antwort.json())["auftraege"]
    assert auftraege[0]["typ"] == "ruhezeit" and auftraege[0]["minuten"] == 60

    # Ohne Auftrag: Antwort nach maxS mit leerer Liste.
    eintrag.runtime_data.auftraege.clear()
    antwort = await _sende(ha, zugang, {"art": "warte", "maxS": 0.1})
    assert (await antwort.json())["auftraege"] == []
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_tschuess_startet_neu_verbinden(hass: HomeAssistant, ha) -> None:
    eintrag, zugang = await _einrichten(hass, ha)
    antwort = await _sende(ha, zugang, {"art": "tschuess"})
    assert antwort.status == 200
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    reauth = [f for f in flows if f["context"]["source"] == "reauth"]
    assert reauth and reauth[0]["step_id"] == "reauth_confirm"  # erst fragen, kein Webhook auf Vorrat
    ergebnis = await hass.config_entries.flow.async_configure(reauth[0]["flow_id"], {})
    assert ergebnis["type"] is FlowResultType.SHOW_PROGRESS
    text = lies_einrichtungstext(ergebnis["description_placeholders"]["text"])
    antwort = await ha.post(f"/api/webhook/{text['w']}", json={"v": 1, "art": "hallo", "k": text["k"], "geraet": GERAET})
    neu = await antwort.json()
    await hass.async_block_till_done()
    assert eintrag.data[CONF_GEHEIMNIS] == neu["geheimnis"]
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_entfernen_hinterlaesst_grabstein(hass: HomeAssistant, ha) -> None:
    eintrag, zugang = await _einrichten(hass, ha)
    assert await hass.config_entries.async_remove(eintrag.entry_id)
    await hass.async_block_till_done()
    antwort = await _sende(ha, zugang, {"art": "zustand"})
    daten = await antwort.json()
    assert daten["ok"] is False and daten["grund"] == "widerrufen"
    assert daten["auftraege"] == [{"id": "a-ende", "typ": "einstellung", "key": "ha.mqttDiscoveryAus", "wert": False}]


async def test_gleiches_auto_neu_verbunden_aktualisiert(hass: HomeAssistant, ha) -> None:
    eintrag, zugang = await _einrichten(hass, ha)
    ergebnis = await _starte(hass)
    text = lies_einrichtungstext(ergebnis["description_placeholders"]["text"])
    antwort = await ha.post(f"/api/webhook/{text['w']}", json={"v": 1, "art": "hallo", "k": text["k"], "geraet": GERAET})
    neu = await antwort.json()
    await hass.async_block_till_done()
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert eintrag.data[CONF_GEHEIMNIS] == neu["geheimnis"]
    await hass.config_entries.async_unload(eintrag.entry_id)
