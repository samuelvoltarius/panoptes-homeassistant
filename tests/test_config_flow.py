"""Einrichtung: Adresse/mDNS → Prüfzahl → Erlauben → Eintrag."""

from __future__ import annotations

from ipaddress import ip_address

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.panoptes.const import (
    CONF_KOPPLUNG_ID,
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
    OPT_EINSTELLUNGEN,
    OPT_POSITION,
)

from .conftest import AUTO_ID, TOKEN, FakePanoptes, einrichten


async def _direkt(hass: HomeAssistant) -> dict:
    # Panoptes 0.1.32 sendet nicht von selbst: Der Internet-Weg bleibt ausgeblendet, es gibt kein Menü.
    ergebnis = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert ergebnis["type"] is FlowResultType.FORM
    assert ergebnis["step_id"] == "direkt"
    return ergebnis


async def _bis_ende(hass: HomeAssistant, ergebnis: dict) -> dict:
    """Fortschrittsanzeige durchlaufen lassen."""
    for _ in range(10):
        if ergebnis["type"] is not FlowResultType.SHOW_PROGRESS:
            break
        await hass.async_block_till_done()
        ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"])
    return ergebnis


async def test_user_flow_mit_erlauben(hass: HomeAssistant, panoptes: FakePanoptes) -> None:
    ergebnis = await _direkt(hass)
    assert ergebnis["type"] is FlowResultType.FORM
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {CONF_URL: panoptes.url})
    assert ergebnis["type"] is FlowResultType.SHOW_PROGRESS
    assert ergebnis["progress_action"] == "freigabe"
    pruefzahl = ergebnis["description_placeholders"]["pruefzahl"]
    assert len(pruefzahl) == 4 and pruefzahl.isdigit()

    ergebnis = await _bis_ende(hass, ergebnis)
    assert ergebnis["type"] is FlowResultType.CREATE_ENTRY
    assert ergebnis["title"] == "Dolphin Surf"
    assert ergebnis["data"][CONF_TOKEN] == TOKEN
    assert ergebnis["data"][CONF_KOPPLUNG_ID] == "k-1"
    assert ergebnis["result"].unique_id == AUTO_ID
    assert ergebnis["options"] == {OPT_POSITION: False, OPT_EINSTELLUNGEN: True}

    anfrage = next(a for a in panoptes.aufrufe if a[1] == "/auth/kopplung/anfrage")
    assert anfrage[2]["art"] == "homeassistant"
    assert anfrage[2]["pruefzahl"] == pruefzahl
    assert len(anfrage[2]["geraet"]) <= 40
    await hass.config_entries.async_unload(ergebnis["result"].entry_id)


async def test_adresse_ohne_schema_und_port(hass: HomeAssistant, panoptes: FakePanoptes) -> None:
    ergebnis = await _direkt(hass)
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {CONF_URL: "http://"})
    assert ergebnis["errors"] == {CONF_URL: "ungueltige_adresse"}


async def test_abgelehnt(hass: HomeAssistant, panoptes: FakePanoptes) -> None:
    panoptes.anfrage_status = "abgelehnt"
    ergebnis = await _direkt(hass)
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {CONF_URL: panoptes.url})
    ergebnis = await _bis_ende(hass, ergebnis)
    assert ergebnis["type"] is FlowResultType.ABORT
    assert ergebnis["reason"] == "abgelehnt"
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_abgelaufen(hass: HomeAssistant, panoptes: FakePanoptes) -> None:
    panoptes.anfrage_status = "abgelaufen"
    ergebnis = await _direkt(hass)
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {CONF_URL: panoptes.url})
    ergebnis = await _bis_ende(hass, ergebnis)
    assert ergebnis["reason"] == "abgelaufen"


async def test_nicht_erreichbar(hass: HomeAssistant, socket_enabled) -> None:
    ergebnis = await _direkt(hass)
    ergebnis = await hass.config_entries.flow.async_configure(
        ergebnis["flow_id"], {CONF_URL: "http://127.0.0.1:9"}
    )
    assert ergebnis["type"] is FlowResultType.FORM
    assert ergebnis["errors"] == {"base": "nicht_erreichbar"}


async def test_panoptes_ohne_ha_schnittstelle(hass: HomeAssistant, panoptes: FakePanoptes) -> None:
    """Gekoppelt, aber alte Version: Kopplung wieder abgeben, nichts liegen lassen."""
    panoptes.mit_ha_api = False
    ergebnis = await _direkt(hass)
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {CONF_URL: panoptes.url})
    ergebnis = await _bis_ende(hass, ergebnis)
    assert ergebnis["reason"] == "panoptes_zu_alt"
    assert panoptes.widerrufen == [TOKEN]


async def test_schon_eingerichtet_gibt_neue_kopplung_zurueck(
    hass: HomeAssistant, panoptes: FakePanoptes, eintrag
) -> None:
    await einrichten(hass, eintrag)
    ergebnis = await _direkt(hass)
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {CONF_URL: panoptes.url})
    ergebnis = await _bis_ende(hass, ergebnis)
    assert ergebnis["reason"] == "already_configured"
    assert panoptes.widerrufen == [TOKEN]
    await hass.config_entries.async_unload(eintrag.entry_id)


def _mdns(port: int) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address("127.0.0.1"),
        ip_addresses=[ip_address("127.0.0.1")],
        hostname="panoptes-a7f3.local.",
        name="Panoptes a7f3._panoptes._tcp.local.",
        port=port,
        type="_panoptes._tcp.local.",
        properties={"id": AUTO_ID, "api": "1", "ver": "0.1.7"},
    )


async def test_zeroconf(hass: HomeAssistant, panoptes: FakePanoptes) -> None:
    port = int(panoptes.url.rsplit(":", 1)[1])
    ergebnis = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_mdns(port)
    )
    assert ergebnis["type"] is FlowResultType.FORM
    assert ergebnis["step_id"] == "zeroconf_confirm"
    assert ergebnis["description_placeholders"]["url"] == panoptes.url
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {})
    ergebnis = await _bis_ende(hass, ergebnis)
    assert ergebnis["type"] is FlowResultType.CREATE_ENTRY
    assert ergebnis["data"][CONF_URL] == panoptes.url
    await hass.config_entries.async_unload(ergebnis["result"].entry_id)


async def test_zeroconf_bekanntes_auto_zieht_adresse_nach(
    hass: HomeAssistant, panoptes: FakePanoptes, eintrag
) -> None:
    eintrag.add_to_hass(hass)
    hass.config_entries.async_update_entry(eintrag, data={**eintrag.data, CONF_URL: "http://10.0.0.9:8180"})
    port = int(panoptes.url.rsplit(":", 1)[1])
    ergebnis = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_mdns(port)
    )
    assert ergebnis["type"] is FlowResultType.ABORT
    assert ergebnis["reason"] == "already_configured"
    assert eintrag.data[CONF_URL] == panoptes.url
    # Keine neue Kopplungs-Anfrage für ein bekanntes Auto.
    assert not any(a[1] == "/auth/kopplung/anfrage" for a in panoptes.aufrufe)


async def test_reauth(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    eintrag.add_to_hass(hass)
    hass.config_entries.async_update_entry(eintrag, data={**eintrag.data, CONF_TOKEN: "alt"})
    ergebnis = await eintrag.start_reauth_flow(hass)
    assert ergebnis["step_id"] == "reauth_confirm"
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {})
    ergebnis = await _bis_ende(hass, ergebnis)
    assert ergebnis["type"] is FlowResultType.ABORT
    assert ergebnis["reason"] == "reauth_successful"
    assert eintrag.data[CONF_TOKEN] == TOKEN
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_reconfigure(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    eintrag.add_to_hass(hass)
    ergebnis = await eintrag.start_reconfigure_flow(hass)
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {CONF_URL: panoptes.url})
    assert ergebnis["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(eintrag.entry_id)


async def test_optionen(hass: HomeAssistant, panoptes: FakePanoptes, eintrag) -> None:
    await einrichten(hass, eintrag)
    ergebnis = await hass.config_entries.options.async_init(eintrag.entry_id)
    ergebnis = await hass.config_entries.options.async_configure(
        ergebnis["flow_id"], {OPT_POSITION: True, OPT_EINSTELLUNGEN: True}
    )
    assert ergebnis["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.dolphin_surf_position") is not None
    await hass.config_entries.async_unload(eintrag.entry_id)
