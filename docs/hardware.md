# Hardware

## Der AD7606 im Überblick

Der AD7606 von Analog Devices ist ein Datenerfassungsbaustein mit acht Kanälen. Er hat für jeden
Kanal eine eigene Eingangsstufe mit Track-and-Hold. Auf ein einziges Startsignal halten alle acht
Kanäle ihre Spannung **im selben Moment** fest, anschließend werden sie nacheinander von einem
16-Bit-SAR-Wandler digitalisiert. Phasenbeziehungen zwischen den Kanälen bleiben dadurch erhalten,
was z. B. bei Strom- und Spannungsmessungen in Drehstromnetzen wichtig ist.

| Eigenschaft | Wert |
|---|---|
| Kanäle | 8, simultan abgetastet |
| Auflösung | 16 Bit, Zweierkomplement |
| Max. Abtastrate | 200 kSPS auf allen Kanälen |
| Messbereiche | ±10 V (RANGE = 1) oder ±5 V (RANGE = 0), echt bipolar aus einer 5-V-Versorgung |
| Eingangswiderstand | 1 MΩ, unabhängig von der Abtastrate |
| Überspannungsschutz | Eingangs-Clamp bis ±16,5 V |
| Anti-Aliasing-Filter | analog, 2. Ordnung, −3 dB bei 22 kHz |
| Wandlungszeit | 4 µs für alle 8 Kanäle (ohne Oversampling) |
| Oversampling | 2× bis 64×, digitaler Mittelwertfilter im Chip |
| Referenz | intern 2,5 V oder extern |
| Versorgung | AVCC 4,75 … 5,25 V, Logikpegel VDRIVE 2,3 … 5,25 V |
| Schnittstelle | parallel 16 Bit, parallel 8 Bit (Byte-Modus) oder seriell |

Dieses Projekt nutzt die **serielle Schnittstelle**. Sie braucht nur drei Datenleitungen
(SCLK, DOUTA, CS) und lässt sich direkt an den SPI-Block des Uno anschließen.

## Benötigte Teile

- Arduino Uno R3 (oder ein anderes Board mit ATmega328P bei 16 MHz, z. B. Nano)
- AD7606-Modul (verbreitet als Breakout-Platine mit Stiftleisten)
- Jumperkabel, möglichst kurz
- USB-Kabel zum PC

## Verdrahtung

| AD7606-Pin | Arduino Uno | AVR-Port | Funktion |
|---|---|---|---|
| CONVST A **und** CONVST B | D7 | PD7 | steigende Flanke startet die Wandlung; A und B verbinden, damit alle 8 Kanäle gleichzeitig abtasten |
| BUSY | D2 | PD2 | HIGH während der Wandlung |
| CS | D10 | PB2 | Chip Select, LOW = Daten lesen |
| RD/SCLK | D13 (SCK) | PB5 | SPI-Takt |
| DB7/DOUTA | D12 (MISO) | PB4 | serieller Datenausgang, liefert alle 8 Kanäle nacheinander |
| DB8/DOUTB | – | – | nicht angeschlossen |
| RESET | D8 | PB0 | HIGH-Puls setzt den Chip zurück |
| RANGE | D9 | PB1 | LOW = ±5 V, HIGH = ±10 V |
| OS0, OS1, OS2 | D3, D4, D5 | PD3 … PD5 | Oversampling-Faktor 2^n |
| PAR/SER/BYTE SEL | VDRIVE (HIGH) | – | wählt die serielle Schnittstelle |
| STBY | VDRIVE (HIGH) | – | normaler Betrieb (LOW = Standby) |
| REF SELECT | VDRIVE (HIGH) | – | interne Referenz (meist auf dem Modul fest eingestellt) |
| VDRIVE | 5 V | – | Logikpegel = Pegel des Uno |
| AVCC | 5 V | – | Analogversorgung |
| AGND, DGND | GND | – | gemeinsame Masse |

Die Pins D2, D7, D10 und D8 werden in der Firmware über direkte Portzugriffe angesprochen.
Wer sie ändert, muss auch die Makros am Anfang von `ad7606_daq.ino` anpassen
(siehe [firmware.md](firmware.md#portierung)).

## Modul-Einstellungen

AD7606-Module unterscheiden sich je nach Hersteller. Vor dem ersten Einschalten Folgendes prüfen:

1. **Parallel oder seriell.** Viele Module sind ab Werk auf die parallele Schnittstelle eingestellt,
   meist über einen 0-Ω-Widerstand oder eine Lötbrücke an PAR/SER/BYTE SEL. Für dieses Projekt
   muss der Pin HIGH sein. Bleibt das Modul im Parallelmodus, kommen nur konstante Werte an
   (typisch 0 oder −1).
2. **Fest verdrahtete Steuerpins.** Manche Module legen RANGE, OS0…OS2 oder STBY über Widerstände
   fest auf einen Pegel. Die Firmware kann solche Pins nicht umschalten. Entweder die Widerstände
   entfernen oder die Einstellung im Viewer passend zur Hardware wählen. Ein falsch eingestellter
   Messbereich zeigt sich als Faktor 2 in allen Messwerten.
3. **CONVST A und B.** Auf manchen Modulen sind sie getrennt herausgeführt. Beide an D7 anschließen.
   Bleibt CONVST B offen, werden V5…V8 nicht oder zu einem undefinierten Zeitpunkt gewandelt.

## Versorgung

Der AD7606 verlangt für AVCC **4,75 V bis 5,25 V**. Wird der Uno nur über USB versorgt, liegt
seine 5-V-Schiene je nach Rechner, Kabel und Hub oft knapp unter 5 V, manchmal auch unter 4,75 V.
Die Spannung an AVCC deshalb einmal nachmessen. Liegt sie zu niedrig, das Modul aus einem
separaten 5-V-Netzteil versorgen und die Massen verbinden.

VDRIVE bestimmt die Logikpegel und muss zum Mikrocontroller passen: 5 V beim Uno, 3,3 V bei
einem 3,3-V-Board wie ESP32 oder RP2040/RP2350.

## Eingangsbeschaltung

- Jeder Kanal hat einen Eingang Vx und einen Bezugspin VxGND. VxGND mit AGND verbinden.
- **Unbenutzte Eingänge auf GND legen.** Offene Eingänge driften und können über Übersprechen
  benachbarte Kanäle stören.
- Der Eingangswiderstand von 1 MΩ bildet zusammen mit dem Innenwiderstand der Quelle einen
  Spannungsteiler. Als Faustregel ergibt ein Quellwiderstand R einen zusätzlichen Verstärkungsfehler
  von etwa R / 1 MΩ, also rund 1 % bei 10 kΩ. Für genaue Messungen an hochohmigen Quellen einen
  Impedanzwandler vorschalten.
- Der interne Clamp verträgt bis ±16,5 V. Signale außerhalb des Messbereichs werden trotzdem
  abgeschnitten (Code +32767 bzw. −32768).
- Zur Wahl der Abtastrate und zum Aliasing siehe
  [Leistung und Grenzen](leistung-und-grenzen.md#aliasing).

## Leitungsführung

- Die SPI-Leitungen laufen mit 8 MHz. Mit Jumperkabeln bis etwa 20 cm funktioniert das in der
  Regel. Bei Bitfehlern zuerst die Kabel kürzen, dann den SPI-Takt in der Firmware senken
  (`SPISettings spiCfg(4000000, …)`).
- AGND und DGND des Moduls direkt mit GND des Uno verbinden. Analoge Signalmasse und die Masse
  der Messobjekte sternförmig am Modul zusammenführen, nicht über den Uno.
