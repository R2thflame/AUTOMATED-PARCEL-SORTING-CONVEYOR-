#!/usr/bin/env python3
# --------------------------------------------------------
# 📦 Automated Parcel Sorting Conveyor System
# 🎓 Bestlink College of the Philippines
# 📷 PiCamera2 (Parcel Scanner) + USB Webcam (Robotic Arm)
# 🤖 Manual Conveyor Start + Automated Arm Pickup
# --------------------------------------------------------

import os
import cv2
import time
import json
import serial
import threading
import psutil
import queue
import numpy as np
import re
import sys
import signal
import smtplib
import random
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, render_template, Response, request, redirect, url_for, session, jsonify, send_from_directory
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# ========================================================
# 🔧 Hardware Detection
# ========================================================
print("\n" + "="*70)
print("🚀 AUTOMATED PARCEL SORTING CONVEYOR SYSTEM")
print("📡 Bestlink College of the Philippines")
print("🤖 Manual Conveyor Start + Automated Arm Pickup")
print("="*70)

try:
    with open('/sys/firmware/devicetree/base/model', 'r') as f:
        model = f.read()
    IS_RASPBERRY_PI = 'Raspberry Pi' in model
    if not IS_RASPBERRY_PI:
        print("❌ ERROR: This system must run on Raspberry Pi hardware")
        sys.exit(1)
    print(f"✅ Detected: {model.strip()}")
except:
    print("❌ ERROR: Not running on Raspberry Pi")
    sys.exit(1)

# ========================================================
# 📸 Raspberry Pi Camera V3 Setup (Parcel Scanner)
# ========================================================
PICAMERA_ENABLED = False
picam2 = None

try:
    from picamera2 import Picamera2
    from libcamera import controls
    PICAMERA_AVAILABLE = True
except ImportError:
    print("❌ ERROR: Picamera2 not available")
    sys.exit(1)

if IS_RASPBERRY_PI and PICAMERA_AVAILABLE:
    try:
        print("\n📷 Initializing Raspberry Pi Camera V3 for Parcel Scanner...")
        
        picam2 = Picamera2()
        
        # Camera configuration optimized for OCR
        camera_config = picam2.create_video_configuration(
            main={
                "size": (1920, 1080),
                "format": "RGB888"
            },
            lores={
                "size": (640, 360),
                "format": "RGB888"
            },
            controls={
                "FrameRate": 30.0,
                "FrameDurationLimits": (33333, 33333),
                "AfMode": controls.AfModeEnum.Continuous,
                "AfSpeed": controls.AfSpeedEnum.Fast,
                "Brightness": 0.3,
                "Contrast": 1.1,
                "Sharpness": 1.5,
                "AeEnable": True,
                "AeExposureMode": controls.AeExposureModeEnum.Normal,
                "AwbEnable": True,
                "AwbMode": controls.AwbModeEnum.Auto,
            }
        )
        
        picam2.configure(camera_config)
        picam2.start()
        
        print("   Warming up camera...")
        time.sleep(2)
        
        test_frame = picam2.capture_array("main")
        if test_frame is not None:
            PICAMERA_ENABLED = True
            h, w = test_frame.shape[:2]
            print(f"✅ PiCamera initialized - {w}x{h} @ 30 FPS")
        else:
            print("❌ ERROR: PiCamera test failed")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ ERROR: PiCamera init failed: {e}")
        sys.exit(1)
else:
    print("❌ ERROR: PiCamera not available")
    sys.exit(1)

# ========================================================
# 📷 USB Webcam Setup (Robotic Arm Cam)
# ========================================================
USBCAM_ENABLED = False
usb_cap = None

def init_usb_webcam():
    """Initialize USB webcam for robotic arm viewing"""
    global usb_cap, USBCAM_ENABLED
    
    print("\n📷 Initializing USB Webcam for Robotic Arm...")
    
    # Try different camera IDs to find the USB webcam
    # Usually USB webcams show up as ID 0, 1, or 2
    for cam_id in [0, 1, 2]:
        try:
            print(f"   Trying camera ID {cam_id}...")
            usb_cap = cv2.VideoCapture(cam_id)
            
            if usb_cap.isOpened():
                # Get camera info
                ret, frame = usb_cap.read()
                if ret and frame is not None:
                    # Set camera properties for better performance
                    usb_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    usb_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    usb_cap.set(cv2.CAP_PROP_FPS, 30)
                    usb_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    
                    USBCAM_ENABLED = True
                    h, w = frame.shape[:2]
                    print(f"✅ USB Webcam found on ID {cam_id} - {w}x{h} @ 30 FPS")
                    print(f"   Camera is ready for Robotic Arm viewing")
                    return True
                else:
                    print(f"   Camera ID {cam_id} opened but no frame received")
                    usb_cap.release()
            else:
                print(f"   Camera ID {cam_id} could not be opened")
                if usb_cap:
                    usb_cap.release()
        except Exception as e:
            print(f"   Error with camera {cam_id}: {e}")
            if usb_cap:
                usb_cap.release()
    
    print("⚠️ WARNING: No USB webcam found for Robotic Arm")
    print("   Robotic Arm CAM will show offline message")
    return False

# Initialize USB webcam
init_usb_webcam()

# ========================================================
# 🔤 Tesseract OCR Setup
# ========================================================
pytesseract_available = False

try:
    import pytesseract
    tesseract_paths = ['/usr/bin/tesseract', '/usr/local/bin/tesseract']
    
    for path in tesseract_paths:
        if os.path.exists(path):
            try:
                pytesseract.pytesseract.tesseract_cmd = path
                version = pytesseract.get_tesseract_version()
                pytesseract_available = True
                print(f"✅ Tesseract OCR found: {version}")
                break
            except:
                continue
    
    if not pytesseract_available:
        print("❌ ERROR: Tesseract OCR not found")
        sys.exit(1)
except ImportError:
    print("❌ ERROR: pytesseract module not installed")
    sys.exit(1)

# ========================================================
# 🧠 MongoDB Connection
# ========================================================
MONGO_ENABLED = False
client = None
parcels_col = None
users_col = None
otps_col = None

try:
    uri = "mongodb+srv://conveyor1:conveyor1@cluster0.gxjaulj.mongodb.net/?appName=Cluster0"
    client = MongoClient(uri, server_api=ServerApi('1'))
    client.admin.command('ping')
    print("✅ MongoDB Connected!")
    db_mongo = client.conveyor_db
    parcels_col = db_mongo.scanned_codes
    users_col = db_mongo.users
    otps_col = db_mongo.otps
    MONGO_ENABLED = True
    
    # Create default admin user if not exists
    if users_col.count_documents({'username': 'admin'}) == 0:
        users_col.insert_one({
            'username': 'admin',
            'password': generate_password_hash('admin'),
            'api_key': '123456',
            'email': 'admin@gmail.com',
            'role': 'admin',
            'created_at': datetime.now()
        })
        print("✅ Default admin user created")
except Exception as e:
    print(f"⚠️ MongoDB connection error: {e}")
    MONGO_ENABLED = False
    parcels_col = None

# ========================================================
# 📧 Email Configuration (for OTP)
# ========================================================
EMAIL_ENABLED = False
try:
    # Configure your email settings here
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    SMTP_USERNAME = "your-email@gmail.com"  # Replace with your email
    SMTP_PASSWORD = "your-app-password"      # Replace with your app password
    EMAIL_ENABLED = True
    print("✅ Email service configured")
except Exception as e:
    print(f"⚠️ Email configuration error: {e}")

def send_otp_email(to_email, otp_code):
    """Send OTP via email"""
    if not EMAIL_ENABLED:
        print(f"\n[EMAIL SIMULATION] OTP for {to_email}: {otp_code}\n")
        return True
    
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USERNAME
        msg['To'] = to_email
        msg['Subject'] = "Your Verification Code - Parcel Sorting System"
        
        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #2563eb;">Email Verification</h2>
            <p>Your verification code is:</p>
            <h1 style="font-size: 48px; letter-spacing: 5px; color: #1e40af;">{otp_code}</h1>
            <p>This code will expire in 5 minutes.</p>
            <hr>
            <p style="color: #6b7280; font-size: 12px;">Bestlink College - Parcel Sorting System</p>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(body, 'html'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        print(f"✅ OTP email sent to {to_email}")
        return True
    except Exception as e:
        print(f"❌ Email send error: {e}")
        # Fallback to console
        print(f"\n[EMAIL FALLBACK] OTP for {to_email}: {otp_code}\n")
        return False

# ========================================================
# 🎥 Camera Stream Classes
# ========================================================

# PiCamera Stream Class (for Parcel Scanner)
class PiCameraStream:
    def __init__(self):
        self.picam2 = picam2
        self.running = False
        self.thread = None
        self.frame_queue = queue.Queue(maxsize=5)
        self.preview_queue = queue.Queue(maxsize=5)
        self.lock = threading.Lock()
        self.fps = 0
        self.frame_count = 0
        self.last_time = time.time()
        self.target_fps = 30
        self.frame_time = 1.0 / 30
        self.stats_interval = 5.0
        self.box_x = 0
        self.box_y = 0
        self.box_w = 0
        self.box_h = 0
        self.last_frame = None
        
    def start(self):
        if not PICAMERA_ENABLED or self.picam2 is None:
            return False
        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        print("📷 PiCamera stream started")
        return True
    
    def _calculate_ocr_box(self, frame_width, frame_height):
        box_width = int(frame_width * 0.6)
        box_height = int(frame_height * 0.3)
        box_x = (frame_width - box_width) // 2
        box_y = (frame_height - box_height) // 2
        return box_x, box_y, box_width, box_height
    
    def _capture_loop(self):
        box_calculated = False
        last_stats_print = time.time()
        
        while self.running:
            try:
                loop_start = time.time()
                
                if self.picam2 is not None:
                    frame_main = self.picam2.capture_array("main")
                    frame_lores = self.picam2.capture_array("lores")
                    
                    if frame_main is not None and frame_lores is not None:
                        self.last_frame = frame_main.copy()
                        
                        frame_main_bgr = cv2.cvtColor(frame_main, cv2.COLOR_RGB2BGR)
                        frame_lores_bgr = cv2.cvtColor(frame_lores, cv2.COLOR_RGB2BGR)
                        
                        if not box_calculated:
                            h, w = frame_lores_bgr.shape[:2]
                            self.box_x, self.box_y, self.box_w, self.box_h = self._calculate_ocr_box(w, h)
                            box_calculated = True
                            print(f"   OCR Box: {self.box_w}x{self.box_h}")
                        
                        self.frame_count += 1
                        
                        current_time = time.time()
                        time_diff = current_time - self.last_time
                        
                        if time_diff >= self.stats_interval:
                            self.fps = self.frame_count / time_diff
                            self.frame_count = 0
                            self.last_time = current_time
                            
                            if current_time - last_stats_print >= 30:
                                print(f"📊 PiCamera FPS: {self.fps:.1f}")
                                last_stats_print = current_time
                        
                        # Draw OCR box on preview
                        cv2.rectangle(frame_lores_bgr, 
                                    (self.box_x, self.box_y), 
                                    (self.box_x + self.box_w, self.box_y + self.box_h), 
                                    (0, 255, 0), 2)
                        
                        label = "OCR SCAN AREA"
                        cv2.rectangle(frame_lores_bgr,
                                    (self.box_x, self.box_y - 25),
                                    (self.box_x + 140, self.box_y - 5),
                                    (0, 255, 0), -1)
                        
                        cv2.putText(frame_lores_bgr, label, 
                                  (self.box_x + 5, self.box_y - 8),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)
                        
                        with self.lock:
                            # Main frame queue (for OCR)
                            if self.frame_queue.full():
                                try:
                                    self.frame_queue.get_nowait()
                                except:
                                    pass
                            self.frame_queue.put_nowait(frame_main_bgr)
                            
                            # Preview queue (for parcel scanner display)
                            if self.preview_queue.full():
                                try:
                                    self.preview_queue.get_nowait()
                                except:
                                    pass
                            self.preview_queue.put_nowait(frame_lores_bgr)
                    
                    loop_end = time.time()
                    elapsed = loop_end - loop_start
                    sleep_time = max(0, self.frame_time - elapsed)
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    
            except Exception as e:
                print(f"PiCamera capture error: {e}")
                time.sleep(0.1)
    
    def get_frame(self):
        try:
            return self.frame_queue.get_nowait()
        except:
            return self.last_frame
    
    def get_preview(self):
        try:
            return self.preview_queue.get_nowait()
        except:
            if self.last_frame is not None:
                return cv2.cvtColor(cv2.resize(self.last_frame, (640, 360)), cv2.COLOR_RGB2BGR)
            return None
    
    def get_fps(self):
        return self.fps
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)

# USB Webcam Stream Class (for Robotic Arm)
class USBCameraStream:
    def __init__(self):
        self.cap = usb_cap
        self.running = False
        self.thread = None
        self.frame_queue = queue.Queue(maxsize=5)
        self.lock = threading.Lock()
        self.fps = 0
        self.frame_count = 0
        self.last_time = time.time()
        self.target_fps = 30
        self.frame_time = 1.0 / 30
        self.stats_interval = 5.0
        self.last_frame = None
        
    def start(self):
        if not USBCAM_ENABLED or self.cap is None:
            return False
        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        print("📷 USB Webcam stream started for Robotic Arm")
        return True
    
    def _capture_loop(self):
        last_stats_print = time.time()
        
        while self.running:
            try:
                loop_start = time.time()
                
                if self.cap is not None and self.cap.isOpened():
                    ret, frame = self.cap.read()
                    
                    if ret and frame is not None:
                        self.last_frame = frame.copy()
                        
                        self.frame_count += 1
                        
                        current_time = time.time()
                        time_diff = current_time - self.last_time
                        
                        if time_diff >= self.stats_interval:
                            self.fps = self.frame_count / time_diff
                            self.frame_count = 0
                            self.last_time = current_time
                            
                            if current_time - last_stats_print >= 30:
                                print(f"📊 USB Webcam FPS: {self.fps:.1f}")
                                last_stats_print = current_time
                        
                        with self.lock:
                            if self.frame_queue.full():
                                try:
                                    self.frame_queue.get_nowait()
                                except:
                                    pass
                            self.frame_queue.put_nowait(frame)
                    
                    loop_end = time.time()
                    elapsed = loop_end - loop_start
                    sleep_time = max(0, self.frame_time - elapsed)
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    
            except Exception as e:
                print(f"USB Webcam capture error: {e}")
                time.sleep(0.1)
    
    def get_frame(self):
        try:
            return self.frame_queue.get_nowait()
        except:
            return self.last_frame
    
    def get_fps(self):
        return self.fps
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.cap:
            self.cap.release()
            print("📷 USB Webcam released")

# Initialize camera streams
picamera_stream = PiCameraStream()
usbcam_stream = USBCameraStream()

# ========================================================
# 🤖 Arduino Monitor Class
# ========================================================
class ArduinoMonitor:
    def __init__(self):
        self.proximity_detected = False
        self.pickup_in_progress = False
        self.object_waiting = False
        self.conveyor_enabled = False
        self.last_proximity_time = 0
        self.last_pickup_time = 0
        self.lock = threading.Lock()
        self.messages = []
        self.max_messages = 50
        self.arm_position = "IDLE"
        self.gripper_status = "OPEN"
        self.current_lane = "NONE"
        
    def update_from_arduino(self, line):
        with self.lock:
            # Store message
            self.messages.append({
                'time': datetime.now().strftime("%H:%M:%S"),
                'message': line
            })
            if len(self.messages) > self.max_messages:
                self.messages = self.messages[-self.max_messages:]
            
            # Parse different message types
            if line.startswith('PROX:'):
                try:
                    self.proximity_detected = (int(line.split(':')[1]) == 1)
                    if self.proximity_detected:
                        self.last_proximity_time = time.time()
                except:
                    pass
                    
            elif 'STATUS:' in line:
                # Parse full status
                try:
                    # Format: STATUS: PROX=1,ENABLED=1,PICKUP=0,GRIP=OPEN,POS=IDLE
                    parts = line.replace('STATUS:', '').strip().split(',')
                    for part in parts:
                        if '=' in part:
                            key, value = part.split('=')
                            if key == 'PROX':
                                self.proximity_detected = (int(value) == 1)
                            elif key == 'ENABLED':
                                self.conveyor_enabled = (int(value) == 1)
                            elif key == 'PICKUP':
                                self.pickup_in_progress = (int(value) == 1)
                            elif key == 'GRIP':
                                self.gripper_status = value
                            elif key == 'POS':
                                self.arm_position = value
                except:
                    pass
                    
            elif 'PICKUP_SEQUENCE: START' in line:
                self.pickup_in_progress = True
                self.arm_position = "PICKING"
                print("🔄 Robotic arm picking up parcel from storage...")
                
            elif 'Parcel GRABBED' in line:
                self.gripper_status = "CLOSED"
                self.arm_position = "MOVING"
                print("🤖 Parcel grabbed, moving to conveyor...")
                
            elif 'Parcel PLACED' in line:
                self.gripper_status = "OPEN"
                self.arm_position = "PLACED"
                print("📦 Parcel placed on conveyor, ready for scanning")
                
            elif 'PICKUP_SEQUENCE: COMPLETE' in line:
                self.pickup_in_progress = False
                self.arm_position = "IDLE"
                self.last_pickup_time = time.time()
                print("✅ Pickup complete, ready for next object")
                
            elif 'PROXIMITY: OBJECT DETECTED' in line:
                self.object_waiting = True
                print("📦 Object detected on storage rack!")
                
            elif 'Conveyor not enabled' in line:
                print("⏸️ Conveyor not enabled - Object waiting for UI start")
                
            elif 'CONVEYOR_STARTED' in line:
                self.conveyor_enabled = True
                print("▶️ Conveyor started from UI")
                
            elif 'CONVEYOR_STOPPED' in line:
                self.conveyor_enabled = False
                print("⏹️ Conveyor stopped from UI")
                
            elif 'EMERGENCY_STOP_ACTIVATED' in line:
                self.conveyor_enabled = False
                self.pickup_in_progress = False
                print("🚨 EMERGENCY STOP ACTIVATED!")
                
            elif 'SORTING TO LANE' in line:
                try:
                    self.current_lane = line.split('LANE')[-1].strip()
                    print(f"➡️ Sorting to Lane {self.current_lane}")
                except:
                    pass
                
    def get_status(self):
        with self.lock:
            return {
                'proximity_detected': self.proximity_detected,
                'pickup_in_progress': self.pickup_in_progress,
                'object_waiting': self.object_waiting,
                'conveyor_enabled': self.conveyor_enabled,
                'last_proximity': self.last_proximity_time,
                'last_pickup': self.last_pickup_time,
                'arm_position': self.arm_position,
                'gripper_status': self.gripper_status,
                'current_lane': self.current_lane,
                'status_text': self._get_status_text()
            }
    
    def _get_status_text(self):
        if self.pickup_in_progress:
            return f"🔄 Picking up parcel... ({self.arm_position})"
        elif self.object_waiting and not self.conveyor_enabled:
            return "⏸️ Object waiting - Start conveyor from UI"
        elif self.object_waiting:
            return "📦 Object detected - Ready to pick up"
        elif self.conveyor_enabled:
            return "▶️ Conveyor running - Waiting for objects"
        else:
            return "⏹️ System idle - Start conveyor from UI"
    
    def get_messages(self):
        with self.lock:
            return self.messages.copy()

arduino_monitor = ArduinoMonitor()

# ========================================================
# 🤖 Arduino Mega Controller
# ========================================================
class ArduinoMega:
    def __init__(self):
        self.port = None
        self.ser = None
        self.connected = False
        self.lock = threading.Lock()
        self.response_queue = queue.Queue()
        self.listener_running = False
        self.proximity_detected = False
        self.servo_positions = [90] * 16
        self.common_ports = ['/dev/ttyACM0', '/dev/ttyACM1', '/dev/ttyUSB0', '/dev/ttyUSB1']
        self.connect()
        
    def connect(self):
        print("\n🔍 Scanning for Arduino Mega...")
        
        for port in self.common_ports:
            try:
                if not os.path.exists(port):
                    continue
                    
                print(f"   Trying {port}...")
                self.ser = serial.Serial(port, 9600, timeout=2)
                time.sleep(2.5)
                self.ser.reset_input_buffer()
                self.ser.write(b"STATUS\n")
                time.sleep(1)
                
                responses = []
                while self.ser.in_waiting > 0:
                    response = self.ser.readline().decode().strip()
                    if response:
                        responses.append(response)
                
                if responses:
                    self.connected = True
                    self.port = port
                    print(f"✅✅✅ Arduino Mega connected on {port}")
                    
                    self.listener_running = True
                    threading.Thread(target=self._listener, daemon=True).start()
                    
                    time.sleep(1)
                    self.home_all()
                    
                    # Start monitoring thread for status updates
                    threading.Thread(target=self._status_monitor, daemon=True).start()
                    
                    return True
                
                self.ser.close()
                
            except Exception as e:
                continue
        
        print("\n❌❌❌ Arduino Mega NOT FOUND")
        print("   Please check USB connection and restart")
        sys.exit(1)
    
    def _listener(self):
        while self.listener_running and self.ser and self.ser.is_open:
            try:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode().strip()
                    if line:
                        # Update monitor
                        arduino_monitor.update_from_arduino(line)
                        
                        # Store in response queue
                        self.response_queue.put(line)
                        
                        # Update local proximity status
                        if line.startswith('PROX:'):
                            try:
                                self.proximity_detected = (int(line.split(':')[1]) == 1)
                            except:
                                pass
            except Exception as e:
                print(f"⚠️ Serial listener error: {e}")
                break
    
    def _status_monitor(self):
        """Periodically request status from Arduino"""
        while self.listener_running:
            try:
                if self.connected:
                    self.send("STATUS")
                time.sleep(2)
            except:
                pass
    
    def send(self, command, wait_response=False):
        if not self.connected:
            return None if wait_response else False
        
        try:
            with self.lock:
                if not command.endswith('\n'):
                    command += '\n'
                self.ser.write(command.encode())
                
                if wait_response:
                    time.sleep(0.5)
                    responses = []
                    start = time.time()
                    while time.time() - start < 2:
                        if self.ser.in_waiting:
                            resp = self.ser.readline().decode().strip()
                            if resp:
                                responses.append(resp)
                        else:
                            time.sleep(0.1)
                    return responses[-1] if responses else None
                return True
        except Exception as e:
            print(f"❌ Serial error: {e}")
            self.connected = False
            return None if wait_response else False
    
    def set_servo(self, channel, angle):
        if 0 <= channel <= 15 and 0 <= angle <= 180:
            cmd = f"SERVO:{channel}:{angle}"
            self.send(cmd)
            self.servo_positions[channel] = angle
            return True
        return False
    
    def home_all(self):
        return self.send("HOME ALL")
    
    def grip_open(self):
        return self.send("GRIP OPEN")
    
    def grip_close(self):
        return self.send("GRIP CLOSE")
    
    def conveyor_start(self):
        return self.send("START_CONVEYOR")
    
    def conveyor_stop(self):
        return self.send("STOP_CONVEYOR")
    
    def emergency_stop(self):
        return self.send("EMERGENCY")
    
    def reset_system(self):
        return self.send("RESET")
    
    def sort_to_lane(self, lane):
        return self.send(f"SORT {lane}")
    
    def trigger_pickup(self):
        """Manually trigger pickup sequence"""
        if not arduino_monitor.pickup_in_progress:
            return self.send("ARM PICKUP")
        return False
    
    def close(self):
        self.listener_running = False
        if self.ser and self.ser.is_open:
            self.ser.close()

arduino = ArduinoMega()

# ========================================================
# 📝 OCR Processing
# ========================================================
unique_code_counter = 1000
unique_code_lock = threading.Lock()

def generate_unique_code():
    global unique_code_counter
    with unique_code_lock:
        unique_code_counter += 1
        return f"PKG-{datetime.now().strftime('%y%m')}-{unique_code_counter:06d}"

def preprocess_for_ocr(frame):
    """Preprocess image for better OCR"""
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(gray)
        _, thresh = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return thresh
    except:
        return frame

def extract_zipcode(text):
    if not text:
        return None
    # Look for Bulacan zipcodes (3000-3024)
    matches = re.findall(r'\b(30[0-2][0-9])\b', text)
    for match in matches:
        try:
            if 3000 <= int(match) <= 3024:
                return match
        except:
            pass
    return matches[0] if matches else None

def process_ocr(frame):
    if not pytesseract_available:
        return None
    
    try:
        h, w = frame.shape[:2]
        box_x = int(w * 0.2)
        box_y = int(h * 0.35)
        box_w = int(w * 0.6)
        box_h = int(h * 0.3)
        
        roi = frame[box_y:box_y+box_h, box_x:box_x+box_w]
        if roi.size == 0:
            return None
        
        processed = preprocess_for_ocr(roi)
        
        # OCR with multiple configs for better detection
        data = pytesseract.image_to_data(processed, config='--oem 3 --psm 6', 
                                        output_type=pytesseract.Output.DICT)
        
        texts = []
        confs = []
        for i, text in enumerate(data['text']):
            try:
                conf = int(data['conf'][i])
                if conf > 60 and text.strip():
                    texts.append(text.strip())
                    confs.append(conf)
            except:
                pass
        
        if texts:
            text = ' '.join(texts)
            avg_conf = sum(confs) / len(confs)
            zipcode = extract_zipcode(text)
            if zipcode:
                return {
                    'zipcode': zipcode,
                    'unique_code': generate_unique_code(),
                    'confidence': avg_conf,
                    'raw_text': text
                }
        return None
    except Exception as e:
        return None

# ========================================================
# 📊 Database
# ========================================================
class ParcelDB:
    def __init__(self):
        self.parcels = []
        self.max_entries = 100
        self.lock = threading.Lock()
        self.locations = {
            '3000': 'Malolos', '3001': 'Paombong', '3002': 'Hagonoy',
            '3003': 'Calumpit', '3004': 'Pulilan', '3005': 'Plaridel',
            '3006': 'Baliuag', '3007': 'Bustos', '3008': 'San Rafael',
            '3009': 'DRT', '3010': 'San Ildefonso', '3011': 'San Miguel',
            '3012': 'Angat', '3013': 'Norzagaray', '3014': 'San Jose Del Monte',
            '3015': 'Guiguinto', '3016': 'Balagtas', '3017': 'Bulakan',
            '3018': 'Bocaue', '3019': 'Marilao', '3020': 'Meycauayan',
            '3021': 'Obando', '3022': 'Santa Maria', '3023': 'Pandi',
            '3024': 'Sapang Palay'
        }
    
    def add(self, zipcode, lane, unique_code=None):
        with self.lock:
            if unique_code is None:
                unique_code = generate_unique_code()
            parcel = {
                'zipcode': zipcode,
                'unique_code': unique_code,
                'lane': lane,
                'address': self.locations.get(zipcode, 'Unknown'),
                'timestamp': datetime.now().isoformat(),
                'datetime': datetime.now()
            }
            self.parcels.append(parcel)
            
            if MONGO_ENABLED and parcels_col is not None:
                try:
                    parcels_col.insert_one(parcel.copy())
                except Exception as e:
                    print(f"⚠️ MongoDB insert error: {e}")
            
            if len(self.parcels) > self.max_entries:
                self.parcels = self.parcels[-self.max_entries:]
            return parcel
    
    def get_all(self):
        with self.lock:
            return self.parcels.copy()
    
    def get_stats(self):
        with self.lock:
            lane_counts = {'Lane A': 0, 'Lane B': 0, 'Lane C': 0, 'Unsorted': 0}
            for p in self.parcels:
                lane_counts[p['lane']] = lane_counts.get(p['lane'], 0) + 1
            return {
                'total': len(self.parcels),
                'lane_a': lane_counts['Lane A'],
                'lane_b': lane_counts['Lane B'],
                'lane_c': lane_counts['Lane C'],
                'unsorted': lane_counts['Unsorted']
            }

db = ParcelDB()

# ========================================================
# 🎯 Sorting Logic
# ========================================================
def get_lane(zipcode):
    try:
        z = int(zipcode)
        if 3000 <= z <= 3008: return 'Lane A'
        elif 3009 <= z <= 3016: return 'Lane B'
        elif 3017 <= z <= 3024: return 'Lane C'
    except:
        pass
    return 'Unsorted'

def control_servo(lane):
    if lane == 'Unsorted' or not arduino.connected:
        return
    
    lane_letter = lane.split()[1]
    arduino.sort_to_lane(lane_letter)

# ========================================================
# 🔄 Background Workers
# ========================================================
class Workers:
    def __init__(self):
        self.running = True
        
    def start(self):
        if PICAMERA_ENABLED:
            picamera_stream.start()
        if USBCAM_ENABLED:
            usbcam_stream.start()
        threading.Thread(target=self._ocr_worker, daemon=True).start()
        threading.Thread(target=self._status_monitor, daemon=True).start()
        print("⚙️ Workers started")
    
    def _ocr_worker(self):
        scan_cooldown = 0
        while self.running:
            if PICAMERA_ENABLED and pytesseract_available:
                frame = picamera_stream.get_frame()
                if frame is not None:
                    # Only scan if conveyor is running and cooldown is 0
                    if arduino_monitor.conveyor_enabled and scan_cooldown <= 0:
                        result = process_ocr(frame)
                        if result:
                            zipcode = result['zipcode']
                            lane = get_lane(zipcode)
                            print(f"✅ OCR SCAN: {zipcode} -> {lane} ({result['confidence']:.1f}%)")
                            
                            parcel = db.add(zipcode, lane, result['unique_code'])
                            
                            if lane != 'Unsorted':
                                control_servo(lane)
                            
                            global last_processed
                            last_processed = {
                                'zipcode': zipcode,
                                'unique_code': result['unique_code'],
                                'lane': lane,
                                'address': parcel['address'],
                                'timestamp': datetime.now().strftime("%H:%M:%S"),
                                'confidence': f"{result['confidence']:.1f}%"
                            }
                            scan_cooldown = 30  # Wait 30 frames before next scan
                    else:
                        scan_cooldown -= 1
                    
                    time.sleep(0.01)
                else:
                    time.sleep(0.001)
            else:
                time.sleep(0.5)
    
    def _status_monitor(self):
        while self.running:
            try:
                global system_status
                # Get Raspberry Pi temperature
                temp = "N/A"
                try:
                    with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                        temp = f"{int(f.read().strip()) / 1000:.1f}°C"
                except:
                    pass
                
                system_status = {
                    'cpu': psutil.cpu_percent(),
                    'ram': psutil.virtual_memory().percent,
                    'cpu_temp': temp,
                    'disk': psutil.disk_usage('/').percent,
                    'uptime': time.time() - psutil.boot_time()
                }
            except:
                pass
            time.sleep(2)
    
    def stop(self):
        self.running = False
        if PICAMERA_ENABLED:
            picamera_stream.stop()
        if USBCAM_ENABLED:
            usbcam_stream.stop()

# ========================================================
# 🔐 Authentication Helpers
# ========================================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

def generate_otp():
    """Generate 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))

def store_otp(email, otp):
    """Store OTP in database with expiry"""
    if MONGO_ENABLED and otps_col is not None:
        # Delete old OTPs for this email
        otps_col.delete_many({'email': email})
        
        # Store new OTP
        otps_col.insert_one({
            'email': email,
            'otp': otp,
            'created_at': datetime.now(),
            'expires_at': datetime.now() + timedelta(minutes=5),
            'used': False
        })
    else:
        # Fallback to in-memory storage
        if not hasattr(store_otp, 'otp_store'):
            store_otp.otp_store = {}
        store_otp.otp_store[email] = {
            'otp': otp,
            'expires_at': time.time() + 300
        }

def verify_otp(email, otp):
    """Verify OTP from database"""
    if MONGO_ENABLED and otps_col is not None:
        # Find valid OTP
        result = otps_col.find_one({
            'email': email,
            'otp': otp,
            'used': False,
            'expires_at': {'$gt': datetime.now()}
        })
        
        if result:
            # Mark as used
            otps_col.update_one({'_id': result['_id']}, {'$set': {'used': True}})
            return True
        return False
    else:
        # Fallback to in-memory
        if hasattr(store_otp, 'otp_store') and email in store_otp.otp_store:
            data = store_otp.otp_store[email]
            if data['otp'] == otp and data['expires_at'] > time.time():
                del store_otp.otp_store[email]
                return True
        return False

# ========================================================
# 🌐 Flask App
# ========================================================
app = Flask(__name__)
app.secret_key = 'bestlink-college-cpe-2026-conveyor-system'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

system_status = {}
conveyor_running = False
last_processed = {
    'zipcode': 'N/A', 
    'unique_code': 'N/A', 
    'lane': 'N/A',
    'address': 'N/A', 
    'timestamp': 'N/A',
    'confidence': 'N/A'
}

# ========================================================
# 📋 Routes
# ========================================================
@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json()
        
        username = data.get('username')
        password = data.get('password')
        api_key = data.get('api_key')
        
        if MONGO_ENABLED and users_col is not None:
            # Check MongoDB
            user = users_col.find_one({'username': username})
            if user and check_password_hash(user['password'], password) and user.get('api_key') == api_key:
                session['user'] = username
                session['user_id'] = str(user['_id'])
                session.permanent = True
                return jsonify({
                    'success': True,
                    'message': 'Login successful',
                    'user': username
                })
        else:
            # Fallback to hardcoded credentials
            if username == 'admin' and password == 'admin' and api_key == '123456':
                session['user'] = username
                session.permanent = True
                return jsonify({
                    'success': True,
                    'message': 'Login successful',
                    'user': username
                })
        
        return jsonify({'success': False, 'message': 'Invalid credentials'}), 401
    
    return render_template('login.html')

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    """Verify email with OTP"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Invalid request'}), 400
            
        email = data.get('email')
        action = data.get('action')
        
        if action == 'send_otp':
            if not email:
                return jsonify({'success': False, 'message': 'Email required'}), 400
                
            # Generate OTP
            otp = generate_otp()
            
            # Store OTP
            store_otp(email, otp)
            
            # Send email
            send_otp_email(email, otp)
            
            return jsonify({
                'success': True,
                'message': 'OTP sent successfully'
            })
        
        elif action == 'verify_otp':
            otp = data.get('otp')
            email = data.get('email')
            
            if not email or not otp:
                return jsonify({'success': False, 'message': 'Email and OTP required'}), 400
            
            if verify_otp(email, otp):
                session['verified'] = True
                session['verified_email'] = email
                return jsonify({
                    'success': True,
                    'message': 'Verification successful'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Invalid or expired OTP'
                }), 401
    
    # GET request - render the verify page
    return render_template('verify.html')

@app.route('/auth/status')
def auth_status():
    """Check authentication status - used by frontend"""
    return jsonify({
        'authenticated': 'user' in session,
        'verified': session.get('verified', False),
        'user': session.get('user')
    })

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ========================================================
# 📊 API Routes
# ========================================================
@app.route('/api/status')
@login_required
def api_status():
    arm_status = arduino_monitor.get_status()
    stats = db.get_stats()
    
    return jsonify({
        'success': True,
        'timestamp': datetime.now().isoformat(),
        'system': {
            'cpu': system_status.get('cpu', 0),
            'ram': system_status.get('ram', 0),
            'cpu_temp': system_status.get('cpu_temp', 'N/A'),
            'disk': system_status.get('disk', 0),
            'uptime': system_status.get('uptime', 0)
        },
        'hardware': {
            'arduino': arduino.connected,
            'picamera': PICAMERA_ENABLED,
            'usbcam': USBCAM_ENABLED,
            'ocr': pytesseract_available,
            'mongodb': MONGO_ENABLED,
            'email': EMAIL_ENABLED
        },
        'conveyor': {
            'running': arduino_monitor.conveyor_enabled,
            'status_text': arm_status['status_text']
        },
        'arm': {
            'proximity': arm_status['proximity_detected'],
            'pickup_in_progress': arm_status['pickup_in_progress'],
            'object_waiting': arm_status['object_waiting'],
            'arm_position': arm_status['arm_position'],
            'gripper_status': arm_status['gripper_status'],
            'current_lane': arm_status['current_lane']
        },
        'last_scan': last_processed,
        'statistics': stats
    })

@app.route('/api/arm/status')
@login_required
def api_arm_status():
    return jsonify({
        'success': True,
        **arduino_monitor.get_status()
    })

@app.route('/api/arm/messages')
@login_required
def api_arm_messages():
    return jsonify({
        'success': True,
        'messages': arduino_monitor.get_messages()
    })

@app.route('/api/parcels')
@login_required
def api_parcels():
    limit = request.args.get('limit', 50, type=int)
    parcels = db.get_all()
    return jsonify({
        'success': True,
        'count': len(parcels),
        'parcels': parcels[-limit:]  # Return last N parcels
    })

@app.route('/api/parcels/stats')
@login_required
def api_parcels_stats():
    return jsonify({
        'success': True,
        **db.get_stats()
    })

@app.route('/api/parcels/recent')
@login_required
def api_parcels_recent():
    parcels = db.get_all()
    recent = parcels[-10:] if parcels else []
    return jsonify({
        'success': True,
        'parcels': recent
    })

# ========================================================
# 🎮 Control API Routes
# ========================================================
@app.route('/api/control/conveyor', methods=['POST'])
@login_required
def api_control_conveyor():
    data = request.get_json()
    action = data.get('action')
    
    if action == 'start':
        result = arduino.conveyor_start()
        if result:
            return jsonify({
                'success': True,
                'message': 'Conveyor started',
                'status': 'running'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to start conveyor'
            }), 500
    
    elif action == 'stop':
        result = arduino.conveyor_stop()
        if result:
            return jsonify({
                'success': True,
                'message': 'Conveyor stopped',
                'status': 'stopped'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to stop conveyor'
            }), 500
    
    return jsonify({'success': False, 'message': 'Invalid action'}), 400

@app.route('/api/control/emergency', methods=['POST'])
@login_required
def api_control_emergency():
    result = arduino.emergency_stop()
    if result:
        return jsonify({
            'success': True,
            'message': 'Emergency stop activated'
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Failed to activate emergency stop'
        }), 500

# ========================================================
# 📹 Video Feed Routes
# ========================================================
@app.route('/video_feed')
def video_feed():
    """Parcel scanner camera feed from PiCamera with overlay"""
    def generate():
        while True:
            if PICAMERA_ENABLED:
                frame = picamera_stream.get_preview()
                if frame is not None:
                    # Add status overlay
                    arm_status = arduino_monitor.get_status()
                    stats = db.get_stats()
                    
                    # Add timestamp
                    cv2.putText(frame, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                              (10, frame.shape[0] - 10),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
                    
                    # Add FPS
                    cv2.putText(frame, f"FPS: {picamera_stream.get_fps():.1f}", 
                              (frame.shape[1] - 80, 20),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
                    
                    # Add "PARCEL SCANNER" label
                    cv2.putText(frame, "PARCEL SCANNER", 
                              (10, 30),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                    
                    # Add conveyor status
                    y_pos = 60
                    if arm_status['conveyor_enabled']:
                        cv2.putText(frame, "▶️ CONVEYOR: RUNNING", (10, y_pos),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                    else:
                        cv2.putText(frame, "⏹️ CONVEYOR: STOPPED", (10, y_pos),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                    y_pos += 25
                    
                    # Add statistics
                    cv2.putText(frame, f"TOTAL: {stats['total']} | A:{stats['lane_a']} B:{stats['lane_b']} C:{stats['lane_c']}", 
                              (10, y_pos),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
                    
                    # Add last scan
                    if last_processed['zipcode'] != 'N/A':
                        cv2.putText(frame, f"LAST: {last_processed['zipcode']} {last_processed['lane']} {last_processed['confidence']}", 
                                  (frame.shape[1] - 280, 30),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 2)
                    
                    # Draw OCR box
                    h, w = frame.shape[:2]
                    box_x = int(w * 0.2)
                    box_y = int(h * 0.35)
                    box_w = int(w * 0.6)
                    box_h = int(h * 0.3)
                    cv2.rectangle(frame, (box_x, box_y), (box_x + box_w, box_y + box_h), (0, 255, 0), 1)
                    cv2.putText(frame, "OCR SCAN AREA", (box_x + 5, box_y - 5),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
                    
                    _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + 
                           jpeg.tobytes() + b'\r\n')
                else:
                    time.sleep(0.001)
            else:
                # PiCamera offline message
                blank = np.zeros((360, 640, 3), dtype=np.uint8)
                cv2.putText(blank, "PICAMERA OFFLINE", (120, 180),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                _, jpeg = cv2.imencode('.jpg', blank, [cv2.IMWRITE_JPEG_QUALITY, 85])
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + 
                       jpeg.tobytes() + b'\r\n')
                time.sleep(1)
    
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/arm_feed')
def arm_feed():
    """Robotic arm camera feed from USB webcam"""
    print("🔴 ARM FEED ROUTE HIT! 🔴")
    def generate():
        while True:
            if USBCAM_ENABLED:
                frame = usbcam_stream.get_frame()
                if frame is not None:
                    # Add arm status overlay
                    arm_status = arduino_monitor.get_status()
                    
                    # Add timestamp
                    cv2.putText(frame, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                              (10, frame.shape[0] - 10),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
                    
                    # Add FPS
                    cv2.putText(frame, f"FPS: {usbcam_stream.get_fps():.1f}", 
                              (frame.shape[1] - 80, 20),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
                    
                    # Add "ROBOTIC ARM VIEW" label
                    cv2.putText(frame, "ROBOTIC ARM VIEW (USB)", 
                              (10, 30),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
                    
                    # Add arm status
                    y_pos = 60
                    if arm_status['pickup_in_progress']:
                        cv2.putText(frame, f"🤖 ARM: {arm_status['arm_position']}", (10, y_pos),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
                        y_pos += 25
                    elif arm_status['object_waiting']:
                        cv2.putText(frame, "📦 OBJECT DETECTED", (10, y_pos),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                        y_pos += 25
                    
                    # Add gripper status
                    cv2.putText(frame, f"GRIPPER: {arm_status['gripper_status']}", (10, y_pos),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
                    
                    _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + 
                           jpeg.tobytes() + b'\r\n')
                else:
                    # If no frame, create a waiting frame
                    test_frame = np.zeros((360, 640, 3), dtype=np.uint8)
                    cv2.putText(test_frame, "USB WEBCAM - WAITING FOR FRAMES", (80, 180),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    cv2.putText(test_frame, datetime.now().strftime("%H:%M:%S"), (220, 240),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                    _, jpeg = cv2.imencode('.jpg', test_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + 
                           jpeg.tobytes() + b'\r\n')
                    time.sleep(0.1)
            else:
                # USB webcam offline message
                blank = np.zeros((360, 640, 3), dtype=np.uint8)
                cv2.putText(blank, "USB WEBCAM OFFLINE", (100, 180),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                cv2.putText(blank, "Check USB connection", (150, 220),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                _, jpeg = cv2.imencode('.jpg', blank, [cv2.IMWRITE_JPEG_QUALITY, 85])
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + 
                       jpeg.tobytes() + b'\r\n')
                time.sleep(1)
    
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# ========================================================
# 🏁 Main
# ========================================================
def signal_handler(sig, frame):
    print("\n🛑 Shutting down...")
    workers.stop()
    arduino.close()
    if PICAMERA_ENABLED and picam2:
        picam2.stop()
    if USBCAM_ENABLED and usb_cap:
        usb_cap.release()
    if client:
        client.close()
    sys.exit(0)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    
    workers = Workers()
    workers.start()
    
    print("\n" + "="*70)
    print("✅ SYSTEM READY - DUAL CAMERA MODE")
    print("="*70)
    print("🌐 Web Interface: http://localhost:5000")
    print("🔑 Login: admin / admin / 123456")
    print("📧 Verification: Enter your Gmail to receive OTP")
    print("="*70)
    print("📦 SYSTEM STATUS:")
    print(f"   📡 Arduino Mega: {'CONNECTED' if arduino.connected else 'NOT FOUND'}")
    print(f"   📷 PiCamera (Scanner): {'ONLINE' if PICAMERA_ENABLED else 'OFFLINE'}")
    print(f"   📷 USB Webcam (Arm): {'ONLINE' if USBCAM_ENABLED else 'OFFLINE'}")
    print(f"   🔤 OCR: {'AVAILABLE' if pytesseract_available else 'NOT AVAILABLE'}")
    print(f"   🗄️ MongoDB: {'CONNECTED' if MONGO_ENABLED else 'DISCONNECTED'}")
    print(f"   📧 Email: {'CONFIGURED' if EMAIL_ENABLED else 'SIMULATION MODE'}")
    print("="*70)
    print("📦 VIDEO FEEDS:")
    print("   📷 Parcel Scanner (PiCamera): http://localhost:5000/video_feed")
    print("   🤖 Arm Camera (USB): http://localhost:5000/arm_feed")
    print("="*70)
    
    # Debug: Print all registered routes
    print("\n" + "="*70)
    print("📋 REGISTERED ROUTES:")
    print("="*70)
    route_found = False
    for rule in app.url_map.iter_rules():
        print(f"   {rule.rule}")
        if rule.rule == '/arm_feed':
            route_found = True
    
    if route_found:
        print("\n✅ SUCCESS: /arm_feed IS REGISTERED!")
    else:
        print("\n❌ ERROR: /arm_feed IS NOT REGISTERED!")
    print("="*70 + "\n")
    
    # Make sure templates folder exists
    if not os.path.exists('templates'):
        os.makedirs('templates')
        print("📁 Created templates folder")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)