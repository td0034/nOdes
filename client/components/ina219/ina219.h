#pragma once

#include "esp_err.h"
#include "driver/i2c.h"  // <-- This is essential

typedef struct {
    float bus_voltage;
    float shunt_voltage;
    float current_mA;
} ina219_data_t;

esp_err_t ina219_init(i2c_port_t i2c_num);
esp_err_t ina219_read(ina219_data_t *data);
