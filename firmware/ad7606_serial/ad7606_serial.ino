/*
 * AD7606 – Inbetriebnahme über serielles Interface (Hardware-SPI)
 * Ziel: Arduino Uno/Nano (5 V). Für 3,3-V-Boards VDRIVE auf 3,3 V legen.
 *
 * Minimaler Test-Sketch: misst alle 8 Kanäle ca. 5× pro Sekunde und gibt sie
 * als Text im Serial Monitor aus (115200 Baud). Gut für die erste Inbetriebnahme,
 * bevor die Firmware ad7606_daq und das PC-Programm zum Einsatz kommen.
 *
 * Verdrahtung (AD7606 -> Arduino Uno):
 *   PAR/SER/BYTE SEL -> VDRIVE (HIGH = serieller Modus)  [bei vielen Modulen per Lötbrücke/Widerstand]
 *   CONVST A + B     -> D7  (zusammen gebrückt, simultanes Sampling aller 8 Kanäle)
 *   RESET            -> D8
 *   BUSY             -> D2
 *   CS               -> D10
 *   RD/SCLK          -> D13 (SCK)
 *   DB7/DOUTA        -> D12 (MISO)
 *   DB8/DOUTB        -> nicht genutzt (dann liefert DOUTA alle 8 Kanäle)
 *   RANGE            -> D9  (LOW = ±5 V, HIGH = ±10 V)
 *   OS0..OS2         -> D3, D4, D5 (Oversampling, 000 = aus)
 *   STBY             -> VDRIVE (HIGH = normaler Betrieb)
 *   VDRIVE           -> 5 V,  AVCC -> 5 V,  AGND/DGND -> GND
 */

#include <SPI.h>

// ---------- Pins ----------
const uint8_t PIN_CONVST = 7;
const uint8_t PIN_RESET  = 8;
const uint8_t PIN_BUSY   = 2;
const uint8_t PIN_CS     = 10;
const uint8_t PIN_RANGE  = 9;
const uint8_t PIN_OS[3]  = {3, 4, 5};

// ---------- Konfiguration ----------
const bool    RANGE_10V    = false;  // false = ±5 V, true = ±10 V
const uint8_t OVERSAMPLING = 0;      // 0..6  -> Ratio 1,2,4,8,16,32,64
const uint8_t NUM_CH       = 8;

// SCLK max. ~20 MHz laut Datenblatt; Uno schafft 8 MHz.
// DOUT wechselt mit fallender SCLK-Flanke, Idle HIGH -> Abtasten auf steigender Flanke = SPI_MODE3
SPISettings adcSpi(8000000, MSBFIRST, SPI_MODE3);

int16_t raw[NUM_CH];

void adcReset() {
  digitalWrite(PIN_RESET, HIGH);
  delayMicroseconds(1);              // min. 50 ns
  digitalWrite(PIN_RESET, LOW);
  delayMicroseconds(1);
}

void adcSetOversampling(uint8_t os) {
  for (uint8_t i = 0; i < 3; i++) digitalWrite(PIN_OS[i], (os >> i) & 1);
}

bool adcRead(int16_t *data) {
  // 1) Wandlung starten: steigende Flanke an CONVST A/B
  digitalWrite(PIN_CONVST, LOW);
  delayMicroseconds(1);              // min. 25 ns LOW
  digitalWrite(PIN_CONVST, HIGH);

  // 2) Auf BUSY warten (geht HIGH, dann wieder LOW wenn fertig)
  uint32_t t0 = micros();
  while (digitalRead(PIN_BUSY) == LOW)  { if (micros() - t0 > 100)   return false; }
  while (digitalRead(PIN_BUSY) == HIGH) { if (micros() - t0 > 1000) return false; }  // OS=64 braucht ~315 µs

  // 3) Ergebnisse auslesen: 8 × 16 Bit über DOUTA, Zweierkomplement
  SPI.beginTransaction(adcSpi);
  digitalWrite(PIN_CS, LOW);
  for (uint8_t ch = 0; ch < NUM_CH; ch++) {
    data[ch] = (int16_t)SPI.transfer16(0x0000);
  }
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  return true;
}

float toVolts(int16_t code) {
  const float fullScale = RANGE_10V ? 10.0f : 5.0f;
  return code * fullScale / 32768.0f;
}

void setup() {
  Serial.begin(115200);

  pinMode(PIN_CONVST, OUTPUT);
  pinMode(PIN_RESET,  OUTPUT);
  pinMode(PIN_CS,     OUTPUT);
  pinMode(PIN_RANGE,  OUTPUT);
  pinMode(PIN_BUSY,   INPUT);
  for (uint8_t i = 0; i < 3; i++) pinMode(PIN_OS[i], OUTPUT);

  digitalWrite(PIN_CS, HIGH);
  digitalWrite(PIN_CONVST, HIGH);
  digitalWrite(PIN_RANGE, RANGE_10V ? HIGH : LOW);
  adcSetOversampling(OVERSAMPLING);

  SPI.begin();
  delay(10);          // Versorgung/Referenz einschwingen lassen
  adcReset();         // nach Power-up Pflicht

  Serial.println(F("AD7606 bereit"));
}

void loop() {
  if (!adcRead(raw)) {
    Serial.println(F("Timeout: BUSY reagiert nicht - Verdrahtung/Modus pruefen"));
    delay(500);
    return;
  }

  for (uint8_t ch = 0; ch < NUM_CH; ch++) {
    Serial.print(F("V")); Serial.print(ch + 1); Serial.print(F(": "));
    Serial.print(toVolts(raw[ch]), 4);
    Serial.print(F(" V  ("));  Serial.print(raw[ch]); Serial.print(F(")"));
    Serial.print(ch < NUM_CH - 1 ? F(" | ") : F("\n"));
  }
  delay(200);
}
