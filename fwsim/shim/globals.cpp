// The singletons the Arduino world expects to already exist, plus the NimBLE
// object graph. Kept apart from sim.cpp so the simulator core has no opinion
// about which framework is being emulated on top of it.

#include "Arduino.h"
#include "FastLED.h"
#include "Wire.h"
#include "NimBLEDevice.h"
#include "SD_MMC.h"

SimSerial Serial;
SimEsp ESP;
CFastLED FastLED;
TwoWire Wire;
SDMMCFS SD_MMC;

uint32_t SimEsp::getFreeHeap() const { return (uint32_t)sim::sim_heap(); }
uint32_t SimEsp::getMinFreeHeap() const { return (uint32_t)sim::sim_heap(); }

// --- NimBLE ----------------------------------------------------------------
//
// One server, one service, and the two characteristics the wideband GATT
// defines. The scenario drives connect/subscribe/write from outside, so these
// globals are how an external event finds its way to the sketch's callbacks.

namespace {

NimBLEServer* g_server = 0;
NimBLEAdvertising g_adv;
NimBLECharacteristic* g_notify_char = 0;   // the NOTIFY one, whichever it is
NimBLECharacteristic* g_write_char = 0;    // the READ|WRITE one

void hook_connect() {
  if (!g_server || !g_server->callbacks()) return;
  NimBLEConnInfo info;
  g_server->callbacks()->onConnect(g_server, info);
}

void hook_disconnect() {
  if (!g_server || !g_server->callbacks()) return;
  NimBLEConnInfo info;
  g_server->callbacks()->onDisconnect(g_server, info, 0x13 /* remote terminated */);
}

void hook_subscribe(uint16_t v) {
  if (!g_notify_char || !g_notify_char->callbacks()) return;
  NimBLEConnInfo info;
  g_notify_char->callbacks()->onSubscribe(g_notify_char, info, v);
}

void hook_hwmode(uint8_t v) {
  if (!g_write_char || !g_write_char->callbacks()) return;
  g_write_char->setValue(&v, 1);
  NimBLEConnInfo info;
  g_write_char->callbacks()->onWrite(g_write_char, info);
}

void install_hooks() {
  sim::BleHooks h;
  h.on_connect = hook_connect;
  h.on_disconnect = hook_disconnect;
  h.on_subscribe = hook_subscribe;
  h.on_hwmode_write = hook_hwmode;
  sim::ble_set_hooks(h);
}

}  // namespace

bool NimBLECharacteristic::notify() {
  if (!sim::ble_connected()) return false;
  sim::ble_note_notify(value_.empty() ? 0 : &value_[0], value_.size());
  return true;
}

NimBLECharacteristic* NimBLEService::createCharacteristic(const char* uuid, uint32_t props) {
  NimBLECharacteristic* c = new NimBLECharacteristic(uuid ? uuid : "", props);
  chars_.push_back(c);
  if (props & NIMBLE_PROPERTY::NOTIFY) g_notify_char = c;
  else if (props & NIMBLE_PROPERTY::WRITE) g_write_char = c;
  return c;
}

NimBLEService* NimBLEServer::createService(const char* uuid) {
  NimBLEService* s = new NimBLEService(uuid ? uuid : "");
  services_.push_back(s);
  return s;
}

bool NimBLEServer::updateConnParams(uint16_t, uint16_t min_i, uint16_t max_i, uint16_t lat, uint16_t timeout) {
  // 1.25 ms units for the interval, 10 ms for the supervision timeout. The
  // check the sketch's comment is really making is that the link survives a
  // dropout quickly, so flag a timeout long enough to leave the phone staring
  // at nothing.
  if (timeout * 10 > 6000)
    sim::fault(sim::SEV_WARN, "BLE_SUPERVISION",
               "supervision timeout %d ms: after a dropout the server is neither connected nor "
               "advertising for that long", timeout * 10);
  if (min_i > max_i)
    sim::fault(sim::SEV_ERROR, "BLE_CONN_PARAMS", "min interval %d > max %d", min_i, max_i);
  (void)lat;
  return true;
}

bool NimBLEAdvertising::start() {
  active_ = sim::ble_adv_start();
  return active_;
}

bool NimBLEDevice::init(const std::string& name) {
  bool ok = sim::ble_init(name.c_str());
  if (ok) install_hooks();
  return ok;
}

void NimBLEDevice::deinit(bool) {
  sim::ble_deinit();
  g_server = 0;
  g_notify_char = 0;
  g_write_char = 0;
}

bool NimBLEDevice::setPower(esp_power_level_t) { return true; }

NimBLEServer* NimBLEDevice::createServer() {
  if (!g_server) g_server = new NimBLEServer();
  return g_server;
}

NimBLEAdvertising* NimBLEDevice::getAdvertising() { return &g_adv; }
