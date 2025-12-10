#!/usr/bin/env python3

import tkinter as tk
from tkinter import ttk
import numpy as np
import json, threading, queue, time
import serial
from serial.tools import list_ports
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

def find_serial_port():
    ports = list_ports.comports()
    if not ports:
        print("No serial ports found.")
        return None
    return ports[0].device  # e.g. 'COM3' or '/dev/ttyUSB0'

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Hydrosync")
        self.root.geometry("1200x750")

        # ---- Serial setup ----
        port = find_serial_port()
        self.ser = None
        if port:
            try:
                self.ser = serial.Serial(port, 115200, timeout=0.05)
                print(f"Connected to {port}")
            except Exception as e:
                print(f"Serial connection failed: {e}")

        # ---- state ----
        self.running = False
        self.x = np.linspace(0, 10, 600)
        self.v_data = np.zeros_like(self.x)
        self.i_data = np.zeros_like(self.x)
        self.p_data = np.zeros_like(self.x)

        # ---- Layout frames ----
        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(main, width=300)
        right.pack(side="right", fill="y")

        # ---- Matplotlib figure ----
        self.fig = Figure(figsize=(7, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Motor Command Data (I, V, P)")
        self.ax.set_xlabel("time (s)")
        self.ax.set_ylabel("amplitude")
        self.ax.grid(True, linewidth=0.5, alpha=0.4)

        self.canvas = FigureCanvasTkAgg(self.fig, master=left)
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        NavigationToolbar2Tk(self.canvas, left)

        
        self.ch1, = self.ax.plot(self.x, np.zeros_like(self.x), label="I")
        self.ch2, = self.ax.plot(self.x, np.zeros_like(self.x), label="V")
        self.ch3, = self.ax.plot(self.x, np.zeros_like(self.x), label="P")
        self.ax.legend(loc="upper right")
        self.ax.set_ylim([-1.2, 1.2])

        #
        # ---- Controls ----
        #
        controls = ttk.LabelFrame(right, text="Controls", padding=10)
        controls.pack(side="top", fill="x", padx=6)

        row = 0
        ttk.Button(controls, text="Start", command=self.start).grid(row=row, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="Stop",  command=self.stop ).grid(row=row, column=1, sticky="ew", padx=3, pady=3)
        row += 1
        ttk.Button(controls, text="Quit",   command=self.on_close).grid(row=row, column=1, sticky="ew", padx=3, pady=3)

        #-- Analog input ----
        row += 1

        def validate_number_input(new_text):
            if new_text.isdigit() or new_text == "" or (new_text.count(".") == 1 and new_text.replace(".", "").isdigit()):
                return True
            return False
        vcmd = root.register(validate_number_input)

        self.analog_var = tk.StringVar()          # user input
        self.analog_v = tk.DoubleVar(value=0)     # displayed analog voltage

        analog_entry = ttk.Entry(
            controls,
            textvariable=self.analog_var,
            validate="key",
            validatecommand=(vcmd, "%P") # %P passes the new value of the widget
        )
        analog_entry.grid(row=row, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="Set Analog", command=self.set_analog).grid(row=row, column=1, sticky="ew", padx=3, pady=3)
        #-- End Analog input ----

        #-- Load resistor ----
        row += 1
        self.load_var = tk.StringVar()          # user input
        self.load_r = tk.IntVar(value=220)      # displayed load value

        load_entry = ttk.Entry(
            controls,
            textvariable=self.load_var,
            validate="key",
            validatecommand=(vcmd, "%P") # %P passes the new value of the widget
        )
        load_entry.grid(row=row, column=0, sticky="ew", padx=3, pady=3)
        
        ttk.Button(controls, text="Set Load", command=self.set_load).grid(row=row, column=1, sticky="ew", padx=3, pady=3)
        #-- End Load resistor ----

        # Configure grid weights
        for c in range(2):
            controls.columnconfigure(c, weight=1)
        
        #
        # End Controls ----
        #

        # ---- Variable Values ----
        variables = ttk.LabelFrame(right, text="Variables", padding=10)
        variables.pack(side="top", fill="x", padx=6)

        row = 0
        tk.Label(variables, text="Analog Output(V):").grid(row=row, column=0, sticky="ew", padx=3, pady=3)
        tk.Label(variables, textvariable=self.analog_v).grid(row=row, column=1, sticky="ew", padx=3, pady=3)

        row += 1
        tk.Label(variables, text="Load(Ω):").grid(row=row, column=0, sticky="ew", padx=3, pady=3)
        tk.Label(variables, textvariable=self.load_r).grid(row=row, column=1, sticky="ew", padx=3, pady=3)
        # End Variable Values ----

        # ---- Threaded serial reader ----
        self.rx_queue = queue.Queue()
        self.queue_period_ms = 100
        self.stop_thread = False
        if self.ser:
            threading.Thread(target=self.read_serial_thread, daemon=True).start() # start background reader
        self.poll_queue()  # start GUI-side queue polling

        # Clean shutdown
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- control callbacks ----
    def start(self):
        if not self.running:
            self.running = True
            self.send_serial("START", None)

    def stop(self):
        if self.running:
            self.running = False
            self.send_serial("STOP", None)

    # ---- set analog voltage between 0 and 3.3V ----
    def set_analog(self):
        text = self.analog_var.get()  # read entry box text
        try:
            value = float(text)
        except ValueError:
            print("Invalid analog voltage:", text)
            return
        if (value < 0):
            value = 0.0
        if (value > 3.3):
            value = 3.3
        
        self.analog_v.set(value)
        self.send_serial("SIMULINK_ANALOG", value)
    
    # ---- set load resistor ----
    def set_load(self):
        text = self.load_var.get()  # read entry box text
        try:
            value = int(text)
        except ValueError:
            print("Invalid load:", text)
            return

        self.load_r.set(value)

    # ---- background serial reader (constant monitoring) ----
    def read_serial_thread(self):
        """Continuously read JSON lines and push to queue."""
        buf = bytearray()
        while not self.stop_thread and self.ser: # keep running until told to stop
            try:
                chunk = self.ser.read(self.ser.in_waiting or 1)  # non-blocking-ish
                if chunk:
                    buf.extend(chunk)
                    while True:
                        nl = buf.find(b'\n')
                        if nl < 0:
                            break
                        raw = buf[:nl].decode(errors="ignore").strip("\r\n \t")
                        del buf[:nl+1]
                        if not raw:
                            continue
                        try:
                            data = json.loads(raw)
                            self.rx_queue.put(data)
                        except json.JSONDecodeError:
                            # ignore non-JSON lines
                            pass
            except Exception as e:
                print("Serial thread error:", e)
                time.sleep(0.1)
            time.sleep(0.005)  # yield

    # ---- GUI side: handle queued packets ----
    def poll_queue(self):
        try:
            while True:
                data = self.rx_queue.get_nowait()
                # Update visualization (always show latest data)
                self.update_plot(data)
        except queue.Empty:
            pass
        self.root.after(self.queue_period_ms, self.poll_queue)

    def on_close(self):
        self.stop_thread = True
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        #simulink.quit()
        self.root.destroy()

    # ---- TX ----
    def send_serial(self, action: str, value):
        if self.ser and self.ser.is_open:
            try:
                obj = {"action": action, "value": value}
                json_str = json.dumps(obj, separators=(",", ":")) + "\n"
                self.ser.write(json_str.encode("utf-8"))
                print("TX:", json_str.strip())
            except Exception as e:
                print("Error writing to serial:", e)

    # ---- GUI updaters ----
    def update_plot(self, data: dict):
        print("RX:", data)
        """Scrolls graph data and rescales to new data."""
        v = float(data.get("motor_cmd_v", 0.0))
        i = v / float(self.load_r.get()) * 1000.0  # convert to mA
        p = v * i # power in mW

        # Scroll data
        self.i_data = np.roll(self.i_data, -1); self.i_data[-1] = i
        self.v_data = np.roll(self.v_data, -1); self.v_data[-1] = v
        self.p_data = np.roll(self.p_data, -1); self.p_data[-1] = p

        # Update plot data
        self.ch1.set_ydata(self.i_data)
        self.ch2.set_ydata(self.v_data)
        self.ch3.set_ydata(self.p_data)

        # --- Autoscale based on recent data ---
        combined = np.concatenate((self.i_data, self.v_data, self.p_data))
        ymin, ymax = np.min(combined), np.max(combined)

        # Add a little padding so lines don't touch the frame
        padding = (ymax - ymin) * 0.1 if ymax != ymin else 0.1
        self.ax.set_ylim(ymin - padding, ymax + padding)

        self.canvas.draw_idle()

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
