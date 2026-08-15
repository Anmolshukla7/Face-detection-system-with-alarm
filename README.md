# ◈ Quantum Sentinel AI // Cyberpunk Biometric Face Recognition HUD

A futuristic, cyberpunk-themed real-time biometric face recognition security system powered by OpenCV and Python. Features animated multi-ring targeting reticles, dynamic facial triangulation mesh, real-time access audit logging, and an animated sonar/radar scanner.

---

## ✨ Features

- **🎯 Animated Biometric Target Reticles**: Rotating multi-ring compass with dynamic circular match confidence gauge.
- **🧬 Facial Landmark Triangulation Mesh**: Real-time pulsing geometric facial anchors and triangulation mapping lines.
- **🛡️ Multi-Tier Neon Clearance System**:
  - 🟣 **Admin / Executive**: Neon Violet Clearance
  - 🟢 **Authorized Personnel**: Neon Emerald Clearance
  - 🟡 **Acquiring / Processing**: Cyber Amber & Cyan
  - 🔴 **Security Breach Warning**: Neon Ruby with full-screen perimeter flashing alarm and audio siren.
- **📋 Real-Time Access Audit Sidebar**: Live glassmorphic side drawer reading and streaming recent access attempts from SQLite database.
- **📡 Sonar / Radar Blip Scanner**: Sweeping animated radar widget plotting relative coordinates of detected targets in the viewport.
- **🎛️ Interactive Hotkeys**:
  - `s` : Toggle Real-Time Audit Sidebar
  - `m` : Mute / Unmute Intruder Alarm Siren
  - `r` : Hot-Reload & Retrain on newly added faces on the fly
  - `q` : Safe Exit

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.9+ installed
- A working webcam

### 2. Clone the Repository
```bash
git clone https://github.com/<your-username>/<your-repo-name>.git
cd <your-repo-name>
```

### 3. Set Up Virtual Environment & Install Dependencies
```bash
python -m venv .venv

# On Windows (PowerShell):
.venv\Scripts\activate

# On Linux/macOS:
source .venv/bin/activate

# Install dependencies:
pip install -r requirements.txt
```

### 4. Add Authorized Faces
Place reference photos (`.jpg`, `.jpeg`, `.png`) of authorized individuals into the `known_faces/` folder named after the person (e.g., `anmol.jpg`, `rahul.png`).

### 5. Run the Application
```bash
python face_recognizer.py
```

---

## 🛠️ Tech Stack
- **Python 3**
- **OpenCV (`opencv-contrib-python`)**: Haar Cascades & LBPH Face Recognizer (`cv2.face`)
- **NumPy**: Vectorized image transformations and math
- **SQLite3**: Security personnel database and access audit logs
- **Winsound**: Windows speaker audio siren alerts
