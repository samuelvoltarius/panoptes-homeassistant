"""Erzeugt strings.json + translations/{en,de}.json aus einer Quelle.

Aufruf (im Repo-Wurzelordner): python scripts/gen_strings.py
"""

import json
from pathlib import Path

ZIEL = Path(__file__).resolve().parent.parent / "custom_components" / "panoptes"


def build(sprache: str) -> dict:
    de = sprache == "de"

    def t(d: str, e: str) -> str:
        return d if de else e

    auto = {"name": t("Auto", "Car"), "description": t("Welches Panoptes.", "Which Panoptes.")}
    zu_alt = t(
        "Diese Panoptes-Version kennt die Home-Assistant-Schnittstelle noch nicht. Bitte Panoptes aktualisieren.",
        "This Panoptes version does not support the Home Assistant interface yet. Please update Panoptes.",
    )
    return {
        "config": {
            "flow_title": "{name}",
            "step": {
                "user": {
                    "title": "Panoptes",
                    "description": t(
                        "Wie soll dein Auto mit Home Assistant reden?",
                        "How should your car talk to Home Assistant?",
                    ),
                    "menu_options": {
                        "internet": t(
                            "Über das Internet (empfohlen) – geht auch unterwegs im Mobilfunk, 3 Tipps",
                            "Over the internet (recommended) – works on mobile data too, 3 taps",
                        ),
                        "direkt": t(
                            "Direkt – gleiches WLAN oder Tailscale, live in beide Richtungen",
                            "Direct – same Wi-Fi or Tailscale, live in both directions",
                        ),
                    },
                },
                "direkt": {
                    "title": t("Direkt verbinden", "Connect directly"),
                    "description": t(
                        "Adresse von Panoptes eingeben (sie steht neben dem Knopf „Mit Home Assistant verbinden“), z. B. `100.64.0.10` oder `http://192.168.0.50:8180`. Danach erlaubst du die Verbindung mit einem Tipp in Telegram oder in Panoptes – kein Code zum Abtippen.",
                        "Enter the Panoptes address (shown next to the “Connect to Home Assistant” button), e.g. `100.64.0.10` or `http://192.168.0.50:8180`. You then allow the connection with one tap in Telegram or in Panoptes – no code to type.",
                    ),
                    "data": {"url": t("Panoptes-Adresse", "Panoptes address")},
                },
                "nur_lokal": {
                    "title": t("Nur im Heim-WLAN", "Home Wi-Fi only"),
                    "description": t(
                        "Dieses Home Assistant hat keine Adresse, die aus dem Internet erreichbar ist (weder Home Assistant Cloud/Nabu Casa noch eine eigene externe Adresse). Dein Auto kann dann nur Daten schicken, wenn es in deinem WLAN ist ({adresse}). Unterwegs kommt nichts an.\n\nFür unterwegs: Home Assistant Cloud einschalten oder eine externe Adresse unter Einstellungen → System → Netzwerk eintragen. Trotzdem fortfahren?",
                        "This Home Assistant has no address reachable from the internet (neither Home Assistant Cloud/Nabu Casa nor an own external URL). Your car can then only send data while it is on your Wi-Fi ({adresse}). Nothing arrives on the road.\n\nFor on the road: enable Home Assistant Cloud or set an external URL under Settings → System → Network. Continue anyway?",
                    ),
                },
                "zeroconf_confirm": {
                    "title": t("Panoptes gefunden", "Panoptes found"),
                    "description": t(
                        "„{name}“ wurde im Netzwerk gefunden ({url}). Jetzt verbinden? Panoptes fragt danach in Telegram bzw. im Panoptes-Web nach deiner Erlaubnis.",
                        "“{name}” was found on your network ({url}). Connect now? Panoptes will then ask for your permission in Telegram or the Panoptes web UI.",
                    ),
                },
                "reauth_confirm": {
                    "title": t("Erneut verbinden", "Reconnect"),
                    "description": t(
                        "Die Kopplung mit Panoptes ({url}) ist abgelaufen oder wurde widerrufen. Neu verbinden?",
                        "The pairing with Panoptes ({url}) expired or was revoked. Connect again?",
                    ),
                },
                "reconfigure": {
                    "title": t("Adresse ändern", "Change address"),
                    "description": t(
                        "Neue Adresse desselben Autos (z. B. Tailscale statt WLAN). Die Kopplung bleibt bestehen.",
                        "New address of the same car (e.g. Tailscale instead of Wi-Fi). The pairing is kept.",
                    ),
                    "data": {"url": t("Panoptes-Adresse", "Panoptes address")},
                },
            },
            "progress": {
                "auto": t(
                    "**1.** [An meinen Panoptes-Bot senden]({link}) – in Telegram den Chat mit deinem Panoptes-Bot wählen.\n\n**2.** Panoptes fragt nach. Prüfzahl vergleichen: **{pruefzahl}**\n\n**3.** Auf **Erlauben** tippen. Fertig – dieses Fenster schließt sich von selbst.\n\nOhne Telegram: in Panoptes → System → Home Assistant diesen Text einfügen:\n\n`{text}`\n\nGültig bis {bis} Uhr.",
                    "**1.** [Send to my Panoptes bot]({link}) – in Telegram pick the chat with your Panoptes bot.\n\n**2.** Panoptes asks you. Compare the check number: **{pruefzahl}**\n\n**3.** Tap **Allow**. Done – this window closes by itself.\n\nWithout Telegram: paste this text in Panoptes → System → Home Assistant:\n\n`{text}`\n\nValid until {bis}.",
                ),
                "freigabe": t(
                    "Bitte jetzt in **Telegram** oder in **Panoptes → Gekoppelte Geräte** auf **Erlauben** tippen.\n\nPrüfzahl: **{pruefzahl}**\n\nVergleiche die Zahl – sie muss dort genauso stehen. Gerät: „{geraet}“. Die Anfrage gilt 2 Minuten.",
                    "Now tap **Allow** in **Telegram** or in **Panoptes → Paired devices**.\n\nCheck number: **{pruefzahl}**\n\nCompare the number – it must match exactly. Device: “{geraet}”. The request is valid for 2 minutes.",
                )
            },
            "error": {
                "ungueltige_adresse": t("Ungültige Adresse.", "Invalid address."),
                "nicht_erreichbar": t(
                    "Panoptes ist nicht erreichbar. Gleiches WLAN oder Tailscale? Auto wach?",
                    "Panoptes is not reachable. Same Wi-Fi or Tailscale? Is the car awake?",
                ),
                "zu_viele_anfragen": t(
                    "Zu viele offene Anfragen. Bitte 2 Minuten warten.",
                    "Too many open requests. Please wait 2 minutes.",
                ),
                "panoptes_zu_alt": zu_alt,
                "anderes_auto": t(
                    "Unter dieser Adresse antwortet ein anderes Auto oder kein Panoptes.",
                    "A different car (or no Panoptes) answers at this address.",
                ),
                "unbekannt": t("Unerwarteter Fehler.", "Unexpected error."),
            },
            "abort": {
                "already_configured": t("Dieses Auto ist schon eingerichtet.", "This car is already set up."),
                "abgelehnt": t("Die Verbindung wurde in Panoptes abgelehnt.", "The connection was declined in Panoptes."),
                "abgelaufen": t(
                    "Die Anfrage ist abgelaufen (2 Minuten). Bitte erneut versuchen.",
                    "The request expired (2 minutes). Please try again.",
                ),
                "schon_abgeholt": t(
                    "Diese Freigabe wurde schon verwendet. Bitte erneut versuchen.",
                    "This approval was already used. Please try again.",
                ),
                "nicht_erreichbar": t(
                    "Panoptes war während der Freigabe nicht erreichbar.",
                    "Panoptes was not reachable during approval.",
                ),
                "panoptes_zu_alt": zu_alt,
                "anderes_auto": t(
                    "Unter dieser Adresse antwortet ein anderes Auto.",
                    "A different car answers at this address.",
                ),
                "keine_adresse": t(
                    "Home Assistant kennt keine eigene Adresse. Bitte unter Einstellungen → System → Netzwerk eintragen.",
                    "Home Assistant does not know its own address. Please set it under Settings → System → Network.",
                ),
                "keine_kennung":t("Panoptes hat keine Kennung angekündigt.", "Panoptes did not announce an id."),
                "reauth_successful": t("Erneut verbunden.", "Reconnected."),
                "reconfigure_successful": t("Adresse geändert.", "Address changed."),
                "unbekannt": t("Unerwarteter Fehler.", "Unexpected error."),
            },
        },
        "options": {
            "step": {
                "init": {
                    "title": "Panoptes",
                    "data": {
                        "position": t("Position als Gerätetracker", "Position as device tracker"),
                        "einstellungen": t("Panoptes-Einstellungen als Entitäten", "Panoptes settings as entities"),
                    },
                    "data_description": {
                        "position": t(
                            "Nur wirksam, wenn „Position mit Home Assistant teilen“ auch in Panoptes eingeschaltet ist.",
                            "Only works if “Share position with Home Assistant” is also enabled in Panoptes.",
                        ),
                        "einstellungen": t(
                            "Nur gespeicherte Panoptes-Einstellungen. Fahrzeugbefehle (Klima, Schloss, Licht, Hupe, Fenster) gibt es bewusst nicht. Zugangseinstellungen bleiben nur lesbar.",
                            "Stored Panoptes settings only. Vehicle commands (climate, locks, lights, horn, windows) are deliberately not available. Access settings stay read-only.",
                        ),
                    },
                }
            }
        },
        "entity": {
            "sensor": {
                "waechter": {
                    "name": t("Wächter", "Guardian"),
                    "state": {
                        "scharf": t("Scharf", "Armed"),
                        "aus": t("Aus", "Off"),
                        "startet": t("Startet", "Starting"),
                        "pausiert": t("Pausiert", "Paused"),
                        "fehler": t("Fehler", "Error"),
                    },
                },
                "gesundheit": {
                    "name": t("Gesundheit", "Health"),
                    "state": {"ok": "OK", "warnung": t("Warnung", "Warning"), "fehler": t("Fehler", "Error")},
                },
                "f1": {
                    "name": t("Reicht es noch?", "Will it make it?"),
                    "state": {
                        "reicht": t("Reicht", "Enough"),
                        "knapp": t("Knapp", "Tight"),
                        "reicht_nicht": t("Reicht nicht", "Not enough"),
                        "keine_route": t("Keine Route", "No route"),
                        "unbekannt": t("Unbekannt", "Unknown"),
                    },
                },
                "app_version": {"name": t("Panoptes-Version", "Panoptes version")},
            },
            "binary_sensor": {"push": {"name": t("Live-Verbindung", "Live connection")}},
            "switch": {"waechter_schalter": {"name": t("Wächter", "Guardian")}},
            "button": {"regel": {"name": t("Regel {name}", "Rule {name}")}},
            "event": {
                "ereignis_fahrt": {
                    "name": t("Ankunft/Abfahrt", "Arrival/departure"),
                    "state_attributes": {
                        "event_type": {
                            "state": {"ankunft": t("Ankunft", "Arrival"), "abfahrt": t("Abfahrt", "Departure")}
                        }
                    },
                },
                "ereignis_waechter": {
                    "name": t("Wächter-Ereignis", "Guardian event"),
                    "state_attributes": {
                        "event_type": {
                            "state": {
                                "bewegung": t("Bewegung", "Motion"),
                                "person": "Person",
                                "erschuetterung": t("Erschütterung", "Impact"),
                            }
                        }
                    },
                },
            },
            "device_tracker": {"position": {"name": "Position"}},
        },
        "exceptions": {
            "aktion_fehlgeschlagen": {
                "message": t("Panoptes hat die Aktion abgelehnt: {grund}", "Panoptes rejected the action: {grund}")
            },
            "einstellung_abgelehnt": {
                "message": t(
                    "Panoptes hat die Einstellung nicht gespeichert: {grund}",
                    "Panoptes did not save the setting: {grund}",
                )
            },
            "eintrag_unbekannt": {"message": t("Unbekannter Panoptes-Eintrag.", "Unknown Panoptes entry.")},
            "eintrag_nicht_geladen": {
                "message": t("Panoptes ist gerade nicht verbunden.", "Panoptes is not connected right now.")
            },
            "regel_nicht_ausloesbar": {
                "message": t(
                    "Diese Regel enthält eine Fahrzeugaktion und kann aus Home Assistant nicht ausgelöst werden.",
                    "This rule contains a vehicle action and cannot be triggered from Home Assistant.",
                )
            },
        },
        "services": {
            "ruhezeit": {
                "name": t("Telegram-Ruhezeit", "Telegram quiet time"),
                "description": t(
                    "Panoptes-Meldungen in Telegram für eine Zeit stummschalten (0 = beenden).",
                    "Mute Panoptes Telegram messages for a while (0 = end).",
                ),
                "fields": {
                    "config_entry_id": auto,
                    "minuten": {
                        "name": t("Minuten", "Minutes"),
                        "description": t("Dauer, höchstens 1440.", "Duration, at most 1440."),
                    },
                },
            },
            "regel_ausfuehren": {
                "name": t("Regel ausführen", "Run rule"),
                "description": t(
                    "Eine Panoptes-Regel ausführen. Regeln mit Fahrzeugaktion sind gesperrt.",
                    "Run a Panoptes rule. Rules with a vehicle action are blocked.",
                ),
                "fields": {
                    "config_entry_id": auto,
                    "regel_id": {"name": t("Regel-ID", "Rule id"), "description": t("Kennung der Regel.", "Rule id.")},
                },
            },
        },
    }


def schreibe(pfad: Path, daten: dict) -> None:
    pfad.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    en = build("en")
    schreibe(ZIEL / "strings.json", en)
    schreibe(ZIEL / "translations" / "en.json", en)
    schreibe(ZIEL / "translations" / "de.json", build("de"))
    print("ok")
