"""
Test render rapide — génère une vidéo avec overlays GPX sur IMG_5979.MOV
Utilise ffmpeg drawtext avec un fichier ASS (subtitles stylés)
pour un rendu en quelques secondes sans PIL frame-par-frame.
"""

import sys, os, subprocess, tempfile
sys.path.insert(0, os.path.dirname(__file__))

from core.gpx_parser import parse_gpx
from core.sync_engine import probe_video, SyncEngine
from core.data_extractor import compute_elevation_gain_series

# ── Config ────────────────────────────────────────────────────────────────────
GPX_FILE    = "/Users/johnsanti/Downloads/Central_Park_in_the_Morning_.gpx"
VIDEO_FILE  = "/Users/johnsanti/Desktop/RUN CENTRAL PARC/IMG_5979.MOV"
OUTPUT_FILE = "/Users/johnsanti/Desktop/RUN CENTRAL PARC/PREVIEW_overlay.mp4"

# Widgets à afficher : (clé, label, position x%, y%, couleur)
WIDGETS = [
    ("speed",    "VITESSE",  0.05, 0.05),
    ("pace",     "ALLURE",   0.05, 0.22),
    ("hr",       "FC",       0.05, 0.39),
    ("slope",    "PENTE",    0.05, 0.56),
    ("elevation","ALTITUDE", 0.05, 0.73),
]

# ── Parse + Sync ──────────────────────────────────────────────────────────────
print("📍 Parse GPX...")
points = parse_gpx(GPX_FILE)

print("🎬 Lecture métadonnées vidéo...")
vm = probe_video(VIDEO_FILE)
print(f"   {vm.filename} — {vm.duration_s:.1f}s à {vm.fps:.0f}fps")
print(f"   Filmé le {vm.creation_time.strftime('%Y-%m-%d')} à {vm.creation_time.strftime('%H:%M:%S')} UTC")

print("⚡ Synchronisation GPX ↔ vidéo...")
engine = SyncEngine(points, global_offset_s=-67.1)
result = engine.sync_video(vm)
compute_elevation_gain_series(result.frame_data)

print(f"   Coverage: {result.coverage_pct:.1f}% | Confidence: {result.video.sync_confidence:.2f}")

# Lissage 3s
from api.routes import _smooth_frame_data
_smooth_frame_data(result.frame_data, window=int(vm.fps * 3))

# ── Génération fichier ASS (subtitles) ───────────────────────────────────────
print("🎨 Génération des overlays...")

def format_pace(s):
    if not s or s <= 0 or s > 1800: return "--"
    return f"{int(s//60)}'{int(s%60):02d}\""

def format_slope(s):
    if s is None: return "--"
    return f"{'+'if s>0 else ''}{s:.1f}%"

def hr_color(hr):
    if not hr: return "&H00FFFFFF&"
    if hr < 130: return "&H00FFD700&"   # bleu clair
    if hr < 150: return "&H0000FF00&"   # vert
    if hr < 165: return "&H0000FFFF&"   # jaune
    if hr < 180: return "&H000080FF&"   # orange
    return "&H000000FF&"                 # rouge

def slope_color(s):
    if not s: return "&H00FFFFFF&"
    if s > 8:  return "&H000000FF&"
    if s > 3:  return "&H000080FF&"
    if s < -5: return "&H00FFD700&"
    return "&H00FFFFFF&"

# Format ASS pour subtitles stylés avec positions
ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Widget,Arial,52,&H00FFFFFF&,&H000000FF&,&H00000000&,&H88000000&,-1,0,0,0,100,100,0,0,1,2,1,7,0,0,0,1
Style: Label,Arial,30,&H00AAAAAA&,&H000000FF&,&H00000000&,&H00000000&,0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def ts(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    cs = int((s - int(s)) * 100)
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"

# Positions en pixels (vidéo 1080x1920 portrait)
widget_positions = [
    # (x_pixel, y_pixel, label_dy)  — ASS \pos(x,y)
    (60,  90,  -45),   # VITESSE
    (60, 270, -45),   # ALLURE
    (60, 450, -45),   # FC
    (60, 630, -45),   # PENTE
    (60, 810, -45),   # ALTITUDE
]

events = []

# 1 événement par seconde (GPS resolution)
prev_sec = -1
for fd in result.frame_data:
    sec = int(fd.video_time_s)
    if sec == prev_sec:
        continue
    prev_sec = sec

    t_start = fd.video_time_s
    t_end   = min(t_start + 1.0, vm.duration_s)

    # Valeurs formatées
    speed_val = f"{fd.speed_kmh:.1f}" if fd.speed_kmh else "--"
    pace_val  = format_pace(fd.pace_s_per_km)
    hr_val    = str(fd.heart_rate) if fd.heart_rate else "--"
    slope_val = format_slope(fd.slope_pct)
    elev_val  = f"{int(fd.elevation_m)}m" if fd.elevation_m else "--"

    data = [
        (speed_val, "km/h",  "&H00FFFFFF&"),
        (pace_val,  "/km",   "&H00FFFFFF&"),
        (hr_val,    "bpm",   hr_color(fd.heart_rate)),
        (slope_val, "",      slope_color(fd.slope_pct)),
        (elev_val,  "",      "&H00CCCCCC&"),
    ]
    labels = ["VITESSE", "ALLURE", "FC", "PENTE", "ALTITUDE"]

    for i, ((val, unit, color), label, (px, py, ldy)) in enumerate(
        zip(data, labels, widget_positions)
    ):
        full_val = f"{val} {unit}".strip()

        # Fond semi-transparent via box
        # Label (petits)
        events.append(
            f"Dialogue: 0,{ts(t_start)},{ts(t_end)},Label,,0,0,0,,{{\\pos({px},{py+ldy})\\c&H00888888&}}{label}"
        )
        # Valeur (grande, colorée)
        events.append(
            f"Dialogue: 0,{ts(t_start)},{ts(t_end)},Widget,,0,0,0,,{{\\pos({px},{py})\\c{color}\\bord3\\shad2}}{full_val}"
        )

ass_content = ass_header + "\n".join(events)

# Sauvegarde du fichier ASS
ass_file = tempfile.mktemp(suffix=".ass")
with open(ass_file, "w", encoding="utf-8") as f:
    f.write(ass_content)

# ── Rendu ffmpeg ──────────────────────────────────────────────────────────────
print("🎬 Rendu ffmpeg en cours...")
print(f"   Sortie : {OUTPUT_FILE}")

cmd = [
    "ffmpeg", "-y",
    "-i", VIDEO_FILE,
    "-vf", f"ass={ass_file}",
    "-c:v", "libx264",
    "-crf", "20",
    "-preset", "fast",
    "-c:a", "aac",
    "-b:a", "128k",
    OUTPUT_FILE
]

result_ffmpeg = subprocess.run(cmd, capture_output=True, text=True)

if result_ffmpeg.returncode != 0:
    print("❌ Erreur ffmpeg:")
    print(result_ffmpeg.stderr[-1000:])
    sys.exit(1)

os.unlink(ass_file)

print(f"✅ Vidéo générée !")
print(f"   → {OUTPUT_FILE}")
print()

# Ouvre directement dans QuickTime
subprocess.Popen(["open", OUTPUT_FILE])
print("▶️  Ouverture dans QuickTime...")
