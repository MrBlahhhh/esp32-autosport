#ifndef ESP_ERR_H
#define ESP_ERR_H

typedef int esp_err_t;

#define ESP_OK              0
#define ESP_FAIL            -1
#define ESP_ERR_NO_MEM      0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_TIMEOUT     0x107
#define ESP_ERR_NOT_SUPPORTED 0x106

// Radio TX power levels (esp_bt.h in the real core; only the one NimBLE
// setPower() call needs it).
typedef enum {
  ESP_PWR_LVL_N12 = 0, ESP_PWR_LVL_N9, ESP_PWR_LVL_N6, ESP_PWR_LVL_N3,
  ESP_PWR_LVL_N0,      ESP_PWR_LVL_P3, ESP_PWR_LVL_P6, ESP_PWR_LVL_P9
} esp_power_level_t;

#endif  // ESP_ERR_H
