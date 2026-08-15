import os
import re
import time
import math
import sqlite3
import urllib.request
from datetime import datetime
import json
import cv2
import numpy as np
import streamlit as st
import streamlit.components.v1 as components

# ==========================================
# STREAMLIT PAGE CONFIG & CYBERPUNK THEME
# ==========================================
st.set_page_config(
    page_title="Quantum Sentinel AI // Bio-HUD",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Cyberpunk Glassmorphic CSS Styling
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@500;600;700&display=swap');

    .stApp {
        background: linear-gradient(135deg, #090d16 0%, #0d1322 50%, #080b12 100%);
        color: #e2e8f0;
        font-family: 'Rajdhani', sans-serif;
    }

    h1, h2, h3 {
        font-family: 'Orbitron', sans-serif !important;
        letter-spacing: 1.5px;
    }

    .main-title {
        font-size: 2.2rem;
        font-weight: 900;
        color: #00E5FF;
        text-shadow: 0 0 15px rgba(0, 229, 255, 0.4);
        margin-bottom: 0px;
    }

    .sub-title {
        font-size: 0.95rem;
        color: #94a3b8;
        letter-spacing: 2px;
        margin-bottom: 1.2rem;
    }

    .metric-card {
        background: rgba(15, 23, 42, 0.75);
        border: 1px solid rgba(0, 229, 255, 0.3);
        border-radius: 10px;
        padding: 15px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
        backdrop-filter: blur(8px);
        margin-bottom: 10px;
    }

    .stButton>button {
        background: linear-gradient(90deg, #00E5FF 0%, #0077B6 100%) !important;
        color: #000 !important;
        font-family: 'Orbitron', sans-serif !important;
        font-weight: 700 !important;
        border: none !important;
        border-radius: 6px !important;
        padding: 0.5rem 1.5rem !important;
        transition: all 0.3s ease !important;
    }

    .stButton>button:hover {
        box-shadow: 0 0 15px rgba(0, 229, 255, 0.7) !important;
        transform: translateY(-2px);
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# DATABASE & MODEL CONFIGURATION
# ==========================================
DB_FILE = "security_database.db"
CASCADE_FILE = "haarcascade_frontalface_default.xml"
CASCADE_URL = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
KNOWN_FACES_DIR = "known_faces"

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
    return {"full_name": user_id.capitalize(), "role": "Authorized Staff", "department": "Operations", "clearance": "LEVEL-3"}

def get_all_registered_users():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, full_name, role, department, clearance_level FROM users")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_registered_faces_json():
    profiles = []
    if os.path.exists(KNOWN_FACES_DIR):
        for filename in os.listdir(KNOWN_FACES_DIR):
            if filename.lower().endswith((".jpg", ".jpeg", ".png")):
                uid = os.path.splitext(filename)[0]
                user_info = get_user_from_db(uid)
                filepath = os.path.join(KNOWN_FACES_DIR, filename)
                try:
                    img = cv2.imread(filepath)
                    if img is not None:
                        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
                        if len(faces) > 0:
                            x, y, w, h = faces[0]
                            base_roi = preprocess_face(gray[y:y+h, x:x+w])
                        else:
                            base_roi = preprocess_face(gray)
                        
                        vectors = []
                        for aug in [
                            base_roi,
                            cv2.flip(base_roi, 1),
                            cv2.convertScaleAbs(base_roi, alpha=1.2, beta=15),
                            cv2.convertScaleAbs(base_roi, alpha=0.8, beta=-15)
                        ]:
                            resized = cv2.resize(aug, (16, 16))
                            norm_res = (resized.flatten() / 255.0).tolist()
                            vectors.append(norm_res)

                        profiles.append({
                            "id": uid,
                            "name": user_info["full_name"],
                            "role": user_info["role"],
                            "clearance": user_info["clearance"],
                            "vectors": vectors
                        })
                except Exception:
                    pass
    return json.dumps(profiles)

def save_user_to_db(user_id, full_name, role, department, clearance_level):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (id, full_name, role, department, clearance_level)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, full_name, role, department, clearance_level))
    conn.commit()
    conn.close()

def log_access_to_db(user_id, person_name, status="AUTHORIZED"):
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO access_logs (user_id, person_name, timestamp, status) VALUES (?, ?, ?, ?)",
                   (user_id, person_name, timestamp_str, status))
    conn.commit()
    conn.close()

def get_recent_access_logs(limit=10):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT log_id, person_name, timestamp, status FROM access_logs ORDER BY log_id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception:
        return []

init_database()

# ==========================================
# OPENCV & HUD GRAPHICS FUNCTIONS
# ==========================================
if not os.path.exists(CASCADE_FILE):
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

def train_models():
    global label_map, reverse_label_map, faces_data, labels_data, recognizer
    label_map = {}
    current_id = 0
    faces_data = []
    labels_data = []

    if not os.path.exists(KNOWN_FACES_DIR):
        os.makedirs(KNOWN_FACES_DIR)

    image_files = [f for f in os.listdir(KNOWN_FACES_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
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
    return len(faces_data), len(label_map)

train_models()

# Colors (BGR)
COLOR_CYAN    = (255, 229, 0)
COLOR_EMERALD = (155, 245, 0)
COLOR_RUBY    = (50, 50, 255)
COLOR_AMBER   = (0, 180, 255)
COLOR_VIOLET  = (255, 80, 180)
COLOR_GRAY    = (140, 155, 175)

def draw_glass_rect(frame, x1, y1, x2, y2, bg_color=(12, 16, 24), alpha=0.80, border_color=None, border_thick=1):
    h, w, _ = frame.shape
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 <= x1 or y2 <= y1:
        return
    roi = frame[y1:y2, x1:x2]
    rect = np.full_like(roi, bg_color, dtype=np.uint8)
    cv2.addWeighted(rect, alpha, roi, 1.0 - alpha, 0, roi)
    if border_color is not None:
        cv2.rectangle(frame, (x1, y1), (x2, y2), border_color, border_thick)

def draw_biometric_reticle(frame, x, y, w, h, color, anim_angle=45, match_pct=100.0, is_locked=True):
    cx = x + w // 2
    cy = y + h // 2
    radius = int(max(w, h) * 0.68)

    for i in range(8):
        start_a = int((anim_angle + i * 45) % 360)
        end_a = int((start_a + 22) % 360)
        cv2.ellipse(frame, (cx, cy), (radius, radius), 0, start_a, end_a, color, 2, cv2.LINE_AA)

    cv2.circle(frame, (cx, cy), int(radius * 0.82), color, 1, cv2.LINE_AA)
    meter_rad = int(radius * 0.92)
    end_meter = int(-90 + (match_pct / 100.0) * 360)
    cv2.ellipse(frame, (cx, cy), (meter_rad, meter_rad), 0, -90, end_meter, color, 3, cv2.LINE_AA)

    bracket_len = max(16, min(32, w // 4))
    bx1, by1 = x - 10, y - 10
    bx2, by2 = x + w + 10, y + h + 10
    cv2.line(frame, (bx1, by1), (bx1 + bracket_len, by1), color, 2, cv2.LINE_AA)
    cv2.line(frame, (bx1, by1), (bx1, by1 + bracket_len), color, 2, cv2.LINE_AA)
    cv2.line(frame, (bx2, by1), (bx2 - bracket_len, by1), color, 2, cv2.LINE_AA)
    cv2.line(frame, (bx2, by1), (bx2, by1 + bracket_len), color, 2, cv2.LINE_AA)
    cv2.line(frame, (bx1, by2), (bx1 + bracket_len, by2), color, 2, cv2.LINE_AA)
    cv2.line(frame, (bx1, by2), (bx1, by2 - bracket_len), color, 2, cv2.LINE_AA)
    cv2.line(frame, (bx2, by2), (bx2 - bracket_len, by2), color, 2, cv2.LINE_AA)
    cv2.line(frame, (bx2, by2), (bx2, by2 - bracket_len), color, 2, cv2.LINE_AA)

def draw_facial_mesh(frame, x, y, w, h, color):
    landmarks = [
        (x + int(w * 0.32), y + int(h * 0.38)),
        (x + int(w * 0.68), y + int(h * 0.38)),
        (x + int(w * 0.50), y + int(h * 0.46)),
        (x + int(w * 0.50), y + int(h * 0.60)),
        (x + int(w * 0.35), y + int(h * 0.76)),
        (x + int(w * 0.65), y + int(h * 0.76)),
        (x + int(w * 0.50), y + int(h * 0.79)),
        (x + int(w * 0.50), y + int(h * 0.94))
    ]
    for i in range(len(landmarks) - 1):
        cv2.line(frame, landmarks[i], landmarks[i+1], color, 1, cv2.LINE_AA)
    for pt in landmarks:
        cv2.circle(frame, pt, 3, color, -1, cv2.LINE_AA)

def draw_glass_badge(frame, x, y, name, role, dept, clearance, match_pct, color):
    card_w, card_h = 260, 75
    h_frame, w_frame, _ = frame.shape
    card_x = max(10, min(w_frame - card_w - 10, x - 20))
    card_y = max(10, y - card_h - 12)
    if card_y < 10:
        card_y = y + 20

    draw_glass_rect(frame, card_x, card_y, card_x + card_w, card_y + card_h,
                    bg_color=(10, 14, 22), alpha=0.88, border_color=color, border_thick=1)
    cv2.rectangle(frame, (card_x, card_y), (card_x + 4, card_y + card_h), color, -1)

    cv2.putText(frame, name.upper(), (card_x + 12, card_y + 22), cv2.FONT_HERSHEY_DUPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, f"{role} | {clearance}", (card_x + 12, card_y + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 200, 220), 1, cv2.LINE_AA)
    cv2.putText(frame, f"{dept}", (card_x + 12, card_y + 55), cv2.FONT_HERSHEY_SIMPLEX, 0.34, COLOR_GRAY, 1, cv2.LINE_AA)
    cv2.putText(frame, f"MATCH: {match_pct:.1f}%", (card_x + 12, card_y + 68), cv2.FONT_HERSHEY_SIMPLEX, 0.32, color, 1, cv2.LINE_AA)

def process_frame(frame, threshold=115):
    h, w, _ = frame.shape
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.18, minNeighbors=5, minSize=(80, 80))

    has_threat = False
    verified_names = []

    for (x, y, w_box, h_box) in faces:
        roi_gray = preprocess_face(gray[y:y+h_box, x:x+w_box])
        user_id = "Unknown"
        confidence_val = 999.0

        if len(faces_data) > 0:
            label_id, confidence_val = recognizer.predict(roi_gray)
            if confidence_val < threshold:
                user_id = reverse_label_map.get(label_id, "Unknown")

        match_pct = max(0.0, min(99.8, 100.0 - (confidence_val / threshold) * 45.0)) if user_id != "Unknown" else 15.0

        if user_id != "Unknown":
            user_info = get_user_from_db(user_id)
            log_access_to_db(user_id, user_info['full_name'], status="AUTHORIZED")
            verified_names.append(user_info['full_name'])
            theme_color = COLOR_VIOLET if "Chief" in user_info['role'] or "Admin" in user_info['role'] else COLOR_EMERALD

            draw_biometric_reticle(frame, x, y, w_box, h_box, theme_color, match_pct=match_pct)
            draw_facial_mesh(frame, x, y, w_box, h_box, theme_color)
            draw_glass_badge(frame, x, y, user_info['full_name'], user_info['role'],
                             user_info['department'], user_info['clearance'], match_pct, theme_color)
        else:
            has_threat = True
            theme_color = COLOR_RUBY
            log_access_to_db("unknown", "Unrecognized Intruder", status="BREACH_ALERT")
            draw_biometric_reticle(frame, x, y, w_box, h_box, theme_color, match_pct=15.0)
            draw_facial_mesh(frame, x, y, w_box, h_box, theme_color)
            draw_glass_badge(frame, x, y, "UNAUTHORIZED PERSON", "SECURITY ALERT",
                             "NO ACCESS", "LEVEL-0", 15.0, theme_color)

    # Top & Bottom Telemetry
    draw_glass_rect(frame, 0, 0, w, 40, bg_color=(8, 12, 18), alpha=0.85, border_color=(30, 45, 65), border_thick=1)
    cv2.putText(frame, "◈ QUANTUM SENTINEL // CLOUD WEB HUD", (15, 26), cv2.FONT_HERSHEY_DUPLEX, 0.55, COLOR_CYAN, 1, cv2.LINE_AA)
    cv2.putText(frame, f"TARGETS: [{len(faces)}]", (w - 140, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_CYAN, 1, cv2.LINE_AA)

    if has_threat:
        cv2.rectangle(frame, (0, 0), (w, h), COLOR_RUBY, 6)

    return frame, len(faces), has_threat, verified_names

# ==========================================
# STREAMLIT UI LAYOUT & TABS
# ==========================================
col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown('<p class="main-title">◈ QUANTUM SENTINEL AI</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-title">AUTONOMOUS BIOMETRIC HUD & INTRUSION DEFENSE SYSTEM</p>', unsafe_allow_html=True)

with col_status:
    status_label = "● SYSTEM ARMED" if len(label_map) > 0 else "⚠ NEEDS ENROLLMENT"
    status_col = "#00F59B" if len(label_map) > 0 else "#00E5FF"
    st.markdown(f"""
    <div class="metric-card" style="text-align: center;">
        <span style="color: {status_col}; font-weight: bold; font-size: 1.1rem;">{status_label}</span><br>
        <span style="color: #94a3b8; font-size: 0.85rem;">Profiles Active: {len(label_map)}</span>
    </div>
    """, unsafe_allow_html=True)

# Tabs
tab_live, tab_register, tab_db, tab_logs = st.tabs([
    "📹 LIVE CONTINUOUS SCANNER",
    "➕ REGISTER YOUR FACE",
    "👥 PERSONNEL DATABASE",
    "📋 SECURITY AUDIT LOGS"
])

with tab_live:
    if len(label_map) == 0:
        st.info("💡 **Tip:** No authorized faces are registered in this session yet. Go to the **'➕ REGISTER YOUR FACE'** tab (Passcode: `QUANTUM-ADMIN-2026`) to add your profile!")

    scanner_mode = st.radio(
        "Select Scanning Mode:",
        ["⚡ Autonomous Live Video Stream (Continuous 30 FPS - No Clicks)", "📷 Single Frame Snapshot Scanner"],
        horizontal=True
    )

    if "Autonomous Live Video" in scanner_mode:
        profiles_json = get_registered_faces_json()

        # Embedded 60 FPS HTML5 WebRTC Autonomous Continuous Biometric Scanner
        components.html(f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{
                    margin: 0;
                    padding: 0;
                    background: transparent;
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    overflow: hidden;
                }}
                .scanner-container {{
                    position: relative;
                    width: 100%;
                    max-width: 800px;
                    margin: 0 auto;
                    border-radius: 12px;
                    overflow: hidden;
                    border: 2px solid #00E5FF;
                    box-shadow: 0 0 25px rgba(0, 229, 255, 0.4);
                    background: #080c14;
                }}
                video {{
                    width: 100%;
                    height: auto;
                    display: block;
                    transform: scaleX(-1);
                }}
                canvas {{
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    pointer-events: none;
                }}
                .hud-overlay {{
                    position: absolute;
                    bottom: 12px;
                    left: 12px;
                    right: 12px;
                    display: flex;
                    justify-content: space-between;
                    background: rgba(8, 12, 20, 0.85);
                    border: 1px solid rgba(0, 229, 255, 0.4);
                    border-radius: 8px;
                    padding: 8px 16px;
                    color: #00E5FF;
                    font-size: 13px;
                    font-weight: 600;
                    backdrop-filter: blur(6px);
                }}
                .status-badge {{
                    color: #00E5FF;
                    font-weight: bold;
                }}
            </style>
        </head>
        <body>
            <div class="scanner-container">
                <video id="webcam" autoplay playsinline muted></video>
                <canvas id="hudCanvas"></canvas>
                <div class="hud-overlay">
                    <div>◈ QUANTUM SENTINEL // AUTONOMOUS AI BIO-HUD</div>
                    <div id="targetStatus" class="status-badge">● SCANNING FOR TARGETS...</div>
                </div>
            </div>

            <script>
                const video = document.getElementById('webcam');
                const canvas = document.getElementById('hudCanvas');
                const ctx = canvas.getContext('2d');
                const statusDiv = document.getElementById('targetStatus');

                const registeredProfiles = {profiles_json};

                let lastSpokenTime = 0;
                let lastBeepTime = 0;
                let scanY = 50;
                let scanDir = 4;
                let animAngle = 0;
                let pulseVal = 0;

                // Guaranteed Web Audio Siren Synthesizer
                function playSirenBeep() {{
                    const now = Date.now();
                    if (now - lastBeepTime < 1200) return;
                    lastBeepTime = now;
                    try {{
                        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                        const osc = audioCtx.createOscillator();
                        const gain = audioCtx.createGain();
                        osc.type = 'sawtooth';
                        osc.frequency.setValueAtTime(800, audioCtx.currentTime);
                        osc.frequency.exponentialRampToValueAtTime(1400, audioCtx.currentTime + 0.15);
                        gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
                        gain.gain.linearRampToValueAtTime(0.01, audioCtx.currentTime + 0.2);
                        osc.connect(gain);
                        gain.connect(audioCtx.destination);
                        osc.start();
                        osc.stop(audioCtx.currentTime + 0.2);
                    }} catch(e) {{}}
                }}

                // Start WebCam Feed
                async function startCamera() {{
                    try {{
                        const stream = await navigator.mediaDevices.getUserMedia({{
                            video: {{ width: {{ ideal: 1280 }}, height: {{ ideal: 720 }} }},
                            audio: false
                        }});
                        video.srcObject = stream;
                        video.onloadedmetadata = () => {{
                            video.play();
                            canvas.width = video.videoWidth || 800;
                            canvas.height = video.videoHeight || 600;
                            requestAnimationFrame(renderContinuousHUD);
                        }};
                    }} catch (err) {{
                        statusDiv.innerText = "⚠ CAMERA ACCESS DENIED // PLEASE ALLOW PERMISSIONS";
                        statusDiv.style.color = "#FF3250";
                    }}
                }}

                function speak(message, cooldownSecs = 8) {{
                    const now = Date.now();
                    if (now - lastSpokenTime < cooldownSecs * 1000) return;
                    lastSpokenTime = now;
                    window.speechSynthesis.cancel();
                    const utterance = new SpeechSynthesisUtterance(message);
                    utterance.rate = 1.0;
                    window.speechSynthesis.speak(utterance);
                }}

                // Face Detection API
                let faceDetector = null;
                if ('FaceDetector' in window) {{
                    try {{
                        faceDetector = new FaceDetector({{ fastMode: true, maxDetectedFaces: 5 }});
                    }} catch (e) {{}}
                }}

                // Offscreen canvas for spatial feature extraction (16x16)
                const offCanvas = document.createElement('canvas');
                offCanvas.width = 16;
                offCanvas.height = 16;
                const offCtx = offCanvas.getContext('2d');

                function matchLiveFace(vx, vy, vw, vh) {{
                    if (!registeredProfiles || registeredProfiles.length === 0) return null;
                    try {{
                        offCtx.drawImage(video, vx, vy, vw, vh, 0, 0, 16, 16);
                        const imgData = offCtx.getImageData(0, 0, 16, 16).data;
                        const grayVals = [];
                        for (let i = 0; i < imgData.length; i += 4) {{
                            const g = (0.299 * imgData[i] + 0.587 * imgData[i+1] + 0.114 * imgData[i+2]) / 255.0;
                            grayVals.push(g);
                        }}

                        // Real-time Histogram Equalization (matching OpenCV cv2.equalizeHist)
                        const hist = new Array(256).fill(0);
                        const len = grayVals.length;
                        for (let i = 0; i < len; i++) {{
                            hist[Math.min(255, Math.max(0, Math.floor(grayVals[i] * 255)))]++;
                        }}
                        const cdf = new Array(256).fill(0);
                        cdf[0] = hist[0];
                        for (let i = 1; i < 256; i++) cdf[i] = cdf[i-1] + hist[i];
                        const cdfMin = cdf.find(v => v > 0) || 1;
                        const liveFeats = new Array(len);
                        for (let i = 0; i < len; i++) {{
                            const val = Math.min(255, Math.max(0, Math.floor(grayVals[i] * 255)));
                            liveFeats[i] = ((cdf[val] - cdfMin) / (len - cdfMin || 1));
                        }}

                        let bestMatch = null;
                        let maxSimilarity = -1;

                        for (const prof of registeredProfiles) {{
                            const vList = prof.vectors || [prof.features];
                            for (const vec of vList) {{
                                if (!vec || vec.length !== liveFeats.length) continue;
                                // Normalized Cosine Similarity (illumination-invariant)
                                let dot = 0, normA = 0, normB = 0;
                                for (let j = 0; j < liveFeats.length; j++) {{
                                    dot += liveFeats[j] * vec[j];
                                    normA += liveFeats[j] * liveFeats[j];
                                    normB += vec[j] * vec[j];
                                }}
                                const sim = (normA > 0 && normB > 0) ? (dot / (Math.sqrt(normA) * Math.sqrt(normB))) : 0;
                                if (sim > maxSimilarity) {{
                                    maxSimilarity = sim;
                                    bestMatch = prof;
                                }}
                            }}
                        }}

                        // Normalized cosine similarity threshold
                        if (bestMatch && maxSimilarity >= 0.76) {{
                            return bestMatch;
                        }}
                    }} catch(e) {{}}
                    return null;
                }}

                async function renderContinuousHUD() {{
                    const w = canvas.width;
                    const h = canvas.height;
                    ctx.clearRect(0, 0, w, h);

                    animAngle = (animAngle + 2) % 360;
                    pulseVal = (Math.sin(Date.now() / 250) + 1) / 2;
                    scanY += scanDir;
                    if (scanY > h - 40 || scanY < 40) scanDir *= -1;

                    // Sweeping Cyan Scanline
                    ctx.strokeStyle = "rgba(0, 229, 255, 0.85)";
                    ctx.lineWidth = 2;
                    ctx.beginPath();
                    ctx.moveTo(0, scanY);
                    ctx.lineTo(w, scanY);
                    ctx.stroke();

                    let faces = [];
                    if (faceDetector) {{
                        try {{
                            faces = await faceDetector.detect(video);
                        }} catch (e) {{}}
                    }}

                    // Face coordinates
                    const hasDetectedFace = (faces.length > 0);
                    const rawX = hasDetectedFace ? faces[0].boundingBox.x : (w * 0.35);
                    const rawY = hasDetectedFace ? faces[0].boundingBox.y : (h * 0.25);
                    const boxW = hasDetectedFace ? faces[0].boundingBox.width : (w * 0.30);
                    const boxH = hasDetectedFace ? faces[0].boundingBox.height : (h * 0.45);

                    const mirroredX = w - rawX - boxW;
                    const cx = mirroredX + boxW / 2;
                    const cy = rawY + boxH / 2;
                    const rad = Math.max(boxW, boxH) * 0.65;

                    // Match against registered database
                    const matchedProfile = matchLiveFace(rawX, rawY, boxW, boxH);
                    const isVerified = (matchedProfile !== null);
                    const themeColor = isVerified ? "#00F59B" : "#FF3250";

                    // 1. Biometric Circular Reticle
                    ctx.strokeStyle = themeColor;
                    ctx.lineWidth = 2;
                    ctx.beginPath();
                    ctx.arc(cx, cy, rad, 0, Math.PI * 2);
                    ctx.stroke();

                    // Rotating Segments
                    for (let i = 0; i < 8; i++) {{
                        const startRad = (animAngle + i * 45) * Math.PI / 180;
                        const endRad = startRad + 0.25;
                        ctx.strokeStyle = themeColor;
                        ctx.lineWidth = 3;
                        ctx.beginPath();
                        ctx.arc(cx, cy, rad + 12, startRad, endRad);
                        ctx.stroke();
                    }}

                    // 2. Precision Corner Brackets
                    ctx.strokeStyle = themeColor;
                    ctx.lineWidth = 3;
                    const bLen = 25;
                    ctx.beginPath();
                    // Top-Left
                    ctx.moveTo(mirroredX - 10, rawY - 10);
                    ctx.lineTo(mirroredX - 10 + bLen, rawY - 10);
                    ctx.moveTo(mirroredX - 10, rawY - 10);
                    ctx.lineTo(mirroredX - 10, rawY - 10 + bLen);
                    // Top-Right
                    ctx.moveTo(mirroredX + boxW + 10, rawY - 10);
                    ctx.lineTo(mirroredX + boxW + 10 - bLen, rawY - 10);
                    ctx.moveTo(mirroredX + boxW + 10, rawY - 10);
                    ctx.lineTo(mirroredX + boxW + 10, rawY - 10 + bLen);
                    // Bottom-Left
                    ctx.moveTo(mirroredX - 10, rawY + boxH + 10);
                    ctx.lineTo(mirroredX - 10 + bLen, rawY + boxH + 10);
                    ctx.moveTo(mirroredX - 10, rawY + boxH + 10);
                    ctx.lineTo(mirroredX - 10, rawY + boxH + 10 - bLen);
                    // Bottom-Right
                    ctx.moveTo(mirroredX + boxW + 10, rawY + boxH + 10);
                    ctx.lineTo(mirroredX + boxW + 10 - bLen, rawY + boxH + 10);
                    ctx.moveTo(mirroredX + boxW + 10, rawY + boxH + 10);
                    ctx.lineTo(mirroredX + boxW + 10, rawY + boxH + 10 - bLen);
                    ctx.stroke();

                    // 3. Center Crosshair
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.moveTo(cx - 8, cy);
                    ctx.lineTo(cx + 8, cy);
                    ctx.moveTo(cx, cy - 8);
                    ctx.lineTo(cx, cy + 8);
                    ctx.stroke();

                    // 4. Identity Badge Overlay
                    const badgeX = Math.max(10, Math.min(w - 270, mirroredX - 10));
                    const badgeY = Math.max(10, rawY - 65);
                    ctx.fillStyle = "rgba(10, 14, 22, 0.90)";
                    ctx.fillRect(badgeX, badgeY, 260, 55);
                    ctx.strokeStyle = themeColor;
                    ctx.lineWidth = 1;
                    ctx.strokeRect(badgeX, badgeY, 260, 55);

                    // Left accent bar
                    ctx.fillStyle = themeColor;
                    ctx.fillRect(badgeX, badgeY, 5, 55);

                    if (isVerified) {{
                        // Authorized Person
                        ctx.fillStyle = "#FFFFFF";
                        ctx.font = "bold 14px sans-serif";
                        ctx.fillText(matchedProfile.name.toUpperCase(), badgeX + 14, badgeY + 22);
                        ctx.fillStyle = "#00F59B";
                        ctx.font = "11px sans-serif";
                        ctx.fillText("AUTHORIZED // " + matchedProfile.clearance, badgeX + 14, badgeY + 40);

                        statusDiv.innerText = "🟢 VERIFIED: " + matchedProfile.name.toUpperCase();
                        statusDiv.style.color = "#00F59B";
                        speak("Welcome " + matchedProfile.name, 8);
                    }} else {{
                        // Unauthorized Person
                        ctx.fillStyle = "#FF3250";
                        ctx.font = "bold 14px sans-serif";
                        ctx.fillText("UNAUTHORIZED PERSON", badgeX + 14, badgeY + 22);
                        ctx.fillStyle = "#FF9999";
                        ctx.font = "11px sans-serif";
                        ctx.fillText("SECURITY ALERT // NO CLEARANCE", badgeX + 14, badgeY + 40);

                        // Flashing Red Alert Perimeter
                        ctx.strokeStyle = `rgba(255, 50, 80, ${{0.4 + 0.6 * pulseVal}})`;
                        ctx.lineWidth = 8;
                        ctx.strokeRect(0, 0, w, h);

                        statusDiv.innerText = "🚨 UNAUTHORIZED PERSON DETECTED!";
                        statusDiv.style.color = "#FF3250";

                        playSirenBeep();
                        speak("Warning! Unauthorized person detected.", 6);
                    }}

                    requestAnimationFrame(renderContinuousHUD);
                }}

                startCamera();
            </script>
        </body>
        </html>
        """, height=560)

    else:
        st.markdown("### 📷 Single Frame Snapshot & Analysis")
        col_controls, col_none = st.columns([2, 1])
        with col_controls:
            match_threshold = st.slider("🎯 Face Match Sensitivity (Threshold)", min_value=75, max_value=150, value=115)

        camera_input = st.camera_input("Take a photo for detailed Biometric Analysis")
        if camera_input is not None:
            bytes_data = camera_input.getvalue()
            cv_img = cv2.imdecode(np.frombuffer(bytes_data, np.uint8), cv2.IMREAD_COLOR)
            processed, count, is_threat, verified_names = process_frame(cv_img, threshold=match_threshold)
            rgb_img = cv2.cvtColor(processed, cv2.COLOR_BGR2RGB)

            col_cam, col_meta = st.columns([2, 1])
            with col_cam:
                st.image(rgb_img, use_container_width=True, caption=f"Quantum Sentinel Telemetry // Targets: {count}")

            with col_meta:
                if count == 0:
                    st.warning("🔍 No face detected in frame. Please look directly at the camera.")
                elif is_threat:
                    st.error("🚨 SECURITY BREACH: Unauthorized Intruder Detected!")
                    st.markdown("""
                    <audio autoplay>
                        <source src="https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3" type="audio/mpeg">
                    </audio>
                    """, unsafe_allow_html=True)
                else:
                    names_str = ", ".join(verified_names)
                    st.success(f"✅ ACCESS GRANTED: Verified identity **{names_str}**!")

                st.metric("Entities Detected", count)
                st.metric("Known Profiles in Memory", len(label_map))

with tab_register:
    st.markdown("### ➕ Register & Train New Authorized Identity")

    try:
        ADMIN_PASSCODE = st.secrets.get("ADMIN_PASSCODE", "QUANTUM-ADMIN-2026")
    except Exception:
        ADMIN_PASSCODE = "QUANTUM-ADMIN-2026"

    if "admin_authenticated" not in st.session_state:
        st.session_state["admin_authenticated"] = False

    if not st.session_state["admin_authenticated"]:
        st.markdown("""
        <div class="metric-card" style="border: 1px solid #FF3250; padding: 25px; text-align: center;">
            <span style="font-size: 1.8rem;">🔒</span><br>
            <span style="color: #FF3250; font-family: 'Orbitron', sans-serif; font-size: 1.2rem; font-weight: bold;">
                RESTRICTED AREA // ADMIN CLEARANCE REQUIRED
            </span><br>
            <p style="color: #94a3b8; font-size: 0.9rem; margin-top: 8px;">
                You must verify Administrator credentials before enrolling new biometric signatures into the central database.
            </p>
        </div>
        """, unsafe_allow_html=True)

        col_pass, col_btn = st.columns([3, 1])
        with col_pass:
            entered_pass = st.text_input("Enter Admin Master Passcode", type="password", placeholder="Enter Master Security Key...")
        with col_btn:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            if st.button("🔓 Authenticate"):
                if entered_pass == ADMIN_PASSCODE:
                    st.session_state["admin_authenticated"] = True
                    log_access_to_db("admin", "Admin Console Unlocked", status="ADMIN_AUTH_SUCCESS")
                    st.success("✅ Admin Clearance Verified!")
                    st.rerun()
                else:
                    log_access_to_db("unauthorized", "Failed Admin Login Attempt", status="FAILED_ADMIN_LOGIN")
                    st.error("❌ Invalid Admin Passcode. Access Denied.")
    else:
        col_auth_msg, col_logout = st.columns([4, 1])
        with col_auth_msg:
            st.markdown("""
            <div style="background: rgba(0, 245, 155, 0.15); border: 1px solid #00F59B; border-radius: 6px; padding: 6px 15px; display: inline-block;">
                <span style="color: #00F59B; font-weight: bold;">🟢 ADMIN CLEARANCE: LEVEL-5 ACTIVE</span>
            </div>
            """, unsafe_allow_html=True)
        with col_logout:
            if st.button("🔒 Lock Console"):
                st.session_state["admin_authenticated"] = False
                st.rerun()

        st.markdown("#### Step 1: Identity Profile Details")
        reg_col1, reg_col2 = st.columns(2)
        with reg_col1:
            reg_name = st.text_input("Full Name", value="Anmol Shukla", placeholder="e.g. Anmol Shukla")
            reg_role = st.text_input("Role / Job Title", value="Chief AI Architect", placeholder="e.g. Lead Engineer")
        with reg_col2:
            reg_dept = st.text_input("Department", value="DeepMind CyberOps", placeholder="e.g. Security Ops")
            reg_clearance = st.selectbox("Clearance Level", ["LEVEL-5 (MAX)", "LEVEL-4", "LEVEL-3", "LEVEL-2", "VIP GUEST"])

        st.markdown("#### Step 2: Provide Reference Face Photo")
        input_method = st.radio("Capture Method:", ["Take Photo with Webcam", "Upload Photo File"], horizontal=True)

        captured_img_bytes = None
        if input_method == "Take Photo with Webcam":
            reg_cam = st.camera_input("Look at the camera and snap reference photo")
            if reg_cam:
                captured_img_bytes = reg_cam.getvalue()
        else:
            uploaded_file = st.file_uploader("Upload reference photo (.jpg, .png)", type=["jpg", "jpeg", "png"])
            if uploaded_file:
                captured_img_bytes = uploaded_file.getvalue()

        if captured_img_bytes and st.button("🚀 Register & Train Identity"):
            user_id = re.sub(r'[^a-zA-Z0-9]', '_', reg_name.lower().strip())
            cv_reg_img = cv2.imdecode(np.frombuffer(captured_img_bytes, np.uint8), cv2.IMREAD_COLOR)

            gray_chk = cv2.cvtColor(cv_reg_img, cv2.COLOR_BGR2GRAY)
            detected_faces = face_cascade.detectMultiScale(gray_chk, scaleFactor=1.1, minNeighbors=4)

            if len(detected_faces) == 0:
                st.error("❌ No face detected in the photo. Please make sure your face is clearly visible and well-lit.")
            else:
                os.makedirs(KNOWN_FACES_DIR, exist_ok=True)
                save_path = os.path.join(KNOWN_FACES_DIR, f"{user_id}.jpg")
                cv2.imwrite(save_path, cv_reg_img)

                save_user_to_db(user_id, reg_name, reg_role, reg_dept, reg_clearance)
                total_samples, total_ids = train_models()
                st.success(f"🎉 **{reg_name}** successfully registered with **{reg_clearance}** clearance! Model trained with {total_samples} samples.")
                st.balloons()

with tab_db:
    st.markdown("### 🛡️ Registered Personnel Database")
    conn = sqlite3.connect(DB_FILE)
    users_df = conn.execute("SELECT id, full_name, role, department, clearance_level FROM users").fetchall()
    conn.close()

    if not users_df:
        st.info("No personnel currently registered in the database.")
    else:
        for uid, name, role, dept, clr in users_df:
            st.markdown(f"""
            <div class="metric-card">
                <span style="color: #00E5FF; font-weight: bold; font-size: 1.1rem;">{name}</span>
                <span style="float: right; color: #00F59B; font-weight: bold;">{clr}</span><br>
                <span style="color: #94a3b8;">Role: {role} | Dept: {dept}</span>
            </div>
            """, unsafe_allow_html=True)

with tab_logs:
    st.markdown("### 📋 Live Security Audit Logs")
    logs = get_recent_access_logs(limit=20)
    if not logs:
        st.info("No audit events recorded yet.")
    else:
        for log_id, name, ts, status in logs:
            badge_color = "#00F59B" if status == "AUTHORIZED" else "#FF3250"
            st.markdown(f"""
            <div class="metric-card" style="padding: 10px 15px; margin-bottom: 6px;">
                <span style="background: {badge_color}; color: black; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold;">{status}</span>
                <span style="margin-left: 12px; font-weight: bold;">{name}</span>
                <span style="float: right; color: #94a3b8; font-size: 0.85rem;">{ts}</span>
            </div>
            """, unsafe_allow_html=True)
