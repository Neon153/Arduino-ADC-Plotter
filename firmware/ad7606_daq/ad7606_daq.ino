/*
 * AD7606 DAQ – Firmware für Arduino Uno (ATmega328P, 16 MHz)
 * Gegenstück auf dem PC: ad7606_viewer.py
 *
 * Zwei Betriebsarten:
 *   Burst : Samples mit fester Rate ins RAM, danach am Stück per USB senden.
 *           -> hohe Abtastrate (ca. 25 kSa/s bei 8 Kanälen), aber nur 640 Werte Puffer
 *              (= 80 Samples bei 8 Kanälen, 640 bei 1 Kanal).
 *   Stream: Jedes Sample sofort senden -> beliebig lange Aufzeichnung,
 *           Rate durch die serielle Verbindung begrenzt (ca. 4 kSa/s bei 8 Kanälen).
 *
 * Abtasttakt: Timer1 im CTC-Modus -> zeitlich exakt, unabhängig von der Laufzeit der Software.
 *
 * Verdrahtung (wie ad7606_serial.ino):
 *   CONVST A+B -> D7    BUSY -> D2    CS -> D10    RESET -> D8    RANGE -> D9
 *   OS0..OS2   -> D3..D5              RD/SCLK -> D13              DB7/DOUTA -> D12
 *   PAR/SER/BYTE SEL und STBY -> VDRIVE (HIGH),  VDRIVE -> 5 V
 *
 * ---------------------------------------------------------------------------
 * Protokoll PC -> Arduino (ASCII, jede Zeile mit \n abgeschlossen)
 *   C<maske>   Kanalmaske, Bit0 = V1 ... Bit7 = V8 (z. B. C255 oder C0x0F)
 *   R<µs>      Abtastperiode 10 ... 4000000 µs (wird auf das Timer-Raster gerundet)
 *   N<anzahl>  Samples pro Burst (max. 640 / Anzahl Kanäle)
 *   O<0..6>    Oversampling-Faktor 2^n
 *   G<0|1>     Messbereich ±5 V / ±10 V
 *   I          Info anfordern
 *   B          Burst starten   (ein beliebiges Byte vom PC bricht ab)
 *   S          Stream starten  (ein beliebiges Byte vom PC beendet, üblich: X)
 *   Jeder Einstellbefehl wird mit einem INFO-Frame quittiert.
 *
 * Protokoll Arduino -> PC (binär, little endian)
 *   0xA5 0x5A | Typ u8 | Länge u16 | Nutzdaten | Prüfsumme u8
 *   (Prüfsumme = Summe über Typ, Länge und Nutzdaten, modulo 256)
 *   Typ 1 INFO  : proto u8, bufWords u16, mask u8, periodUs u32, nsets u16, os u8, range u8
 *   Typ 2 BURST : mask u8, periodUs u32, nsets u16, os u8, range u8, overruns u16,
 *                 danach nsets × nch int16 (Kanäle verschachtelt: S0V1 S0V2 ... S1V1 ...)
 *   Typ 3 STREAM: flags u8 (Bit0 = Sample kam zu spät), danach nch int16
 *   Typ 4 TEXT  : ASCII-Meldung
 */

#include <SPI.h>
#include <avr/pgmspace.h>

// ============================== Pins ==============================
const uint8_t PIN_CONVST = 7;    // PD7
const uint8_t PIN_BUSY   = 2;    // PD2
const uint8_t PIN_CS     = 10;   // PB2
const uint8_t PIN_RESET  = 8;    // PB0
const uint8_t PIN_RANGE  = 9;    // PB1
const uint8_t PIN_OS[3]  = {3, 4, 5};

// Direkte Portzugriffe für den zeitkritischen Pfad (nur ATmega328P!)
// digitalWrite() braucht ~4 µs, ein sbi/cbi-Befehl 125 ns.
#define CONVST_LOW()   (PORTD &= ~_BV(PD7))
#define CONVST_HIGH()  (PORTD |=  _BV(PD7))
#define CS_LOW()       (PORTB &= ~_BV(PB2))
#define CS_HIGH()      (PORTB |=  _BV(PB2))
#define BUSY_IS_HIGH() (PIND & _BV(PD2))

// ============================ Konstanten ============================
const uint32_t BAUD      = 1000000;  // bei 16 MHz exakt erreichbar (115200 hat 2,1 % Fehler)
const uint16_t BUF_WORDS = 640;      // 1280 Byte – mehr lässt der 2-KB-RAM nicht zu
const uint8_t  PROTO     = 1;
enum : uint8_t { F_INFO = 1, F_BURST = 2, F_STREAM = 3, F_TEXT = 4 };

// DOUT wechselt mit fallender SCLK-Flanke, SCLK ruht HIGH -> Lesen bei steigender Flanke
SPISettings spiCfg(8000000, MSBFIRST, SPI_MODE3);

// ============================== Zustand ==============================
int16_t buf[BUF_WORDS];

struct Config {
  uint8_t  mask;      // aktive Kanäle
  uint32_t periodUs;  // tatsächliche (gerundete) Abtastperiode
  uint16_t nsets;     // Samples pro Burst
  uint8_t  os;        // Oversampling 0..6
  uint8_t  range;     // 0 = ±5 V, 1 = ±10 V
};
Config cfg = { 0xFF, 100, 80, 0, 0 };

uint8_t  tmrCs;       // Prescaler-Bits für TCCR1B
uint16_t tmrOcr;      // Vergleichswert OCR1A

char    line[24];
uint8_t lineLen = 0;
uint8_t frameSum;

struct __attribute__((packed)) InfoPayload {
  uint8_t proto; uint16_t bufWords; uint8_t mask; uint32_t periodUs;
  uint16_t nsets; uint8_t os; uint8_t range;
};
struct __attribute__((packed)) BurstHeader {
  uint8_t mask; uint32_t periodUs; uint16_t nsets; uint8_t os; uint8_t range; uint16_t overruns;
};

// ========================== Hilfsfunktionen ==========================
uint8_t channelCount(uint8_t m) { uint8_t n = 0; while (m) { n += m & 1; m >>= 1; } return n; }
uint8_t lastChannel(uint8_t m)  { uint8_t n = 0; while (m) { n++; m >>= 1; } return n; }  // höchstes Bit + 1
uint16_t maxSets()              { return BUF_WORDS / channelCount(cfg.mask); }

// ------------------------- Frames an den PC -------------------------
void frameBegin(uint8_t type, uint16_t len) {
  Serial.write(0xA5); Serial.write(0x5A);
  Serial.write(type); Serial.write(len & 0xFF); Serial.write(len >> 8);
  frameSum = type + (len & 0xFF) + (len >> 8);
}

void frameData(const void* data, uint16_t n) {
  const uint8_t* p = (const uint8_t*)data;
  for (uint16_t i = 0; i < n; i++) { frameSum += p[i]; Serial.write(p[i]); }
}

void frameEnd() { Serial.write(frameSum); }

void sendInfo() {
  InfoPayload p = { PROTO, BUF_WORDS, cfg.mask, cfg.periodUs, cfg.nsets, cfg.os, cfg.range };
  frameBegin(F_INFO, sizeof p); frameData(&p, sizeof p); frameEnd();
}

void sendText(PGM_P s) {            // Text liegt im Flash -> spart RAM
  uint16_t n = strlen_P(s);
  frameBegin(F_TEXT, n);
  for (uint16_t i = 0; i < n; i++) {
    uint8_t c = pgm_read_byte(s + i);
    frameSum += c; Serial.write(c);
  }
  frameEnd();
}

// ----------------------- Timer1 als Abtasttakt -----------------------
// Wählt den kleinsten Prescaler, bei dem die Periode in 16 Bit passt.
void setPeriod(uint32_t us) {
  if (us < 10) us = 10;
  if (us > 4000000UL) us = 4000000UL;
  uint32_t ticks;
  if (us <= 32768UL)        { tmrCs = _BV(CS11);             ticks = us * 2;  cfg.periodUs = ticks / 2;  } // /8    -> 0,5 µs
  else if (us <= 262144UL)  { tmrCs = _BV(CS11) | _BV(CS10); ticks = us / 4;  cfg.periodUs = ticks * 4;  } // /64   -> 4 µs
  else if (us <= 1048576UL) { tmrCs = _BV(CS12);             ticks = us / 16; cfg.periodUs = ticks * 16; } // /256  -> 16 µs
  else                      { tmrCs = _BV(CS12) | _BV(CS10); ticks = us / 64; cfg.periodUs = ticks * 64; } // /1024 -> 64 µs
  tmrOcr = ticks - 1;
}

void timerStart() {
  TCCR1B = 0; TCCR1A = 0; TCNT1 = 0;
  OCR1A  = tmrOcr;
  TIFR1  = _BV(OCF1A);                // altes Flag löschen (durch Schreiben einer 1)
  TCCR1B = _BV(WGM12) | tmrCs;        // CTC-Modus, Timer läuft
}

void timerStop() { TCCR1B = 0; }

// Kein Interrupt, nur Flag-Polling: gibt die kürzeste und konstanteste Reaktionszeit.
inline void waitTick()    { while (!(TIFR1 & _BV(OCF1A))) {} TIFR1 = _BV(OCF1A); }
inline bool tickPending() { return TIFR1 & _BV(OCF1A); }

// ------------------------------ AD7606 ------------------------------
void adcReset() {
  digitalWrite(PIN_RESET, HIGH);
  delayMicroseconds(1);               // min. 50 ns
  digitalWrite(PIN_RESET, LOW);
  delayMicroseconds(1);
}

void applyAnalogConfig() {
  digitalWrite(PIN_RANGE, cfg.range ? HIGH : LOW);
  for (uint8_t i = 0; i < 3; i++) digitalWrite(PIN_OS[i], (cfg.os >> i) & 1);
}

// Startet eine Wandlung und wartet auf ihr Ende. false = BUSY reagiert nicht.
// Wandlungszeit: 4 µs ohne Oversampling ... ca. 315 µs bei OS = 64.
bool adcConvert() {
  CONVST_LOW();
  __asm__ __volatile__("nop\n\tnop\n\t");   // > 25 ns LOW
  CONVST_HIGH();                             // steigende Flanke startet alle 8 Kanäle gleichzeitig

  uint16_t n = 0;                            // ein Durchlauf ≈ 0,35 µs -> Timeout ≈ 23 ms
  while (!BUSY_IS_HIGH()) if (++n == 0) return false;
  n = 0;
  while (BUSY_IS_HIGH())  if (++n == 0) return false;
  return true;
}

// Liest V1..V(lastCh) über DOUTA und speichert nur die Kanäle aus 'mask'.
// Höhere Kanäle werden nicht getaktet -> weniger Kanäle = schnelleres Auslesen.
int16_t* adcRead(int16_t* dst, uint8_t mask, uint8_t lastCh) {
  CS_LOW();
  for (uint8_t ch = 0; ch < lastCh; ch++) {
    int16_t v = (int16_t)SPI.transfer16(0);
    if (mask & (1 << ch)) *dst++ = v;
  }
  CS_HIGH();
  return dst;
}

// ---------------------------- Betriebsarten ----------------------------
void doBurst() {
  const uint8_t  mask   = cfg.mask;
  const uint8_t  lastCh = lastChannel(mask);
  const uint16_t nsets  = min(cfg.nsets, maxSets());
  uint16_t overruns = 0, done = 0;
  bool ok = true;
  int16_t* p = buf;

  Serial.flush();                      // laufende Sendungen abwarten -> kein UART-Interrupt-Jitter
  uint8_t timsk0 = TIMSK0;
  TIMSK0 = 0;                          // millis()-Interrupt aus -> kein Jitter durch Timer0
  SPI.beginTransaction(spiCfg);
  timerStart();

  while (done < nsets) {
    waitTick();
    if (!adcConvert()) { ok = false; break; }
    p = adcRead(p, mask, lastCh);
    done++;
    if (tickPending()) overruns++;     // nächster Takt schon vorbei -> Periode zu kurz
    if (Serial.available()) break;     // Abbruch durch den PC
  }

  timerStop();
  SPI.endTransaction();
  TIMSK0 = timsk0;

  if (!ok) sendText(PSTR("BUSY-Timeout: Verdrahtung/STBY pruefen"));
  BurstHeader h = { mask, cfg.periodUs, done, cfg.os, cfg.range, overruns };
  uint16_t dataLen = done * channelCount(mask) * 2;
  frameBegin(F_BURST, sizeof h + dataLen);
  frameData(&h, sizeof h);
  frameData(buf, dataLen);
  frameEnd();
}

void doStream() {
  const uint8_t  mask   = cfg.mask;
  const uint8_t  lastCh = lastChannel(mask);
  const uint16_t len    = 1 + 2 * channelCount(mask);
  struct { uint8_t flags; int16_t v[8]; } pkt;   // auf AVR ohne Padding
  pkt.flags = 0;
  bool ok = true;

  Serial.flush();
  uint8_t timsk0 = TIMSK0;
  TIMSK0 = 0;
  SPI.beginTransaction(spiCfg);
  timerStart();

  while (!Serial.available()) {
    waitTick();
    if (!adcConvert()) { ok = false; break; }
    adcRead(pkt.v, mask, lastCh);
    frameBegin(F_STREAM, len); frameData(&pkt, len); frameEnd();   // blockiert, wenn der Sendepuffer voll ist
    pkt.flags = tickPending() ? 1 : 0;                              // -> das nächste Sample kommt zu spät
  }

  timerStop();
  SPI.endTransaction();
  TIMSK0 = timsk0;

  if (Serial.available()) Serial.read();   // Stopp-Zeichen verwerfen
  if (!ok) sendText(PSTR("BUSY-Timeout: Verdrahtung/STBY pruefen"));
  sendInfo();                               // signalisiert dem PC: Stream beendet
}

// ------------------------------ Befehle ------------------------------
// Ein evtl. noch folgendes \r\n (z. B. vom Serial Monitor) entfernen,
// damit es Burst/Stream nicht sofort wieder abbricht.
void swallowEol() {
  delayMicroseconds(100);
  while (Serial.available() && (Serial.peek() == '\n' || Serial.peek() == '\r')) Serial.read();
}

void execCommand(char* s) {
  char cmd = toupper(s[0]);
  uint32_t arg = strtoul(s + 1, nullptr, 0);   // Basis 0: erlaubt auch 0x..

  switch (cmd) {
    case 'C': cfg.mask = arg & 0xFF; if (!cfg.mask) cfg.mask = 1;
              cfg.nsets = min(cfg.nsets, maxSets());              sendInfo(); break;
    case 'R': setPeriod(arg);                                     sendInfo(); break;
    case 'N': cfg.nsets = constrain(arg, 1UL, (uint32_t)maxSets()); sendInfo(); break;
    case 'O': cfg.os = min(arg, 6UL); applyAnalogConfig();        sendInfo(); break;
    case 'G': cfg.range = arg ? 1 : 0; applyAnalogConfig();       sendInfo(); break;
    case 'I': sendInfo(); break;
    case 'B': swallowEol(); doBurst();  break;
    case 'S': swallowEol(); doStream(); break;
    case 'X': break;                                              // Stopp im Leerlauf: ignorieren
    default:  sendText(PSTR("Unbekannter Befehl")); break;
  }
}

// ========================== setup / loop ==========================
void setup() {
  Serial.begin(BAUD);

  pinMode(PIN_CONVST, OUTPUT);
  pinMode(PIN_RESET,  OUTPUT);
  pinMode(PIN_CS,     OUTPUT);
  pinMode(PIN_RANGE,  OUTPUT);
  pinMode(PIN_BUSY,   INPUT);
  for (uint8_t i = 0; i < 3; i++) pinMode(PIN_OS[i], OUTPUT);

  digitalWrite(PIN_CS, HIGH);
  digitalWrite(PIN_CONVST, HIGH);
  digitalWrite(PIN_RESET, LOW);

  SPI.begin();
  setPeriod(cfg.periodUs);
  applyAnalogConfig();
  delay(10);                 // Versorgung/Referenz einschwingen lassen
  adcReset();                // nach Power-up Pflicht

  sendText(PSTR("AD7606 DAQ bereit"));
  sendInfo();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) { line[lineLen] = 0; lineLen = 0; execCommand(line); }
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
}
