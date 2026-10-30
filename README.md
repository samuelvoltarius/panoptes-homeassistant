# Panoptes für Home Assistant

Verbindet **Panoptes** (App für BYD-Autos mit DiLink 3, 4 und 5) mit Home Assistant, in beide Richtungen.
Du gibst die Adresse ein, vergleichst eine Prüfzahl und tippst in Panoptes auf **Erlauben**. Passwörter brauchst du keine.

> Privates Projekt. Nicht von BYD und nicht mit BYD verbunden. Alle Marken gehören ihren Inhabern.

## Was du bekommst

**Vom Auto nach Home Assistant** (live per Push, sonst alle 30 s abgefragt)
- Sensoren: Ladestand, Reichweite, 12-V-Batterie, Lädt/Geparkt, Temperaturen und mehr
- **Wächter**: scharf, aus, startet, pausiert, Fehler
- **Gesundheit** von Panoptes und **„Reicht es noch?“**
- Ereignisse **Ankunft/Abfahrt** (nur der Name der gespeicherten Zone) und **Wächter-Ereignis** (Bewegung oder Person, ohne Bilder).
  Sie kommen auch als HA-Events: `panoptes_ankunft`, `panoptes_abfahrt`, `panoptes_waechter_ereignis`
- **Position** als `device_tracker` nur, wenn du sie zweimal erlaubst: in Panoptes („Position mit Home Assistant teilen“)
  **und** in den Optionen der Integration. Standard ist aus.

**Von Home Assistant ins Auto** (nur Panoptes-eigene Funktionen)
- Schalter **Wächter** an/aus
- Gespeicherte **Panoptes-Einstellungen** als Schalter, Zahlen oder Auswahl
- Knöpfe für **Panoptes-Regeln**, aber nur für Regeln ohne Fahrzeugaktion
- Dienste `panoptes.ruhezeit` (Telegram stummschalten) und `panoptes.regel_ausfuehren`

### Was es bewusst nicht gibt
- **Keine Fahrzeugbefehle:** kein Klima, kein Schloss, keine Fenster, keine Hupe, kein Licht. Die Integration kennt diese
  Endpunkte nicht. Regeln mit Fahrzeugaktion werden weder als Knopf angezeigt noch per Dienst ausgelöst.
- **Zugangseinstellungen** (Fernzugang, Login, Ports, adb) bleiben nur lesbar.
- **Keine Kamerabilder.** Die Integration ruft keine Bilder oder Clips ab, Panoptes-Rechte `kamera` und `standbild` sind nicht nötig.
  Wächter-Ereignisse tragen nur eine `ereignis_id`.
- Keine FIN, keine Koordinaten in Sensoren oder Logs. Der Diagnose-Download ist geschwärzt.

## Voraussetzungen
- Home Assistant **2025.2** oder neuer
- Panoptes **0.1.32** oder neuer
- Home Assistant erreicht das Auto auf Port **8180**: gleiches WLAN oder gleiches Tailnet (Panoptes-Fernzugang)

**Unterwegs im Mobilfunk** geht es nur über Tailscale. Dass das Auto selbst über das Internet an Home Assistant sendet, kann
Panoptes noch nicht, dieser Weg ist deshalb ausgeblendet.

## Einrichten
Kurz: über HACS installieren, neu starten, Adresse eingeben, Prüfzahl vergleichen, Erlauben. Schritt für Schritt: [INSTALL.md](INSTALL.md).

**Entfernen:** Löschst du die Integration, widerruft Home Assistant die Kopplung sofort. Trennst du sie in Panoptes, meldet
Home Assistant „Erneut verbinden“.

## Lizenz
MIT, siehe [LICENSE](LICENSE).

## Entwicklung
Die Tests brauchen Linux oder macOS.
```bash
uv venv -p 3.14 .venv && . .venv/bin/activate
uv pip install -r requirements_test.txt
pytest
```
Die Tests laufen gegen ein nachgebautes Panoptes auf 127.0.0.1, das echtes SSE spricht. Jeder Test prüft am Ende, dass kein
Fahrzeug- oder Zugangs-Endpunkt aufgerufen wurde. Übersetzungen: `python scripts/gen_strings.py` erzeugt `strings.json`,
`translations/en.json` und `translations/de.json` aus einer Quelle. Schnittstelle: [docs/HA_VERTRAG.md](docs/HA_VERTRAG.md).
