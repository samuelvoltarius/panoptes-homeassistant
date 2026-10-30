"""Client ohne HA: Adressen, SSE-Parser, Erlaubnisliste."""

from __future__ import annotations

import aiohttp
import pytest

from custom_components.panoptes.api import (
    PanoptesApi,
    PanoptesGesperrt,
    lies_sse,
    normalisiere_url,
)
from custom_components.panoptes.const import ERLAUBTE_AKTIONEN
from custom_components.panoptes.entity import ist_schreibbar


@pytest.mark.parametrize(
    ("eingabe", "erwartet"),
    [
        ("100.64.0.10", "http://100.64.0.10:8180"),
        ("http://192.168.0.50:8180/", "http://192.168.0.50:8180"),
        (" panoptes.local ", "http://panoptes.local:8180"),
        ("https://auto.example.ts.net/web?x=1", "https://auto.example.ts.net:443"),
        ("[fd7a::1]", "http://[fd7a::1]:8180"),
    ],
)
def test_normalisiere_url(eingabe: str, erwartet: str) -> None:
    assert normalisiere_url(eingabe) == erwartet


@pytest.mark.parametrize("eingabe", ["", "ftp://x", "http://"])
def test_normalisiere_url_fehler(eingabe: str) -> None:
    with pytest.raises(ValueError):
        normalisiere_url(eingabe)


def test_erlaubnisliste_ohne_fahrzeugbefehle() -> None:
    assert ERLAUBTE_AKTIONEN == {"waechter", "ruhezeit", "regel"}


async def test_aktion_ausserhalb_der_liste_sendet_nichts() -> None:
    async with aiohttp.ClientSession() as session:
        api = PanoptesApi(session, "http://127.0.0.1:9", "t")
        with pytest.raises(PanoptesGesperrt):
            await api.aktion("unlock")


class _Strom:
    def __init__(self, zeilen: list[bytes]) -> None:
        self._zeilen = zeilen

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for z in self._zeilen:
            yield z


async def test_sse_parser() -> None:
    strom = _Strom(
        [
            b": ping\n", b"\n",
            b"event: zustand\n", b'data: {"telemetrie":\n', b'data: {"soc": 5}}\n', b"\n",
            b"data: kaputt\n", b"\n",
            b"event: ankunft\r\n", b'data: {"ort": "Zuhause"}\r\n', b"\r\n",
        ]
    )
    ergebnis = [e async for e in lies_sse(strom)]  # type: ignore[arg-type]
    assert ergebnis == [("zustand", {"telemetrie": {"soc": 5}}), ("ankunft", {"ort": "Zuhause"})]


@pytest.mark.parametrize(
    ("feld", "erwartet"),
    [
        ({"key": "planner.band", "typ": "bool", "nurLesendExtern": False}, True),
        ({"key": "planner.band", "typ": "bool"}, False),
        ({"key": "planner.band", "typ": "bool", "nurLesendExtern": True}, False),
        ({"key": "x.y", "typ": "bool", "nurLesendExtern": False, "gefahr": True}, False),
        ({"key": "x.y", "typ": "auswahl", "nurLesendExtern": False, "bestaetigung": "k"}, False),
        ({"key": "x.y", "typ": "geheim", "nurLesendExtern": False}, False),
        ({"key": "x.y", "typ": "bool", "nurLesendExtern": False, "sichtbar": False}, False),
        ({"key": "x.y", "typ": "bool", "nurLesendExtern": False, "neustartNoetig": True}, False),
        ({"key": "deterrent.display", "typ": "bool", "nurLesendExtern": False}, False),
        ({"key": "remote.tailscale.x", "typ": "bool", "nurLesendExtern": False}, False),
        ({"key": "ha.positionTeilen", "typ": "bool", "nurLesendExtern": False}, False),
    ],
)
def test_ist_schreibbar(feld: dict, erwartet: bool) -> None:
    assert ist_schreibbar(feld) is erwartet
