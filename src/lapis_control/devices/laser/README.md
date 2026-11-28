# Flare NX in VS Code

Der Treiber steuert den Coherent Flare NX über einen seriellen oder virtuellen COM-Port. Das [Testnotebook](test_laser.ipynb) prüft den LAPIS-Aufbau mit externem Interlock und TTL-Trigger vom Oszilloskop.

## Test am LAPIS-Aufbau

Im Projekt-Kernel zuerst die Offline-Tests ausführen. Für einen Hardwaretest den COM-Port in der gewählten Konfigurationsdatei prüfen und die betreffende Zelle einzeln aktivieren.

1. **Interlock prüfen.** Den Status bei offenem und anschließend bei geschlossenem Interlock lesen. Im Controller-Fehlerregister `SYST:FAUL` bedeutet Bit 19 „offen“ und Bit 18 „Interlockfehler“. Bleibt nach dem Schließen nur Bit 18 gesetzt (`00040000`), entspricht die Notebook-Zelle „Gespeicherten Interlockfehler quittieren“ dem Reset in der Hersteller-GUI (OEM 5.2.4). `reset_interlock_fault()` prüft Interlock, ENA und REM vor dem Reset und liest das Fehlerregister danach erneut. REM steuert die elektrische Freigabe über Utility-Pin 8. Der Sicherheitsinterlock hat einen eigenen Eingang.
2. **WaveGen prüfen.** Bei abgezogener Lasertriggerleitung erzeugt der Loopback-Test einen 0–5-V-Pulszug an CH1 und misst beide Pegel und die Pulsbreite. Vor dem Anschließen am Laser die Last und am Triggereingang den tatsächlichen Ruhepegel prüfen.
3. **Modus prüfen.** Bei TTL-Low setzt `configure_single_shot()` den Laser auf OFF, prüft vor einem Moduswechsel das Triggerbit und liest Modus 3 zurück.
4. **Einzelpuls prüfen.** Erst mit nachgewiesener Einzelpulsquelle und manuell freigegebenem Interlock den Scharftest ausführen. `arm_single_shot()` prüft bei aktivem Handshake den Modus, ENA, REM, Softwaretrigger, Fehlerregister und Standby mit inaktivem Triggereingang. Bei unklarem Ergebnis des ENA-Schreibbefehls versucht der Treiber die Abschaltung und schließt den Port. Das Notebook wartet auf den externen Puls und vergleicht den Kopfzähler vor der Freigabe mit dem Stand nach bestätigtem OFF. Erwartet wird eine Differenz von eins.

Der automatische Test `python -m unittest discover -s src/lapis_control/devices/laser/tests -v` läuft vom Repository-Root ohne Hardware. Ein Tastendruck auf *Single* begrenzt beim DSOX1204G die Aufnahme. Der implementierte WaveGen-Pulsmodus erzeugt einen periodischen Pulszug. Für den scharfen Test muss die Quelle einen einzelnen Puls liefern können.

**Herstellerabgleich.** Das OEM-Handbuch 1306058 AE beschreibt Interlock und Quittierung in Abschnitt 5.2.4, Single Shot in 6.3.2.3, REM in 6.3.3 und den Pulszähler in 6.3.7. Die Registerbelegung steht in Tabellen 8-2 und 8-4. Die beigefügte Tabelle *User Command Set*, Blatt *User Commands*, nennt 32-Bit-Hexwerte für `SYST:STAT` und `SYST:FAUL` (Zeilen 15/16), `uint8` für den Pulsmodus (Zeile 23), die Schalterformate für ENA, REM und TRI (Zeilen 27–29) sowie `uint32` für `HEAD:PULS:COUNT` (Zeile 42). Die parameterlose Abfrage liest den rücksetzbaren Kopfzähler. Die Tests lassen den Zähler unangetastet.

## Installation unter Windows

Die Desktop-Anwendung bietet zusätzlich einen getrennten WaveGen-Versuch mit
zeitbegrenzter periodischer Ausgabe und anschließender Zählerprüfung. Für eine
hardwareseitig begrenzte Einzelausgabe steht der Modus mit externer Quelle zur
Verfügung. Die gespeicherten Ergebnisse dieses Notebooks stammen aus dem
extern ausgelösten Versuch. [Desktop-Bedienung](../../../../README.md#am-arbeitsplatz)

1. Das LAPIS-Projekt öffnen. Dieser Treiber liegt unter `src/lapis_control/devices/laser/`.
2. Python 3.10 oder neuer und die Python-Erweiterung für VS Code installieren.
3. Für die gemeinsame Umgebung der Anleitung in der Projekt-README folgen. Für eine separate Treiberumgebung die folgenden Befehle aus diesem Ordner ausführen.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Über „Python: Select Interpreter“ in der Befehlspalette den Interpreter unter `.venv\Scripts\python.exe` auswählen.

## Vorhandenen Statusaufruf starten

Der geprüfte Controller ist direkt über USB an COM10 angeschlossen. Eine andere Anwendung, die denselben Port belegt, vorher schließen.

```powershell
.\.venv\Scripts\python.exe flare_nx.py COM10 --interface USB
```

Der Aufruf öffnet den Port, aktiviert und prüft den Protokoll-Handshake und liest Temperatur und Pulsmodus. Beim Verlassen versucht der Treiber, den Laser auszuschalten und den Aus-Zustand zu bestätigen. Anschließend schließt er den Port. Der Statusaufruf enthält damit auch Schreibbefehle.

Optional akzeptiert der Aufruf `--interface SERIAL` oder `--interface USB`. Der Wert bezeichnet die am Laser verwendete Schnittstelle. Die Auswahl muss zur Verbindung und zur Befehlssyntax der Firmware passen. Ohne diese Option wird `SYST:COM:HAND on` gesendet.

## Verbesserte Single-Shot-Konfiguration

In einer vorhandenen Anwendung nach erfolgreichem `connect()` die Methode `laser.configure_single_shot()` aufrufen. Der Ablauf ist:

1. `LD:SET:ENA off` senden und den Aus-Zustand abfragen.
2. Den aktuellen Modus mit `LD:PULS:MOD` lesen.
3. Bei Modus `3` die Methode erfolgreich beenden.
4. Bei einem anderen Benutzermodus und inaktivem Triggereingang `LD:PULS:MOD 3` senden und den Modus erneut prüfen. Die zusätzlich lesbaren Modi 0 und 1 führen zum Abbruch der automatischen Moduswahl.

So entfällt das erneute Schreiben von `3`, wenn der Controller bereits diesen Modus meldet. Die Konfiguration endet mit ausgeschaltetem Laser. Das Auslösen eines Einzelpulses ist in dieser Methode nicht implementiert.

Die Fotos zeigen einen erfolgreichen und einen fehlgeschlagenen wiederholten Modusbefehl. Die genaue Ursache von Fehler −200 ist damit noch offen. Die Änderung vermeidet den unnötigen Schreibvorgang.

## SCPI-Fehler verstehen

Die Fehlernamen sind aus dem bereitgestellten Handbuchfoto, Tabelle 9-6, übernommen. Eine Fehlerantwort wird als `FlareNxCommandError` weitergegeben. Sie enthält den Originaltext, die zugeordnete Fehlernummer, den Fehlernamen und den gesendeten Befehl.

Beispiel:

```text
Flare NX rejected the command: ERR-200 [SCPI_ERROR_EXECUTION_ERROR, code=-200] | Sent: 'LD:PULS:MOD 3'
```

| Code | Name | Bedeutung |
| --- | --- | --- |
| −100 | SCPI_ERROR_COMMAND_ERROR | Allgemeiner Befehlsfehler |
| −102 | SCPI_ERROR_SYNTAX_ERROR | Syntaxfehler |
| −109 | SCPI_ERROR_PARAMETER_MISSING | Parameter fehlt |
| −200 | SCPI_ERROR_EXECUTION_ERROR | Ausführung fehlgeschlagen |
| −203 | SCPI_ERROR_COMMAND_PROTECTED | Befehl geschützt |
| −220 | SCPI_ERROR_INVALID_PARAMETER | Ungültiger Parameter |
| −221 | SCPI_ERROR_SETTINGS_CONFLICT | Einstellungskonflikt |
| −241 | SCPI_ERROR_DEVICE_UNAVAILABLE | Gerät nicht verfügbar |
| −250 | SCPI_ERROR_BYTE_ORDER_ERROR | Fehler der Byte-Reihenfolge |
| −256 | SCPI_ERROR_FILE_DOES_NOT_EXIST | Datei existiert nicht |
| −257 | SCPI_ERROR_FILE_NOT_NAMED | Dateiname fehlt |
| −310 | SCPI_ERROR_SYSTEM_ERROR | Systemfehler |
| −321 | SCPI_ERROR_OUT_OF_MEMORY | Speicher erschöpft |
| −350 | SCPI_ERROR_QUEUE_OVERFLOW | Warteschlange übergelaufen |
| −400 | SCPI_ERROR_QUERY_UNAVAILABLE | Abfrage nicht verfügbar |
| −500 | SCPI_ERROR_NO_ANSWER_WANTED | Antwort nicht gewünscht |
| −600 | SCPI_ERROR_TIMEOUT | Zeitüberschreitung |
| −610 | SCPI_ERROR_EXECUTION_TIMEOUT | Zeitüberschreitung bei Ausführung |
| 0 | SCPI_ERROR_NONE | Kein SCPI-Fehler |

Die Schreibweisen `ERR-200`, `ERR -200` und `ERR 200` werden derselben Fehlernummer −200 zugeordnet. Unbekannte numerische Fehler behalten ihre Nummer und erhalten `UNKNOWN_SCPI_ERROR`. Fehler werden an den Aufrufer weitergegeben. Es erfolgt kein automatischer Wiederholungsversuch und kein automatischer Wechsel über Modus 12.

Für die Auswertung in einer bestehenden GUI:

```python
from lapis_control.devices.laser.flare_nx import FlareNxError, FlareNxCommandError

try:
    laser.configure_single_shot()  # laser ist die bestehende, verbundene Instanz
except FlareNxCommandError as error:
    print(error)
    print(error.command, error.code, error.error_name)
except FlareNxError as error:
    print(error)
```

Lokale Prüfungen, etwa „Single-shot mode was not accepted“, können ebenfalls `FlareNxCommandError` auslösen. In diesem Fall ist `code` gleich `None`. Ein Lesetimeout ohne Controllerantwort erscheint als `FlareNxTimeoutError`.

## Einstellungen und Grenzen

Der Treiber verwendet die bisherigen Einstellungen aus dem hochgeladenen Code: 9600 Baud, 8N1, CR LF, mindestens 100 ms Befehlsabstand und 1 s Lese-/Schreibtimeout. `threading.RLock()` erlaubt die verschachtelten Aufrufe.

Die Handshake-Abfrage akzeptiert ohne Schnittstellenauswahl ausschließlich ON/OFF beziehungsweise 1/0. Wenn der Controller eine Antwort mit Schnittstellenbezeichnung liefert, kann die explizite Schnittstellenauswahl erforderlich sein.

Die implementierten Befehle wurden mit der eingescannten Befehlstabelle auf den gedruckten Seiten 59–64 und dem OEM-Handbuch 1306058 AE abgeglichen. Beide Fassungen haben unterschiedliche Kapitel- und Seitenzählungen. Der bisherige Empfangspuffer wird vor jedem Befehl gelöscht; verspätete Antworten können dabei verloren gehen.

Die Statuszelle wurde am 24. November 2026 mit Firmware V3.0.24 an COM10 geprüft. Der Controller wies `SYST:COM:HAND on` mit `ERR-220` zurück. Mit `handshake_interface: "USB"` meldete die USB-Handshake-Abfrage `ON`. Bei offenem Interlock lieferte `SYST:FAUL` den Wert `000C0000` (offen und Interlockfehler), ENA und REM meldeten jeweils `OFF`. Der Kopfzähler gab `2.1322 * 10^4` für 21 322 Pulse zurück. Die Statuszelle einschließlich bestätigter Abschaltung lief durch. Der Scharftest mit externem Puls steht aus.

## Dateien

- `flare_nx.py` enthält den Treiber und den Statusaufruf.
- `requirements.txt` enthält die Python-Abhängigkeit.
- `README.md` enthält Einrichtung, Ablauf und Fehlerbeschreibung.

## Handbuchabgleich vom 13. November 2026

| Prüfung | Fundstelle und Ergebnis |
| --- | --- |
| 9600 Baud, 8N1, keine Flusskontrolle | Eingescannte Fassung, S. 59, bestätigt. 9600 ist der Standardwert. |
| ASCII, Leerzeichen vor Wert, CR LF | Eingescannte Fassung, S. 60, bestätigt. Groß-/Kleinschreibung erlaubt. |
| Rückabfrage nach Schreibbefehlen | Eingescannte Fassung, S. 60, vorgeschrieben. |
| Mindestens 100 ms zwischen Befehlen | OEM 1306058 AE, Abschnitt 6.2.2, S. 51. |
| LD:PULS:MOD mit 2, 3, 5, 12 | Eingescannte Fassung, S. 62, bestätigt. Antwortformat float. |
| LD:SET:ENA mit ON/OFF oder 1/0 | Eingescannte Fassung, S. 63, bestätigt. |
| TEMP:LMT:READ als uint32 in m°C | Eingescannte Fassung, S. 64, bestätigt. |
| SCPI-Fehlerzuordnung | OEM 1306058 AE, Tabelle 8-6, und *User Command Set*, Blatt *SCPI Errors*. |
| Schnittstellenspezifischer Handshake | OEM 1306058 AE, Abschnitt 8.1.5.1, nennt ausdrücklich SYST:COM:HAND:SERIAL ON und SYST:COM:HAND:USB ON. |

Die Tabellenzelle auf S. 62 der eingescannten Fassung stellt SERIAL/USB unter „Parameter“ dar. Das OEM-Handbuch enthält die ausgeschriebenen Befehle mit Doppelpunkt. Der Treiber verwendet für eine explizit ausgewählte Schnittstelle diese Schreibweise und fragt denselben Befehl zurück. Ohne Auswahl bleibt der auf der Terminal-Startseite dokumentierte Befehl SYST:COM:HAND on aktiv. Für den LAPIS-Aufbau ist deshalb USB in der Konfiguration eingetragen.

Vor dem Aufruf von `configure_single_shot()` muss der externe Trigger bereits TTL-Low sein. Das OEM-Handbuch, S. 54, nennt diese Voraussetzung vor dem Setzen von Modus 3. Vor dem Moduswechsel wertet der Treiber das Triggerbit als Momentaufnahme aus. Die tatsächliche Spannung lässt sich am Aufbau messen.

Das OEM-Handbuch, S. 56, beschreibt LD:SET:REM. Bei aktivem Remote Enable soll LD:SET:ENA nicht verwendet werden. Die allgemeine Ein-/Aus-Steuerung fragt zuerst LD:SET:REM ab. Bei ON oder 1 sendet sie LD:SET:REM off und bestätigt durch erneute Abfrage OFF beziehungsweise 0. Erst dann sendet sie den angeforderten LD:SET:ENA-Befehl. Bei bereits ausgeschaltetem REM genügt die erste Abfrage.

Die Handbücher belegen keine allgemeine Regel, dass das erneute Setzen desselben Pulsmodus Fehler −200 auslösen muss. Die zusätzliche Modusabfrage vermeidet lediglich diesen unnötigen Schreibvorgang.

## Automatische REM-Abschaltung

Diese Prüfung gilt für turn_on(), turn_off(), configure_single_shot() über den dortigen turn_off()-Aufruf und disconnect() über dessen turn_off()-Aufruf. Sie läuft zusammen mit der Ein-/Aus-Steuerung unter demselben RLock.

Bei einer SCPI-Fehlermeldung, einem Timeout, einer ungültigen REM-Antwort oder weiterhin aktivem REM wird die Aktion mit einer Fehlermeldung abgebrochen. Eine fehlgeschlagene REM-Abfrage kann deshalb auch die Bestätigung des Ausschaltens verhindern. Beim disconnect() wird der Port weiterhin im finally-Block geschlossen und der Fehler weitergegeben.

Die REM-Abschaltung allein meldet den Laser noch nicht als ausgeschaltet. Dazu muss anschließend LD:SET:ENA off gesendet und dessen Zustand zurückgelesen werden. Eine serielle Freigabe erfolgt ausdrücklich über `turn_on()`, `set_laser_enabled(True)` oder `arm_single_shot()`. Die Einzelpulsfreigabe verlangt bereits ausgeschaltetes REM.

read_remote_enabled() steht auch für eine reine Zustandsabfrage zur Verfügung. Die Änderung setzt deine gewünschte serielle Steuerung voraus und deaktiviert bei Bedarf die Steuerung über Pin 8. Sie garantiert keinen Laserpuls; die Voraussetzungen des gewählten Pulsmodus gelten weiterhin.
