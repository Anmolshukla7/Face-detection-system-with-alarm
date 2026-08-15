import os
import re
import time
import math
import sqlite3
import urllib.request
from datetime import datetime
import cv2
import numpy as np
import streamlit as st

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

    # Outer rotating segments
    for i in range(8):
        start_a = int((anim_angle + i * 45) % 360)
        end_a = int((start_a + 22) % 360)
        cv2.ellipse(frame, (cx, cy), (radius, radius), 0, start_a, end_a, color, 2, cv2.LINE_AA)

    # Inner circle
    cv2.circle(frame, (cx, cy), int(radius * 0.82), color, 1, cv2.LINE_AA)

    # Confidence arc
    meter_rad = int(radius * 0.92)
    end_meter = int(-90 + (match_pct / 100.0) * 360)
    cv2.ellipse(frame, (cx, cy), (meter_rad, meter_rad), 0, -90, end_meter, color, 3, cv2.LINE_AA)

    # Corner brackets
    bracket_len = max(16, min(32, w // 4))
    bx1, by1 = x - 10, y - 10
    bx2, by2 = x + w + 10, y + h + 10
    cv2.line(frame, (bx1, by1), (bx1 + bracket_len, by1), color, 2, cv2.LINE_AA)
    cv2.line(frame, (bx1, by1), (bx1 + bracket_len, by1), color, 2, cv2.LINE_AA)
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
    st.markdown('<p class="sub-title">BIOMETRIC RECOGNITION & INTRUSION DEFENSE SYSTEM</p>', unsafe_allow_html=True)

with col_status:
    status_label = "● SYSTEM ARMED" if len(label_map) > 0 else "⚠ NEEDS TRAINING"
    status_col = "#00F59B" if len(label_map) > 0 else "#00E5FF"
    st.markdown(f"""
    <div class="metric-card" style="text-align: center;">
        <span style="color: {status_col}; font-weight: bold; font-size: 1.1rem;">{status_label}</span><br>
        <span style="color: #94a3b8; font-size: 0.85rem;">Identities: {len(label_map)}</span>
    </div>
    """, unsafe_allow_html=True)

# Tabs
tab_live, tab_register, tab_db, tab_logs = st.tabs([
    "📹 LIVE BIOMETRIC SCANNER",
    "➕ REGISTER YOUR FACE",
    "👥 PERSONNEL DATABASE",
    "📋 SECURITY AUDIT LOGS"
])

with tab_live:
    if len(label_map) == 0:
        st.info("💡 **Welcome!** No authorized faces are registered yet in this cloud session. Go to the **'➕ REGISTER YOUR FACE'** tab to take or upload your photo and register yourself in seconds!")

    col_controls, col_none = st.columns([2, 1])
    with col_controls:
        match_threshold = st.slider("🎯 Face Match Sensitivity (Threshold)", min_value=75, max_value=150, value=115,
                                    help="Higher value makes matching more lenient, lower value makes matching stricter.")

    st.markdown("### 📷 Camera & Biometric Analyzer")
    camera_input = st.camera_input("Snap a live photo for Biometric Scan & HUD Recognition")

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
    st.markdown("Add your face into the AI model directly from your browser:")

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
        reg_cam = st.camera_input("Look at the camera and snap your reference photo")
        if reg_cam:
            captured_img_bytes = reg_cam.getvalue()
    else:
        uploaded_file = st.file_uploader("Upload reference photo (.jpg, .png)", type=["jpg", "jpeg", "png"])
        if uploaded_file:
            captured_img_bytes = uploaded_file.getvalue()

    if captured_img_bytes and st.button("🚀 Register & Train Identity"):
        user_id = re.sub(r'[^a-zA-Z0-9]', '_', reg_name.lower().strip())
        cv_reg_img = cv2.imdecode(np.frombuffer(captured_img_bytes, np.uint8), cv2.IMREAD_COLOR)

        # Check if face exists in photo
        gray_chk = cv2.cvtColor(cv_reg_img, cv2.COLOR_BGR2GRAY)
        detected_faces = face_cascade.detectMultiScale(gray_chk, scaleFactor=1.1, minNeighbors=4)

        if len(detected_faces) == 0:
            st.error("❌ No face detected in the photo. Please make sure your face is clearly visible and well-lit.")
        else:
            # Save image
            os.makedirs(KNOWN_FACES_DIR, exist_ok=True)
            save_path = os.path.join(KNOWN_FACES_DIR, f"{user_id}.jpg")
            cv2.imwrite(save_path, cv_reg_img)

            # Update DB
            save_user_to_db(user_id, reg_name, reg_role, reg_dept, reg_clearance)

            # Retrain model
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
