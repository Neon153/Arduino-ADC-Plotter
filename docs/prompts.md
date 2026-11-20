# Entstehung des Projekts – die Prompts

Code und Dokumentation dieses Projekts sind im Dialog mit **Claude** (Anthropic, Modell Claude Opus 5.5)
entstanden. Diese Seite hält fest, welche Prompts dazu geführt haben und was jeweils dabei herauskam.

Die Prompts stehen hier **wörtlich**, einschließlich Tippfehlern, weil genau diese Formulierungen
zum Ergebnis geführt haben. Claudes Antworten sind nicht abgedruckt; ihr Ergebnis ist der Code und
die Dokumentation in diesem Repository. Zu jedem Prompt steht außerdem, welche Entscheidungen Claude
getroffen hat, ohne dass sie im Prompt verlangt waren. Diese Punkte sollte man kennen, wenn man den
Code verändert.

## Übersicht

| # | Datum | Prompt (Kurzform) | Ergebnis |
|---|---|---|---|
| 1 | 11.11.2026 | AD7606 mit dem Arduino in Betrieb nehmen | `firmware/ad7606_serial` |
| 2 | 11.11.2026 | Code mit Ausschnitten erklären | Erklärung, später Grundlage für [firmware.md](firmware.md) |
| 3 | 11.11.2026 | PC-Programm mit grafischer Oberfläche, Uno sampelt erst und sendet dann | `firmware/ad7606_daq`, `viewer/` |
| 4 | 12.11.2026 | Auf GitHub pushen und dokumentieren | Repository-Struktur, `README.md`, `docs/` |
| 5 | 21.11.2026 | Prompts zur Doku hinzufügen | diese Seite |

---

## 1. Inbetriebnahme des AD7606

> Kennst du den Ad7606 ADC wandler.
> Schreib mir ein kleines Programm für Arduinon um diesen in Betrieb zu nehmen

**Ergebnis:** `firmware/ad7606_serial/ad7606_serial.ino`. Der Sketch misst alle acht Kanäle und gibt
sie als Text im Serial Monitor aus.

**Entscheidungen von Claude:**
- Serielle Schnittstelle des AD7606 über den Hardware-SPI des Uno statt der parallelen
  Schnittstelle: drei Datenleitungen statt sechzehn.
- CONVST A und B zusammengelegt, damit alle acht Kanäle gleichzeitig abtasten.
- SPI-Modus 3, abgeleitet aus dem Timing im Datenblatt (Daten wechseln mit der fallenden Flanke).
- Hinweise auf typische Stolperfallen: Module ab Werk im Parallelmodus, Pegel an VDRIVE, STBY.

**Fehler, später korrigiert:** Ein Kommentar gab die Wandlungszeit bei 64-fachem Oversampling mit
etwa 15 ms an. Richtig sind etwa 315 µs. Claude hat das in Schritt 3 selbst angemerkt und in
Schritt 4 im Code korrigiert. Auf die Funktion hatte der Fehler keinen Einfluss; der Timeout war
nur unnötig lang.

## 2. Erklärung des Codes

> Erkläre den Code mit den passenden pasagen aus dem Code

**Ergebnis:** eine Erklärung Abschnitt für Abschnitt mit den zugehörigen Codestellen: Pins und
Konfiguration, Initialisierung, Start der Wandlung, Warten auf BUSY, Auslesen über SPI,
Umrechnung von Zweierkomplement in Volt, dazu ein Timing-Diagramm. In überarbeiteter Form steht
das jetzt in [firmware.md](firmware.md#ablauf-eines-samples).

## 3. PC-Programm mit grafischer Oberfläche

> Wäre es möglich ein PC Programm schreiben, für Linux und Windows das auf einer Grfaischen
> Oberfläche die empfangenen Daten vom ADC Wandler visualisiert. Ich weiß die Rechenleistung
> könnte nicht reiche, deswegen soll der Uno erst über eine gewisse zeit samplen bis es dann über
> den USB Port die Daten an den PC sendet und das Programm dort auf dem PC die Daten Visualisiert

**Ergebnis:** die Firmware `firmware/ad7606_daq/ad7606_daq.ino` und das PC-Programm
`viewer/ad7606_viewer.py`.

**Wie die Vorgabe hinterfragt wurde:** Der Prompt vermutete, dass die Rechenleistung des Uno nicht
reicht. Die eigentlichen Engpässe sind aber der RAM (2 KB, also nur 80 Samples bei 8 Kanälen) und
die Bandbreite der seriellen Verbindung. Claude hat deshalb den gewünschten Burst-Modus umgesetzt
und zusätzlich einen Stream-Modus für lange Aufzeichnungen eingebaut. Die Zahlen dazu stehen in
[Leistung und Grenzen](leistung-und-grenzen.md).

**Entscheidungen von Claude:**
- **Abtasttakt aus Timer1 im CTC-Modus** statt einer Warteschleife, damit der Abstand zwischen
  den Samples unabhängig von der Laufzeit des Codes stimmt.
- **Direkte Portzugriffe** im zeitkritischen Pfad statt `digitalWrite()` (125 ns statt rund 4 µs).
- **1 MBaud** statt 115200 Baud: bei 16 MHz exakt einstellbar und fast neunmal schneller.
- **Binärprotokoll mit Sync-Muster und Prüfsumme** statt Textausgabe, weil Text die Datenmenge
  etwa verdreifacht. Siehe [protokoll.md](protokoll.md).
- **Overrun-Erkennung:** Die Firmware meldet, wenn die gewünschte Rate nicht erreicht wird,
  statt stillschweigend falsche Zeitstempel zu liefern.
- **Python mit PySide6 und pyqtgraph** für die Oberfläche: läuft ohne Anpassung unter Linux und
  Windows und zeichnet auch lange Messreihen flüssig.
- **Eigener Lese-Thread mit Queue**, damit die Oberfläche nie auf die serielle Schnittstelle wartet.
- **Demo-Modus** mit simuliertem Gerät, das dasselbe Protokoll spricht. Damit ließ sich das
  Programm ohne Hardware testen.

**Prüfung:** Die Firmware wurde mit avr-gcc für den ATmega328P kompiliert (74 % RAM, keine
Warnungen). Das PC-Programm wurde automatisch im Demo-Modus durchgespielt. Dabei fiel ein Fehler im
Simulator auf: Er erzeugte ein nicht zusammenhängendes numpy-Array, wodurch der Stream-Modus
abbrach. Der Fehler wurde vor der Auslieferung behoben.

## 4. GitHub und Dokumentation

> https://github.com/Neon153/Arduino-ADC-Plotter.git
>
> Bitte push alles Programmier Datein auf Github, und schreibe eine Dokumentation zu diesem Projekt

**Ergebnis:** die Ordnerstruktur dieses Repositorys, `README.md` mit Schnellstart und
Systemdiagramm, die sechs Dokumente in `docs/` und die Screenshots aus dem Demo-Modus.

**Entscheidungen von Claude:**
- Jeder Sketch in einem eigenen Ordner, weil die Arduino-IDE verlangt, dass Ordner und Sketch
  gleich heißen.
- Die Eckdaten des AD7606 in [hardware.md](hardware.md) vor dem Schreiben mit dem Datenblatt
  abgeglichen.
- Im README offen vermerkt, dass das Projekt noch nicht mit echter Hardware getestet ist und die
  Abtastraten berechnete Schätzungen sind.
- Alle internen Links und Sprungmarken der Dokumentation automatisch geprüft.

## 5. Diese Seite

> Füge zur Doku noch die relevanten Prompts hinzu die zum Ziel geführt haben und push es auf github

---

## Erfahrungen

- **Kurze Prompts haben gereicht**, weil die wichtigen Rahmenbedingungen genannt waren: das
  konkrete Bauteil (AD7606), das Board (Uno) und die Zielplattformen (Linux und Windows).
- **Die eigene Vermutung mitzuliefern hat geholfen.** Der Satz zur Rechenleistung in Prompt 3 gab
  Claude die Gelegenheit, die Annahme zu prüfen und die tatsächlichen Engpässe zu benennen. Ohne
  ihn wäre vermutlich nur der Burst-Modus entstanden.
- **Schrittweise vorgehen:** erst ein Minimalprogramm zur Inbetriebnahme, dann verstehen, dann
  ausbauen. Der kleine Test-Sketch ist bei der Fehlersuche an der Hardware weiterhin nützlich
  (siehe [Fehlersuche](fehlersuche.md)).
- **Ergebnisse gegenprüfen:** Auch hier hat sich ein falscher Wert eingeschlichen
  (Wandlungszeit in Prompt 1). Datenblattwerte und berechnete Raten sollte man vor dem Einsatz
  selbst nachvollziehen.

## Mögliche nächste Prompts

- Messergebnisse vom echten Aufbau zurückmelden, z. B. tatsächliche Höchstraten, Rauschen bei
  kurzgeschlossenem Eingang oder Übertragungsfehler, und die Dokumentation daran anpassen lassen.
- Einen Trigger in die Firmware einbauen, damit der Run-Modus stehende Kurven zeigt.
- Eine FFT-Ansicht im Viewer ergänzen.
- Die Firmware auf einen RP2350 portieren, mit beiden Datenleitungen per PIO und größerem Puffer.
