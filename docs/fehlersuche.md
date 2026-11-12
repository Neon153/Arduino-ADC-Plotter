# Fehlersuche

Empfohlene Reihenfolge bei der ersten Inbetriebnahme:

1. `firmware/ad7606_serial` flashen und im Serial Monitor (115200 Baud) die Textausgabe prüfen.
   Einen Eingang auf GND legen (≈ 0 V), einen an 3,3 V vom Uno (≈ 3,3 V).
2. Erst wenn das stimmt, `firmware/ad7606_daq` flashen und den Viewer verwenden.

So lassen sich Hardware- und Softwareprobleme sauber trennen.

## Hardware und Messwerte

| Symptom | Wahrscheinliche Ursache | Abhilfe |
|---|---|---|
| Alle Kanäle zeigen konstant 0 oder −1 (bzw. 0 V oder −0,00015 V) | Modul im Parallelmodus; DOUTA nicht an D12 | PAR/SER/BYTE SEL auf HIGH (Lötbrücke/Widerstand am Modul), Verdrahtung prüfen |
| „BUSY-Timeout“ im Log | BUSY reagiert nicht: STBY auf LOW, CONVST nicht verbunden, keine Versorgung an AVCC, RESET hängt auf HIGH | STBY auf VDRIVE, CONVST A+B an D7, 5 V an AVCC messen |
| Nur V1–V4 plausibel, V5–V8 nicht | CONVST B nicht angeschlossen | CONVST A und B beide an D7 |
| Alle Werte um Faktor 2 falsch | RANGE auf dem Modul fest verdrahtet, Viewer auf anderem Bereich | Messbereich im Viewer an die Hardware anpassen oder Widerstand entfernen |
| Oversampling hat keine Wirkung | OS-Pins auf dem Modul fest verdrahtet | Widerstände an OS0…OS2 prüfen |
| Werte springen sporadisch um große Beträge, Bitmuster sehen verschoben aus | SPI-Signalqualität: Kabel zu lang, Masse schlecht | Kabel kürzen, gemeinsame Masse prüfen, SPI-Takt auf 4 MHz senken |
| Offener Eingang zeigt beliebige Werte | Hochohmiger Eingang driftet | Unbenutzte Eingänge auf GND legen |
| Messwerte etwas zu klein | Quellwiderstand bildet mit 1 MΩ Eingangswiderstand einen Teiler | Siehe [hardware.md](hardware.md#eingangsbeschaltung) |
| Grundrauschen deutlich über einigen LSB | Versorgung verrauscht (USB), lange ungeschirmte Leitungen | Separate 5-V-Versorgung, Oversampling, kürzere Leitungen |
| Signal zeigt Frequenzen, die es nicht gibt | Aliasing | Siehe [Leistung und Grenzen](leistung-und-grenzen.md#aliasing) |

## Verbindung und Viewer

| Symptom | Wahrscheinliche Ursache | Abhilfe |
|---|---|---|
| Port lässt sich nicht öffnen: „Access denied“ / „Zugriff verweigert“ (Windows) | Port ist von einem anderen Programm belegt | Serial Monitor/Plotter der Arduino-IDE schließen |
| „Permission denied: /dev/ttyACM0“ (Linux) | Benutzer nicht in der Gruppe `dialout` | `sudo usermod -aG dialout $USER`, ab- und wieder anmelden |
| Verbunden, aber das Gerät meldet sich nie | Falsche Firmware (`ad7606_serial` statt `ad7606_daq`) oder falscher Port | `ad7606_daq` flashen, Port prüfen |
| Hochladen aus der IDE schlägt fehl | Viewer hält den Port offen | Im Viewer zuerst „Trennen“ |
| „⚠ … fehlerhafte Frames verworfen“ | Übertragungsfehler: USB-Hub, schlechtes Kabel, überlasteter USB-Seriell-Wandler | Direkt am Rechner anschließen, anderes Kabel. Treten die Fehler nur bei hohen Stream-Raten auf, die Rate senken |
| „⚠ Overruns“ im Burst | Periode kürzer als Wandlung + Auslesen | Periode erhöhen, weniger Kanäle oder niedrigere Kanalnummern, Oversampling reduzieren |
| „⚠ Samples verspätet“ im Stream | Die serielle Verbindung schafft die Rate nicht | Periode erhöhen oder Kanäle reduzieren; für hohe Raten den Burst-Modus verwenden |
| Empfangene Stream-Rate deutlich unter der Sollrate, aber keine Overrun-Meldung | PC kommt nicht hinterher (sehr selten) | Stream-Fenster verkleinern, andere Programme schließen |
| Qt-Fehler „Could not load the Qt platform plugin "xcb"“ (Linux) | Systembibliothek fehlt | `sudo apt install libxcb-cursor0` |
| Run-Modus zeigt „springende“ Kurven | Kein Trigger: jede Aufnahme beginnt an einer zufälligen Stelle der Periode | Erwartetes Verhalten, siehe Erweiterungsideen in [pc-programm.md](pc-programm.md#erweiterungsideen) |

## Firmware ohne Viewer testen

Die Firmware lässt sich auch im Serial Monitor bedienen (1000000 Baud, Zeilenende „Neue Zeile“).
`I` oder `C3` werden angenommen. Die Antworten sind allerdings binär und erscheinen als
unlesbare Zeichen. Ein Stream (`S`) wird durch die nächste Eingabe beendet.

Zum Mitlesen der Rohdaten eignet sich ein Terminalprogramm mit Hex-Ansicht (z. B. HTerm unter
Windows, `picocom` oder `python -m serial.tools.miniterm` unter Linux). Der Aufbau der Frames
steht in [protokoll.md](protokoll.md).
