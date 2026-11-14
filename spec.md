# Go-Kart Telemetry Spec

> Working draft of what the kart should measure and the hardware we need.
> Items marked **Maybe** are not decided yet.

## Measurements

| Category              | Measurement             | Status    | Notes           |
| --------------------- | ----------------------- | --------- | --------------- |
| **Motion & position** | GPS                     | Planned   |                 |
|                       | Velocity                | Planned   |                 |
|                       | Acceleration            | Planned   |                 |
|                       | G-force                 | Planned   |                 |
| **Timing & distance** | Time elapsed            | Planned   |                 |
|                       | Lap time                | Planned   |                 |
|                       | Total distance traveled | Planned   |                 |
| **Temperature**       | Tire temperature        | **Maybe** | Infrared camera |
|                       | Battery temperature     | **Maybe** |                 |
|                       | Motor temperature       | **Maybe** |                 |
| **Power & system**    | Battery                 | **Maybe** |                 |
|                       | Link status             | Planned   |                 |

## Components

| Component | Purpose         | Notes |
| --------- | --------------- | ----- |
| ESP32     | Microcontroller |       |
