#include "ina219.h"
#include "driver/i2c.h"
#include "esp_log.h"
#include <math.h>

#define INA219_ADDR        0x40
#define REG_BUS_VOLTAGE    0x02
#define REG_SHUNT_VOLTAGE  0x01
#define REG_CALIBRATION    0x05
#define REG_CONFIG         0x00

static const char *TAG = "INA219";
static i2c_port_t g_i2c_num;

static esp_err_t write_register(uint8_t reg, uint16_t value)
{
    uint8_t data[3] = { reg, value >> 8, value & 0xFF };
    return i2c_master_write_to_device(g_i2c_num, INA219_ADDR, data, sizeof(data), pdMS_TO_TICKS(100));
}

static esp_err_t read_register(uint8_t reg, uint16_t *value)
{
    uint8_t data[2];
    esp_err_t err = i2c_master_write_read_device(g_i2c_num, INA219_ADDR, &reg, 1, data, 2, pdMS_TO_TICKS(100));
    if (err == ESP_OK) {
        *value = (data[0] << 8) | data[1];
    }
    return err;
}

esp_err_t ina219_init(i2c_port_t i2c_num)
{
    g_i2c_num = i2c_num;

    // Default config (you may tweak this based on your Arduino code)
    esp_err_t err = write_register(REG_CONFIG, 0x399F); // 32V, 2A, 12bit
    if (err != ESP_OK) return err;

    // Calibration (based on shunt resistor)
    return write_register(REG_CALIBRATION, 4096);
}

esp_err_t ina219_read(ina219_data_t *data)
{
    uint16_t raw_bus, raw_shunt;

    if (read_register(REG_BUS_VOLTAGE, &raw_bus) != ESP_OK) return ESP_FAIL;
    if (read_register(REG_SHUNT_VOLTAGE, &raw_shunt) != ESP_OK) return ESP_FAIL;

    data->bus_voltage   = (raw_bus >> 3) * 4.0 / 1000.0; // in V
    int16_t shunt_raw   = (int16_t)raw_shunt;
    data->shunt_voltage = shunt_raw * 10.0 / 1000.0;     // in mV
    data->current_mA    = data->shunt_voltage / 0.005;   // assuming 5mΩ shunt

    return ESP_OK;
}
