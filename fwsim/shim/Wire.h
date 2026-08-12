// Host stand-in for the Arduino TwoWire API, backed by the simulator's I2C
// model. Wire.begin(sda, scl) is checked against the board: a bus brought up on
// pins this board uses for something else fails here rather than silently
// reading zeroes.
#ifndef WIRE_H
#define WIRE_H

#include <stdint.h>
#include <vector>
#include "sim.h"

class TwoWire {
 public:
  TwoWire() : addr_(0), clock_(100000), rx_pos_(0), begun_(false) {}

  bool begin(int sda, int scl) {
    sim::i2c_begin(sda, scl);
    begun_ = true;
    return true;
  }
  bool begin() { return begin(-1, -1); }
  void setClock(uint32_t hz) { clock_ = hz; }
  void end() { begun_ = false; }

  void beginTransmission(uint8_t addr) {
    addr_ = addr;
    tx_.clear();
  }
  size_t write(uint8_t b) { tx_.push_back(b); return 1; }

  // Returns 0 on success, matching Arduino. A repeated start (stop == false)
  // leaves the register pointer set for the requestFrom() that follows.
  uint8_t endTransmission(bool stop = true) {
    if (tx_.empty()) return 4;
    uint8_t reg = tx_[0];
    if (tx_.size() >= 3) {
      uint16_t value = (uint16_t)((tx_[1] << 8) | tx_[2]);
      bool ok = sim::i2c_write_reg(addr_, reg, value);
      pending_reg_ = reg;
      return ok ? 0 : 2;
    }
    pending_reg_ = reg;
    uint16_t probe = 0;
    bool ok = sim::i2c_read_reg(addr_, reg, &probe);
    (void)stop;
    return ok ? 0 : 2;
  }

  uint8_t requestFrom(uint8_t addr, uint8_t count) {
    rx_.clear();
    rx_pos_ = 0;
    uint16_t v = 0;
    if (!sim::i2c_read_reg(addr, pending_reg_, &v)) return 0;
    rx_.push_back((uint8_t)(v >> 8));
    rx_.push_back((uint8_t)(v & 0xFF));
    if (count < rx_.size()) rx_.resize(count);
    return (uint8_t)rx_.size();
  }
  uint8_t requestFrom(int addr, int count) { return requestFrom((uint8_t)addr, (uint8_t)count); }

  int available() { return (int)(rx_.size() - rx_pos_); }
  int read() { return rx_pos_ < rx_.size() ? rx_[rx_pos_++] : -1; }

 private:
  uint8_t addr_;
  uint8_t pending_reg_ = 0;
  uint32_t clock_;
  std::vector<uint8_t> tx_, rx_;
  size_t rx_pos_;
  bool begun_;
};

extern TwoWire Wire;

#endif  // WIRE_H
