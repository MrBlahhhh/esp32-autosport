// Host stand-in for the Arduino FS/File API, backed by the simulator's card
// model. Nothing is written to a real filesystem: what matters to the studies
// here is how many bytes are outstanding and whether the file was closed before
// the rails went, not the contents.
#ifndef FS_H
#define FS_H

#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <string>

#include "sim.h"

#define FILE_READ "r"
#define FILE_WRITE "w"
#define FILE_APPEND "a"

namespace fs {

class File {
 public:
  File() : open_(false) {}
  explicit File(bool ok) : open_(ok) {}

  operator bool() const { return open_; }

  size_t write(const uint8_t*, size_t len) { sim::sd_write(len); return len; }
  size_t print(const char* s) { size_t n = strlen(s); sim::sd_write(n); return n; }
  size_t println(const char* s) { size_t n = strlen(s) + 1; sim::sd_write(n); return n; }
  size_t printf(const char* fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n < 0) return 0;
    sim::sd_write((size_t)n);
    return (size_t)n;
  }

  void flush() { sim::sd_flush(); }
  void close() { if (open_) sim::sd_close(); open_ = false; }
  size_t size() const { return 0; }
  const char* name() const { return "/log.csv"; }

 private:
  bool open_;
};

}  // namespace fs

using fs::File;

#endif  // FS_H
