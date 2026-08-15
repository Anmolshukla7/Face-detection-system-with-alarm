import os
import re
import time
import math
import sqlite3
import queue
import subprocess
try:
    import pyttsx3
except ImportError:
    pyttsx3 = None
try:
    import winsound
except ImportError:
    winsound = None
from datetime import datetime
import cv2
import numpy as np

# ==========================================
# 1. DATABASE & LOGGING CONFIGURATION
# ==========================================
DB_FILE = "security_database.db"

def init_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        role TEXT,
        department TEXT,
        clearance_level TEXT DEFAULT 'LEVEL-3'
    )
    """)
    # Automatically migrate existing database if clearance_level column is missing
    cursor.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    if "clearance_level" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN clearance_level TEXT DEFAULT 'LEVEL-3'")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS access_logs (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        person_name TEXT,
        timestamp TEXT,
        status TEXT
    )
    """)
    sample_users = [
        ("anmol", "Anmol Shukla", "Chief AI Architect", "DeepMind CyberOps", "LEVEL-5 (MAX)"),
        ("rahul", "Rahul Sharma", "Lead Security Engineer", "Threat Intelligence", "LEVEL-4"),
        ("elon", "Elon Musk", "Visiting VIP", "Special Operations", "LEVEL-4"),
    ]
    for uid, name, role, dept, lvl in sample_users:
        cursor.execute("""
            INSERT OR REPLACE INTO users (id, full_name, role, department, clearance_level)
            VALUES (?, ?, ?, ?, ?)
        """, (uid, name, role, dept, lvl))
    conn.commit()
    conn.close()

def get_user_from_db(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, role, department, clearance_level FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"full_name": row[0], "role": row[1], "department": row[2], "clearance": row[3]}
    return {"full_name": user_id.capitalize(), "role": "Authorized Personnel", "department": "Operations", "clearance": "LEVEL-2"}

def get_recent_access_logs(limit=5):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT person_name, timestamp, status FROM access_logs ORDER BY log_id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception:
        return []

last_logged_time = {}
def log_access_to_db(user_id, person_name, status="AUTHORIZED"):
    global last_logged_time
    now = time.time()
    if user_id in last_logged_time and (now - last_logged_time[user_id] < 6):
        return
    last_logged_time[user_id] = now
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO access_logs (user_id, person_name, timestamp, status) VALUES (?, ?, ?, ?)",
                   (user_id, person_name, timestamp_str, status))
    conn.commit()
    conn.close()

init_database()

# ==========================================
# 2. OPENCV MODEL TRAINING & SETUP
# ==========================================
CASCADE_FILE = "haarcascade_frontalface_default.xml"
CASCADE_URL = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
KNOWN_FACES_DIR = "known_faces"

if not os.path.exists(CASCADE_FILE):
    print("[INFO] Downloading face detection cascade...")
    urllib.request.urlretrieve(CASCADE_URL, CASCADE_FILE)

face_cascade = cv2.CascadeClassifier(CASCADE_FILE)
recognizer = cv2.face.LBPHFaceRecognizer_create(radius=2, neighbors=8, grid_x=8, grid_y=8)

label_map = {}
reverse_label_map = {}
faces_data = []
labels_data = []

def preprocess_face(roi):
    roi = cv2.resize(roi, (200, 200))
    return cv2.equalizeHist(roi)

def extract_person_name(filename):
    base_name = os.path.splitext(filename)[0]
    cleaned = re.sub(r'[\d_]+$', '', base_name)
    return cleaned if cleaned else base_name

def train_recognizer():
    global label_map, reverse_label_map, faces_data, labels_data, recognizer
    label_map = {}
    current_id = 0
    faces_data = []
    labels_data = []

    if not os.path.exists(KNOWN_FACES_DIR):
        os.makedirs(KNOWN_FACES_DIR)

    image_files = [f for f in os.listdir(KNOWN_FACES_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    print(f"[INFO] Training on {len(image_files)} image(s) in '{KNOWN_FACES_DIR}'...")

    for filename in image_files:
        name = extract_person_name(filename)
        filepath = os.path.join(KNOWN_FACES_DIR, filename)

        if name not in label_map:
            label_map[name] = current_id
            current_id += 1
        label_id = label_map[name]

        img = cv2.imread(filepath, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue

        faces = face_cascade.detectMultiScale(img, scaleFactor=1.1, minNeighbors=4)
        for (x, y, w, h) in faces:
            base_roi = preprocess_face(img[y:y+h, x:x+w])
            faces_data.extend([
                base_roi,
                cv2.flip(base_roi, 1),
                cv2.convertScaleAbs(base_roi, alpha=1.2, beta=20),
                cv2.convertScaleAbs(base_roi, alpha=0.8, beta=-20)
            ])
            labels_data.extend([label_id] * 4)

    if len(faces_data) > 0:
        recognizer.train(faces_data, np.array(labels_data))
        reverse_label_map = {v: k for k, v in label_map.items()}
        print(f"[SUCCESS] Trained with {len(faces_data)} face samples across {len(label_map)} identities.")
    else:
        print("[WARN] No faces found in 'known_faces/' folder to train on.")

train_recognizer()

# ==========================================
# 3. AI VOICE SYNTHESIZER & AUDIO ALARM
# ==========================================
is_muted = False
last_beep_time = 0
last_spoken_time = {}
speech_queue = queue.Queue()

def speech_worker_thread():
    engine = None
    try:
        if pyttsx3:
            engine = pyttsx3.init()
            engine.setProperty('rate', 170)
    except Exception:
        engine = None

    while True:
        text = speech_queue.get()
        if text is None:
            break
        if not is_muted:
            try:
                if engine:
                    engine.say(text)
                    engine.runAndWait()
                else:
                    # Windows PowerShell Speech fallback
                    subprocess.run([
                        "powershell", "-Command",
                        f"Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{text}')"
                    ], capture_output=True)
            except Exception:
                pass
        speech_queue.task_done()

threading.Thread(target=speech_worker_thread, daemon=True).start()

def speak_announcement(text, cooldown_key, cooldown_secs=7.0):
    global last_spoken_time, is_muted
    if is_muted:
        return
    now = time.time()
    if cooldown_key in last_spoken_time and (now - last_spoken_time[cooldown_key] < cooldown_secs):
        return
    last_spoken_time[cooldown_key] = now
    speech_queue.put(text)

def trigger_beep_alarm():
    global last_beep_time, is_muted
    if is_muted:
        return
    now = time.time()
    if now - last_beep_time > 1.2:
        last_beep_time = now
        def sound_worker():
            try:
                if winsound:
                    winsound.MessageBeep(winsound.MB_ICONHAND)
                    winsound.Beep(1600, 250)
            except Exception:
                pass
        threading.Thread(target=sound_worker, daemon=True).start()

# ==========================================
# 4. ADVANCED SCI-FI HUD GRAPHICS ENGINE
# ==========================================

# Color Palette (BGR)
COLOR_CYAN    = (255, 229, 0)     # Electric Cyan (Scanning / Default)
COLOR_EMERALD = (155, 245, 0)     # Neon Emerald (Verified / Authorized)
COLOR_RUBY    = (50, 50, 255)     # Neon Ruby / Crimson (Intruder Threat)
COLOR_AMBER   = (0, 180, 255)     # Cyber Amber (Acquiring / Processing)
COLOR_VIOLET  = (255, 80, 180)    # Neon Violet (Admin / Root Clearance)
COLOR_DARK_BG = (12, 16, 24)      # Translucent Dark Obsidian Glass
COLOR_GRAY    = (140, 155, 175)   # Slate Gray for secondary metrics

def draw_glass_rect(frame, x1, y1, x2, y2, bg_color=COLOR_DARK_BG, alpha=0.80, border_color=None, border_thick=1):
    """Draws a fast, hardware-friendly glassmorphic translucent rectangle with optional border."""
    h, w, _ = frame.shape
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return
    roi = frame[y1:y2, x1:x2]
    rect = np.full_like(roi, bg_color, dtype=np.uint8)
    cv2.addWeighted(rect, alpha, roi, 1.0 - alpha, 0, roi)
    if border_color is not None:
        cv2.rectangle(frame, (x1, y1), (x2, y2), border_color, border_thick)

def draw_biometric_reticle(frame, x, y, w, h, color, anim_angle, match_pct=100.0, is_locked=True, **kwargs):
    """Draws a high-tech multi-ring biometric targeting reticle with animated rotating compass ticks."""
    confidence_pct = kwargs.get('confidence_pct', match_pct)
    cx = x + w // 2
    cy = y + h // 2
    radius = int(max(w, h) * 0.68)
    thick = 2

    # 1. Outer Segmented Rotating Compass Ring
    num_segments = 8
    segment_angle = 360 / num_segments
    for i in range(num_segments):
        start_a = (anim_angle + i * segment_angle) % 360
        end_a = (start_a + 22) % 360
        cv2.ellipse(frame, (cx, cy), (radius, radius), 0, start_a, end_a, color, 2, cv2.LINE_AA)

    # 2. Inner Dashed Guidance Circle
    inner_rad = int(radius * 0.82)
    cv2.circle(frame, (cx, cy), inner_rad, color, 1, cv2.LINE_AA)

    # 3. Dynamic Confidence Progress Arc (Circular meter on the left)
    meter_rad = int(radius * 0.92)
    start_meter = -90
    end_meter = -90 + (confidence_pct / 100.0) * 360
    cv2.ellipse(frame, (cx, cy), (meter_rad, meter_rad), 0, start_meter, end_meter, color, 3, cv2.LINE_AA)

    # 4. Animated Precision Corner Brackets
    bracket_len = max(18, min(36, w // 4))
    b_margin = 12
    bx1, by1 = x - b_margin, y - b_margin
    bx2, by2 = x + w + b_margin, y + h + b_margin

    # Top-Left
    cv2.line(frame, (bx1, by1), (bx1 + bracket_len, by1), color, thick, cv2.LINE_AA)
    cv2.line(frame, (bx1, by1), (bx1, by1 + bracket_len), color, thick, cv2.LINE_AA)
    # Top-Right
    cv2.line(frame, (bx2, by1), (bx2 - bracket_len, by1), color, thick, cv2.LINE_AA)
    cv2.line(frame, (bx2, by1), (bx2, by1 + bracket_len), color, thick, cv2.LINE_AA)
    # Bottom-Left
    cv2.line(frame, (bx1, by2), (bx1 + bracket_len, by2), color, thick, cv2.LINE_AA)
    cv2.line(frame, (bx1, by2), (bx1, by2 - bracket_len), color, thick, cv2.LINE_AA)
    # Bottom-Right
    cv2.line(frame, (bx2, by2), (bx2 - bracket_len, by2), color, thick, cv2.LINE_AA)
    cv2.line(frame, (bx2, by2), (bx2, by2 - bracket_len), color, thick, cv2.LINE_AA)

    # 5. Center Targeting Crosshair
    ch_len = 8
    cv2.line(frame, (cx - ch_len, cy), (cx + ch_len, cy), color, 1, cv2.LINE_AA)
    cv2.line(frame, (cx, cy - ch_len), (cx, cy + ch_len), color, 1, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), 2, color, -1, cv2.LINE_AA)

    # 6. Biometric Coordinate Tag
    coord_txt = f"[{cx},{cy}]"
    cv2.putText(frame, coord_txt, (bx2 - 55, by2 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)

def draw_facial_mesh_triangulation(frame, x, y, w, h, color, pulse_val):
    """Renders simulated biometric triangulation mesh lines and facial feature anchor points."""
    # Key facial landmark relative anchors
    p_left_eye   = (x + int(w * 0.32), y + int(h * 0.38))
    p_right_eye  = (x + int(w * 0.68), y + int(h * 0.38))
    p_nose_top   = (x + int(w * 0.50), y + int(h * 0.46))
    p_nose_tip   = (x + int(w * 0.50), y + int(h * 0.60))
    p_mouth_l    = (x + int(w * 0.35), y + int(h * 0.76))
    p_mouth_r    = (x + int(w * 0.65), y + int(h * 0.76))
    p_mouth_c    = (x + int(w * 0.50), y + int(h * 0.79))
    p_chin       = (x + int(w * 0.50), y + int(h * 0.94))
    p_temple_l   = (x + int(w * 0.16), y + int(h * 0.34))
    p_temple_r   = (x + int(w * 0.84), y + int(h * 0.34))

    landmarks = [p_left_eye, p_right_eye, p_nose_top, p_nose_tip, p_mouth_l, p_mouth_r, p_mouth_c, p_chin, p_temple_l, p_temple_r]

    # Triangulation connecting lines
    connections = [
        (p_temple_l, p_left_eye), (p_left_eye, p_nose_top), (p_nose_top, p_right_eye), (p_right_eye, p_temple_r),
        (p_left_eye, p_nose_tip), (p_right_eye, p_nose_tip),
        (p_nose_top, p_nose_tip),
        (p_nose_tip, p_mouth_l), (p_nose_tip, p_mouth_c), (p_nose_tip, p_mouth_r),
        (p_mouth_l, p_mouth_c), (p_mouth_c, p_mouth_r),
        (p_mouth_l, p_chin), (p_mouth_c, p_chin), (p_mouth_r, p_chin)
    ]

    # Subtle translucent mesh
    for p1, p2 in connections:
        cv2.line(frame, p1, p2, color, 1, cv2.LINE_AA)

    # Glowing node points
    pt_size = int(2 + 1.2 * pulse_val)
    for pt in landmarks:
        cv2.circle(frame, pt, pt_size, color, -1, cv2.LINE_AA)

    # Eye targeting diamonds
    for eye in [p_left_eye, p_right_eye]:
        ex, ey = eye
        diamond = np.array([[ex, ey - 5], [ex + 5, ey], [ex, ey + 5], [ex - 5, ey]], np.int32)
        cv2.polylines(frame, [diamond], True, color, 1, cv2.LINE_AA)

def draw_cyber_identity_card(frame, x, y, name, role, dept, clearance, match_pct, color):
    """Draws a futuristic glassmorphic identity badge floating adjacent to the subject."""
    card_w = 280
    card_h = 82
    h_frame, w_frame, _ = frame.shape

    # Position smart clamp
    card_x = x + w_frame if False else max(15, min(w_frame - card_w - 320, x - (card_w // 4)))
    card_y = y - card_h - 16
    if card_y < 55:
        card_y = y + 25  # Flip below if near the top edge

    # Translucent Glass Background
    draw_glass_rect(frame, card_x, card_y, card_x + card_w, card_y + card_h,
                    bg_color=(10, 14, 22), alpha=0.88, border_color=color, border_thick=1)

    # Left Vertical Neon Accent Stripe
    cv2.rectangle(frame, (card_x, card_y), (card_x + 5, card_y + card_h), color, -1)

    # Clearance Level Pill
    pill_w = 115
    pill_h = 16
    pill_x = card_x + card_w - pill_w - 8
    pill_y = card_y + 8
    draw_glass_rect(frame, pill_x, pill_y, pill_x + pill_w, pill_y + pill_h,
                    bg_color=color, alpha=0.25, border_color=color, border_thick=1)
    cv2.putText(frame, clearance.upper(), (pill_x + 6, pill_y + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.33, color, 1, cv2.LINE_AA)

    # Full Name
    cv2.putText(frame, name.upper(), (card_x + 15, card_y + 24),
                cv2.FONT_HERSHEY_DUPLEX, 0.62, (255, 255, 255), 1, cv2.LINE_AA)

    # Role & Department
    cv2.putText(frame, f"{role}", (card_x + 15, card_y + 44),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, (180, 200, 220), 1, cv2.LINE_AA)
    cv2.putText(frame, f"{dept}", (card_x + 15, card_y + 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, COLOR_GRAY, 1, cv2.LINE_AA)

    # Match Confidence Meter
    conf_str = f"MATCH: {match_pct:.1f}%"
    cv2.putText(frame, conf_str, (card_x + 15, card_y + 74),
                cv2.FONT_HERSHEY_SIMPLEX, 0.33, color, 1, cv2.LINE_AA)

    # Security Token ID
    token_str = f"ID#{abs(hash(name)) % 9000 + 1000}"
    cv2.putText(frame, token_str, (card_x + card_w - 65, card_y + 74),
                cv2.FONT_HERSHEY_SIMPLEX, 0.33, COLOR_GRAY, 1, cv2.LINE_AA)

def draw_sonar_radar_widget(frame, center_x, center_y, radius, sweep_angle, targets_detected, face_positions):
    """Draws an animated sci-fi radar/sonar widget scanning detected entities."""
    # Outer radar background
    draw_glass_rect(frame, center_x - radius - 10, center_y - radius - 10,
                    center_x + radius + 10, center_y + radius + 22,
                    bg_color=(8, 12, 18), alpha=0.85, border_color=(40, 60, 80), border_thick=1)

    # Concentric rings
    cv2.circle(frame, (center_x, center_y), radius, (50, 75, 100), 1, cv2.LINE_AA)
    cv2.circle(frame, (center_x, center_y), int(radius * 0.66), (40, 60, 80), 1, cv2.LINE_AA)
    cv2.circle(frame, (center_x, center_y), int(radius * 0.33), (30, 45, 60), 1, cv2.LINE_AA)

    # Crosshairs
    cv2.line(frame, (center_x - radius, center_y), (center_x + radius, center_y), (40, 60, 80), 1, cv2.LINE_AA)
    cv2.line(frame, (center_x, center_y - radius), (center_x, center_y + radius), (40, 60, 80), 1, cv2.LINE_AA)

    # Sweeper needle
    rad = math.radians(sweep_angle)
    end_x = int(center_x + radius * math.cos(rad))
    end_y = int(center_y + radius * math.sin(rad))
    cv2.line(frame, (center_x, center_y), (end_x, end_y), COLOR_CYAN, 2, cv2.LINE_AA)

    # Plot relative target blips on radar
    frame_h, frame_w, _ = frame.shape
    for (fx, fy, fw, fh, is_auth) in face_positions:
        rel_x = (fx + fw / 2) / frame_w - 0.5
        rel_y = (fy + fh / 2) / frame_h - 0.5
        blip_x = int(center_x + rel_x * (radius * 1.6))
        blip_y = int(center_y + rel_y * (radius * 1.6))
        blip_color = COLOR_EMERALD if is_auth else COLOR_RUBY
        cv2.circle(frame, (blip_x, blip_y), 4, blip_color, -1, cv2.LINE_AA)
        cv2.circle(frame, (blip_x, blip_y), 7, blip_color, 1, cv2.LINE_AA)

    # Label
    cv2.putText(frame, "RADAR SCANNER", (center_x - 38, center_y + radius + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, COLOR_CYAN, 1, cv2.LINE_AA)

def draw_sidebar_access_feed(frame, show_sidebar=True):
    """Draws a live event audit log sidebar showing real-time access history."""
    if not show_sidebar:
        return
    h, w, _ = frame.shape
    sb_w = 300
    sb_x1 = w - sb_w
    sb_y1 = 50
    sb_y2 = h - 45

    # Glass background
    draw_glass_rect(frame, sb_x1, sb_y1, w, sb_y2, bg_color=(8, 12, 18), alpha=0.88, border_color=(35, 55, 75), border_thick=1)

    # Header
    cv2.putText(frame, "// REAL-TIME ACCESS LOGS", (sb_x1 + 14, sb_y1 + 22),
                cv2.FONT_HERSHEY_DUPLEX, 0.42, COLOR_CYAN, 1, cv2.LINE_AA)
    cv2.line(frame, (sb_x1 + 10, sb_y1 + 30), (w - 10, sb_y1 + 30), (45, 65, 90), 1)

    # Logs list
    logs = get_recent_access_logs(limit=6)
    if not logs:
        cv2.putText(frame, "No access events yet", (sb_x1 + 15, sb_y1 + 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, COLOR_GRAY, 1)
        return

    entry_y = sb_y1 + 45
    for person_name, timestamp, status in logs:
        is_auth = (status == "AUTHORIZED")
        stat_color = COLOR_EMERALD if is_auth else COLOR_RUBY
        stat_text = "AUTH" if is_auth else "ALERT"

        # Mini card for log entry
        draw_glass_rect(frame, sb_x1 + 8, entry_y, w - 8, entry_y + 46,
                        bg_color=(14, 20, 30), alpha=0.75, border_color=(30, 45, 65), border_thick=1)

        # Status Tag Pill
        cv2.rectangle(frame, (sb_x1 + 12, entry_y + 6), (sb_x1 + 54, entry_y + 20), stat_color, -1)
        cv2.putText(frame, stat_text, (sb_x1 + 15, entry_y + 17),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.30, (0, 0, 0), 1, cv2.LINE_AA)

        # Person Name
        cv2.putText(frame, person_name[:18], (sb_x1 + 60, entry_y + 17),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)

        # Timestamp
        time_part = timestamp.split(" ")[-1] if " " in timestamp else timestamp
        cv2.putText(frame, time_part, (sb_x1 + 14, entry_y + 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, COLOR_GRAY, 1, cv2.LINE_AA)

        entry_y += 52

def draw_telemetry_hud(frame, fps, face_count, status_text, status_color, is_breach=False):
    """Draws top and bottom futuristic command center telemetry bars."""
    h, w, _ = frame.shape

    # 1. Top HUD Header Bar
    draw_glass_rect(frame, 0, 0, w, 45, bg_color=(8, 12, 18), alpha=0.88, border_color=(30, 45, 65), border_thick=1)

    # Quantum Title
    cv2.putText(frame, "◈ QUANTUM SENTINEL AI // BIO-HUD v4.0", (18, 28),
                cv2.FONT_HERSHEY_DUPLEX, 0.56, COLOR_CYAN, 1, cv2.LINE_AA)

    # Status Center Display
    cv2.putText(frame, status_text, (w // 2 - 140, 28),
                cv2.FONT_HERSHEY_DUPLEX, 0.52, status_color, 1, cv2.LINE_AA)

    # Audio Mute Indicator
    audio_txt = "[AUDIO: MUTED]" if is_muted else "[AUDIO: ACTIVE]"
    audio_col = COLOR_GRAY if is_muted else COLOR_EMERALD
    cv2.putText(frame, audio_txt, (w - 380, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.42, audio_col, 1, cv2.LINE_AA)

    # FPS Metric
    fps_color = COLOR_EMERALD if fps >= 24 else (0, 180, 255)
    cv2.putText(frame, f"FPS: {int(fps)}", (w - 240, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, fps_color, 1, cv2.LINE_AA)

    # Target Count
    cv2.putText(frame, f"TARGETS: [{face_count}]", (w - 140, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, COLOR_CYAN, 1, cv2.LINE_AA)

    # 2. Bottom HUD Footer Bar
    draw_glass_rect(frame, 0, h - 38, w, h, bg_color=(8, 12, 18), alpha=0.88, border_color=(30, 45, 65), border_thick=1)

    # Date & Time
    time_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    cv2.putText(frame, time_str, (18, h - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, (140, 160, 185), 1, cv2.LINE_AA)

    # System Status & Hotkey Helpers
    nav_help = "[S] SIDEBAR  |  [M] MUTE ALARM  |  [R] RETRAIN  |  [Q] QUIT"
    cv2.putText(frame, nav_help, (w // 2 - 200, h - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, COLOR_GRAY, 1, cv2.LINE_AA)

    defense_status = "● DEFENSE BREACHED" if is_breach else "● DEFENSE ONLINE"
    defense_color = COLOR_RUBY if is_breach else COLOR_EMERALD
    cv2.putText(frame, defense_status, (w - 180, h - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, defense_color, 1, cv2.LINE_AA)

# ==========================================
# 5. MAIN LIVE VIDEO PROCESSING LOOP
# ==========================================
def main():
    global is_muted
    print("=" * 65)
    print(" [INFO] Starting Quantum Sentinel Cyberpunk Biometric HUD...")
    print(" [CONTROLS] 's' = Toggle Sidebar  |  'm' = Mute  |  'r' = Retrain  |  'q' = Quit")
    print("=" * 65)

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    scan_y = 55
    scan_direction = 7
    show_sidebar = True
    unknown_streak = 0
    prev_time = time.time()

    retrain_banner_timer = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] Camera feed unavailable.")
            break

        curr_time = time.time()
        fps = 1.0 / (curr_time - prev_time) if (curr_time - prev_time) > 0 else 30
        prev_time = curr_time

        h_frame, w_frame, _ = frame.shape
        anim_angle = (curr_time * 110) % 360
        pulse_val = (math.sin(curr_time * 6) + 1.0) / 2.0  # 0.0 to 1.0
        radar_sweep = (curr_time * 160) % 360

        # 1. Animate sweeping scanline
        scan_y += scan_direction
        if scan_y >= (h_frame - 45) or scan_y <= 55:
            scan_direction *= -1
        # Draw glowing dual scanline
        cv2.line(frame, (0, scan_y), (w_frame, scan_y), COLOR_CYAN, 1, cv2.LINE_AA)
        cv2.line(frame, (0, max(55, scan_y - 2)), (w_frame, max(55, scan_y - 2)), (200, 180, 0), 1)

        # 2. Face Detection & Recognition
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.18, minNeighbors=5, minSize=(85, 85))

        has_confirmed_unknown = False
        status_text = "STATUS: SYSTEM ARMED // SCANNING"
        status_color = COLOR_CYAN
        face_telemetry_positions = []

        for (x, y, w, h) in faces:
            roi_gray = preprocess_face(gray[y:y+h, x:x+w])
            user_id = "Unknown"
            confidence_val = 999.0

            if len(faces_data) > 0:
                label_id, confidence_val = recognizer.predict(roi_gray)
                if confidence_val < 112:
                    user_id = reverse_label_map.get(label_id, "Unknown")

            # Calculate match percentage
            match_pct = max(0.0, min(99.8, 100.0 - (confidence_val / 112.0) * 45.0)) if user_id != "Unknown" else 24.5

            if user_id != "Unknown":
                unknown_streak = 0
                user_info = get_user_from_db(user_id)
                log_access_to_db(user_id, user_info['full_name'], status="AUTHORIZED")

                # Voice Announcement for verified identity
                speak_announcement(f"Access granted. Welcome {user_info['full_name']}.", cooldown_key=user_id, cooldown_secs=9.0)

                # Choose color based on role / clearance
                if "Chief" in user_info['role'] or "Administrator" in user_info['role']:
                    theme_color = COLOR_VIOLET
                else:
                    theme_color = COLOR_EMERALD

                status_text = f"ACCESS GRANTED: {user_info['full_name'].upper()}"
                status_color = theme_color
                face_telemetry_positions.append((x, y, w, h, True))

                # Draw Visuals
                draw_biometric_reticle(frame, x, y, w, h, theme_color, anim_angle, match_pct, is_locked=True)
                draw_facial_mesh_triangulation(frame, x, y, w, h, theme_color, pulse_val)
                draw_cyber_identity_card(frame, x, y, user_info['full_name'], user_info['role'],
                                        user_info['department'], user_info['clearance'], match_pct, theme_color)

            else:
                unknown_streak += 1
                if unknown_streak >= 3:
                    has_confirmed_unknown = True
                    theme_color = COLOR_RUBY
                    status_text = "CRITICAL WARNING: SECURITY BREACH!"
                    status_color = COLOR_RUBY
                    face_telemetry_positions.append((x, y, w, h, False))

                    # Voice Warning for unauthorized intruder
                    speak_announcement("Security breach detected. Unauthorized personnel.", cooldown_key="threat_alarm", cooldown_secs=7.0)

                    draw_biometric_reticle(frame, x, y, w, h, theme_color, anim_angle, match_pct=15.0, is_locked=True)
                    draw_facial_mesh_triangulation(frame, x, y, w, h, theme_color, pulse_val)
                    draw_cyber_identity_card(frame, x, y, "UNAUTHORIZED INTRUDER", "SECURITY THREAT",
                                            "NO CLEARANCE", "UNREGISTERED", 12.0, theme_color)
                else:
                    theme_color = COLOR_AMBER
                    status_text = "ACQUIRING BIOMETRIC SIGNATURE..."
                    status_color = COLOR_AMBER
                    face_telemetry_positions.append((x, y, w, h, False))

                    draw_biometric_reticle(frame, x, y, w, h, theme_color, anim_angle, match_pct=45.0, is_locked=False)

        # 3. Handle Intruder Alarm & Full-Screen Perimeter Warning
        if has_confirmed_unknown:
            trigger_beep_alarm()
            # Flashing red perimeter HUD border
            border_thick = int(6 + 6 * pulse_val)
            cv2.rectangle(frame, (0, 0), (w_frame, h_frame), COLOR_RUBY, border_thick)

            # Centralized Alert Glitch Banner
            banner_w = 460
            banner_h = 42
            bx1 = w_frame // 2 - banner_w // 2
            by1 = 60
            draw_glass_rect(frame, bx1, by1, bx1 + banner_w, by1 + banner_h,
                            bg_color=(35, 10, 20), alpha=0.90, border_color=COLOR_RUBY, border_thick=2)
            cv2.putText(frame, "⚠ UNAUTHORIZED PERSONNEL DETECTED", (bx1 + 18, by1 + 28),
                        cv2.FONT_HERSHEY_DUPLEX, 0.60, COLOR_RUBY, 1, cv2.LINE_AA)

        # 4. Corner Sonar Radar Widget (Bottom-Left)
        draw_sonar_radar_widget(frame, center_x=95, center_y=h_frame - 130, radius=55,
                                sweep_angle=radar_sweep, targets_detected=len(faces),
                                face_positions=face_telemetry_positions)

        # 5. Live Sidebar Access Feed (Right Drawer)
        draw_sidebar_access_feed(frame, show_sidebar=show_sidebar)

        # 6. Retraining Overlay Banner (If triggered by user)
        if time.time() < retrain_banner_timer:
            draw_glass_rect(frame, w_frame // 2 - 200, h_frame // 2 - 30, w_frame // 2 + 200, h_frame // 2 + 30,
                            bg_color=(10, 25, 40), alpha=0.90, border_color=COLOR_CYAN, border_thick=2)
            cv2.putText(frame, "RE-CALIBRATING BIOMETRICS...", (w_frame // 2 - 160, h_frame // 2 + 8),
                        cv2.FONT_HERSHEY_DUPLEX, 0.65, COLOR_CYAN, 1, cv2.LINE_AA)

        # 7. Telemetry Top & Bottom HUD
        draw_telemetry_hud(frame, fps, len(faces), status_text, status_color, is_breach=has_confirmed_unknown)

        # Display Window
        cv2.imshow("QUANTUM SENTINEL - Cyber Security HUD", frame)

        # Key Listeners
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            show_sidebar = not show_sidebar
        elif key == ord('m'):
            is_muted = not is_muted
        elif key == ord('r'):
            train_recognizer()
            retrain_banner_timer = time.time() + 2.0

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()