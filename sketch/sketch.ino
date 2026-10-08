// Daybreak: STM32U585 (MCU) side.
// Owns everything that must keep working if Linux doesn't: alarm buzzer,
// latch, override button, watchdog, max-lock timer, and real-time sensing.
//
// Latch = 5 V holding electromagnet via logic MOSFET (with flyback diode).
// Magnet OFF = lid free, so power loss always opens the box (fail-open).

#include <Arduino_RouterBridge.h>
#include <Wire.h>
// Light (BH1750), UV (GUVA-S12SD, analog) and motion (MPU6050) are all read over raw
// Wire/analogRead below - no sensor library needed, same approach as the DS3231. No Modulino:
// Abhi's board has no Qwiic cable, so the MPU6050 is the only motion sensor, not a fallback.

// ---------------- MPU6050 (motion, task 6) ----------------
// AD0 tied high on the board -> address 0x69 (0x68 is the DS3231, sharing this same bus).
const int MPU_ADDR = 0x69;

bool mpuBegin() {
  Wire2.beginTransmission(MPU_ADDR);
  Wire2.write(0x6B);  // PWR_MGMT_1
  Wire2.write(0x00);  // wake from sleep, internal oscillator
  return Wire2.endTransmission() == 0;
}

// Accel only, +-2g range (power-on default) - returns g's, false if the chip didn't answer.
bool mpuReadAccel(float &ax, float &ay, float &az) {
  Wire2.beginTransmission(MPU_ADDR);
  Wire2.write(0x3B);  // ACCEL_XOUT_H
  if (Wire2.endTransmission(false) != 0) return false;
  if (Wire2.requestFrom(MPU_ADDR, 6) != 6) return false;
  int16_t rx = (Wire2.read() << 8) | Wire2.read();
  int16_t ry = (Wire2.read() << 8) | Wire2.read();
  int16_t rz = (Wire2.read() << 8) | Wire2.read();
  ax = rx / 16384.0f; ay = ry / 16384.0f; az = rz / 16384.0f;  // LSB/g at +-2g
  return true;
}

// ---------------- BH1750 (lux) ----------------
// ADD pin left floating/low on the board -> address 0x23.
const int BH1750_ADDR = 0x23;

bool bh1750Begin() {
  Wire2.beginTransmission(BH1750_ADDR);
  Wire2.write(0x01);  // power on
  return Wire2.endTransmission() == 0;
}

// Returns -1 if the chip didn't answer. UNCALIBRATED, same as the lux formula below -
// CALIBRATE vs a lux meter/phone app.
float bh1750Read() {
  Wire2.beginTransmission(BH1750_ADDR);
  Wire2.write(0x10);  // continuous H-resolution mode, 1 lx step
  if (Wire2.endTransmission() != 0) return -1.0f;
  delay(130);  // worst-case H-res conversion time
  if (Wire2.requestFrom(BH1750_ADDR, 2) != 2) return -1.0f;
  uint16_t raw = (Wire2.read() << 8) | Wire2.read();
  return raw / 1.2f;  // datasheet conversion factor
}

// ---------------- DS3231 RTC (task 8) ----------------
// No RTC library added - it's a handful of BCD register reads/writes, and the only thing a
// library would save here is the calendar<->epoch math below. Battery not fitted yet (ponytail:
// Abhi's board is bare), so this chip loses time on a power cut same as before until he adds a
// CR2032 - wiring and Bridge calls work identically either way, only power-cut survival waits.
const int DS3231_ADDR = 0x68;

uint8_t dsBcd2bin(uint8_t v) { return (v & 0x0F) + ((v >> 4) * 10); }
uint8_t dsBin2bcd(uint8_t v) { return (v % 10) + ((v / 10) << 4); }

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

  Wire2.beginTransmission(DS3231_ADDR);
  Wire2.write(0x00);
  Wire2.write(dsBin2bcd(ss));
  Wire2.write(dsBin2bcd(mm));
  Wire2.write(dsBin2bcd(hh));
  Wire2.write(dsBin2bcd(1));           // day-of-week unused, write a valid placeholder
  Wire2.write(dsBin2bcd(d));
  Wire2.write(dsBin2bcd(m));
  Wire2.write(dsBin2bcd(y - 2000));
  return Wire2.endTransmission() == 0;
}

// Returns 0 if the chip didn't answer (not fitted, or NACK).
uint32_t rtcRead() {
  Wire2.beginTransmission(DS3231_ADDR);
  Wire2.write(0x00);
  if (Wire2.endTransmission(false) != 0) return 0;
  if (Wire2.requestFrom(DS3231_ADDR, 7) != 7) return 0;
  int ss = dsBcd2bin(Wire2.read());
  int mm = dsBcd2bin(Wire2.read());
  int hh = dsBcd2bin(Wire2.read() & 0x3F);  // mask 12/24h + AM/PM bits, we always set 24h
  Wire2.read();                           // day-of-week, unused
  int d  = dsBcd2bin(Wire2.read());
  int m  = dsBcd2bin(Wire2.read());
  int y  = dsBcd2bin(Wire2.read()) + 2000;

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
const int PIN_UV       = A2;  // GUVA-S12SD analog output
const bool HAS_LIGHT_UV = false;  // BH1750 + GUVA-S12SD arrive this weekend - set true once fitted
const int PIN_PHONE    = 6;   // bay microswitch, closes to GND when the phone is in, INPUT_PULLUP
const int PIN_LID      = 7;   // lid reed switch, closes to GND when the lid is shut, INPUT_PULLUP

// ---------------- safety timing ----------------
const unsigned long WATCHDOG_MS = 30UL * 1000;
unsigned long lastHeartbeat = 0;
unsigned long lockedAt = 0;
unsigned long maxLockMs = 90UL * 60 * 1000;
bool locked = false;
bool alarmOn = false;

// Wall-clock alarm: Linux sends the target as a unix time, and this fires by comparing
// against the DS3231's own clock (read once a second in loop(), not on every tick - it's an
// I2C transaction). Falls back to a millis() countdown when the RTC hasn't answered (not
// fitted, or NACKing), same behavior as before this existed.
uint32_t alarmAtUnix = 0;
unsigned long alarmAtMs = 0;
bool alarmArmed = false;
bool bh1750Ok = false;      // false until begin() says the light sensor answered
bool mpuOk = false;         // false until begin() says the motion sensor answered
bool rtcOk = false;         // false until the DS3231 answers on the bus
uint32_t rtcNowUnix = 0;    // refreshed once a second in loop() when rtcOk

long steps = 0;
bool phoneIn = false, lidShut = false;  // task 5/7: bay microswitch, lid reed switch

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
// seconds-until-next-fire, resent by Linux whenever the alarm is set, skipped, or on boot.
// Also stamped as an RTC wall-clock target (if the RTC answered at boot), so the alarm survives
// a power cycle that wipes millis(); the millis() target remains the fallback for no-RTC boards.
void hSetAlarm(int secs) {
  alarmAtMs = millis() + (unsigned long)secs * 1000UL;
  alarmAtUnix = rtcOk ? rtcRead() + (uint32_t)secs : 0;
  alarmArmed = true;
}
// Linux calls this whenever NTP syncs (fire-and-forget, same shape as the other Linux->MCU calls).
void hRtcSet(uint32_t unixtime) { rtcOk = rtcWrite(unixtime); }

// ---------------- sensing ----------------
// Walking check (task 6): cadence (steady 1.4-2.6 steps/s for >=15s) and "carried upright"
// (gravity mostly along one axis - whichever one, since the board's mounting orientation inside
// the box isn't fixed here). Both ride on the same accel reads as step detection, so they're
// computed alongside it rather than as a separate pass.
unsigned long lastStepAt = 0;
unsigned long steadyGaitMs = 0;
float gxAvg = 0, gyAvg = 0, gzAvg = 0;  // slow EMA of accel - gravity direction while carried

bool cadenceOk() { return steadyGaitMs >= 15000UL; }
bool uprightOk() {
  float dom = max(fabs(gxAvg), max(fabs(gyAvg), fabs(gzAvg)));
  float other = fabs(gxAvg) + fabs(gyAvg) + fabs(gzAvg) - dom;
  return dom > 0.75f && other < 0.6f;
}

// Simple step detector on |a| with hysteresis (good enough for "did you walk").
void updateSteps() {
  static bool high = false;
  float ax, ay, az;
  if (!mpuOk || !mpuReadAccel(ax, ay, az)) return;
  // Slow EMA (~1s time constant at this loop's ~10ms tick) - a hand-carried box swings
  // through a full gait cycle (0.4-0.7s) each stride, and that swing has to average out
  // before "which axis is gravity" means anything, not just track the latest swing.
  const float alpha = 0.01f;
  gxAvg += alpha * (ax - gxAvg);
  gyAvg += alpha * (ay - gyAvg);
  gzAvg += alpha * (az - gzAvg);

  float a = sqrt(ax*ax + ay*ay + az*az); // in g
  unsigned long now = millis();
  // 250ms debounce: a human stride can't land faster than ~4/s, so a second crossing
  // inside that window is arm-swing bounce on the same step, not a new one. Without this
  // the raw threshold double-triggers per stride, which both overcounts steps and feeds
  // the cadence check pairs of intervals too uneven to ever call steady.
  if (!high && a > 1.20f && (lastStepAt == 0 || now - lastStepAt >= 250)) {
    high = true; steps++;
    if (lastStepAt > 0) {
      float interval = (now - lastStepAt) / 1000.0f;
      // CALIBRATE: 1.0-4.0 steps/s. Spec said 1.4-2.6, but a real hand-carried brisk walk
      // sits right against that ceiling, and the 250ms debounce above already permits
      // intervals down to 4 steps/s - capping inRange below that let the debounce's own
      // fastest-allowed intervals fall outside it and reset the streak on a normal stride.
      bool inRange = interval >= (1.0f / 4.0f) && interval <= (1.0f / 1.0f);
      // "Steady" just means the rate stays in-range, not that consecutive strides are
      // near-identical - a real hand-carried walk varies stride to stride more than that,
      // and the gait-broke-off reset below (>1500ms gap) already catches an actual stop.
      steadyGaitMs = inRange ? steadyGaitMs + (now - lastStepAt) : 0;
    }
    lastStepAt = now;
  }
  if (high && a < 1.05f) { high = false; }
  if (lastStepAt > 0 && now - lastStepAt > 1500) { steadyGaitMs = 0; }  // gait broke off
}

// Phone bay + lid (task 5/7): both switches are to GND when closed, so LOW = phone in / lid
// shut. Debounced like any mechanical switch (50 ms settle), and only notified on a real
// change - Linux tracks the last value it heard, so a resend on every tick would be no-ops
// but a changed value arriving late (debounce) would still be the thing woken up on.
void checkPhoneLid() {
  const unsigned long DEBOUNCE_MS = 50;
  static bool lastPhoneRaw = HIGH, phoneChanging = false;
  static unsigned long phoneChangedAt = 0;
  bool phoneRaw = digitalRead(PIN_PHONE);
  if (phoneRaw != lastPhoneRaw) { lastPhoneRaw = phoneRaw; phoneChangedAt = millis(); phoneChanging = true; }
  if (phoneChanging && millis() - phoneChangedAt > DEBOUNCE_MS) {
    phoneChanging = false;
    bool phoneNow = (phoneRaw == LOW);
    if (phoneNow != phoneIn) { phoneIn = phoneNow; Bridge.notify("phone", phoneIn ? 1 : 0); }
  }

  static bool lastLidRaw = HIGH, lidChanging = false;
  static unsigned long lidChangedAt = 0;
  bool lidRaw = digitalRead(PIN_LID);
  if (lidRaw != lastLidRaw) { lastLidRaw = lidRaw; lidChangedAt = millis(); lidChanging = true; }
  if (lidChanging && millis() - lidChangedAt > DEBOUNCE_MS) {
    lidChanging = false;
    bool lidNow = (lidRaw == LOW);
    if (lidNow != lidShut) { lidShut = lidNow; Bridge.notify("lid", lidShut ? 1 : 0); }
  }
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

// BH1750 gives lux directly (one channel, unlike the old LTR390's separate ALS/UVS pair), so
// als just mirrors lux here - kept as its own field so the "sensors" message shape (and the
// dashboard parsing it) doesn't change. UV comes from the GUVA-S12SD's analog output: raw ADC
// counts, UNCALIBRATED, same as the old UVS field was - CALIBRATE against a known UV index source.
void readLight(float &lux, float &als, float &uvs) {
  if (!HAS_LIGHT_UV || !bh1750Ok) { lux = -1.0f; als = -1.0f; } else { lux = bh1750Read(); als = lux; }
  uvs = HAS_LIGHT_UV ? analogRead(PIN_UV) : -1.0f;
}

void setup() {
  pinMode(PIN_MAGNET, OUTPUT);   digitalWrite(PIN_MAGNET, LOW);   // boot unlocked
  pinMode(PIN_BUZZER, OUTPUT);   digitalWrite(PIN_BUZZER, LOW);
  pinMode(PIN_OVERRIDE, INPUT_PULLUP);
  pinMode(PIN_TALK, INPUT_PULLUP);
  pinMode(PIN_PHONE, INPUT_PULLUP);
  pinMode(PIN_LID, INPUT_PULLUP);

  Bridge.begin();
  Bridge.provide_safe("heartbeat", hHeartbeat);
  Bridge.provide_safe("release",   hRelease);
  Bridge.provide_safe("lock",      hLock);
  Bridge.provide_safe("alarm",     hAlarm);
  Bridge.provide_safe("set_alarm", hSetAlarm);
  Bridge.provide_safe("rtc_set",   hRtcSet);

  Wire2.begin();
  mpuOk = mpuBegin();
  bh1750Ok = HAS_LIGHT_UV && bh1750Begin();
  lastHeartbeat = millis();

  Wire2.beginTransmission(DS3231_ADDR);
  rtcOk = (Wire2.endTransmission() == 0);
  // rtc_get: no call-with-reply in this Bridge, so the "rtc" notify is sent periodically from
  // loop() below instead of once here - a one-shot notify in setup() fires before the Python
  // container's Bridge handlers exist (MCU boots before Linux does), so it was always lost.
}

void loop() {
  unsigned long now = millis();
  updateSteps();
  checkPhoneLid();

  // --- safety, always first ---
  if (digitalRead(PIN_OVERRIDE) == LOW && locked) {
    setLock(false); alarmOn = false; digitalWrite(PIN_BUZZER, LOW);
    Bridge.notify("override", 1);
  }
  if (locked && (now - lastHeartbeat > WATCHDOG_MS)) setLock(false);   // Linux went quiet
  if (locked && (now - lockedAt > maxLockMs))        setLock(false);   // max lock time
  // Wall-clock compare when the RTC answered at boot (survives a power cycle); millis()
  // countdown otherwise (survives Linux crashing/hanging, same as before this RTC path existed).
  bool alarmDue = alarmAtUnix ? (rtcNowUnix >= alarmAtUnix) : (now >= alarmAtMs);
  if (alarmArmed && alarmDue) { alarmArmed = false; alarmOn = true; }  // rings with or without Linux

  // --- talk button (debounced) ---
  static unsigned long talkAt = 0;
  if (digitalRead(PIN_TALK) == LOW && now - talkAt > 1500) { talkAt = now; Bridge.notify("talk", 1); }

  // --- alarm pattern: 300 ms on / 700 ms off ---
  digitalWrite(PIN_BUZZER, (alarmOn && (now % 1000) < 300) ? HIGH : LOW);

  // --- 1 Hz sensor report ---
  static unsigned long lastReport = 0;
  if (now - lastReport >= 1000) {
    lastReport = now;
    if (rtcOk) { rtcNowUnix = rtcRead(); Bridge.notify("rtc", rtcNowUnix); }
    float lux, als, uvs;
    readLight(lux, als, uvs);
    float fl = flickerRatio();
    String msg = String(lux, 0) + "," + String(uvs, 0) + "," + String(als, 0) + "," +
                 String(fl, 3) + "," + String(steps) + "," +
                 String(mpuOk && cadenceOk() ? 1 : 0) + "," + String(mpuOk && uprightOk() ? 1 : 0);
    Bridge.notify("sensors", msg);
  }
  delay(10);
}
