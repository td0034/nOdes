idf_component_register(
    SRCS "hello_world_main.c"
    INCLUDE_DIRS ""
    PRIV_REQUIRES spi_flash esp_https_ota
    REQUIRES driver esp_wifi esp_event nvs_flash esp_netif esp_timer ina219 esp_https_ota app_update esp_https_ota esp_http_client
                           
                           
)


target_include_directories(${COMPONENT_LIB} PRIVATE
    $ENV{IDF_PATH}/components/esp_wifi/include/esp_private
)
