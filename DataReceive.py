import serial, json

ser = serial.Serial('COM3', 115200)  # your port here
while True:
    line = ser.readline().decode().strip()
    try:
        data = json.loads(line)
        print(f"Speed: {data['speed']} | RPM: {data['rpm']}")
    except json.JSONDecodeError:
        pass  # ignore garbled lines