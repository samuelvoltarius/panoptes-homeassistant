# Installation

## A) Über HACS (empfohlen)
1. **HACS → ⋮ → Benutzerdefinierte Repositories**: die Adresse dieses Repositorys eintragen, Typ **Integration**, Hinzufügen.
2. In HACS nach **Panoptes** suchen und **Herunterladen**.
3. **Home Assistant neu starten** (Einstellungen → System → Neu starten).
4. Weiter mit **Verbinden**.

Updates erscheinen in HACS, sobald ein neuer Release da ist.

## B) Von Hand
1. Den Ordner `custom_components/panoptes` nach `/config/custom_components/panoptes` kopieren.
   Danach muss `/config/custom_components/panoptes/manifest.json` existieren.
2. **Home Assistant neu starten**.
3. Weiter mit **Verbinden**.

Zum Aktualisieren den Ordner ersetzen und neu starten. Die Kopplung bleibt erhalten.

## Verbinden
1. Einstellungen → Geräte & Dienste → **Integration hinzufügen** → „Panoptes“.
2. Die **Panoptes-Adresse** eingeben (gleiches WLAN oder Tailscale). Du findest sie in Panoptes unter „Mit Home Assistant verbinden“.
3. Home Assistant zeigt eine **Prüfzahl**. Panoptes meldet die Anfrage in Telegram oder in Panoptes selbst.
   Vergleiche die Zahl und tippe **Erlauben**. Die Anfrage gilt 2 Minuten.

Steht unter „Entdeckt“ schon **Panoptes** (mDNS, in Panoptes einschaltbar), genügt dort ein Tipp auf Hinzufügen.

## Optionen (Zahnrad an der Integration)
- **Position als Gerätetracker**: Standard aus. Wirkt nur, wenn Panoptes die Position für Home Assistant ebenfalls freigibt.
- **Panoptes-Einstellungen als Entitäten**: Standard an. Ausschalten, wenn Home Assistant nur lesen soll.

## Adresse geändert?
Integration → ⋮ → **Neu konfigurieren**.
