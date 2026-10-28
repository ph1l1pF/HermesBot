# HermesBot — Shopify → Cargoboard

## Lokal auf dem Mac starten

Python 3.11 oder neuer vorausgesetzt:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/configure_local.py --environment test
./scripts/start_local.sh
```

Der API-Key wird im Terminal verdeckt abgefragt und nur in `.env` gespeichert (Dateirechte 600, von Git ausgeschlossen). Der lokale Starter lädt Werte ohne Variablenersetzung. `.env` nicht mit `source` ausführen.

Die neue Vorlage startet im **Testmodus**. Oben auf jeder Seite kann zwischen Test und Produktion gewechselt werden:

- Test: `https://api-sandbox.cargoboard.com`, Key in `CARGOBOARD_SANDBOX_API_KEY`.
- Produktion: `https://api.cargoboard.com`, Key in `CARGOBOARD_PRODUCTION_API_KEY`.

Keys verdeckt hinterlegen:

```sh
.venv/bin/python scripts/configure_local.py --environment test
.venv/bin/python scripts/configure_local.py --environment production
```

Nach Änderungen der Keys ist ein Neustart erforderlich. Die Auswahl der Umgebung wird lokal in `.data/cargoboard_environment.json` gespeichert und bleibt beim Neustart erhalten. Bei jedem echten Wechsel werden offene Preisangebote und deren Buchungsfreigaben ungültig. Test- und Produktivaufträge werden getrennt behandelt. Ein fehlender Sandbox-Key wird niemals durch den Produktions-Key ersetzt.

Bestehende Installationen mit `CARGOBOARD_API_KEY` und `CARGOBOARD_BASE_URL` bleiben kompatibel; ihr Key ist ausschließlich an die bisher eingestellte URL gebunden. `CARGOBOARD_ENVIRONMENT=test` oder `production` legt die anfängliche Auswahl fest, solange noch keine Auswahl gespeichert wurde.

Öffne http://127.0.0.1:8000. Nach Änderungen an `.env` den laufenden Server mit Strg+C beenden und erneut starten. Nur eine Instanz/einen Worker mit derselben `.data`-Ablage betreiben.

Zusätzlich in `.env` die Shopify-Zugangsdaten eintragen (siehe unten), Shopify verbinden und unter **Abholadressen** das tatsächliche Abhollager hinterlegen. Lokale Aufträge und OAuth-Daten liegen unter `.data/` und werden nicht eingecheckt. Bestehende Daten können über die jeweiligen Pfadvariablen weiterverwendet werden.


## Cargoboard prüfen und buchen

**Verbindung prüfen** auf der Startseite liest nur die Angebotsliste. Das Prüfergebnis wird als lesbare Seite angezeigt. Bei Zeitüberschreitungen ist der Key noch nicht bestätigt. Für API-Aufrufe ohne HTML-Anforderung liefert der Endpunkt weiterhin JSON (`connected: true` bei Erfolg).

1. Shopify-Bestellung öffnen und **Speditionsauftrag erstellen** wählen.
2. Abhollager, vollständige Lieferadresse, Packstücke und künftigen Werktag prüfen.
3. Standard oder Express wählen; gegebenenfalls Versicherung und Hebebühne bei Lieferung aktivieren.
4. Bei Privatkunden Lieferavisierung mit Telefonnummer oder E-Mail angeben.
5. **Kosten berechnen** fragt einen echten Cargoboard-Preis ab und zeigt Nettopreis und, falls geliefert, Bruttopreis.
6. Im Testmodus erstellt **Testauftrag bei Cargoboard buchen** nur einen Sandbox-Auftrag.
7. In Produktion öffnet **Auftrag prüfen und buchen** zunächst eine Sicherheitsabfrage mit Abholung, Lieferung, Packstücken und Preis. Erst **Ja, kostenpflichtig buchen** legt den echten Auftrag an. Die zusätzliche Bestätigung wird serverseitig geprüft und kann nicht durch einen direkten Formularaufruf übersprungen werden. Dafür ist ein passendes, höchstens 15 Minuten altes Angebot erforderlich. Änderungen erfordern eine neue Preisabfrage.

Nach einer erfolgreichen Buchung werden Order-ID und Sendungsreferenz angezeigt. Dokumente und Sendungsverfolgung sind vorerst im Cargoboard-Portal verfügbar. Ein erneuter Buchungsversuch wird blockiert. Bei `booking` oder `booking_unknown` zuerst im Portal prüfen, ob der Auftrag angelegt wurde; nicht einfach einen weiteren Auftrag erstellen oder den lokalen Status zurücksetzen.

Die Anbindung verarbeitet die `data`-Antworten der API und bucht über `/v1/quotations/{id}/book`. Alte direkte Bestätigungsrouten buchen nicht mehr. Weitere Produkte wie FIX/DIRECT, Gefahrgut und besondere Zollanforderungen sind noch nicht im Formular abgebildet. Die API prüft zusätzlich länder- und produktspezifische Anforderungen. Die Checkbox Hebebühne bezieht sich auf die Lieferung.

Tests mit simulierten HTTP-Antworten, ohne echte Buchungen:

```sh
.venv/bin/python -m pytest -q
```

Referenz: [Cargoboard API](https://docs.cargoboard.com/docs/usage), [Buchung auf Angebotsbasis](https://docs.cargoboard.com/reference/book-an-order-based-on-a-quotation).

## Shopify OAuth setup
1. Create or open the app in the Shopify Dev Dashboard.
2. Copy the **Client ID** and **Client Secret** into environment variables.
3. Set the shop domain, for example:
   - `SHOPIFY_SHOP_DOMAIN=caae00` or `SHOPIFY_SHOP_DOMAIN=caae00.myshopify.com`
4. Add the app's callback URL to the allowed redirection URLs.
   - For this repo, the callback is `/shopify/callback` on the current public URL.
5. Open the app and click **Shopify verbinden**.
6. Finish the Shopify consent screen.

## Environment variables
- `SHOPIFY_SHOP_DOMAIN`
- `SHOPIFY_CLIENT_ID`
- `SHOPIFY_CLIENT_SECRET`
- `SHOPIFY_SCOPES` (optional, default `read_orders`)
- `SHOPIFY_ACCESS_MODE` (optional, `offline` default, `online` optional)
- `SHOPIFY_API_VERSION` (optional, default `2024-10`)
- `SHOPIFY_AUTH_PATH` (optional, default `~/.hermes/shopify_auth.json`)
- `CARGOBOARD_SANDBOX_API_KEY`
- `CARGOBOARD_PRODUCTION_API_KEY`
- `CARGOBOARD_ENVIRONMENT` (optional: `test` oder `production`)
- `CARGOBOARD_API_KEY` und `CARGOBOARD_BASE_URL` (nur für bestehende Installationen)
- `BILLING_NAME`
- `BILLING_STREET`
- `BILLING_CITY`
- `BILLING_POST_CODE`
- `BILLING_COUNTRY`

## Versandregeln
- 1 order = 1 shipment with identical packages per line
- Billing address is constant
- Delivery address comes from Shopify but is editable
- Dimensions and weight are entered manually
- No fully automatic shipping

## Telefonnummer bei der Abholung

Unter Abholadressen kann eine Telefonnummer gespeichert werden, auch nachträglich bei bestehenden Adressen. Sie wird über `shipper.contactPerson.phone` mit dem Namen der Abholadresse bei Preisabfrage und Buchung übertragen. Eine Nummer aktiviert keine kostenpflichtige Abholavisierung. Nach einer Änderung der Nummer muss ein bereits angefragter Preis vor dem Buchen neu berechnet werden.

## Empfänger-Telefon aus Shopify

Die Nummer der Lieferadresse wird übernommen. Fehlt sie, wird die Telefonnummer der Bestellung, danach des Kunden und zuletzt seiner Standardadresse verwendet. Die Nummer bleibt im Versandformular editierbar und wird unter `consignee.contactPerson.phone` bei Preisabfrage und Buchung übermittelt, auch für Geschäftskunden. Manuell gespeicherte Änderungen einschließlich einer geleerten Nummer bleiben beim erneuten Öffnen erhalten.

## Vorgaben für neue Versandentwürfe

Packstück-Typ Karton (KT), Gewicht 10 kg und Höhe 10 cm. Länge und Breite bleiben bei den bisherigen Vorgaben 120 × 80 cm. Bereits bearbeitete Versanddaten werden beim Öffnen beibehalten.
