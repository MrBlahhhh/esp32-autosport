// fwsim runner: boot the sketch under test on a virtual board, feed it the
// scenario, and write out what happened.
//
//   fwsim.exe --scenario s.txt [--trace t.csv] [--serial s.log] [--faults f.txt]
//
// The sketch supplies setup() and loop() and is compiled straight from its own
// repository -- there is no copy of it here to drift out of date.

#include <stdio.h>
#include <string.h>
#include <string>

#include "shim/sim.h"

extern void setup();
extern void loop();

int main(int argc, char** argv) {
  std::string scenario, trace, serial, faults;
  for (int i = 1; i < argc; i++) {
    std::string a = argv[i];
    const char* next = (i + 1 < argc) ? argv[i + 1] : "";
    if (a == "--scenario") { scenario = next; i++; }
    else if (a == "--trace") { trace = next; i++; }
    else if (a == "--serial") { serial = next; i++; }
    else if (a == "--faults") { faults = next; i++; }
    else { fprintf(stderr, "fwsim: unknown argument %s\n", argv[i]); return 2; }
  }
  if (scenario.empty()) { fprintf(stderr, "fwsim: --scenario is required\n"); return 2; }

  sim::scenario_load(scenario);
  if (!trace.empty()) sim::trace_open(trace);

  setup();
  unsigned long iterations = 0;
  while (!sim::run_should_stop()) {
    loop();
    iterations++;
    // A loop() that never advances the clock would spin here forever; the real
    // board would just be busy, but the simulator has to notice.
    if (iterations > 20000000UL) {
      sim::run_stop("loop() iteration cap -- is anything advancing time?");
      break;
    }
  }
  sim::trace_close();

  if (!serial.empty()) {
    FILE* f = fopen(serial.c_str(), "w");
    if (f) { fputs(sim::serial_log().c_str(), f); fclose(f); }
  }

  const std::vector<std::string>& fl = sim::faults();
  FILE* out = faults.empty() ? stdout : fopen(faults.c_str(), "w");
  if (!out) out = stdout;
  fprintf(out, "board            %s\n", sim::board().name.c_str());
  fprintf(out, "stopped          %.2f ms -- %s\n", sim::now_us() / 1000.0, sim::run_stop_reason());
  fprintf(out, "loop iterations  %lu\n", iterations);
  fprintf(out, "LED frames       %lu\n", sim::leds_frames());
  fprintf(out, "BLE notifies     %lu\n", sim::ble_notify_count());
  fprintf(out, "CAN dropped      %lu\n", sim::can_rx_dropped());
  if (sim::power_fail_at_ms() >= 0.0)
    fprintf(out, "PWR_FAIL_ms      %.2f\n", sim::power_fail_at_ms());
  fprintf(out, "errors %d  warnings %d  notes %d\n",
          sim::fault_count(sim::SEV_ERROR), sim::fault_count(sim::SEV_WARN),
          sim::fault_count(sim::SEV_NOTE));
  fprintf(out, "----\n");
  for (size_t i = 0; i < fl.size(); i++) fprintf(out, "%s\n", fl[i].c_str());
  if (out != stdout) fclose(out);

  return sim::fault_count(sim::SEV_ERROR) > 0 ? 1 : 0;
}
