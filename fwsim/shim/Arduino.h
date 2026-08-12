// Host stand-in for the arduino-esp32 core. Only what the sketches under test
// actually call -- adding more is cheap, guessing at semantics is not.
#ifndef ARDUINO_H
#define ARDUINO_H

#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
// Pulled in before min/max become macros further down: a macro named min()
// walks straight into libstdc++'s own use of std::min and the error lands
// hundreds of lines deep in a standard header.
#include <algorithm>
#include <string>
#include <vector>

#include "sim.h"

// On the ESP32 core <math.h> leaves isfinite/isnan/isinf as macros and sketches
// use them unqualified. libstdc++ undefines them in favour of the std:: forms,
// so hoist those back into the global namespace -- otherwise a sketch that
// compiles for the target fails here for a reason that has nothing to do with
// the firmware.
#include <cmath>
using std::isfinite;
using std::isnan;
using std::isinf;

#define HIGH 1
#define LOW  0
#define INPUT        0x01
#define OUTPUT       0x03
#define INPUT_PULLUP 0x05
#define INPUT_PULLDOWN 0x09

// Placement attributes: meaningless on the host, required for the sketch to
// compile unchanged.
#define IRAM_ATTR
#define DRAM_ATTR
#define ICACHE_RAM_ATTR

#define RISING  0x01
#define FALLING 0x02
#define CHANGE  0x03

// ADC attenuation, arduino-esp32 names.
typedef enum { ADC_0db = 0, ADC_2_5db = 1, ADC_6db = 2, ADC_11db = 3, ADC_12db = 3 } adc_attenuation_t;

typedef bool boolean;
typedef uint8_t byte;

#define constrain(amt, low, high) ((amt) < (low) ? (low) : ((amt) > (high) ? (high) : (amt)))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define max(a, b) ((a) > (b) ? (a) : (b))

inline long map(long x, long in_min, long in_max, long out_min, long out_max) {
  return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
}

inline unsigned long millis() { return (unsigned long)(sim::now_us() / 1000ULL); }
inline unsigned long micros() { return (unsigned long)sim::now_us(); }
inline void delay(unsigned long ms) { sim::advance_us((uint64_t)ms * 1000ULL); }
inline void delayMicroseconds(unsigned int us) { sim::advance_us(us); }

inline void pinMode(int pin, int mode) { sim::pin_mode(pin, mode); }
inline void digitalWrite(int pin, int level) { sim::pin_write(pin, level); }
inline int  digitalRead(int pin) { return sim::pin_read(pin); }
inline void attachInterrupt(int pin, void (*fn)(), int mode) { sim::attach_isr(pin, fn, mode); }
inline int  digitalPinToInterrupt(int pin) { return pin; }

inline int  analogRead(int pin) { return sim::adc_read_raw(pin); }
inline uint32_t analogReadMilliVolts(int pin) { return (uint32_t)sim::adc_read_mv(pin); }
inline void analogReadResolution(int bits) { sim::adc_set_resolution(bits); }
inline void analogSetPinAttenuation(int pin, adc_attenuation_t a) { sim::adc_set_attenuation(pin, (int)a); }
inline void analogSetAttenuation(adc_attenuation_t a) { sim::adc_set_attenuation(-1, (int)a); }

class SimSerial {
 public:
  void begin(unsigned long) {}
  void end() {}
  operator bool() const { return true; }   // USB-CDC host is always attached here

  size_t print(const char* s) { sim::serial_out(s); return strlen(s); }
  size_t println(const char* s) { sim::serial_out(s); sim::serial_out("\n"); return strlen(s) + 1; }
  size_t println() { sim::serial_out("\n"); return 1; }
  size_t print(int v) { char b[24]; snprintf(b, sizeof b, "%d", v); sim::serial_out(b); return strlen(b); }
  size_t println(int v) { size_t n = print(v); sim::serial_out("\n"); return n + 1; }
  size_t print(unsigned long v) { char b[24]; snprintf(b, sizeof b, "%lu", v); sim::serial_out(b); return strlen(b); }
  size_t println(unsigned long v) { size_t n = print(v); sim::serial_out("\n"); return n + 1; }
  size_t print(float v) { char b[32]; snprintf(b, sizeof b, "%.2f", v); sim::serial_out(b); return strlen(b); }
  size_t println(float v) { size_t n = print(v); sim::serial_out("\n"); return n + 1; }

  size_t printf(const char* fmt, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n > 0) sim::serial_out(buf);
    return n > 0 ? (size_t)n : 0;
  }
  void flush() {}
  int available() { return 0; }
  int read() { return -1; }
};

extern SimSerial Serial;

class SimEsp {
 public:
  uint32_t getFreeHeap() const;
  uint32_t getMinFreeHeap() const;
  const char* getChipModel() const { return "ESP32-S3"; }
};
extern SimEsp ESP;

// gpio_num_t, so a sketch can say GPIO_NUM_5 outside of driver headers.
#ifndef SIM_GPIO_NUM_T
#define SIM_GPIO_NUM_T
typedef int gpio_num_t;
#define GPIO_NUM_0  0
#define GPIO_NUM_1  1
#define GPIO_NUM_2  2
#define GPIO_NUM_3  3
#define GPIO_NUM_4  4
#define GPIO_NUM_5  5
#define GPIO_NUM_6  6
#define GPIO_NUM_7  7
#define GPIO_NUM_8  8
#define GPIO_NUM_9  9
#define GPIO_NUM_10 10
#define GPIO_NUM_11 11
#define GPIO_NUM_12 12
#define GPIO_NUM_13 13
#define GPIO_NUM_14 14
#define GPIO_NUM_15 15
#define GPIO_NUM_16 16
#define GPIO_NUM_17 17
#define GPIO_NUM_18 18
#define GPIO_NUM_19 19
#define GPIO_NUM_20 20
#define GPIO_NUM_21 21
#define GPIO_NUM_38 38
#define GPIO_NUM_39 39
#define GPIO_NUM_43 43
#define GPIO_NUM_44 44
#define GPIO_NUM_47 47
#define GPIO_NUM_48 48
#endif

#endif  // ARDUINO_H
