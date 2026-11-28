# Edwards TIC mit nEXT 300 und nXDS

`EdwardsTIC` liest Betriebsdaten über die RS232-PC-Schnittstelle eines einzelnen TIC.
Für die lokale Zuordnung den Abschnitt `vacuum` in `config/devices.local.json` übernehmen
und den COM-Port des Controllers eintragen. Am aktuellen Aufbau ist das COM13.
Der Treiber setzt am PC 9600 Baud, 8N1 und CR als Abschluss. Der TIC muss für diese
Punkt-zu-Punkt-Verbindung eingerichtet sein. [Edwards, TIC-Serienprotokoll D397-30-880](https://www.lesker.com/newweb/gauges/pdf/tic-instrument-controller-serial-communications.pdf)

```python
from lapis_control.configuration import load_devices
from lapis_control.devices.vacuum.edwards_tic import EdwardsTIC

tic = load_devices("config/devices.local.json")["vacuum"]
assert isinstance(tic, EdwardsTIC)
with tic:
    pressure_pa = tic.read_pressure_pa(1)  # Select the connected gauge channel (1–3).
    print(f"{pressure_pa:.4g} Pa · {pressure_pa / 100:.4g} mbar")
```

## Welche Daten der Treiber liest

| Quelle | Objekt-IDs | Rückgabe |
|---|---|---|
| TIC, Turbopumpe | 904–909 | Betriebszustand, Drehzahl in Prozent, Leistung, Normaldrehzahl erreicht, Standby, Zyklusdauer |
| TIC, Vorpumpe | 910–912 | Vom TIC gemeldeter Zustand, Drehzahl in Prozent und Leistung der Vorpumpe |
| nEXT über TIC | 852, 859 | Gemessene Drehzahl in U/s und U/min, 32-Bit-Statuswort, Motor- und Controllertemperatur |
| nXDS über TIC | 802, 808 | Motorfrequenz, Warn- und Fehlerregister, Pumpen- und Controllertemperatur |
| TIC, weitere Kanäle | 913–915, 919, 920, 924 | Messröhren mit Einheit und Zustand, Netzteil- und Innentemperatur, Luftkühlerzustand |

Die Verfügbarkeit der Messwerte hängt von TIC-Ausführung, Firmware und Pumpenanbindung ab.
Bei einer über Relais angeschlossenen Vorpumpe ist der Schaltzustand des TIC kein
Drehzahlnachweis. Die nEXT-Objekte erfordern die serielle Weiterleitung durch den TIC.
Eine abgewiesene Abfrage liefert eine Exception mit Objekt-ID und Fehlercode.
[Edwards, TIC-Serienprotokoll, Abschnitt 1.6.2 und Tabelle 1](https://www.lesker.com/newweb/gauges/pdf/tic-instrument-controller-serial-communications.pdf)
und [Edwards, nEXT-Handbuch B800-00-880, Abschnitt 3.7.5 und Tabelle 30](https://www.idealvac.com/files/cataloghelpfiles/Edwards_nEXT_Turbo_Pumps_Product_Manual.pdf)

Bei der nXDS kennzeichnet der Rückgabewert −200 einen ungenutzten Temperatursensor.
Der Treiber liefert dafür `None`. Jede gesetzte Stelle im Warn- und Fehlerregister
bleibt mit ihrer Bitnummer sichtbar. [Edwards, nXDS-Serienprotokoll A735-01-860, Tabelle 3 und Abschnitt 3.9.1](https://mmrc.caltech.edu/Vacuum/Edwards/nXDSi%20Scroll/A735-01-860%20Serial%20comm.pdf)

## Änderungen gegenüber dem Diagnoseskript V7

- Beim Öffnen und Schließen wird ausschließlich der lokale Port verwaltet. Die einzige Sendestelle akzeptiert die oben aufgeführten ganzzahligen IDs und erzeugt daraus jeweils eine einzelne `?V`-Abfrage.
- Jede Abfrage ordnet vollständige, mit CR abgeschlossene Telegramme unter derselben Sperre ihrer Objekt-ID zu. Fragmente dürfen über mehrere Lesevorgänge eintreffen. Fremde Objekt-IDs und Zeilen ohne gültigen Kopf werden protokolliert. Der Treiber wartet innerhalb derselben Antwortfrist auf das gesuchte Telegramm. Auch bei einer Fehlerantwort muss die ID passen.
- Nach einer abgelaufenen Abfrage wartet der Treiber auf Ruhe am Eingang. Steht für dieselbe ID noch eine alte Antwort aus, fragt er zunächst ein anderes lesbares Objekt ab. Dessen Antwort grenzt die neue Messung von der vorhergehenden ab. Wenn der Abgleich oder die Messung scheitert, meldet der Treiber einen Fehler. Im Sitzungsprotokoll der GUI stehen die betreffenden Rohdaten und Zeiten. [Edwards, Reihenfolge und Zeitverhalten der seriellen Anfragen, Abschnitte 1.3–1.4](https://www.lesker.com/newweb/gauges/pdf/tic-instrument-controller-serial-communications.pdf#page=6)
- `read_pressure()` liefert die Diagnose samt Einheit, Zustand und Alarm. `read_pressure_pa()` liefert einen einzelnen Druckwert nur für eine aktive, störungsfrei meldende Messröhre mit Einheit Pa. Zustand 4 bedeutet bei aktiven Messröhren Alarm, Zustand 11 bedeutet eingeschaltet. [Edwards, Abschnitte 1.7.4 und 1.7.7](https://www.lesker.com/newweb/gauges/pdf/tic-instrument-controller-serial-communications.pdf)
- `read_turbo_faults()` führt TIC-Meldung und nEXT-Statuswort zusammen. Die Einzelabfragen und ihre Rohantworten bleiben für die Diagnose verfügbar. Die beiden Werte entstehen nacheinander.

## Messungen am Aufbau

Im [Testnotebook](test_vacuum.ipynb) genügt nach der Einrichtung die Zelle **Druck ablesen** für eine Einzelmessung. Am TIC auf COM13 lieferte Kanal 1 **2,283 × 10⁻⁶ Pa** bzw. **2,283 × 10⁻⁸ mbar**. Die unbelegten Kanäle 2 und 3 meldeten Zustand 0 und den Rohwert `9.9e+09`. Der Einzelwertzugriff weist solche Kanäle zurück.

Das Notebook enthält außerdem Messungen von Drehzahl, Leistung und Temperaturen sowie die getrennt abgefragten Diagnosen von TIC und Pumpen. Für die nXDS meldete der TIC 222 W. Der Wert beschreibt die vom Controller gemeldete Vorpumpenleistung. Für Objekt 910 lieferte der Aufbau den Zustandscode 5, während Edwards für dieses Objekt die Codes 0 bis 4 angibt. Der Treiber gibt 5 deshalb als unbekannt aus und erhält die Rohantwort. [Edwards, TIC-Serienprotokoll, Tabelle 1](https://www.lesker.com/newweb/gauges/pdf/tic-instrument-controller-serial-communications.pdf)
