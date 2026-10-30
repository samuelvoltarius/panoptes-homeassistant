# Vertrag: Home-Assistant-Integration „Panoptes“ ↔ Panoptes im Auto (v1.0)

Zwei Wege: **Webhook (Teil B)**, das Auto sendet ausgehend an das HA des Nutzers, und **„Direkt“ (Teil C)**, HA spricht die
Auto-API über Tailscale oder das Heim-WLAN.

**Stand Panoptes 0.1.32:** Nur Teil C ist im Auto gebaut.
- Teil B fehlt im Auto. Die Integration blendet den Weg bei neuen Einrichtungen aus (`WEBHOOK_VERFUEGBAR` in `const.py`).
- `ziel` antwortet `{"ok":false,"grund":"nicht_verfuegbar"}`. Die Integration bietet dafür keinen Dienst an.
- `geraet` liefert `autoId` statt `haId`, `dilink` ist `null`.
- Wächter-Ereignisse kommen per SSE als `{"art":"bewegung"|"person","zeit":ms,"ereignisId":"<32 hex>"}`. `erschuetterung` kommt nicht vor.
- `GET /api/waechter/standbild/<ereignisId>` braucht das Recht `standbild` oder `kamera`, sonst 403
  `{"ok":false,"grund":"nicht_erlaubt","recht":…,"text":…}`. Die Integration ruft keine Bilder ab.

Für alle gilt: Es gibt **nie** Fahrzeugbefehle (Regel A), Zugangseinstellungen bleiben nur lesbar (Regel B), und wir setzen keine
bestimmte HA-Installation voraus.

---
## Teil A – gemeinsam für beide Wege

### A1 Zustandsobjekt (Panoptes → HA)
```json
{
  "geraet": { "haId": "h-…", "name": "Dolphin Surf", "modell": "Dolphin Surf", "appVersion": "0.1.8", "dilink": 3 },
  "telemetrie": { "soc": 81.0, "ev_range_km": 260, "volt_12v": 13.1, "is_charging": false, "is_parked": true },
  "felder": [ { "key": "soc", "titel": "Ladestand", "typ": "sensor", "deviceClass": "battery", "stateClass": "measurement",
                "einheit": "%", "icon": "mdi:battery", "erweitert": false } ],
  "waechter": { "zustand": "scharf", "grund": null, "seit": 1761040000000 },
  "gesundheit": { "zustand": "ok", "punkte": [ { "id": "nicht_scharf", "ok": true, "titel": "…" } ] },
  "f1": { "stufe": "reicht", "grund": null },
  "ruhezeit": { "aktiv": false, "bis": null },
  "position": null
}
```
- `haId`: eigene Zufallskennung für HA. **Nicht** die Planer-`autoId` und nie die VIN. Sie bleibt je Auto gleich, auch bei neuer Kopplung.
- `telemetrie`-Schlüssel = TelemetryFieldCatalog (wie MQTT). `felder` = Katalog als Daten (Titel in der Panoptes-Sprache).
  **Nie** `lat/lon/elevation/heading/vin` in `telemetrie`.
- `waechter.zustand`: `scharf | startet | pausiert` (Wächter-Stopp, sicherer Ort, Zeitplan) `| aus | fehler`
  (Gesundheitsbefund nicht_scharf/keine_bilder).
- `gesundheit.zustand`: `ok | warnung` (nur mqtt_getrennt/telemetrie_alt) `| fehler`. `punkte[]` aus den Befund-Kennungen.
- `f1.stufe`: `reicht | knapp | reicht_nicht | keine_route | unbekannt`.
- `position` nur, wenn `ha.positionTeilen` = true (Standard aus, nurLesendExtern): `{lat, lon, genauigkeitM, zeit}`, sonst `null`.
  HA legt zusätzlich nur bei eigener Option einen device_tracker an.
- Teil-Updates erlaubt: fehlende Schlüssel = unverändert. `null` = „nicht verfügbar“.

### A2 Ereignisse (Panoptes → HA)
`{"art":"ankunft"|"abfahrt","ort":"Zuhause"|null,"zeit":ms}` (nur Zonenname, keine Koordinaten) ·
`{"art":"ereignis","was":"bewegung"|"person"|"erschuetterung","zeit":ms}` (keine Bilder) ·
`{"art":"waechter","zustand":"…"}`.

### A3 Aufträge HA → Panoptes – **abschließende Erlaubnisliste**, im Auto hart geprüft
| typ | Felder | Wirkung |
|---|---|---|
| `einstellung` | `key`, `wert` | wie `PUT /api/einstellungen` mit den Rechten einer Kopplung `art=homeassistant` (nurLesendExtern, gefahr, bestaetigung, min/max/option) |
| `waechter` | `an` (bool) | Wächter scharf/aus |
| `ruhezeit` | `minuten` 0–1440 | Telegram-Ruhezeit (0 = beenden) |
| `ziel` | `text` **oder** `name`,`lat`,`lon` | **nur ins Planer-Postfach** (wie „An Auto senden“ auf /ev/), nie direkt ins Navi; ohne Planer → `kein_planer` |
| `regel` | `id` | nur bei `ausloesbar:true` (siehe A4) |
Alles andere → Quittung `{ok:false, grund:"unbekannt"}`, und es passiert **nichts**.
Jeder Auftrag hat eine `id` (`a-<hex>`). Das Auto führt jede id höchstens **einmal** aus und merkt sich die letzten 200 ids.

### A4 Regeln
`{"regeln":[{"id","name","ausloesbar":bool,"grund":"fahrzeugaktion"|null}]}`. `ausloesbar:false` bei jeder Aktion aus
VehicleControlCatalog oder riskyActions des Community-Servers, dazu MQTT senden, Regeln schalten, Shell. **Im Zweifel false.**

### A5 Einstellungen
Schema und Werte genau wie `GET /api/einstellungen/schema` bzw. `GET /api/einstellungen` (EINSTELLUNGEN_SCHEMA.md).
HA legt nur Felder mit `nurLesendExtern:false` an, ohne `gefahr`, `bestaetigung`, `neustartNoetig`, `geheim`, `alias`.
**`ha.mqttDiscoveryAus`** (bool): HA setzt das Feld beim Einrichten auf true und beim Entfernen auf false (nur per Direkt-Modus oder als Auftrag).

---
## Teil B – Webhook (Standard)

### B1 Einrichtung („3 Tipps“)
1. Nutzer in HA: „Panoptes hinzufügen“ → „Über das Internet“. HA ermittelt seine Adressen und meldet sie in dieser Reihenfolge:
   - Nabu-Casa-Remote-UI `https://<id>.ui.nabu.casa`. Diese Adresse ist E2E; **Cloudhooks (hooks.nabu.casa) werden nicht verwendet.**
   - eigene externe HTTPS-Adresse
   - interne Adresse, nur für das Heim-WLAN
   Ohne externe Adresse warnt HA: „nur im Heim-WLAN“.
2. HA erzeugt einen **Einrichtungs-Webhook** `w0`, einen **Einmal-Schlüssel** `k` (≥ 24 Byte, base64url) und eine **Prüfzahl** `p`.
   Daraus wird der Einrichtungstext (eine Zeile, höchstens ~400 Zeichen):
   `PANOPTES-HA1 <base64url(JSON)>` mit JSON
   `{"v":1,"n":"Home Assistant Zuhause","u":["https://…ui.nabu.casa","http://192.168.1.5:8123"],"w":"<w0>","k":"<k>","p":"4711","bis":<ms>}`
   HA zeigt den Knopf **„An meinen Panoptes-Bot senden“** (`https://t.me/share/url?url=<Text>`) und den Text zum Kopieren.
3. **Auto (Telegram-Bot oder Panoptes-Web „Home Assistant verbinden“, Feld zum Einfügen):** erkennt `PANOPTES-HA1` und fragt:
   „Home Assistant ‚Zuhause‘ verbinden? Prüfzahl 4711 [Erlauben] [Ablehnen]“. Im Bot zählt das nur, wenn Absender und Chat dem Besitzer gehören.
   Abgelaufen (`bis`) → ablehnen. Der Text wird nicht geloggt.
4. Nach **Erlauben** schickt das Auto `POST <u[i]>/api/webhook/<w0>`:
   `{"v":1,"art":"hallo","k":"<k>","geraet":{…A1.geraet…}}`
   HA antwortet `200 {"ok":true,"webhook":"<w1>","geheimnis":"<32 Byte base64url>","sendeTaktS":60}`.
   Danach ist `w0` sofort ungültig (Telegram kannte `w0` und `k`, aber nicht `w1` und `geheimnis`).
   Falscher oder alter `k` → `{"ok":false,"grund":"schluessel"}`. Abgelaufen → `{"ok":false,"grund":"abgelaufen"}`.
5. Ab jetzt nutzt das Auto nur noch `w1` + `geheimnis`. HA schließt die Einrichtung ab, auch ohne offenes Browserfenster.

### B2 Laufender Betrieb: `POST <u[i]>/api/webhook/<w1>`
Kopf `Authorization: Bearer <geheimnis>`, `Content-Type: application/json`. Das Auto probiert `u[]` der Reihe nach und merkt sich die zuletzt
erfolgreiche Adresse. **Nur HTTPS**, außer die Adresse ist privat (RFC1918/ULA/`.local`).
```json
{ "v":1, "art":"zustand", "seq":42, "zeit":1761040000000,
  "zustand": { …A1, Teil-Update… },
  "ereignisse": [ …A2… ],
  "quittungen": [ { "id":"a-1f", "ok":true }, { "id":"a-20", "ok":false, "grund":"nurLesendExtern" } ],
  "etags": { "schema":"s-3f", "werte":"w-8c", "regeln":"r-11" },
  "schema": { … nur wenn gewünscht … }, "werte": { …GET /api/einstellungen… }, "regeln": [ … ] }
```
Antwort `200`:
```json
{ "ok":true,
  "auftraege":[ { "id":"a-21","typ":"einstellung","key":"planner.reserve","wert":20 }, { "id":"a-22","typ":"waechter","an":false } ],
  "wuensche": { "schema":true, "werte":false, "regeln":false },
  "sendeTaktS": 60 }
```
- `wuensche`: HA fordert an, was ihm fehlt oder dessen etag sich geändert hat. Das Auto schickt es **gleich** im nächsten POST mit.
- **Aufträge** bleiben in HA stehen, bis eine Quittung kommt. Das Auto bekommt sie also ggf. mehrfach und führt jede id nur einmal aus.
- **`"art":"warte"`** (Lange Abfrage, optional): `{"v":1,"art":"warte","maxS":25}`. HA hält die Antwort bis 25 s offen und antwortet
  sofort, sobald ein Auftrag da ist. Das Auto nutzt es nur bei ACC an oder wenn ein Panoptes-Fenster offen ist; sonst reicht der Sendetakt.
- Sendetakt: Bei Änderung sofort (gedrosselt wie MQTT minInterval), sonst alle `sendeTaktS` (geparkt), höchstens 1 POST je 5 s.
  Funkloch: in die Warteschlange (höchstens 50 Ereignisse, Zustand nur der neueste), dann Backoff 5 s … 15 min.
- Antwortcodes: `401 {"ok":false,"grund":"geheimnis"}` → Kopplung kaputt, dem Nutzer melden. **`{"ok":false,"grund":"widerrufen"}`**
  (Grabstein, HA hat die Integration entfernt) → Geheimnis löschen, Panoptes/Telegram melden „Home Assistant getrennt“.
  Ein **leerer 200** (HA kennt den Webhook gerade nicht, z. B. beim Neustart) zählt **nicht** als Widerruf, sondern führt zu Backoff.
- Größe: höchstens 256 KB je POST (Schema einzeln, wenn nötig).

### B3 Trennen
- **In HA entfernt:** HA hinterlegt für `w1` 90 Tage einen Grabstein, der mit `{"ok":false,"grund":"widerrufen"}` antwortet.
  `ha.mqttDiscoveryAus` setzt HA vorher per Auftrag auf false zurück (best effort).
- **In Panoptes getrennt** (Liste „Gekoppelte Geräte“ mit `art:"homeassistant"`, `modus:"webhook"`): Das Auto sendet einmal
  `{"v":1,"art":"tschuess"}` und löscht dann das Geheimnis. HA meldet „Erneut verbinden“.

---
## Teil C – Direkt (Tailscale / Heim-WLAN), wie bisher gebaut
- Kopplung `POST /auth/kopplung/anfrage {geraet, pruefzahl, art:"homeassistant"}` → Abholen des JWT. Täglich `POST /api/sitzung/erneuern`.
  Abmelden mit `POST /api/sitzung/abmelden`.
- `GET /api/ha/v1/zustand` (A1, `?position=1`), `GET /api/ha/v1/ereignisse` (SSE: `event: zustand|ankunft|abfahrt|waechter|ereignis`,
  `: ping` alle 25 s, höchstens 2 Streams je kid, bei Widerruf schließen), `GET /api/ha/v1/regeln` (A4), `POST /api/ha/v1/aktion` (A3 ohne
  `einstellung`; Einstellungen über `PUT /api/einstellungen`). Alle Pfade gehören ausdrücklich in die Kopplungs-Erlaubnisliste.
- mDNS `_panoptes._tcp` **Standard aus** (`ha.mdns`, nurLesendExtern). TXT nur eine eigene Zufalls-`id`, `api=1`.
- Fehlt die Schnittstelle (404), bricht HA die Einrichtung ab und meldet sich selbst ab.

## Prüfpunkte für das Auto
1. Ein Auftragstyp außerhalb von A3 → Quittung `unbekannt`, keine Wirkung. Auch `typ:"einstellung"` mit `key` remote./auth./adb./deterrent.* → `nurLesendExtern`.
2. Gleiche Auftrags-id zweimal → nur einmal ausgeführt.
3. `ziel` landet nur im Planer-Postfach.
4. `regel` mit Fahrzeugaktion → `fahrzeugaktion`, keine Wirkung.
5. Einrichtungstext abgelaufen, falscher Schlüssel oder fremder Chat → nichts passiert.
6. Kein HTTP an öffentliche Adressen. Geheimnis, Webhook-id und Einrichtungstext nie im Log.
7. Grabstein-Antwort → Geheimnis gelöscht. Leerer 200 → kein Löschen.
