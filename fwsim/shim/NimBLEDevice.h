// Host stand-in for NimBLE-Arduino 2.x. The API surface is only what the
// sketches use, but the *semantics* that matter are modelled: createServer()
// and advertising can be made to fail, a connect happens before the CCCD write,
// and a dropped link never delivers an unsubscribe. Those are the cases the
// real sketch has comments about, so they are the cases worth being able to
// reproduce on demand.
#ifndef NIMBLEDEVICE_H
#define NIMBLEDEVICE_H

#include <stdint.h>
#include <string>
#include <vector>

#include "esp_err.h"
#include "sim.h"

namespace NIMBLE_PROPERTY {
static const uint32_t READ = 0x0002;
static const uint32_t WRITE = 0x0008;
static const uint32_t NOTIFY = 0x0010;
static const uint32_t INDICATE = 0x0020;
}  // namespace NIMBLE_PROPERTY

class NimBLEServer;
class NimBLEService;
class NimBLECharacteristic;

class NimBLEConnInfo {
 public:
  NimBLEConnInfo() : handle_(1) {}
  uint16_t getConnHandle() const { return handle_; }
  std::string getAddress() const { return "aa:bb:cc:dd:ee:ff"; }

 private:
  uint16_t handle_;
};

class NimBLEAttValue {
 public:
  NimBLEAttValue() {}
  explicit NimBLEAttValue(const std::vector<uint8_t>& d) : data_(d) {}
  size_t length() const { return data_.size(); }
  uint8_t operator[](size_t i) const { return i < data_.size() ? data_[i] : 0; }
  const uint8_t* data() const { return data_.empty() ? 0 : &data_[0]; }

 private:
  std::vector<uint8_t> data_;
};

class NimBLECharacteristicCallbacks {
 public:
  virtual ~NimBLECharacteristicCallbacks() {}
  virtual void onRead(NimBLECharacteristic*, NimBLEConnInfo&) {}
  virtual void onWrite(NimBLECharacteristic*, NimBLEConnInfo&) {}
  virtual void onSubscribe(NimBLECharacteristic*, NimBLEConnInfo&, uint16_t) {}
  virtual void onStatus(NimBLECharacteristic*, int) {}
};

class NimBLEServerCallbacks {
 public:
  virtual ~NimBLEServerCallbacks() {}
  virtual void onConnect(NimBLEServer*, NimBLEConnInfo&) {}
  virtual void onDisconnect(NimBLEServer*, NimBLEConnInfo&, int) {}
  virtual void onMTUChange(uint16_t, NimBLEConnInfo&) {}
};

class NimBLECharacteristic {
 public:
  NimBLECharacteristic(const std::string& uuid, uint32_t props)
      : uuid_(uuid), props_(props), cb_(0) {}

  void setCallbacks(NimBLECharacteristicCallbacks* cb) { cb_ = cb; }
  NimBLECharacteristicCallbacks* callbacks() const { return cb_; }

  void setValue(const uint8_t* data, size_t len) {
    value_.assign(data, data + len);
  }
  template <typename T>
  void setValue(const T& v) {
    const uint8_t* p = reinterpret_cast<const uint8_t*>(&v);
    value_.assign(p, p + sizeof(T));
  }
  NimBLEAttValue getValue() const { return NimBLEAttValue(value_); }

  bool notify();
  bool indicate() { return notify(); }
  const std::string& uuid() const { return uuid_; }

 private:
  std::string uuid_;
  uint32_t props_;
  NimBLECharacteristicCallbacks* cb_;
  std::vector<uint8_t> value_;
};

class NimBLEService {
 public:
  explicit NimBLEService(const std::string& uuid) : uuid_(uuid) {}
  NimBLECharacteristic* createCharacteristic(const char* uuid, uint32_t props);
  bool start() { return true; }  // deprecated no-op in NimBLE 2.x, as in the sketch

 private:
  std::string uuid_;
  std::vector<NimBLECharacteristic*> chars_;
};

class NimBLEAdvertising {
 public:
  NimBLEAdvertising() : active_(false) {}
  void setName(const char* n) { name_ = n ? n : ""; }
  void addServiceUUID(const char* u) { uuid_ = u ? u : ""; }
  void enableScanResponse(bool) {}
  void setMinInterval(uint16_t) {}
  void setMaxInterval(uint16_t) {}
  bool start();
  bool stop() { active_ = false; return true; }
  bool isAdvertising() const { return sim::ble_adv_active(); }

 private:
  std::string name_, uuid_;
  bool active_;
};

class NimBLEServer {
 public:
  NimBLEServer() : cb_(0) {}
  void setCallbacks(NimBLEServerCallbacks* cb) { cb_ = cb; }
  NimBLEServerCallbacks* callbacks() const { return cb_; }
  void advertiseOnDisconnect(bool on) { adv_on_disc_ = on; }
  bool advertiseOnDisconnect() const { return adv_on_disc_; }
  NimBLEService* createService(const char* uuid);
  bool updateConnParams(uint16_t, uint16_t min_i, uint16_t max_i, uint16_t lat, uint16_t timeout);
  void disconnect(uint16_t) {}

 private:
  NimBLEServerCallbacks* cb_;
  bool adv_on_disc_ = false;
  std::vector<NimBLEService*> services_;
};

class NimBLEDevice {
 public:
  static bool init(const std::string& name);
  static void deinit(bool clear_all);
  static bool setPower(esp_power_level_t lvl);
  static NimBLEServer* createServer();
  static NimBLEAdvertising* getAdvertising();
  static void setMTU(uint16_t) {}
};

#endif  // NIMBLEDEVICE_H
