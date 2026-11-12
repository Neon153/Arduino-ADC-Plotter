# Leistung und Grenzen

Der AD7606 schafft 200 kSPS auf allen acht Kanälen. Mit dem Arduino Uno wird davon nur ein Teil
erreicht. Die Grenze setzt dabei weniger die Rechenleistung als drei andere Engpässe.

> Alle Zahlen in diesem Dokument sind aus Datenblatt und Taktzyklen **berechnete Schätzungen**.
> Verbindlich ist, ob die Firmware Overruns meldet und welche Rate der Viewer im Stream-Modus
> tatsächlich misst.

## Die drei Engpässe

### 1. RAM: 2 KB

Der Burst-Modus speichert die Messwerte, bevor er sie sendet. Für den Puffer bleiben 1280 Byte,
also **640 Werte**:

| Aktive Kanäle | Samples pro Burst |
|---|---|
| 1 | 640 |
| 2 | 320 |
| 4 | 160 |
| 8 | 80 |

Bei 8 Kanälen und 25 kSa/s dauert eine Aufnahme damit nur 3,2 ms. Das reicht für schnelle
Vorgänge, aber nicht, um mehrere Perioden eines 50-Hz-Signals bei hoher Rate zu erfassen.

### 2. Serielle Verbindung: ca. 100 kB/s

Bei 1 MBaud und 10 Bit pro Byte (Start, 8 Daten, Stopp) passen 100 000 Byte/s durch die Leitung.
Ein STREAM-Frame hat 7 Byte Rahmen plus 2 Byte pro Kanal:

```
max. Stream-Rate ≈ 100 000 / (7 + 2 · n)  Samples/s
```

Dazu kommt, dass jedes Byte im Uno Rechenzeit kostet (Aufruf von `Serial.write()` und ein
UART-Interrupt, zusammen geschätzt 5 µs). Bei den hier relevanten Raten bleibt die Leitung aber
der Engpass.

### 3. Wandlung und Auslesen

```
Wandlung:  4 µs (ohne Oversampling)
Auslesen:  ca. 3,5 µs pro Kanal bis zum höchsten aktiven Kanal (2 × 8 Bit SPI bei 8 MHz + Schleife)
Overhead:  ca. 5 µs (Warten auf den Takt, CONVST, CS, Overrun- und Abbruchprüfung)
```

Weil DOUTA die Kanäle in fester Reihenfolge ausgibt, zählt der **höchste** aktive Kanal, nicht
die Anzahl. V1 + V2 ist schneller als V1 + V8.

## Geschätzte Höchstraten

Mit den Kanälen V1 … Vn, ohne Oversampling:

| Kanäle | Burst: min. Periode | Burst: max. Rate | Burst-Länge bei max. Rate | Stream: Frame | Stream: max. Rate |
|---|---|---|---|---|---|
| 1 | ≈ 12,5 µs | ≈ 80 kSa/s | 640 Samples = 8 ms | 9 Byte | ≈ 11 kSa/s |
| 2 | ≈ 16 µs | ≈ 62 kSa/s | 320 Samples = 5 ms | 11 Byte | ≈ 9 kSa/s |
| 4 | ≈ 23 µs | ≈ 43 kSa/s | 160 Samples = 3,7 ms | 15 Byte | ≈ 6,7 kSa/s |
| 8 | ≈ 37 µs | ≈ 27 kSa/s | 80 Samples = 3 ms | 23 Byte | ≈ 4,3 kSa/s |

Der Viewer zeigt diese Schätzung für die aktuellen Einstellungen unter „Erfassung“ an.

## Aliasing

Der AD7606 hat einen analogen Anti-Aliasing-Filter mit −3 dB bei 22 kHz. Er ist auf die volle
Abtastrate von 200 kSPS ausgelegt; dort liegt die Nyquist-Frequenz bei 100 kHz, weit oberhalb der
Filtergrenze.

**Mit dem Uno wird viel langsamer abgetastet.** Bei 4 kSa/s liegt die Nyquist-Frequenz bei 2 kHz.
Signalanteile zwischen 2 kHz und über 22 kHz passieren den Filter nahezu ungedämpft und erscheinen
im Ergebnis als falsche, niedrigere Frequenzen. Das ist kein Fehler der Software, sondern eine
Eigenschaft jeder Abtastung.

Im Demo-Modus sieht man den Effekt an V2 (Sinus 1 kHz): Bei 500 µs Periode (2 kSa/s) wird genau
mit der doppelten Signalfrequenz abgetastet, je nach Phasenlage erscheint dann eine flache Linie.

Gegenmaßnahmen:

- **Abtastrate an das Signal anpassen:** mindestens das 2,5-Fache der höchsten Frequenz, die im
  Signal tatsächlich vorkommt, auch der unerwünschten (Störungen, Schaltflanken).
- **Externer Tiefpass** vor dem Eingang, z. B. ein RC-Glied mit einer Grenzfrequenz unter der
  halben Abtastrate. Der Eingangswiderstand von 1 MΩ erlaubt einen relativ hochohmigen Widerstand,
  der dann aber den Verstärkungsfehler erhöht (siehe [hardware.md](hardware.md#eingangsbeschaltung)).
- **Oversampling** wirkt als Mittelwertfilter und dämpft hohe Frequenzen. Es ersetzt aber keinen
  richtigen Tiefpass, weil ein Mittelwertfilter Lücken im Sperrbereich hat.

## Oversampling

Mit OS0…OS2 lässt der AD7606 jede Wandlung mehrfach durchlaufen und mittelt die Ergebnisse.
Das senkt das Rauschen, verlängert aber die Wandlungszeit:

| Faktor | Wandlungszeit (Datenblatt, max.) | max. Rate mit 1 Kanal (geschätzt) |
|---|---|---|
| 1× | 4 µs | ≈ 80 kSa/s |
| 2× | 9,1 µs | ≈ 57 kSa/s |
| 4× | 18,8 µs | ≈ 36 kSa/s |
| 8× | 39 µs | ≈ 21 kSa/s |
| 16× | 78 µs | ≈ 12 kSa/s |
| 32× | 158 µs | ≈ 6 kSa/s |
| 64× | 315 µs | ≈ 3 kSa/s |

Laut Datenblatt steigt der Signal-Rausch-Abstand im ±10-V-Bereich von typisch 90 dB ohne
Oversampling auf typisch 95,5 dB mit 16× Oversampling. Die −3-dB-Bandbreite sinkt dabei mit
steigendem Faktor (Tabelle im Abschnitt *Digital Filter* des Datenblatts).

Faustregel: Für langsame Signale (Temperaturen, Gleichspannungen, Netzfrequenz) Oversampling
nutzen; für schnelle Vorgänge ausschalten.

## Auflösung

| Messbereich | 1 LSB | Effektive Auflösung ohne Oversampling |
|---|---|---|
| ±5 V | 152,6 µV | ≈ 14,5 Bit (SNR typ. 89 dB) |
| ±10 V | 305,2 µV | ≈ 14,7 Bit (SNR typ. 90 dB) |

Effektive Auflösung nach ENOB = (SNR − 1,76 dB) / 6,02 dB. Die letzten ein bis zwei Bit sind also
Rauschen; das ist normal und lässt sich mit Oversampling verbessern.

## Wann ein anderes Board sinnvoll ist

Der Uno ist für Messungen bis in den niedrigen kHz-Bereich gut geeignet. Wer mehr will, braucht
vor allem mehr RAM und eine schnellere Verbindung zum PC.

| Board | RAM | Verbindung zum PC | Bemerkung |
|---|---|---|---|
| Arduino Uno (ATmega328P) | 2 KB | USB-Seriell, 1 MBaud ≈ 0,1 MB/s | dieses Projekt |
| RP2040 / RP2350 | 264 / 520 KB | natives USB Full Speed | PIO kann DOUTA und DOUTB gleichzeitig lesen, DMA |
| ESP32-S3 | 512 KB | natives USB Full Speed | zusätzlich WLAN |
| STM32F4 | je nach Typ 64–256 KB | USB Full Speed | SPI mit DMA, Timer-getriggerte Wandlung |
| Teensy 4.x | 1 MB | USB High Speed (480 Mbit/s) | einziges Board in der Liste, das 200 kSPS × 8 Kanäle (3,2 MB/s) kontinuierlich streamen kann |

Rechnung für die volle Rate: 200 000 Samples/s × 8 Kanäle × 2 Byte = 3,2 MB/s. Das übersteigt
USB Full Speed (12 Mbit/s brutto, in der Praxis rund 1 MB/s). Mit Full-Speed-Boards ist die volle
Rate deshalb nur im Burst-Modus möglich, dann aber mit Puffern von mehreren zehntausend Samples.

Für die volle Geschwindigkeit sollten beide Datenleitungen genutzt werden: Mit DOUTA (V1–V4) und
DOUTB (V5–V8) parallel und 20 MHz SCLK dauert das Auslesen aller acht Kanäle nur 3,2 µs.

Die Portierung betrifft nur die Firmware. Das Protokoll und der Viewer können unverändert bleiben;
lediglich `BUF_WORDS` im INFO-Frame wird größer, und der Viewer passt seine Grenzen daran an.
