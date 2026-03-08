"""
Renderer — Moteur de rendu vidéo piloté 100% par config widgets
================================================================
ZERO overlay hardcodé ici. Ce module ne sait PAS à quoi ressemble
un widget. Il reçoit une liste de WidgetConfig et les dessine.

Pipeline :
  VideoSyncResult + WidgetLayout
    ↓
  Pour chaque frame : extrait les valeurs → dessine les widgets → réassemble
    ↓
  Fichier vidéo final avec overlays

Deux modes de rendu :
  1. PILLOW (défaut) : dessine frame par frame via PIL, flexible et customisable
  2. FFMPEG FILTER   : drawtext/overlay pour perfs maximales (widgets simples)
"""

import os
import subprocess
import tempfile
import shutil
from dataclasses import dataclass, field
from typing import Optional
from datetime import timezone

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .sync_engine import VideoSyncResult, FrameData
from .data_extractor import (
    extract_widget_values,
    compute_elevation_gain_series,
    WidgetValue,
)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration des widgets (vient du frontend)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WidgetPosition:
    x: float          # 0.0 → 1.0 (proportion de la largeur vidéo)
    y: float          # 0.0 → 1.0 (proportion de la hauteur vidéo)
    anchor: str = "top-left"   # top-left | center | bottom-right | etc.


@dataclass
class WidgetStyle:
    font_size: int = 48
    font_color: str = "#FFFFFF"
    bg_color: str = "#00000088"    # RGBA hex (8 chars)
    border_radius: int = 12
    padding: int = 16
    show_label: bool = True
    show_unit: bool = True
    use_color_hint: bool = True    # utilise la couleur de zone (HR, slope, etc.)
    opacity: float = 1.0
    font_path: Optional[str] = None   # None → cherche une police système


@dataclass
class WidgetConfig:
    """Configuration d'un widget tel qu'envoyé par le frontend."""
    key: str                    # ex: "speed", "hr", "slope"
    position: WidgetPosition
    style: WidgetStyle = field(default_factory=WidgetStyle)
    visible: bool = True


@dataclass
class RenderConfig:
    """Configuration complète d'un rendu."""
    widgets: list[WidgetConfig]
    output_format: str = "mp4"       # mp4 | mov
    output_quality: str = "high"     # low | medium | high | lossless
    output_fps: Optional[float] = None   # None → conserve le FPS source
    output_resolution: Optional[tuple[int, int]] = None  # None → conserve
    watermark: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Renderer principal
# ─────────────────────────────────────────────────────────────────────────────

class VideoRenderer:
    """
    Rend une vidéo avec overlays GPX.
    Reçoit un VideoSyncResult (sync déjà faite) + RenderConfig.
    """

    QUALITY_PRESETS = {
        "low":      {"crf": "28", "preset": "ultrafast"},
        "medium":   {"crf": "23", "preset": "fast"},
        "high":     {"crf": "18", "preset": "medium"},
        "lossless": {"crf": "0",  "preset": "slow"},
    }

    def __init__(self, render_config: RenderConfig, gpx_start=None):
        self.config = render_config
        self.gpx_start = gpx_start
        self._font_cache: dict[int, any] = {}

    # ── Entrée principale ─────────────────────────────────────────────────

    def render(
        self,
        sync_result: VideoSyncResult,
        output_path: str,
    ) -> str:
        """
        Rend la vidéo avec overlays et sauvegarde dans output_path.
        Retourne le chemin du fichier généré.
        """
        if not sync_result.has_data:
            raise ValueError(
                f"Aucune donnée GPX pour {sync_result.video.filename}. "
                "Impossible de rendre."
            )

        if not PIL_AVAILABLE:
            raise RuntimeError(
                "Pillow n'est pas installé. "
                "Lancez : pip install Pillow"
            )

        # Pré-calcul D+
        compute_elevation_gain_series(sync_result.frame_data)

        # Widgets actifs
        active_widgets = [w for w in self.config.widgets if w.visible]
        widget_keys = [w.key for w in active_widgets]

        video = sync_result.video

        quality = self.QUALITY_PRESETS.get(
            self.config.output_quality,
            self.QUALITY_PRESETS["high"]
        )

        # ── Vraies infos vidéo via ffprobe (rotation comprise) ──────────
        # Les vidéos iPhone verticales sont raw landscape → swap w/h si rotate=90/270
        probe = _probe_video_full(video.local_path)
        fps   = self.config.output_fps or probe["fps"]
        width  = self.config.output_resolution[0] if self.config.output_resolution else probe["width"]
        height = self.config.output_resolution[1] if self.config.output_resolution else probe["height"]
        real_total_frames = probe["frames"] or video.total_frames

        # Dossier temporaire pour les frames overlay
        tmpdir = tempfile.mkdtemp(prefix="gpx_overlay_")

        try:
            # ── Phase 1 : génère les frames overlay (PNG transparents) ──
            overlay_dir = os.path.join(tmpdir, "overlays")
            os.makedirs(overlay_dir)

            # ── Génère 1 overlay par seconde (pas par frame) ───────────
            # Les données GPX changent à ~1Hz, inutile de régénérer 60×/s.
            # On crée un PNG par seconde et FFmpeg l'affiche pendant 1s.
            duration_s = real_total_frames / fps
            n_seconds = math.ceil(duration_s)

            # Index frame → FrameData pour accès O(1)
            frame_map = {fd.frame_index: fd for fd in sync_result.frame_data}

            concat_lines = ["ffconcat version 1.0"]

            for sec in range(n_seconds):
                # Frame la plus proche du milieu de cette seconde
                mid_frame = int((sec + 0.5) * fps)
                fd = frame_map.get(mid_frame)
                # Cherche la frame la plus proche si manquante
                if fd is None:
                    for delta in range(1, int(fps) + 1):
                        fd = frame_map.get(mid_frame + delta) or frame_map.get(mid_frame - delta)
                        if fd:
                            break

                if fd is None:
                    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                else:
                    values = extract_widget_values(fd, widget_keys, self.gpx_start)
                    overlay = self._draw_overlay(width, height, active_widgets, values)

                png_name = f"sec_{sec:05d}.png"
                overlay.save(os.path.join(overlay_dir, png_name), "PNG")

                # Durée réelle de cette seconde (la dernière peut être < 1s)
                sec_dur = min(1.0, duration_s - sec)
                concat_lines.append(f"file '{png_name}'")
                concat_lines.append(f"duration {sec_dur:.6f}")

            concat_path = os.path.join(overlay_dir, "concat.txt")
            with open(concat_path, "w") as f:
                f.write("\n".join(concat_lines) + "\n")

            # ── Phase 2 : composite via ffmpeg ──────────────────────────
            output = self._ffmpeg_composite(
                video_path=video.local_path,
                overlay_dir=overlay_dir,
                concat_path=concat_path,
                output_path=output_path,
                fps=fps,
                width=width,
                height=height,
                quality=quality,
            )

            return output

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Dessin des widgets ────────────────────────────────────────────────

    def _draw_overlay(
        self,
        width: int,
        height: int,
        widget_configs: list[WidgetConfig],
        values: dict[str, WidgetValue],
    ) -> Image.Image:
        """Dessine tous les widgets sur un canvas RGBA transparent."""
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        for wc in widget_configs:
            wv = values.get(wc.key)
            if wv is None:
                continue
            self._draw_single_widget(canvas, draw, wc, wv, width, height)

        return canvas

    def _draw_single_widget(
        self,
        canvas: Image.Image,
        draw: ImageDraw.Draw,
        wc: WidgetConfig,
        wv: WidgetValue,
        vid_w: int,
        vid_h: int,
    ):
        """Dessine un widget individuel avec son style."""
        style = wc.style
        font = self._get_font(style.font_size, style.font_path)
        font_small = self._get_font(max(12, style.font_size // 2), style.font_path)

        # Couleur du texte
        text_color = wv.color_hint if (style.use_color_hint and wv.color_hint) else style.font_color
        text_color_rgb = _hex_to_rgba(text_color)
        label_color = _hex_to_rgba("#AAAAAA")
        unit_color = _hex_to_rgba("#CCCCCC")

        # Construction du texte
        value_text = wv.value
        label_text = wv.label if style.show_label else ""
        unit_text = wv.unit if style.show_unit else ""

        # Calcul dimensions du widget
        pad = style.padding
        bbox_value = draw.textbbox((0, 0), value_text, font=font)
        bbox_label = draw.textbbox((0, 0), label_text, font=font_small) if label_text else (0, 0, 0, 0)
        bbox_unit = draw.textbbox((0, 0), unit_text, font=font_small) if unit_text else (0, 0, 0, 0)

        text_w = max(
            bbox_value[2] - bbox_value[0],
            bbox_label[2] - bbox_label[0],
        )
        text_h = (bbox_value[3] - bbox_value[1]) + \
                 (bbox_label[3] - bbox_label[1] + 4 if label_text else 0)

        box_w = text_w + pad * 2
        box_h = text_h + pad * 2

        # Position (proportions → pixels)
        px = int(wc.position.x * vid_w)
        py = int(wc.position.y * vid_h)

        # Ancre
        px, py = _apply_anchor(px, py, box_w, box_h, wc.position.anchor)

        # Clamp dans les limites vidéo
        px = max(0, min(vid_w - box_w, px))
        py = max(0, min(vid_h - box_h, py))

        # Fond du widget (rectangle arrondi)
        bg_rgba = _hex_to_rgba(style.bg_color)
        bg_opacity = int(bg_rgba[3] * style.opacity)
        bg = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        bg_draw = ImageDraw.Draw(bg)
        r = style.border_radius
        bg_draw.rounded_rectangle(
            [(0, 0), (box_w - 1, box_h - 1)],
            radius=r,
            fill=(*bg_rgba[:3], bg_opacity),
        )
        canvas.alpha_composite(bg, dest=(px, py))

        # Redessiner sur canvas principal
        draw2 = ImageDraw.Draw(canvas)

        current_y = py + pad

        # Label (au dessus)
        if label_text:
            draw2.text(
                (px + pad, current_y),
                label_text,
                font=font_small,
                fill=label_color,
            )
            current_y += bbox_label[3] - bbox_label[1] + 4

        # Valeur + unité sur la même ligne
        draw2.text(
            (px + pad, current_y),
            value_text,
            font=font,
            fill=text_color_rgb,
        )

        if unit_text:
            val_w = bbox_value[2] - bbox_value[0]
            draw2.text(
                (px + pad + val_w + 4, current_y + (bbox_value[3] - bbox_value[1]) // 2),
                unit_text,
                font=font_small,
                fill=unit_color,
                anchor="lm",
            )

    def _get_font(self, size: int, font_path: Optional[str] = None):
        """Charge et met en cache une police."""
        cache_key = (size, font_path)
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        font = None

        # 1. Police fournie explicitement
        if font_path and os.path.exists(font_path):
            try:
                font = ImageFont.truetype(font_path, size)
            except Exception:
                pass

        # 2. Polices système (macOS / Linux)
        if font is None:
            candidates = [
                # macOS
                "/System/Library/Fonts/Helvetica.ttc",
                "/System/Library/Fonts/SFNSDisplay.ttf",
                "/Library/Fonts/Arial Bold.ttf",
                "/Library/Fonts/Arial.ttf",
                # Linux
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            ]
            for path in candidates:
                if os.path.exists(path):
                    try:
                        font = ImageFont.truetype(path, size)
                        break
                    except Exception:
                        continue

        # 3. Fallback PIL
        if font is None:
            font = ImageFont.load_default()

        self._font_cache[cache_key] = font
        return font

    # ── Composite ffmpeg ──────────────────────────────────────────────────

    def _ffmpeg_composite(
        self,
        video_path: str,
        overlay_dir: str,
        concat_path: str,
        output_path: str,
        fps: float,
        width: int,
        height: int,
        quality: dict,
    ) -> str:
        """
        Composite la vidéo originale + les overlays PNG via ffmpeg concat demuxer.
        1 PNG par seconde → 60× moins de fichiers qu'une frame-sequence.
        """
        # Représente le fps en fraction exacte (ex: 59.94 → "60000/1001")
        fps_frac = str(fractions.Fraction(fps).limit_denominator(1001))

        cmd = [
            "ffmpeg", "-y",
            # Input 1 : vidéo originale (autorotate applique la rotation des métadonnées)
            "-i", video_path,
            # Input 2 : overlays via concat demuxer (1 PNG/s, durée variable)
            "-f", "concat", "-safe", "0",
            "-i", concat_path,
            # Filtres : autorotate sur vidéo source, scale, puis overlay
            "-filter_complex",
            f"[0:v]scale={width}:{height}[base];[1:v]scale={width}:{height}[ov];[base][ov]overlay=0:0,fps={fps_frac}[out]",
            "-map", "[out]",
            "-map", "0:a:0?",          # premier stream audio seulement (évite les tracks mebx codec=none)
            # Codec
            "-c:v", "libx264",
            "-crf", quality["crf"],
            "-preset", quality["preset"],
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg error:\n{result.stderr[-2000:]}"
            )

        return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Rendu image statique (stats card)
# ─────────────────────────────────────────────────────────────────────────────

def render_stats_image(
    activity_summary: dict,
    widget_keys: list[str],
    output_path: str,
    background_image_path: Optional[str] = None,
    width: int = 1080,
    height: int = 1080,
) -> str:
    """
    Génère une image statique de stats sportives (style Strava).
    Peut utiliser une photo de fond.

    activity_summary : résultat de gpx_parser.get_activity_summary()
    widget_keys      : métriques à afficher
    background_image_path : photo de fond (optionnel)
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow requis : pip install Pillow")

    if background_image_path and os.path.exists(background_image_path):
        bg = Image.open(background_image_path).convert("RGBA")
        bg = bg.resize((width, height), Image.LANCZOS)
        # Assombrissement pour lisibilité
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 140))
        canvas = Image.alpha_composite(bg, overlay)
    else:
        # Fond dégradé sportif par défaut
        canvas = _create_gradient_background(width, height)

    draw = ImageDraw.Draw(canvas)

    # Police
    try:
        font_large = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 80)
        font_medium = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
        font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
    except Exception:
        font_large = font_medium = font_small = ImageFont.load_default()

    # Titre
    draw.text((width // 2, 80), "ACTIVITÉ", font=font_large, fill="#FFFFFF", anchor="mm")

    # Grille de stats
    metrics = _summary_to_display(activity_summary, widget_keys)
    _draw_stats_grid(draw, metrics, width, height, font_medium, font_small)

    canvas = canvas.convert("RGB")
    canvas.save(output_path, "JPEG", quality=95)
    return output_path


def _create_gradient_background(width: int, height: int) -> Image.Image:
    """Fond dégradé sombre style sport."""
    img = Image.new("RGBA", (width, height))
    for y in range(height):
        t = y / height
        r = int(10 + t * 20)
        g = int(10 + t * 15)
        b = int(30 + t * 40)
        for x in range(width):
            img.putpixel((x, y), (r, g, b, 255))
    return img


def _summary_to_display(summary: dict, widget_keys: list[str]) -> list[dict]:
    """Convertit le résumé GPX en liste de métriques affichables."""
    mapping = {
        "distance":   ("DISTANCE",  f"{summary.get('distance_km', 0):.2f}", "km"),
        "speed":      ("VITESSE MOY", f"{summary.get('avg_speed_kmh', 0):.1f}", "km/h"),
        "pace":       ("ALLURE MOY",  _fmt_pace_s(summary.get('avg_pace_s_per_km', 0)), "/km"),
        "elevation":  ("D+",          f"{int(summary.get('elevation_gain_m', 0))}", "m"),
        "hr":         ("FC MOY",      str(summary.get('avg_hr') or '--'), "bpm"),
        "time_elapsed":("DURÉE",      _fmt_duration(summary.get('duration_s', 0)), ""),
        "slope":      ("PENTE MAX",   "--", "%"),
        "cadence":    ("CADENCE",     "--", "spm"),
        "power":      ("PUISSANCE",   "--", "W"),
    }
    result = []
    for key in widget_keys:
        if key in mapping:
            label, value, unit = mapping[key]
            result.append({"label": label, "value": value, "unit": unit})
    return result


def _draw_stats_grid(draw, metrics, width, height, font_val, font_label):
    """Dessine une grille de métriques centrée."""
    if not metrics:
        return

    cols = min(3, len(metrics))
    rows = math.ceil(len(metrics) / cols)
    cell_w = width // cols
    cell_h = (height - 200) // rows
    start_y = 180

    for i, m in enumerate(metrics):
        col = i % cols
        row = i // cols
        cx = col * cell_w + cell_w // 2
        cy = start_y + row * cell_h + cell_h // 2

        # Valeur
        draw.text((cx, cy - 20), m["value"], font=font_val, fill="#FFFFFF", anchor="mm")
        # Unité
        if m["unit"]:
            draw.text((cx, cy + 30), m["unit"], font=font_label, fill="#AAAAAA", anchor="mm")
        # Label
        draw.text((cx, cy + 65), m["label"], font=font_label, fill="#888888", anchor="mm")

        # Séparateur
        if col < cols - 1:
            draw.line([(col * cell_w + cell_w, start_y + row * cell_h + 20),
                       (col * cell_w + cell_w, start_y + (row + 1) * cell_h - 20)],
                      fill="#444444", width=1)


def _fmt_pace_s(s: float) -> str:
    if not s or s <= 0 or s > 1800:
        return "--"
    return f"{int(s // 60)}'{int(s % 60):02d}\""


def _fmt_duration(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _hex_to_rgba(hex_color: str) -> tuple:
    """Convertit #RRGGBB ou #RRGGBBAA en tuple (r,g,b,a)."""
    h = hex_color.lstrip("#")
    if len(h) == 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (r, g, b, 255)
    elif len(h) == 8:
        r, g, b, a = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
        return (r, g, b, a)
    return (255, 255, 255, 255)


def _apply_anchor(x: int, y: int, w: int, h: int, anchor: str) -> tuple[int, int]:
    """Ajuste la position selon l'ancre."""
    anchor_map = {
        "top-left":     (0, 0),
        "top-center":   (-w // 2, 0),
        "top-right":    (-w, 0),
        "center-left":  (0, -h // 2),
        "center":       (-w // 2, -h // 2),
        "center-right": (-w, -h // 2),
        "bottom-left":  (0, -h),
        "bottom-center":(-w // 2, -h),
        "bottom-right": (-w, -h),
    }
    dx, dy = anchor_map.get(anchor, (0, 0))
    return x + dx, y + dy


import math
import fractions


def _probe_video(video_path: str, fallback_fps: float, fallback_frames: int) -> tuple[float, int]:
    """
    Utilise ffprobe pour obtenir le vrai fps et le vrai nombre de frames.
    Fallback sur les valeurs fournies si ffprobe échoue.
    """
    try:
        import json as _json
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate,nb_frames,duration,width,height,side_data_list:stream_tags=rotate",
                "-of", "json",
                video_path,
            ],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return fallback_fps, fallback_frames

        data = _json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            return fallback_fps, fallback_frames

        s = streams[0]

        # FPS exact (ex: "60000/1001" → 59.94)
        fps_str = s.get("r_frame_rate", "")
        real_fps = fallback_fps
        if "/" in fps_str:
            num, den = fps_str.split("/")
            real_fps = int(num) / int(den)
        elif fps_str:
            real_fps = float(fps_str)

        # Frame count exact
        nb_frames = s.get("nb_frames")
        if nb_frames and int(nb_frames) > 0:
            real_frames = int(nb_frames)
        else:
            # Calcul depuis la durée
            duration = float(s.get("duration", 0))
            real_frames = round(duration * real_fps) if duration > 0 else fallback_frames

        return real_fps, real_frames

    except Exception:
        return fallback_fps, fallback_frames


def _probe_video_full(video_path: str) -> dict:
    """
    Retourne fps, frames, width, height en tenant compte de la rotation.
    Les vidéos iPhone verticales sont stockées en raw landscape (1920×1080)
    avec un tag rotate=90. On swap les dimensions pour avoir les vraies dims d'affichage.
    """
    import json as _json
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate,nb_frames,duration,width,height,side_data_list:stream_side_data=rotation:stream_tags=rotate",
                "-of", "json",
                video_path,
            ],
            capture_output=True, text=True, timeout=15
        )
        data = _json.loads(result.stdout)
        s = data.get("streams", [{}])[0]

        fps_str = s.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            n, d = fps_str.split("/")
            fps = int(n) / int(d)
        else:
            fps = float(fps_str)

        nb = s.get("nb_frames")
        duration = float(s.get("duration") or 0)
        frames = int(nb) if nb and int(nb) > 0 else round(duration * fps)

        w = int(s.get("width") or 1080)
        h = int(s.get("height") or 1920)

        # Rotation — cherche dans tags OU directement dans side_data
        rotate = 0
        tags = s.get("tags", {})
        if "rotate" in tags:
            rotate = int(tags["rotate"])
        else:
            for sd in s.get("side_data_list", []):
                if "rotation" in sd:
                    rotate = int(sd["rotation"])
                    break

        # Si rotation ±90° ou ±270° → swapper largeur/hauteur
        if abs(rotate) in (90, 270):
            w, h = h, w

        return {"fps": fps, "frames": frames, "width": w, "height": h, "rotate": rotate}
    except Exception:
        return {"fps": 30.0, "frames": 0, "width": 1080, "height": 1920, "rotate": 0}
