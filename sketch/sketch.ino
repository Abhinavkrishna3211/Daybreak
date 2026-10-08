// Daybreak: STM32U585 (MCU) side.
// Owns everything that must keep working if Linux doesn't: alarm buzzer,
// latch, override button, watchdog, max-lock timer, and real-time sensing.
//
// Latch = 5 V holding electromagnet via logic MOSFET (with flyback diode).
// Magnet OFF = lid free, so power loss always opens the box (fail-open).

#include <Arduino_RouterBridge.h>
#include <Wire.h>
#include <Adafruit_LTR390.h>     // VERIFY it builds on the arduino:zephyr core
#include <Modulino.h>            // Modulino Movement over Qwiic

// ---------------- DS3231 RTC (task 8) ----------------
// No RTC library added - it's a handful of BCD register reads/writes, and the only thing a
// library would save here is the calendar<->epoch math below. Battery not fitted yet (ponytail:
// Abhi's board is bare), so this chip loses time on a power cut same as before until he adds a
// CR2032 - wiring and Bridge calls work identically either way, only power-cut survival waits.
const int DS3231_ADDR = 0x68;

uint8_t bcd2bin(uint8_t v) { return (v & 0x0F) + ((v >> 4) * 10); }
uint8_t bin2bcd(uint8_t v) { return (v % 10) + ((v / 10) << 4); }

bool rtcWrite(uint32_t unixtime) {
  // Civil-from-days algorithm (Howard Hinnant), good for any Gregorian date - avoids pulling in
  // a Time library just to turn an epoch into y/m/d.
  time_t t = (time_t)unixtime;
  int32_t days = t / 86400L, rem = t % 86400L;
  if (rem < 0) { rem += 86400L; days--; }
  int hh = rem / 3600, mm = (rem % 3600) / 60, ss = rem % 60;
  days += 719468;
  int32_t era = (days >= 0 ? days : days - 146096) / 146097;
  uint32_t doe = days - era * 146097;
  uint32_t yoe = (doe - doe/1460 + doe/36524 - doe/146096) / 365;
  int32_t y = yoe + era * 400;
  uint32_t doy = doe - (365*yoe + yoe/4 - yoe/100);
  uint32_t mp = (5*doy + 2) / 153;
  uint32_t d = doy - (153*mp + 2)/5 + 1;
  uint32_t m = mp + (mp < 10 ? 3 : -9);
  y += (m <= 2);

  Wire.beginTransmission(DS3231_ADDR);
  Wire.write(0x00);
  Wire.write(bin2bcd(ss));
  Wire.write(bin2bcd(mm));
  Wire.write(bin2bcd(hh));
  Wire.write(bin2bcd(1));           // day-of-week unused, write a valid placeholder
  Wire.write(bin2bcd(d));
  Wire.write(bin2bcd(m));
  Wire.write(bin2bcd(y - 2000));
  return Wire.endTransmission() == 0;
}

// Returns 0 if the chip didn't answer (not fitted, or NACK).
uint32_t rtcRead() {
  Wire.beginTransmission(DS3231_ADDR);
  Wire.write(0x00);
  if (Wire.endTransmission(false) != 0) return 0;
  if (Wire.requestFrom(DS3231_ADDR, 7) != 7) return 0;
  int ss = bcd2bin(Wire.read());
  int mm = bcd2bin(Wire.read());
  int hh = bcd2bin(Wire.read() & 0x3F);  // mask 12/24h + AM/PM bits, we always set 24h
  Wire.read();                           // day-of-week, unused
  int d  = bcd2bin(Wire.read());
  int m  = bcd2bin(Wire.read());
  int y  = bcd2bin(Wire.read()) + 2000;

  int32_t yy = y - (m <= 2);
  int32_t era = (yy >= 0 ? yy : yy - 399) / 400;
  uint32_t yoe = yy - era * 400;
  uint32_t mp = (m + 9) % 12;
  uint32_t doy = (153*mp + 2)/5 + d - 1;
  uint32_t doe = yoe*365 + yoe/4 - yoe/100 + doy;
  int32_t days = era * 146097 + (int32_t)doe - 719468;
  return (uint32_t)days * 86400UL + hh*3600UL + mm*60UL + ss;
}

// ---------------- pins (edit to match your wiring) ----------------
const int PIN_MAGNET   = 3;   // MOSFET gate
const int PIN_BUZZER   = 5;   // active buzzer
const int PIN_OVERRIDE = 2;   // button to GND, INPUT_PULLUP
const int PIN_TALK     = 4;   // button to GND, INPUT_PULLUP
const int PIN_FLICKER  = A0;  // phototransistor + 10k to GND (optional)
const bool HAS_FLICKER = false;  // set true once the phototransistor is fitted

// ---------------- safety timing ----------------
const unsigned long WATCHDOG_MS = 30UL * 1000;
unsigned long lastHeartbeat = 0;
unsigned long lockedAt = 0;
unsigned long maxLockMs = 90UL * 60 * 1000;
bool locked = false;
bool alarmOn = false;

// ponytail: the alarm itself still runs off millis(), not the DS3231 below, so this is a relative countdown from
// millis(), not a wall-clock alarm. It survives the Linux app crashing or hanging (millis()
// keeps running on this MCU regardless), which is the case this exists for. It does NOT
// survive a power cycle - millis() resets to 0 and the countdown is lost until Linux
// reconnects and resends it. Fix that by swapping this for a DS3231 compare-match alarm.
unsigned long alarmAtMs = 0;
bool alarmArmed = false;
bool ltrOk = false;         // false until begin() says the light sensor answered
bool rtcOk = false;         // false until the DS3231 answers on the bus

Adafruit_LTR390 ltr;
ModulinoMovement imu;
long steps = 0;

// ---------------- latch ----------------
void setLock(bool on) {
  locked = on;
  digitalWrite(PIN_MAGNET, on ? HIGH : LOW);
  if (on) lockedAt = millis();
}

// ---------------- Bridge handlers (called from Linux) ----------------
void hHeartbeat()          { lastHeartbeat = millis(); }
void hRelease()            { setLock(false); }
void hLock(int minutes)    { maxLockMs = (unsigned long)minutes * 60000UL; setLock(true); }
void hAlarm(int on)        { alarmOn = on; alarmArmed = false; if (!on) digitalWrite(PIN_BUZZER, LOW); }
// seconds-until-next-fire, resent by Linux whenever the alarm is set, skipped, or on boot -
// see the ponytail note above for why this is relative rather than wall-clock.
void hSetAlarm(int secs)  { alarmAtMs = millis() + (unsigned long)secs * 1000UL; alarmArmed = true; }
// Linux calls this whenever NTP syncs (fire-and-forget, same shape as the other Linux->MCU calls).
void hRtcSet(uint32_t unixtime) { rtcOk = rtcWrite(unixtime); }

// ---------------- sensing ----------------
// Simple step detector on |a| with hysteresis (good enough for "did you walk").
void updateSteps() {
  static bool high = false;
  if (!imu.update()) return;
  float a = sqrt(imu.getX()*imu.getX() + imu.getY()*imu.getY() + imu.getZ()*imu.getZ()); // in g
  if (!high && a > 1.20f) { high = true; steps++; }
  if (high && a < 1.05f)  { high = false; }
}

// Goertzel at 100 Hz over 200 samples @ 2 kHz (100 ms). Returns AC/DC ratio 0..1.
float flickerRatio() {
  if (!HAS_FLICKER) return -1.0f;
  const int N = 200; const float fs = 2000.0f, f = 100.0f;
  const float k = 2.0f * cos(2.0f * PI * f / fs);
  float s1 = 0, s2 = 0, sum = 0;
  unsigned long t = micros();
  for (int i = 0; i < N; i++) {
    while (micros() - t < 500) {}  // 2 kHz
    t += 500;
    float x = analogRead(PIN_FLICKER);
    sum += x;
    float s0 = x + k * s1 - s2; s2 = s1; s1 = s0;
  }
  float power = s1*s1 + s2*s2 - k*s1*s2;
  float dc = sum / N;
  if (dc < 1) return 0;
  return min(1.0f, (2.0f * sqrt(power) / N) / dc);
}

// LTR390 has one channel at a time: read ALS, then UVS.
void readLight(float &lux, float &als, float &uvs) {
  // Not reached if the sensor never answered begin(). Reading it anyway is what produced
  // "4294967296 lux" on the dashboard: a dead I2C read returns all ones, which is both brighter
  // than the sun and, since UVS reads the same way, a UV ratio of exactly 1.0 - a fault that
  // looks like perfect daylight. -1 is the same "not fitted" sentinel the flicker sensor uses.
  if (!ltrOk) { lux = -1.0f; als = -1.0f; uvs = -1.0f; return; }

  ltr.setMode(LTR390_MODE_ALS);  delay(110);
  als = ltr.readALS();
  lux = 0.6f * als / (1.0f * 1.0f);   // datasheet: lux = 0.6*ALS/(gain*int_factor); gain=1, 18-bit=1. CALIBRATE vs a lux meter/phone app
  ltr.setMode(LTR390_MODE_UVS);  delay(110);
  uvs = ltr.readUVS();
}

void setup() {
  pinMode(PIN_MAGNET, OUTPUT);   digitalWrite(PIN_MAGNET, LOW);   // boot unlocked
  pinMode(PIN_BUZZER, OUTPUT);   digitalWrite(PIN_BUZZER, LOW);
  pinMode(PIN_OVERRIDE, INPUT_PULLUP);
  pinMode(PIN_TALK, INPUT_PULLUP);

  Bridge.begin();
  Bridge.provide_safe("heartbeat", hHeartbeat);
  Bridge.provide_safe("release",   hRelease);
  Bridge.provide_safe("lock",      hLock);
  Bridge.provide_safe("alarm",     hAlarm);
  Bridge.provide_safe("set_alarm", hSetAlarm);
  Bridge.provide_safe("rtc_set",   hRtcSet);

  Wire.begin();
  Modulino.begin();
  imu.begin();
  ltrOk = ltr.begin();
  if (ltrOk) {
    ltr.setGain(LTR390_GAIN_1);            // low gain so direct sun doesn't saturate
    ltr.setResolution(LTR390_RESOLUTION_18BIT);
  }
  lastHeartbeat = millis();

  // rtc_get: no call-with-reply in this Bridge, so report what the RTC knows once at boot -
  // the one moment Linux actually needs it (to set its own clock before NTP has had a chance).
  Wire.beginTransmission(DS3231_ADDR);
  rtcOk = (Wire.endTransmission() == 0);
  if (rtcOk) Bridge.notify("rtc", rtcRead());
}

void loop() {
  unsigned long now = millis();
  updateSteps();

  // --- safety, always first ---
  if (digitalRead(PIN_OVERRIDE) == LOW && locked) {
    setLock(false); alarmOn = false; digitalWrite(PIN_BUZZER, LOW);
    Bridge.notify("override", 1);
  }
  if (locked && (now - lastHeartbeat > WATCHDOG_MS)) setLock(false);   // Linux went quiet
  if (locked && (now - lockedAt > maxLockMs))        setLock(false);   // max lock time
  if (alarmArmed && now >= alarmAtMs) { alarmArmed = false; alarmOn = true; }  // rings with or without Linux

  // --- talk button (debounced) ---
  static unsigned long talkAt = 0;
  if (digitalRead(PIN_TALK) == LOW && now - talkAt > 1500) { talkAt = now; Bridge.notify("talk", 1); }

  // --- alarm pattern: 300 ms on / 700 ms off ---
  digitalWrite(PIN_BUZZER, (alarmOn && (now % 1000) < 300) ? HIGH : LOW);

  // --- 1 Hz sensor report ---
  static unsigned long lastReport = 0;
  if (now - lastReport >= 1000) {
    lastReport = now;
    float lux, als, uvs;
    readLight(lux, als, uvs);
    float fl = flickerRatio();
    String msg = String(lux, 0) + "," + String(uvs, 0) + "," + String(als, 0) + "," +
                 String(fl, 3) + "," + String(steps);
    Bridge.notify("sensors", msg);
  }
  delay(10);
}
