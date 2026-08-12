// Host stand-in for FastLED. Enough of CRGB and CFastLED for a shift-light
// sketch; show() reports the frame to the simulator and charges the loop the
// time a real WS2812 refresh costs, because on a 20 ms loop that time is not
// free and the CAN queue keeps filling while it runs.
#ifndef FASTLED_H
#define FASTLED_H

#include <stdint.h>
#include <vector>
#include "sim.h"

typedef enum { RGB = 0012, RBG = 0021, GRB = 0102, GBR = 0120, BRG = 0201, BGR = 0210 } EOrder;

struct CRGB {
  uint8_t r, g, b;
  CRGB() : r(0), g(0), b(0) {}
  CRGB(uint8_t ir, uint8_t ig, uint8_t ib) : r(ir), g(ig), b(ib) {}
  bool operator==(const CRGB& o) const { return r == o.r && g == o.g && b == o.b; }
  bool operator!=(const CRGB& o) const { return !(*this == o); }
  enum HTMLColorCode { Black = 0x000000, Red = 0xFF0000, Green = 0x008000, Blue = 0x0000FF };
};

template <uint8_t DATA_PIN, EOrder RGB_ORDER>
class WS2812B {};
template <uint8_t DATA_PIN, EOrder RGB_ORDER>
class WS2812 {};
template <uint8_t DATA_PIN, EOrder RGB_ORDER>
class NEOPIXEL {};

class CFastLED {
 public:
  CFastLED() : leds_(0), count_(0), brightness_(255) {}

  template <template <uint8_t, EOrder> class CHIPSET, uint8_t DATA_PIN, EOrder ORDER>
  void addLeds(CRGB* leds, int count) {
    leds_ = leds;
    count_ = count;
    sim::leds_attach(DATA_PIN, leds, count);
  }

  void setBrightness(uint8_t b) { brightness_ = b; }
  uint8_t getBrightness() const { return brightness_; }

  void show() {
    if (!leds_) return;
    // sim::Rgb and CRGB have the same layout, but copy rather than alias it --
    // the shim should not depend on that staying true.
    static std::vector<sim::Rgb> px;
    px.resize(count_);
    for (int i = 0; i < count_; i++) {
      px[i].r = leds_[i].r;
      px[i].g = leds_[i].g;
      px[i].b = leds_[i].b;
    }
    sim::leds_show(px.empty() ? 0 : &px[0], count_, brightness_);
  }

  void clear(bool write = false) {
    for (int i = 0; i < count_; i++) leds_[i] = CRGB(0, 0, 0);
    if (write) show();
  }

  void clearData() { clear(false); }
  void setMaxPowerInVoltsAndMilliamps(uint8_t, uint32_t) {}
  void delay(unsigned long ms) { sim::advance_us((uint64_t)ms * 1000ULL); }

 private:
  CRGB* leds_;
  int count_;
  uint8_t brightness_;
};

extern CFastLED FastLED;

#endif  // FASTLED_H
