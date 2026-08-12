// Host stand-in for the ESP32 SD_MMC driver.
//
// The two things worth modelling on this board are both power, not storage:
// the card supply is switched by SD_PWR_EN and off at reset, and dropping that
// supply while the bus pins are still driven back-feeds the card through its
// I/O (README section 5). Both are checked here.
#ifndef SD_MMC_H
#define SD_MMC_H

#include <stdint.h>
#include "FS.h"
#include "sim.h"

#define CARD_NONE    0
#define CARD_MMC     1
#define CARD_SD      2
#define CARD_SDHC    3

class SDMMCFS {
 public:
  bool setPins(int clk, int cmd, int d0, int d1 = -1, int d2 = -1, int d3 = -1) {
    sim::sd_set_pins(clk, cmd, d0, d1, d2, d3);
    return true;
  }

  bool begin(const char* mount = "/sdcard", bool mode1bit = false,
             bool format_if_empty = false, int freq_khz = 20000) {
    (void)mount; (void)format_if_empty; (void)freq_khz;
    return sim::sd_begin(mode1bit);
  }

  void end() { sim::sd_end(); }

  fs::File open(const char* path, const char* mode = FILE_READ) {
    (void)path; (void)mode;
    if (!sim::sd_ready()) return fs::File(false);
    sim::sd_open();
    return fs::File(true);
  }

  bool exists(const char*) { return false; }
  bool remove(const char*) { return true; }
  uint8_t cardType() { return sim::sd_ready() ? CARD_SDHC : CARD_NONE; }
  uint64_t cardSize() { return sim::sd_ready() ? 32ULL * 1024 * 1024 * 1024 : 0; }
  uint64_t totalBytes() { return cardSize(); }
  uint64_t usedBytes() { return 0; }
};

extern SDMMCFS SD_MMC;

#endif  // SD_MMC_H
