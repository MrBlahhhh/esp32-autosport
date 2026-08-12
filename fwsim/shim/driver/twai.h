// Host stand-in for the ESP-IDF TWAI (CAN) driver.
//
// The pin numbers handed to twai_driver_install() are the point of this shim:
// they are checked against what the board model says those pins are, which is
// how a sketch carried over from another board gets caught wiring CAN to the
// analog front end.
#ifndef DRIVER_TWAI_H
#define DRIVER_TWAI_H

#include <stdint.h>
#include <string.h>

#include "../esp_err.h"
#include "../sim.h"

#ifndef SIM_GPIO_NUM_T
#define SIM_GPIO_NUM_T
typedef int gpio_num_t;
#endif

typedef enum { TWAI_MODE_NORMAL = 0, TWAI_MODE_NO_ACK, TWAI_MODE_LISTEN_ONLY } twai_mode_t;

typedef enum {
  TWAI_STATE_STOPPED = 0,
  TWAI_STATE_RUNNING,
  TWAI_STATE_BUS_OFF,
  TWAI_STATE_RECOVERING
} twai_state_t;

typedef struct {
  twai_mode_t mode;
  gpio_num_t  tx_io;
  gpio_num_t  rx_io;
  gpio_num_t  clkout_io;
  gpio_num_t  bus_off_io;
  uint32_t    tx_queue_len;
  uint32_t    rx_queue_len;
  uint32_t    alerts_enabled;
  uint32_t    clkout_divider;
  int         intr_flags;
} twai_general_config_t;

typedef struct {
  uint32_t brp;
  uint8_t  tseg_1;
  uint8_t  tseg_2;
  uint8_t  sjw;
  bool     triple_sampling;
} twai_timing_config_t;

typedef struct {
  uint32_t acceptance_code;
  uint32_t acceptance_mask;
  bool     single_filter;
} twai_filter_config_t;

typedef struct {
  union {
    struct {
      uint32_t extd : 1;
      uint32_t rtr : 1;
      uint32_t ss : 1;
      uint32_t self : 1;
      uint32_t dlc_non_comp : 1;
      uint32_t reserved : 27;
    };
    uint32_t flags;
  };
  uint32_t identifier;
  uint8_t  data_length_code;
  uint8_t  data[8];
} twai_message_t;

typedef struct {
  twai_state_t state;
  uint32_t msgs_to_tx;
  uint32_t msgs_to_rx;
  uint32_t tx_error_counter;
  uint32_t rx_error_counter;
  uint32_t tx_failed_count;
  uint32_t rx_missed_count;
  uint32_t rx_overrun_count;
  uint32_t arb_lost_count;
  uint32_t bus_error_count;
} twai_status_info_t;

inline twai_general_config_t twai_general_config_default(gpio_num_t tx, gpio_num_t rx, twai_mode_t mode) {
  twai_general_config_t g;
  memset(&g, 0, sizeof g);
  g.mode = mode;
  g.tx_io = tx;
  g.rx_io = rx;
  g.clkout_io = -1;
  g.bus_off_io = -1;
  g.tx_queue_len = 5;
  g.rx_queue_len = 5;
  g.clkout_divider = 0;
  return g;
}
#define TWAI_GENERAL_CONFIG_DEFAULT(tx, rx, mode) twai_general_config_default((tx), (rx), (mode))

inline twai_timing_config_t twai_timing_config_500kbits() {
  twai_timing_config_t t;
  memset(&t, 0, sizeof t);
  t.brp = 8; t.tseg_1 = 15; t.tseg_2 = 4; t.sjw = 3;
  return t;
}
#define TWAI_TIMING_CONFIG_500KBITS() twai_timing_config_500kbits()
#define TWAI_TIMING_CONFIG_250KBITS() twai_timing_config_500kbits()

inline twai_filter_config_t twai_filter_config_accept_all() {
  twai_filter_config_t f;
  f.acceptance_code = 0;
  f.acceptance_mask = 0xFFFFFFFF;
  f.single_filter = true;
  return f;
}
#define TWAI_FILTER_CONFIG_ACCEPT_ALL() twai_filter_config_accept_all()

inline esp_err_t twai_driver_install(const twai_general_config_t* g,
                                     const twai_timing_config_t*,
                                     const twai_filter_config_t*) {
  sim::can_install(g->tx_io, g->rx_io, (int)g->mode, (int)g->rx_queue_len);
  return ESP_OK;
}

inline esp_err_t twai_start() { return sim::can_start() ? ESP_OK : ESP_FAIL; }
inline esp_err_t twai_stop() { return ESP_OK; }
inline esp_err_t twai_driver_uninstall() { return ESP_OK; }

inline esp_err_t twai_receive(twai_message_t* msg, uint32_t /*ticks*/) {
  sim::Frame f;
  if (!sim::can_receive(&f)) return ESP_ERR_TIMEOUT;
  memset(msg, 0, sizeof *msg);
  msg->identifier = f.id;
  msg->extd = f.extd ? 1 : 0;
  msg->rtr = f.rtr ? 1 : 0;
  msg->data_length_code = f.dlc;
  memcpy(msg->data, f.data, 8);
  return ESP_OK;
}

inline esp_err_t twai_transmit(const twai_message_t*, uint32_t) { return ESP_OK; }

inline esp_err_t twai_get_status_info(twai_status_info_t* st) {
  memset(st, 0, sizeof *st);
  st->state = sim::can_bus_off() ? TWAI_STATE_BUS_OFF : TWAI_STATE_RUNNING;
  st->rx_missed_count = (uint32_t)sim::can_rx_dropped();
  return ESP_OK;
}

inline esp_err_t twai_initiate_recovery() { sim::can_recover(); return ESP_OK; }

#endif  // DRIVER_TWAI_H
