#include <SPI.h>
#include <LoRa.h>

#define LORA_SS   5
#define LORA_RST  14
#define LORA_DIO0 2

struct __attribute__((packed)) Telemetry {
  uint32_t t_ms;
  int16_t  speed_x10;        // speed * 10
  int16_t  gx_x100, gy_x100, gz_x100;
  int32_t  lat_e7, lon_e7;   // degrees * 1e7
  uint16_t battery_mv;
  int16_t  tire_x10[4];      // temps * 10
};                           // 30 bytes total

void setup() {
  Serial.begin(115200);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  if (!LoRa.begin(915E6)) {
    Serial.println("LoRa init failed");
    while (true);
  }
  LoRa.setSpreadingFactor(7);      // lower = faster, shorter range
  LoRa.setSignalBandwidth(125E3);
}

void loop() {
  Telemetry p;
  p.t_ms = millis();
  p.speed_x10 = 421;               // replace with real sensor readings
  p.gx_x100 = 5; p.gy_x100 = -120; p.gz_x100 = 98;
  p.lat_e7 = 415500000; p.lon_e7 = -726500000;
  p.battery_mv = 48200;
  for (int i = 0; i < 4; i++) p.tire_x10[i] = 650;

  LoRa.beginPacket();
  LoRa.write((uint8_t*)&p, sizeof(p));
  LoRa.endPacket();                // blocks until sent

  delay(200);                      // ~5 Hz
}