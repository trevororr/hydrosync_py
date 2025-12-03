import serial
import time
import re

PORT = "COM3"   # Change this to your serial port
BAUD = 115200
OUTFILE = "hydro_log.csv"

# Regex for [flow,current,power,voltage]
pattern = re.compile(r'\[([\d\.\-]+),([\d\.\-]+),([\d\.\-]+),([\d\.\-]+)\]')

def main():
    ser = serial.Serial(PORT, BAUD, timeout=1)
    time.sleep(2)  # wait for the ESP32’s USB reset

    with open(OUTFILE, "w", buffering=1) as f:
        # Write new header every start
        if f.tell() == 0:
            f.write("timestamp,flow(L_s),current(A),power(W),voltage(V)\n")

        print(f"Logging to {OUTFILE}… (Ctrl+C to stop)")

        try:
            while True:
                line = ser.readline().decode(errors="ignore").strip()
                if not line:
                    continue

                match = pattern.match(line)
                if match:
                    flow, current, power, voltage = match.groups()
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    csv_line = f"{timestamp},{flow},{current},{power},{voltage}\n"

                    f.write(csv_line)
                    print(csv_line.strip())
                else:
                    print("Skipped:", line)

        except KeyboardInterrupt:
            print("\nLogging stopped by user.")

    ser.close()

if __name__ == "__main__":
    main()
