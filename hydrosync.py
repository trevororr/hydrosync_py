#!/usr/bin/env python3

import tkinter as tk
from tkinter import ttk
import numpy as np
import json, threading, queue, time
import serial
from serial.tools import list_ports
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

import matlab.engine

# simulink = matlab.engine.start_matlab("-nodisplay -noFigureWindows -nosplash -nodesktop -nojvm  -r 'start_simulink'")
# # Navigate to the directory containing your Simulink model
# simulink.cd('hs_simulink', nargout=0)
# # Simulate the model
# # You can pass arguments for simulation parameters if needed
# simout = simulink.sim('test', nargout=1)

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
        self.ax.set_title("Generation")
        self.ax.set_xlabel("time (s)")
        self.ax.set_ylabel("amplitude")
        self.ax.grid(True, linewidth=0.5, alpha=0.4)

        self.canvas = FigureCanvasTkAgg(self.fig, master=left)
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        NavigationToolbar2Tk(self.canvas, left)

        self.ch1, = self.ax.plot(self.x, np.zeros_like(self.x), label="V")
        self.ch2, = self.ax.plot(self.x, np.zeros_like(self.x), label="I")
        self.ax.legend(loc="upper right")
        self.ax.set_ylim([-1.2, 1.2])

        # ---- Progress bars ----
        style = ttk.Style()
        style.configure("Thick.Vertical.TProgressbar", thickness=40)

        self.bar_frame = ttk.Frame(right, padding=(10, 0))
        self.bar_frame.pack(fill="y", expand=False)

        self.bars = []
        self.value_labels = []
        labels = ["UR", "LR", "Power", "% Charge"]
        for i, name in enumerate(labels):
            col = ttk.Frame(self.bar_frame, padding=(10, 15))
            col.grid(row=0, column=i, sticky="n")
            val = ttk.Label(col, text="0%", font=("TkDefaultFont", 11, "bold"))
            val.pack()
            self.value_labels.append(val)
            pb = ttk.Progressbar(
                col, orient="vertical", mode="determinate",
                maximum=100, value=0, length=420,
                style="Thick.Vertical.TProgressbar",
            )
            pb.pack()
            ttk.Label(col, text=name).pack(pady=(8, 0))
            self.bars.append(pb)

        # ---- Controls ----
        controls = ttk.LabelFrame(right, text="Controls", padding=10)
        controls.pack(side="top", fill="x", padx=6)

        row = 0
        ttk.Button(controls, text="⏵ Start", command=self.start).grid(row=row, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="⏸ Stop",  command=self.stop ).grid(row=row, column=1, sticky="ew", padx=3, pady=3)
        row += 1
        ttk.Button(controls, text="Quit",   command=self.on_close).grid(row=row, column=1, sticky="ew", padx=3, pady=3)

        for c in range(2):
            controls.columnconfigure(c, weight=1)

        # ---- Threaded serial reader ----
        self.rx_queue = queue.Queue()
        self.queue_period_ms = 100
        self.stop_thread = False
        if self.ser:
            threading.Thread(target=self.read_serial_thread, daemon=True).start()
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

    # ---- background serial reader (constant monitoring) ----
    def read_serial_thread(self):
        """Continuously read JSON lines and push to queue."""
        buf = bytearray()
        while not self.stop_thread and self.ser:
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
                # Update visualization (always show latest telemetry)
                self.update_plot(data)
                self.update_bars(data)
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
        """Scrolls graph data and rescales to new data."""
        v = float(data.get("voltage", 0.0))
        i = float(data.get("current", 0.0))

        self.v_data = np.roll(self.v_data, -1); self.v_data[-1] = v
        self.i_data = np.roll(self.i_data, -1); self.i_data[-1] = i

        self.ch1.set_ydata(self.v_data)
        self.ch2.set_ydata(self.i_data)

        # --- Autoscale based on recent data ---
        combined = np.concatenate((self.v_data, self.i_data))
        ymin, ymax = np.min(combined), np.max(combined)

        # Add a little padding so lines don't touch the frame
        padding = (ymax - ymin) * 0.1 if ymax != ymin else 0.1
        self.ax.set_ylim(ymin - padding, ymax + padding)

        self.canvas.draw_idle()

    def update_bars(self, data: dict):
        vals = [data.get("UR", 0), data.get("LR", 0),
                data.get("power", 0), data.get("charge", 0)]
        for pb, lbl, val in zip(self.bars, self.value_labels, vals):
            try:
                fval = float(val)
            except Exception:
                fval = 0.0
            pb["value"] = max(0.0, min(100.0, fval))
            lbl.config(text=f"{pb['value']:.0f}%")

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
