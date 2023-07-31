"""Main script for LightPy"""
from datetime import datetime
import threading
import keyboard
import pyautogui
import cv2
import numpy as np
import ttkbootstrap as ttk

# Variables.
WIDTH = 300
HEIGHT = 300


class ScreenRecorder:
    def __init__(self):
        self.resolution = pyautogui.size()
        self.codec = cv2.VideoWriter_fourcc(*"mp4v")
        self.fps = 30.0
        self.recording_flag = False
        self.out = None

    def start_recording_thread(self):
        """
        Start recording function thread
        """
        self.recording_flag = True
        self.toggle_record_buttons()
        # btnRecord.config(text="Recording...")
        recording_thread = threading.Thread(target=self.record_screen)
        recording_thread.start()

    def record_screen(self):
        """
        Main function for recording the user's screen.
        """
        try:
            current_time = datetime.now().strftime("%d_%m_%Y_%H_%M_%S")
            filename = f"./recording_{current_time}.mp4"
            writer = cv2.VideoWriter(
                filename, self.codec, self.fps, self.resolution)

            if not writer.isOpened():
                print("Error: VideoWriter could not be opened.")

            while self.recording_flag:
                screenshot = pyautogui.screenshot()
                frame = np.array(screenshot)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                writer.write(frame)

                if keyboard.is_pressed("esc"):
                    break

            # Stop recording.
            writer.release()
        except Exception as ex:
            print(ex)
        btnRecord.config(text="Record")

    def stop_recording(self):
        """
        Stop recording the user's screen.
        """
        self.recording_flag = False
        self.toggle_record_buttons()

    def toggle_record_buttons(self):
        """
        Toggle start and stop record buttons
        """
        if self.recording_flag:
            btnRecord.grid_forget()
            btnStop.grid(row=3, pady=20, padx=80)
        else:
            btnRecord.grid(row=2, pady=20, padx=80)
            btnStop.grid_forget()


# Main window.
root = ttk.Window(title="LightPy", size=(WIDTH, HEIGHT),
                  position=(0, 0), resizable=(False, False), themename="superhero")

# Center main window.
x = int((root.winfo_screenwidth()/2) - (WIDTH/2))
y = int((root.winfo_screenheight()/2) - (HEIGHT/2))
root.geometry(f"+{x}+{y}")

# Screen recorder instance.
screen_recorder = ScreenRecorder()

# Components.
lblTitle = ttk.Label(text="LightPy", font=("Helvetica", 28))
lblDescription = ttk.Label(
    text="Record your screen", font=("Helvetica", 12))
btnRecord = ttk.Button(
    text="Record", bootstyle="success, outline", command=screen_recorder.start_recording_thread)
btnStop = ttk.Button(text="Stop Recording",
                     bootstyle="danger, outline", command=screen_recorder.stop_recording)

# Packing.
window_width = root.winfo_width()
lblTitle.grid(row=0, pady=(60, 5), padx=80)
lblDescription.grid(row=1, pady=5, padx=80)
screen_recorder.toggle_record_buttons()

# Run app.
root.mainloop()


# todo
# icon
# sound
# specify parts of screen
# windows
# recording location
# pip install ffmpeg-python
