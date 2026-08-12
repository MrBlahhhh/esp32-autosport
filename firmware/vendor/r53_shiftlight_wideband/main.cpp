// R53 Shift Light + Wideband BLE Bridge — Waveshare ESP32-S3-Zero
//
// The car's CAN shift light (esp32-canbus-SN65HVD230-v2) with the wideband BLE
// bridge folded in: RPM off CAN drives an 8-LED WS2812B strip, the wideband is
// read on the ADC and notified to the phone app. The two halves are
// independent — the light works with no phone connected.
//
// Pins (S3-Zero silkscreen = GPIO number):
//   4  WS2812B DIN
//   5  CAN transceiver TXD      6  CAN transceiver RXD
//   1  wideband via 10k/10k divider (ADC1 — do NOT feed 0–5 V in raw)
//   7  ADS1115 SDA (optional)   8  ADS1115 SCL (optional)
//
// GATT must match WidebandBleManager in the logger app.
// Libraries: FastLED 3.7+, NimBLE-Arduino 2.x. CAN uses the core's TWAI driver.

#include <Arduino.h>
#include <FastLED.h>
#include <NimBLEDevice.h>
#include <Wire.h>
#include <driver/twai.h>
#include <math.h>

#define LED_PIN 4
#define NUM_LEDS 8
#define LED_TYPE WS2812B
#define COLOR_ORDER GRB
//#define SIMULATE_RPM      // Uncomment to sweep RPM instead of reading CAN

#define RPM_STALE_MS 2000   // blank the strip if CAN goes quiet

// CAN pins and frame decode below are lifted from the working car sketch,
// esp32-canbus-SN65HVD230-v2 — same board, same transceiver, proven on the R53.
// Do not "fix" them from first principles; that repo is the reference.
#define CAN_TX_PIN GPIO_NUM_5
#define CAN_RX_PIN GPIO_NUM_6

// 500 kbit/s, id 0x316, RPM in bytes 2-3 little-endian ÷ 6.4.
#define CAN_RPM_ID 0x316

CRGB leds[NUM_LEDS];
uint16_t rpm = 0;
bool redBlinkState = false;
unsigned long lastBlinkTime = 0;
const unsigned long blinkInterval = 100; // 100ms for 5Hz blink at 7100+ RPM
unsigned long lastSimTime = 0;
const unsigned long simInterval = 100;
const unsigned long simPeriod = 10000;
unsigned long lastRpmMs = 0;
bool canUp = false;

unsigned long canFrames = 0;   // frames since the last status line

// Wideband bridge
static const char* SERVICE_UUID      = "4fafc201-1fb5-459e-8fcc-c5c9c331914b";
static const char* VOLTS_CHAR_UUID   = "beb5483e-36e1-4688-b7f5-ea07361b26a8";
static const char* HW_MODE_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9";

static const float DIVIDER_GAIN = 2.0f;   // sensor_V = pin_V * gain
static const int   ESP_ADC_PIN  = 1;      // GPIO1 = ADC1_CH0
static const int   ADS_SDA_PIN  = 7;
static const int   ADS_SCL_PIN  = 8;
static const uint8_t ADS_ADDR   = 0x48;
static const float   ADS_LSB_MV = 0.125f; // GAIN_ONE (±4.096 V)

enum HwMode : uint8_t { HW_RESISTOR = 0, HW_ADS1115 = 1 };

static const char* DEVICE_NAME = "R53-Wideband";

// The strip is redrawn every LOOP_PERIOD_MS; the wideband is notified more
// slowly. 50 Hz notifications outrun the connection interval a phone actually
// negotiates, which backs up NimBLE's buffers until the link drops — and the
// sensor itself only responds in ~100 ms, so 20 Hz loses nothing.
static const uint32_t LOOP_PERIOD_MS   = 20;
static const uint32_t NOTIFY_PERIOD_MS = 50;
static const uint32_t BLE_RETRY_MS     = 5000;

NimBLECharacteristic* voltsCharacteristic  = nullptr;
NimBLECharacteristic* hwModeCharacteristic = nullptr;
bool clientConnected = false;
bool bleReady        = false;   // stack up, service started, advertising began
uint32_t bleRetryAtMs = 0;
uint32_t lastNotifyMs = 0;
// Set from the NimBLE host task, read from loop(). Tracked by hand because this
// NimBLE has no getSubscribedCount() — and it is worth tracking: a phone is
// connected for a beat before it writes the CCCD.
volatile bool voltsSubscribed = false;

volatile uint8_t hwMode = HW_RESISTOR;
bool adsPresent = false;
bool adsTried   = false;

void updateLEDs();
void simulateRPM();
bool bleInit();
void bleWatchdog();

/**
 * One status line a second — the only routine output, and the thing that says
 * which half is unhappy without a scope. Frames counts EVERY id on the bus, so
 * "frames 0" is a wiring/transceiver problem while "frames high, rpm 0" is a
 * decode problem. Per-frame printing (what the v2 sketch does) costs more time
 * than the 20 ms loop has once the bus is busy.
 */
void printStatus() {
  static unsigned long last = 0;
  unsigned long now = millis();
  if (now - last < 1000) return;
  last = now;
  const char* ble;
  if (!bleReady)              ble = "DOWN";        // stack never came up
  else if (clientConnected)   ble = "connected";
  else if (NimBLEDevice::getAdvertising()->isAdvertising()) ble = "advertising";
  else                        ble = "IDLE";        // up but invisible — a bug
  Serial.printf("rpm %4u  can %s frames/s %-4lu  ble %s\n",
                rpm,
                canUp ? "up" : "DOWN",
                canFrames,
                ble);
  canFrames = 0;
}

// --- CAN --------------------------------------------------------------------
void canInit() {
  // NORMAL, matching the proven car sketch. LISTEN_ONLY would be the textbook
  // choice for a receive-only node, but this configuration is the one that has
  // actually run on the car — swapping it here would trade a known-good setup
  // for an untested one.
  twai_general_config_t g =
      TWAI_GENERAL_CONFIG_DEFAULT(CAN_TX_PIN, CAN_RX_PIN, TWAI_MODE_NORMAL);
  g.rx_queue_len = 32;  // FastLED.show() blocks ~1 ms; don't drop frames in it
  twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();

  if (twai_driver_install(&g, &t, &f) != ESP_OK || twai_start() != ESP_OK) {
    Serial.println("TWAI: init failed");
    return;
  }
  canUp = true;
  Serial.println("TWAI initialized");
}

void canPoll() {
  if (!canUp) return;
  twai_message_t msg;
  // Drain the queue: a busy bus delivers many frames per 20 ms tick.
  while (twai_receive(&msg, 0) == ESP_OK) {
    canFrames++;
    if (msg.extd || msg.rtr || msg.identifier != CAN_RPM_ID) continue;
    if (msg.data_length_code < 4) continue;
    uint16_t raw = msg.data[2] | ((uint16_t)msg.data[3] << 8);
    rpm = (uint16_t)constrain(raw / 6.4f, 0.0f, 9000.0f);
    lastRpmMs = millis();
  }
  // Bus-off stays dead until told to recover; without this one fault at key-on
  // would leave the strip dark for the whole drive.
  twai_status_info_t st;
  if (twai_get_status_info(&st) == ESP_OK && st.state == TWAI_STATE_BUS_OFF) {
    twai_initiate_recovery();
  }
}

// --- Wideband ADC -----------------------------------------------------------
float readEspAdcSensorVolts() {
  // analogReadMilliVolts applies the chip's eFuse calibration; the raw count
  // curve is non-linear enough to cost accuracy at both ends of the range.
  float v = (analogReadMilliVolts(ESP_ADC_PIN) / 1000.0f) * DIVIDER_GAIN;
  return constrain(v, 0.0f, 5.5f);
}

bool adsWriteReg(uint8_t reg, uint16_t value) {
  Wire.beginTransmission(ADS_ADDR);
  Wire.write(reg);
  Wire.write((uint8_t)(value >> 8));
  Wire.write((uint8_t)(value & 0xFF));
  return Wire.endTransmission() == 0;
}

bool adsReadReg(uint8_t reg, uint16_t* out) {
  Wire.beginTransmission(ADS_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(ADS_ADDR, (uint8_t)2) != 2) return false;
  uint16_t hi = Wire.read();
  uint16_t lo = Wire.read();
  *out = (hi << 8) | lo;
  return true;
}

/** Lazy I²C — only touch the bus when the phone asks for ADS1115 mode. */
void ensureAdsProbed() {
  if (adsTried) return;
  adsTried = true;
  Wire.begin(ADS_SDA_PIN, ADS_SCL_PIN);
  Wire.setClock(400000);
  // Continuous AIN0, ±4.096 V, 128 SPS.
  if (!adsWriteReg(0x01, 0x4283)) return;
  delay(10);
  uint16_t check = 0;
  adsPresent = adsReadReg(0x01, &check);
}

float readAdsSensorVolts() {
  ensureAdsProbed();
  if (!adsPresent) return NAN;
  uint16_t raw = 0;
  if (!adsReadReg(0x00, &raw)) return NAN;
  float v = (((int16_t)raw) * ADS_LSB_MV / 1000.0f) * DIVIDER_GAIN;
  return constrain(v, 0.0f, 5.5f);
}

float readSensorVolts() {
  if (hwMode == HW_ADS1115) {
    float v = readAdsSensorVolts();
    if (isfinite(v)) return v;
  }
  return readEspAdcSensorVolts();
}

// --- BLE --------------------------------------------------------------------
class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* server, NimBLEConnInfo& info) override {
    clientConnected = true;
    // Ask for a link the phone can hold: 15–30 ms interval, no slave latency,
    // 4 s supervision timeout. The default timeout can run to 20 s, and while
    // it counts down after a dropout the server is neither connected nor
    // advertising — twenty seconds of "no wideband bridge found" on the phone.
    server->updateConnParams(info.getConnHandle(), 12, 24, 0, 400);
  }
  void onDisconnect(NimBLEServer*, NimBLEConnInfo&, int) override {
    clientConnected = false;
    // Cleared here, not left to onSubscribe(0): a link that drops out of range
    // or times out never delivers an unsubscribe, and a stale true would have
    // us notifying into nothing until the next client happened to connect.
    voltsSubscribed = false;
    // Deliberately NOT calling startAdvertising() here: this runs on the NimBLE
    // host task while the connection is still being torn down, and its failure
    // return has nowhere to go. advertiseOnDisconnect() restarts it at a safe
    // point and bleWatchdog() catches it if that ever doesn't take.
  }
};

class VoltsCallbacks : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic*, NimBLEConnInfo&, uint16_t subValue) override {
    // subValue: bit 0 notifications, bit 1 indications, 0 = client unsubscribed.
    voltsSubscribed = (subValue != 0);
  }
};

class HwModeCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* c, NimBLEConnInfo&) override {
    NimBLEAttValue v = c->getValue();
    if (v.length() < 1) return;
    uint8_t mode = v[0];
    if (mode == HW_RESISTOR || mode == HW_ADS1115) {
      hwMode = mode;
      c->setValue(&mode, 1);
    }
  }
};

/**
 * Bring the whole BLE stack up, checking every step. Returns false — leaving
 * the stack torn back down so the next attempt starts clean — if any of them
 * fails. Every one of these calls returns a status in NimBLE 2.x; ignoring them
 * is exactly how the board ends up running the shift light perfectly with no
 * BLE at all and nothing on the terminal to say why.
 */
bool bleInit() {
  voltsCharacteristic  = nullptr;
  hwModeCharacteristic = nullptr;

  if (!NimBLEDevice::init(DEVICE_NAME)) {
    Serial.println("BLE: NimBLEDevice::init() failed");
    return false;
  }
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);

  NimBLEServer* server = NimBLEDevice::createServer();
  if (server == nullptr) {
    Serial.println("BLE: createServer() failed");
    NimBLEDevice::deinit(true);
    return false;
  }
  server->setCallbacks(new ServerCallbacks());
  server->advertiseOnDisconnect(true);

  NimBLEService* service = server->createService(SERVICE_UUID);
  if (service == nullptr) {
    Serial.println("BLE: createService() failed");
    NimBLEDevice::deinit(true);
    return false;
  }
  voltsCharacteristic = service->createCharacteristic(
      VOLTS_CHAR_UUID, NIMBLE_PROPERTY::NOTIFY);
  hwModeCharacteristic = service->createCharacteristic(
      HW_MODE_CHAR_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE);
  if (voltsCharacteristic == nullptr || hwModeCharacteristic == nullptr) {
    Serial.println("BLE: createCharacteristic() failed");
    voltsCharacteristic = nullptr;
    hwModeCharacteristic = nullptr;
    NimBLEDevice::deinit(true);
    return false;
  }
  voltsCharacteristic->setCallbacks(new VoltsCallbacks());
  hwModeCharacteristic->setCallbacks(new HwModeCallbacks());
  uint8_t hwModeInit = hwMode;
  hwModeCharacteristic->setValue(&hwModeInit, 1);
  // No service->start() — it is a deprecated no-op in this NimBLE (`return
  // true`); services are started with the server.
  voltsSubscribed = false;

  // The phone filters the scan on the 128-bit service UUID, so that UUID has to
  // be advertised or nothing will ever match. 18 bytes of UUID + 3 of flags
  // fills most of the 31-byte advert, which is why the name goes in the scan
  // response instead of competing for room with it.
  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  adv->setName(DEVICE_NAME);
  adv->addServiceUUID(SERVICE_UUID);
  adv->enableScanResponse(true);
  if (!adv->start()) {
    Serial.println("BLE: advertising failed to start");
    voltsCharacteristic = nullptr;
    hwModeCharacteristic = nullptr;
    NimBLEDevice::deinit(true);
    return false;
  }

  bleReady = true;
  Serial.printf("BLE: advertising as %s (free heap %u)\n",
                DEVICE_NAME, (unsigned)ESP.getFreeHeap());
  return true;
}

/**
 * Two failures this recovers from, both of which used to need a power cycle:
 * a bring-up that never succeeded (retry it), and a stack that is up but has
 * silently stopped advertising while nothing is connected (restart it).
 */
void bleWatchdog() {
  uint32_t now = millis();

  if (!bleReady) {
    if ((int32_t)(now - bleRetryAtMs) < 0) return;
    bleRetryAtMs = now + BLE_RETRY_MS;
    Serial.printf("BLE: retrying bring-up (free heap %u)\n",
                  (unsigned)ESP.getFreeHeap());
    bleInit();
    return;
  }

  static uint32_t lastCheck = 0;
  if (now - lastCheck < 1000) return;
  lastCheck = now;
  if (!clientConnected && !NimBLEDevice::getAdvertising()->isAdvertising()) {
    Serial.println("BLE: advertising had stopped — restarting");
    NimBLEDevice::getAdvertising()->start();
  }
}

// ----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  // Serial on this board is USB-CDC: the port only enumerates once the sketch
  // runs, so without a short wait the boot lines are gone before any monitor
  // can attach and the terminal looks dead. Bounded — the car has no host.
  unsigned long serialWait = millis();
  while (!Serial && millis() - serialWait < 1500) delay(10);

  // BLE goes first, on purpose. The BT controller wants a sizeable block of
  // internal RAM (it cannot use PSRAM) and it is the one subsystem here that
  // fails silently — FastLED's RMT buffers and TWAI's rx queue both succeed on
  // whatever is left. Claiming the radio's memory before them turns "BLE
  // sometimes doesn't come up" into a problem the watchdog can retry rather
  // than one only a power cycle fixes.
  if (!bleInit()) {
    bleRetryAtMs = millis() + BLE_RETRY_MS;
    Serial.println("BLE: bring-up failed at boot — will keep retrying");
  }

  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(75);
  FastLED.clear(true);

  // Warm the channel before setting attenuation (required on arduino-esp32 3.x).
  analogReadResolution(12);
  (void)analogRead(ESP_ADC_PIN);
  analogSetPinAttenuation(ESP_ADC_PIN, ADC_11db);

#ifndef SIMULATE_RPM
  canInit();
#endif
}

void loop() {
#ifdef SIMULATE_RPM
  simulateRPM();
#else
  canPoll();
#endif

  bleWatchdog();

  // getSubscribedCount() — not just clientConnected. A phone is connected for
  // a beat before it discovers services and writes the CCCD, and notifying into
  // that gap is pure churn on the link at the least forgiving moment.
  if (bleReady && voltsCharacteristic != nullptr && voltsSubscribed &&
      (uint32_t)(millis() - lastNotifyMs) >= NOTIFY_PERIOD_MS) {
    lastNotifyMs = millis();
    float volts = readSensorVolts();
    if (isfinite(volts)) {
      voltsCharacteristic->setValue((uint8_t*)&volts, sizeof(volts));
      voltsCharacteristic->notify();
    }
  }

  updateLEDs();
  FastLED.show();
  printStatus();
  delay(LOOP_PERIOD_MS);
}

void updateLEDs() {
#if defined(RPM_STALE_MS) && !defined(SIMULATE_RPM)
  if (rpm != 0 && (unsigned long)(millis() - lastRpmMs) > RPM_STALE_MS) rpm = 0;
#endif

  CRGB color;

  // Calculate number of LED pairs to light (0 to 4 pairs)
  int numPairs = constrain(map(rpm, 0, 7100, 0, 4), 0, 4);

  // Determine color based on RPM
  if (rpm < 3000) {
    color = CRGB(0, 0, 0); // Off below 3000 RPM
  } else if (rpm < 6000) {
    // Solid green from 3000 to 6000 RPM
    color = CRGB(0, 255, 0);
  } else if (rpm <= 7100) {
    // Fade from green to red (6000 to 7100 RPM)
    uint8_t t = map(rpm, 6000, 7100, 0, 255);
    uint8_t red = t;
    uint8_t green = 255 - t; // Scale green down (255 to 0)
    color = CRGB(red, green, 0);
  } else {
    // Blink red at 7100+ RPM
    if (millis() - lastBlinkTime >= blinkInterval) {
      redBlinkState = !redBlinkState;
      lastBlinkTime = millis();
    }
    color = redBlinkState ? CRGB(255, 0, 0) : CRGB(0, 0, 0);
    numPairs = 4; // All LEDs blink at 7100+ RPM
  }

  // Set LEDs from ends to center based on numPairs
  for (int i = 0; i < NUM_LEDS; i++) {
    leds[i] = CRGB(0, 0, 0); // Default to off
    if (numPairs >= 1 && (i == 0 || i == 7)) leds[i] = color; // Pair 1: Ends
    if (numPairs >= 2 && (i == 1 || i == 6)) leds[i] = color; // Pair 2
    if (numPairs >= 3 && (i == 2 || i == 5)) leds[i] = color; // Pair 3
    if (numPairs >= 4 && (i == 3 || i == 4)) leds[i] = color; // Pair 4: Center
  }
}

void simulateRPM() {
  unsigned long currentTime = millis();
  if (currentTime - lastSimTime >= simInterval) {
    unsigned long cycleTime = currentTime % simPeriod;
    if (cycleTime < simPeriod / 2) {
      // Ramp up from 1000 to 8000 RPM
      rpm = 1000 + ((cycleTime * 7000) / (simPeriod / 2));
    } else {
      // Ramp down from 8000 to 1000 RPM
      rpm = 8000 - (((cycleTime - simPeriod / 2) * 7000) / (simPeriod / 2));
    }
    lastRpmMs = currentTime;
    lastSimTime = currentTime;
  }
}
