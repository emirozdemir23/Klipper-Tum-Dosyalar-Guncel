#include "HX711.h"

// =====================================================================
//  Z-TOUCH (load-cell) endstop emulatoru — SERTLESTIRILMIS SURUM
//
//  Orijinale gore giderilen 4 KRITIK madde:
//   1) Her kanal BAGIMSIZ okunur/tetiklenir. Bir hucre olurse digerleri
//      etkilenmez (eski koddaki "3'u birden is_ready()" AND kilidi kalkti).
//   2) FAIL-SAFE: bir hucreden SENSOR_TIMEOUT_MS boyunca taze ornek
//      gelmezse o kanal ARIZALI sayilir, cikisi TETIKLENIR (motoru
//      durdurur) ve seri porta "error chN ..." basilir. Guvenli ariza
//      yonu = "tetiklendi", asla "sessizce inmeye devam et".
//   3) HANDSHAKE: "read" sonrasi tum kanallarin tare'i bitince bir kez
//      "offset_ready" basilir. Klipper makrosu inise BASLAMADAN once bu
//      satiri (veya "error"i) beklemeli.
//   4) TRIP YONU dogrulanabilir: her kanal icin INVERT[] var + "debug"
//      komutu ile canli deger akisi. Basinca deger POZITIFE giden bir
//      hucre varsa o kanalin INVERT'ini true yap (asagidaki prosedur).
// =====================================================================


// ------------------------- KULLANICI AYARLARI -------------------------
#define NUM_CELLS 3

// HX711 pinleri (kanal sirasina gore) — {DOUT, SCK}
const uint8_t HX_DOUT[NUM_CELLS] = { 3, 5, 7 };   // LC1, LC2, LC3
const uint8_t HX_SCK [NUM_CELLS] = { 2, 4, 6 };

// Klipper'a giden open-drain endstop cikislari
const uint8_t OUT_PIN[NUM_CELLS] = { 8, 9, 10 };  // Z1, Z2, Z3

// Basinc yonu:
//   Hucre SIKISTIRILINCA (tare sonrasi) deger NEGATIFE gidiyorsa  -> false
//   Hucre sikistirilinca deger POZITIFE gidiyorsa (ters kablo)    -> true
// Dogrulama prosedurunu asagidaki "debug" aciklamasindan uygula.
const bool INVERT[NUM_CELLS] = { false, false, false };

// Esikler (isaretli, tare sonrasi deger uzerinde). histerezis icin
// TRIP_LEVEL, RELEASE_LEVEL'dan KUCUK (daha negatif) olmali.
const long TRIP_LEVEL    = -1000;   // bu degerin ALTINA inince tetikle
const long RELEASE_LEVEL =  -900;   // bu degerin USTUNE cikinca birak

// Tare (offset) icin ortalanacak ornek sayisi (kanal basina)
const int  BOOT_SAMPLES  = 10;

// Sensor ariza timeout'u (ms). Bir kanaldan bu sure boyunca taze ornek
// gelmezse ARIZALI kabul edilir -> fail-safe tetik. HX711 ~10 SPS iken
// ornekler ~100 ms arayla gelir; 1000 ms genis ve guvenli bir esiktir.
const unsigned long SENSOR_TIMEOUT_MS = 1000;
// ----------------------------------------------------------------------


HX711 scale[NUM_CELLS];

long          sum[NUM_CELLS];
int           sampleCount[NUM_CELLS];
long          offset[NUM_CELLS];
bool          offsetReady[NUM_CELLS];
bool          tripped[NUM_CELLS];
bool          faulted[NUM_CELLS];
unsigned long lastSampleMs[NUM_CELLS];

bool readingEnabled = false;
bool debugStream    = false;
bool announcedReady = false;   // "offset_ready" bu okuma dizisinde basildi mi

// ---- open-drain cikis: active=tetik (LOW surer), pasif=birak (yuksek-Z) ----
inline void od_set(uint8_t pin, bool active) {
  if (active) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);      // tetik: Klipper `^!` pininde "triggered"
  } else {
    pinMode(pin, INPUT);         // birak: pull-up ile yuksek = "not triggered"
  }
}

// INVERT uygulanmis (yon-normalize) ham deger. Boylece asagidaki tum
// karsilastirmalar tek yonlu (negatif = basinc) kalir.
inline long signedRaw(int i, long raw) {
  return INVERT[i] ? -raw : raw;
}

void resetChannel(int i) {
  sum[i]          = 0;
  sampleCount[i]  = 0;
  offsetReady[i]  = false;
  tripped[i]      = false;
  faulted[i]      = false;
  lastSampleMs[i] = millis();
  od_set(OUT_PIN[i], false);     // baslarken serbest (tetiklenmemis)
}

void startReadSequence() {
  readingEnabled = true;
  announcedReady = false;
  for (int i = 0; i < NUM_CELLS; i++) resetChannel(i);
}

void stopReadSequence() {
  readingEnabled = false;
  announcedReady = false;
  for (int i = 0; i < NUM_CELLS; i++) {
    offsetReady[i] = false;
    tripped[i]     = false;
    faulted[i]     = false;
    od_set(OUT_PIN[i], false);
  }
}

void handleSerial() {
  if (!Serial.available()) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  cmd.toLowerCase();

  if (cmd == "read") {
    startReadSequence();
  } else if (cmd == "stop") {
    stopReadSequence();
  } else if (cmd == "zero") {
    if (readingEnabled) {           // yeniden tare
      announcedReady = false;
      for (int i = 0; i < NUM_CELLS; i++) resetChannel(i);
    }
  } else if (cmd == "debug") {
    debugStream = !debugStream;      // canli deger akisini ac/kapat
  }
}

// Bir kanali servis et: tare topla VEYA trip/release + fail-safe timeout.
void serviceChannel(int i) {
  if (faulted[i]) return;            // arizali kanal tetikte kilitli kalir

  if (scale[i].is_ready()) {
    long raw = signedRaw(i, scale[i].read());
    lastSampleMs[i] = millis();

    if (!offsetReady[i]) {
      // ---- tare (offset) toplama ----
      sum[i] += raw;
      if (++sampleCount[i] >= BOOT_SAMPLES) {
        offset[i]      = sum[i] / BOOT_SAMPLES;
        offsetReady[i] = true;
      }
    } else {
      // ---- normal olcum + histerezisli trip/release ----
      long v = raw - offset[i];

      if (debugStream) {
        Serial.print("ch"); Serial.print(i + 1);
        Serial.print('=');  Serial.println(v);
      }

      if (!tripped[i] && v <= TRIP_LEVEL) {
        tripped[i] = true;
        od_set(OUT_PIN[i], true);        // TETIK
      } else if (tripped[i] && v >= RELEASE_LEVEL) {
        tripped[i] = false;
        od_set(OUT_PIN[i], false);       // birak
      }
    }
  } else {
    // Taze ornek yok. HX711 donusumleri arasi (~100 ms) bu normaldir;
    // ancak SENSOR_TIMEOUT_MS asilirsa gercekten arizadir -> fail-safe.
    if (millis() - lastSampleMs[i] > SENSOR_TIMEOUT_MS) {
      faulted[i] = true;
      od_set(OUT_PIN[i], true);          // FAIL-SAFE: tetikle, motoru durdur
      Serial.print("error ch"); Serial.print(i + 1);
      Serial.println(" sensor timeout");
    }
  }
}

// Tum kanallar hazir olunca (ve hicbiri arizali degilse) bir kez bildir.
void checkAllReady() {
  if (announcedReady || !readingEnabled) return;
  for (int i = 0; i < NUM_CELLS; i++) {
    if (faulted[i]) return;          // ariza varsa "offset_ready" basma
    if (!offsetReady[i]) return;
  }
  announcedReady = true;
  Serial.println("offset_ready");    // Klipper makrosu inise BUNDAN sonra baslar
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(20);             // bozuk/eksik komutta 1 sn'lik blok yok

  for (int i = 0; i < NUM_CELLS; i++) {
    scale[i].begin(HX_DOUT[i], HX_SCK[i]);
    offsetReady[i]  = false;
    tripped[i]      = false;
    faulted[i]      = false;
    lastSampleMs[i] = millis();
    od_set(OUT_PIN[i], false);       // boot'ta tum cikislar serbest
  }

  Serial.println("ready");
}

void loop() {
  handleSerial();

  if (!readingEnabled) {
    delay(5);
    return;
  }

  for (int i = 0; i < NUM_CELLS; i++) serviceChannel(i);
  checkAllReady();

  delay(2);   // kanallar bagimsiz + is_ready() bloklamadan pollandigi icin kisa
}

// =====================================================================
//  TRIP YONU (INVERT) DOGRULAMA PROSEDURU  — madde #4
//  1) Sketch'i yukle, seri monitoru 115200'de ac.
//  2) "read"  yaz  -> tare baslar, kisa surede "offset_ready" gelmeli.
//  3) "debug" yaz  -> ch1=.. ch2=.. ch3=.. akisi baslar.
//  4) Her load cell'e ELINLE hafifce bastir:
//       - ilgili kanalin degeri NEGATIFE gidiyorsa  -> INVERT o kanal = false (dogru)
//       - POZITIFE gidiyorsa                         -> INVERT o kanal = true yap
//     (deger -1000'in altina inince o kanal tetiklenmeli.)
//  5) INVERT[] duzelt, tekrar yukle. "debug" ile tekrar kapatabilirsin.
//
//  SERI PROTOKOL OZETI (Klipper tarafi):
//    ->  "read\n"   : tare baslat
//    <-  "offset_ready"  : inise HAZIR (bunu bekle)
//    <-  "error chN ..." : arizali kanal, iptal et
//    ->  "stop\n"   : bitir, cikislari birak
//    ->  "zero\n"   : yeniden tare (okuma acikken)
// =====================================================================
