"""Konstanten der Panoptes-Integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "panoptes"
HERSTELLER: Final = "Panoptes"

CONF_URL: Final = "url"
CONF_TOKEN: Final = "token"
CONF_KOPPLUNG_ID: Final = "kopplung_id"
CONF_AUTO_ID: Final = "auto_id"

CONF_MODUS: Final = "modus"
MODUS_DIREKT: Final = "direkt"
MODUS_WEBHOOK: Final = "webhook"
CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_GEHEIMNIS: Final = "geheimnis"
CONF_ADRESSEN: Final = "adressen"

# Weg (e) Webhook – Vertrag Teil B. Panoptes 0.1.32 sendet noch nicht von selbst an HA: Ohne Gegenstelle bleibt der
# Weg bei neuen Einrichtungen ausgeblendet. Einschalten, sobald eine Panoptes-Version ihn kann.
WEBHOOK_VERFUEGBAR: Final = False
EINRICHTUNG_PRAEFIX: Final = "PANOPTES-HA1"
EINRICHTUNG_GUELTIG: Final = timedelta(minutes=10)
SENDETAKT_S: Final = 60
WARTEN_MAX_S: Final = 25
MAX_AUFTRAEGE: Final = 20
GRABSTEIN_DAUER: Final = timedelta(days=90)
GRABSTEIN_SPEICHER: Final = "panoptes.grabsteine"
# Ohne Lebenszeichen so lange gilt die Verbindung als getrennt (geparkt sendet das Auto alle SENDETAKT_S).
STILLE_BIS_GETRENNT: Final = timedelta(minutes=5)

MQTT_DISCOVERY_AUS: Final = "ha.mqttDiscoveryAus"

OPT_POSITION: Final = "position"
OPT_EINSTELLUNGEN: Final = "einstellungen"

STANDARD_PORT: Final = 8180

# Polling nur als Rückfall, wenn kein Push (SSE) läuft. Auftrag: höchstens 60 s.
POLL_OHNE_PUSH: Final = timedelta(seconds=30)
# Bei laufendem Push nur noch ein seltener Abgleich.
POLL_MIT_PUSH: Final = timedelta(minutes=5)
SITZUNG_ERNEUERN: Final = timedelta(hours=24)
REGELN_NEU_LADEN: Final = timedelta(minutes=15)

KOPPLUNG_ART: Final = "homeassistant"
KOPPLUNG_ABFRAGE_STANDARD_S: Final = 2.0
KOPPLUNG_HOECHSTENS_S: Final = 150.0  # Server: 2 min gültig + Puffer

# Regel A: HA darf nur diese Panoptes-eigenen Aktionen auslösen, nie Fahrzeugbefehle.
ERLAUBTE_AKTIONEN: Final = frozenset({"waechter", "ruhezeit", "regel"})

# Regel A/B, zweite Sperre auf HA-Seite (der Server prüft selbst): Einstellungen mit diesen
# Präfixen legt HA nie als schreibbare Entität an, egal was das Schema sagt.
GESPERRTE_EINSTELLUNGS_PRAEFIXE: Final = (
    "deterrent.",
    "remote.",
    "auth.",
    "adb.",
    "kopplungen.",
    "ha.",
    "telegram.botToken",
    "planner.memberToken",
    "planner.autoId",
    "update.",
)

WAECHTER_ZUSTAENDE: Final = ["scharf", "aus", "startet", "pausiert", "fehler"]
GESUNDHEIT_ZUSTAENDE: Final = ["ok", "warnung", "fehler"]
F1_STUFEN: Final = ["reicht", "knapp", "reicht_nicht", "keine_route", "unbekannt"]
FAHRT_EREIGNISSE: Final = ["ankunft", "abfahrt"]
WAECHTER_EREIGNISSE: Final = ["bewegung", "person", "erschuetterung"]

EVENT_ANKUNFT: Final = f"{DOMAIN}_ankunft"
EVENT_ABFAHRT: Final = f"{DOMAIN}_abfahrt"
EVENT_WAECHTER: Final = f"{DOMAIN}_waechter_ereignis"

SIGNAL_EREIGNIS: Final = f"{DOMAIN}_ereignis_{{}}"
