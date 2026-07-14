#include "HX711.h"

// HX711
const int LC1_DOUT = 3;
const int LC1_SCK  = 2;

const int LC2_DOUT = 5;
const int LC2_SCK  = 4;

const int LC3_DOUT = 7;
const int LC3_SCK  = 6;

HX711 scale1;
HX711 scale2;
HX711 scale3;

// Z endstop
const int LC1_OUT = 8;   // Z1 endstop-PE15
const int LC2_OUT = 9;   // Z2 endstop-PD9
const int LC3_OUT = 10;  // Z3 endstop-PE11


// Offset
const int BOOT_SAMPLES = 10;

long offset1 = 0, offset2 = 0, offset3 = 0;
long sum1 = 0, sum2 = 0, sum3 = 0;
int sampleCount = 0;

bool readingEnabled = false;
bool offsetReady    = false;

// Eşik
const long TRIP_LEVEL    = -1000;
const long RELEASE_LEVEL = -900;

bool tripped1 = false;
bool tripped2 = false;
bool tripped3 = false;

// open-drain
inline void od_set(uint8_t pin, bool active) {
  if (active) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  } else {
    pinMode(pin, INPUT);
  }
}

void releaseAllOutputs() {
  od_set(LC1_OUT, false);
  od_set(LC2_OUT, false);
  od_set(LC3_OUT, false);
  tripped1 = tripped2 = tripped3 = false;
}

void resetOffsetAccumulator() {
  sum1 = sum2 = sum3 = 0;
  sampleCount = 0;
  offsetReady = false;
}

void startReadSequence() {
  readingEnabled = true;
  resetOffsetAccumulator();
  releaseAllOutputs();         
}

void stopReadSequence() {
  readingEnabled = false;
  offsetReady = false;
  releaseAllOutputs();
}

void setup() {
  Serial.begin(115200);

  // HX711 init
  scale1.begin(LC1_DOUT, LC1_SCK);
  scale2.begin(LC2_DOUT, LC2_SCK);
  scale3.begin(LC3_DOUT, LC3_SCK);

  releaseAllOutputs();

  Serial.println("ready");
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
    if (readingEnabled) {
      resetOffsetAccumulator();
      releaseAllOutputs();
    }
  }
}

void computeOffsetIfNeeded() {
  if (offsetReady) return;

  if (scale1.is_ready() && scale2.is_ready() && scale3.is_ready()) {
    long r1 = scale1.read();
    long r2 = scale2.read();
    long r3 = scale3.read();

    sum1 += r1;
    sum2 += r2;
    sum3 += r3;

    sampleCount++;

    if (sampleCount >= BOOT_SAMPLES) {
      offset1 = sum1 / BOOT_SAMPLES;
      offset2 = sum2 / BOOT_SAMPLES;
      offset3 = sum3 / BOOT_SAMPLES;

      offsetReady = true;
    }
  }
}

void updateTripsAndOutputs(long v1, long v2, long v3) {
  // LC1
  if (!tripped1 && v1 <= TRIP_LEVEL) {
    tripped1 = true;
    od_set(LC1_OUT, true);
    //Serial.println("lc1");
  } else if (tripped1 && v1 >= RELEASE_LEVEL) {
    tripped1 = false;
    od_set(LC1_OUT, false);
  }

  // LC2
  if (!tripped2 && v2 <= TRIP_LEVEL) {
    tripped2 = true;
    od_set(LC2_OUT, true);
    //Serial.println("lc2");
  } else if (tripped2 && v2 >= RELEASE_LEVEL) {
    tripped2 = false;
    od_set(LC2_OUT, false);
  }

  // LC3
  if (!tripped3 && v3 <= TRIP_LEVEL) {
    tripped3 = true;
    od_set(LC3_OUT, true);
    //Serial.println("lc3");
  } else if (tripped3 && v3 >= RELEASE_LEVEL) {
    tripped3 = false;
    od_set(LC3_OUT, false);
  }
}

void loop() {
  handleSerial();

  if (!readingEnabled) {
    delay(5);
    return;
  }

  if (!offsetReady) {
    computeOffsetIfNeeded();
    delay(5);
    return;
  }

  if (scale1.is_ready() && scale2.is_ready() && scale3.is_ready()) {
    long raw1 = scale1.read();
    long raw2 = scale2.read();
    long raw3 = scale3.read();

    long val1 = raw1 - offset1;
    long val2 = raw2 - offset2;
    long val3 = raw3 - offset3;

    
    // Serial.print(val1); Serial.print(",");
    // Serial.print(val2); Serial.print(",");
    // Serial.println(val3);

    updateTripsAndOutputs(val1, val2, val3);
  }

  delay(10);
}
