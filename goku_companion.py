#!/usr/bin/env python3
"""
Goku Desktop Companion — نسخة خفيفة لـ Linux Mint (X11)
======================================================
شخصية متحركة بدون خلفية تتجوّل على الشاشة، تتفاعل مع الماوس، وتشغّل أصواتاً عند الأحداث.
تعتمد فقط على مكتبات موجودة أصلاً في Mint: Python 3 + GTK3 + Cairo (بدون pip).

التشغيل:
    python3 goku_companion.py                       تشغيل
    python3 goku_companion.py --install-autostart   يشتغل تلقائياً مع بدء الجهاز
    python3 goku_companion.py --remove-autostart    إلغاء التشغيل التلقائي

التفاعل:
    نقرة ← يفرح (click)  |  نقرتين ← حركة خاصة (special)  |  5 نقرات سريعة ← يعصّب (powerup)
    اسحبه وارمِه ← يطيح بالجاذبية (grab / drop)  |  زر يمين ← القائمة
    تبتعد عن الجهاز ← ينام (sleep)، ولما ترجع يصحى ويرحّب فيك (wake)

ملفات الحركة في assets/sprites — لكل حركة صيغة وحدة من هذي:
    walk.gif  |  walk/ (مجلد صور PNG مرقّمة)  |  walk_strip8.png (شريط 8 إطارات)  |  walk.png
    الحركات: idle walk run fly happy special powerup wave drag fall sleep

ملفات الصوت في assets/sounds/<الحدث>/ (wav / ogg / mp3) — يختار واحد عشوائياً كل مرة:
    startup click grab drop special powerup sleep wake random goodbye
"""
import os

os.environ.setdefault("GDK_BACKEND", "x11")      # Mint = X11 (ولو جربت جلسة Wayland يشتغل عبر XWayland)

import argparse
import ctypes
import fcntl
import json
import math
import random
import re
import shutil
import signal
import subprocess
import sys
import time
import wave
from array import array
from pathlib import Path

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    gi.require_foreign("cairo")
    import cairo
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
except (ImportError, ValueError):
    sys.exit("GTK3 for Python is missing. Run:\n"
             "  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0")

# ══════════════════════════════════════════════════════════════════
#  الإعدادات — عدّل براحتك
# ══════════════════════════════════════════════════════════════════
CONFIG = {
    "name": "Goku",
    "character_height": 150,     # ارتفاع الشخصية بالبكسل (None = الحجم الأصلي للملفات)
    "pixel_art": True,           # True = تكبير حاد يناسب السبرايتات البكسلية
    "sprites_face": "right",     # اتجاه الشخصية في ملفاتك الأصلية: "right" أو "left"
    "frame_ms": 100,             # مدة الإطار (ملّي ثانية) لصور PNG أو GIF بدون توقيت
    "fps": 25,                   # سلاسة الحركة وهو يمشي/يطير (أقل = أخف على الجهاز)

    "walk_speed": 90,            # بكسل/ثانية
    "run_speed": 240,
    "fly_speed": 280,
    "gravity": 2400,
    "floor_offset": 0,           # ارفعه لو الشخصية "طايرة" فوق حافة الشاشة

    "volume": 0.8,               # من 0.0 إلى 1.0
    "placeholder_sounds": True,  # نغمات مؤقتة للأحداث اللي ما لها ملفات صوت
    "random_voice_per_hour": 6,  # كم مرة تقريباً بالساعة يقول جملة من مجلد random
    "sleep_after_minutes": 5,    # ينام إذا ابتعدت عن الجهاز هالمدة

    # كل ما كبر الرقم زادت فرصة الحركة
    "behavior": {"idle": 30, "walk": 32, "run": 6, "fly": 14,
                 "special": 7, "powerup": 4, "wave": 4, "sleep": 3},

    # "dock" : فوق النوافذ، ويختفي ورا الألعاب والفيديو بملء الشاشة (الأفضل)
    # "popup": فوق كل شي دائماً (جرّبه لو مدير النوافذ عندك ما تعامل معه صح)
    "window_type": "dock",

    # إعدادات خاصة لكل حركة (اختياري):
    #   "special": {"frame_ms": 70, "loop": False, "offset": [20, 0]}
    #   offset = إزاحة الصورة بالبكسل الأصلي [يمين, تحت] لضبط مكان القدمين
    "animations": {},
}

# اسم الحركة: (تتكرر؟, بدائلها إذا ملفها ناقص)
ANIMATIONS = {
    "idle":    (True,  []),
    "walk":    (True,  ["idle"]),
    "run":     (True,  ["walk", "idle"]),
    "fly":     (True,  ["idle"]),
    "happy":   (False, ["wave", "idle"]),
    "special": (False, ["powerup", "happy", "idle"]),
    "powerup": (True,  ["special", "happy", "idle"]),
    "wave":    (False, ["happy", "idle"]),
    "drag":    (True,  ["fall", "idle"]),
    "fall":    (True,  ["drag", "idle"]),
    "sleep":   (True,  ["idle"]),
}
SOUND_EVENTS = ("startup", "click", "grab", "drop", "special",
                "powerup", "sleep", "wake", "random", "goodbye")

APP_ID = "goku-companion"
SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
AUDIO_EXTS = {".wav", ".ogg", ".oga", ".opus", ".flac", ".mp3", ".m4a", ".aac"}
FRAME_EXTS = {".png", ".gif", ".webp", ".bmp"}


def log(msg):
    print(f"[{CONFIG['name']}] {msg}", flush=True)


def xdg(var, default):
    return Path(os.environ.get(var) or Path.home() / default)


def natural_key(path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


# ══════════════════════════════════════════════════════════════════
#  الحركات (Sprites)
# ══════════════════════════════════════════════════════════════════
class Animation:
    """إطارات حركة وحدة (متجهة لليمين) + توقيتها + مناطق النقر داخل النافذة."""

    def __init__(self, name, surfaces, delays, loop, offset=(0.0, 0.0)):
        self.name, self.loop, self.surfaces = name, loop, surfaces
        self.delays = [max(20, int(d)) for d in delays]
        self.w, self.h = surfaces[0].get_width(), surfaces[0].get_height()
        self.ax, self.ay = self.w / 2 - offset[0], self.h - offset[1]    # نقطة القدمين في الإطار
        self.pos, self.regions = {}, {}

    def __len__(self):
        return len(self.delays)

    @property
    def duration(self):
        return sum(self.delays) / 1000.0

    def layout(self, foot_x, foot_y):
        """مكان رسم الإطار داخل النافذة + منطقة النقر لكل إطار (يمين ويسار)."""
        self.pos = {1: (round(foot_x - self.ax), round(foot_y - self.ay)),
                    -1: (round(foot_x - (self.w - self.ax)), round(foot_y - self.ay))}
        self.regions = {1: [], -1: []}
        for surf in self.surfaces:
            base = Gdk.cairo_region_create_from_surface(surf)     # البكسلات الظاهرة فقط
            rects = [base.get_rectangle(i) for i in range(base.num_rectangles())]
            for d, rs in ((1, rects),
                          (-1, [cairo.RectangleInt(self.w - r.x - r.width, r.y, r.width, r.height) for r in rects])):
                region = cairo.Region(rs)
                region.translate(*self.pos[d])
                self.regions[d].append(region)


def _read_frames(path):
    """يقرأ GIF متحرك أو صورة ويرجّع (الإطارات, التوقيت)."""
    anim = GdkPixbuf.PixbufAnimation.new_from_file(str(path))
    if anim.is_static_image():
        return [anim.get_static_image()], [None]
    frames, delays = [], []
    tv = GLib.TimeVal()
    it = anim.get_iter(tv)
    while len(frames) < 500:
        frames.append(it.get_pixbuf().copy())
        delay = it.get_delay_time()
        delays.append(delay if delay > 10 else None)
        if delay < 0 or it.on_currently_loading_frame():       # آخر إطار
            break
        tv.tv_usec += delay * 1000
        tv.tv_sec += tv.tv_usec // 1_000_000
        tv.tv_usec %= 1_000_000
        it.advance(tv)
    return frames, delays


def _to_surfaces(pixbufs, scale, flip):
    """تكبير الإطارات ووضعها على لوحة موحّدة (في المنتصف، والقدمين على الأرض)."""
    mw = max(p.get_width() for p in pixbufs)
    mh = max(p.get_height() for p in pixbufs)
    w, h = max(1, round(mw * scale)), max(1, round(mh * scale))
    out = []
    for pb in pixbufs:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        if flip:
            cr.translate(w, 0)
            cr.scale(-1, 1)
        cr.scale(w / mw, h / mh)
        Gdk.cairo_set_source_pixbuf(cr, pb, (mw - pb.get_width()) // 2, mh - pb.get_height())
        if CONFIG["pixel_art"]:
            cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        out.append(surf)
    return out


class SpriteLibrary:
    def __init__(self, folder):
        self.folder = folder

    def load(self):
        index = {p.name.lower(): p for p in self.folder.iterdir()} if self.folder.is_dir() else {}
        raw = {}
        for name in ANIMATIONS:
            try:
                got = self._read(name, index)
            except GLib.Error as e:
                log(f"Could not read sprite '{name}': {e.message}")
                got = None
            if got:
                raw[name] = got

        if raw:
            ref = raw.get("idle") or next(iter(raw.values()))
            target = CONFIG["character_height"]
            scale = target / max(p.get_height() for p in ref[0]) if target else 1.0
            flip = str(CONFIG["sprites_face"]).lower() == "left"
            built = {}
            for name, (pixbufs, delays) in raw.items():
                opts = CONFIG["animations"].get(name, {})
                delays = [opts.get("frame_ms") or d or CONFIG["frame_ms"] for d in delays]
                off = opts.get("offset", (0, 0))
                built[name] = Animation(name, _to_surfaces(pixbufs, scale, flip), delays,
                                        opts.get("loop", ANIMATIONS[name][0]), (off[0] * scale, off[1] * scale))
            log("Sprites: " + " ".join(f"{n}({len(a)})" for n, a in built.items()))
        else:
            log("No sprites in assets/sprites yet -> using the built-in placeholder character.")
            built = placeholder_animations(int(CONFIG["character_height"] or 150))

        final, notes = {}, []
        first = built.get("idle") or next(iter(built.values()))
        for name, (_, fallbacks) in ANIMATIONS.items():
            alt = name if name in built else next((f for f in fallbacks if f in built), first.name)
            final[name] = built[alt]
            if alt != name:
                notes.append(f"{name}->{alt}")
        if notes:
            log("Missing, using instead: " + ", ".join(notes))
        return final

    def _read(self, name, index):
        for ext in (".gif", ".webp", ".png"):                       # 1) ملف وحد
            path = index.get(name + ext)
            if path and path.is_file():
                return _read_frames(path)
        folder = index.get(name)                                    # 2) مجلد صور مرقّمة
        if folder and folder.is_dir():
            files = sorted((f for f in folder.iterdir() if f.suffix.lower() in FRAME_EXTS), key=natural_key)
            if files:
                return [GdkPixbuf.Pixbuf.new_from_file(str(f)) for f in files], [None] * len(files)
        for key, path in index.items():                             # 3) شريط: name_strip8.png
            m = re.fullmatch(re.escape(name) + r"_strip(\d+)\.png", key)
            if m and path.is_file():
                sheet, n = GdkPixbuf.Pixbuf.new_from_file(str(path)), max(1, int(m.group(1)))
                fw = sheet.get_width() // n
                return [sheet.new_subpixbuf(i * fw, 0, fw, sheet.get_height()).copy() for i in range(n)], [None] * n
        return None


# ══════════════════════════════════════════════════════════════════
#  شخصية مؤقتة تُرسم بالكود — تظهر فقط إذا ما فيه ملفات حركة
# ══════════════════════════════════════════════════════════════════
def _hex(h, a=1.0):
    return int(h[1:3], 16) / 255, int(h[3:5], 16) / 255, int(h[5:7], 16) / 255, a


INK = _hex("#1E3A46")


def _ellipse(cr, x, y, rx, ry):
    cr.save()
    cr.translate(x, y)
    cr.scale(rx, ry)
    cr.new_sub_path()
    cr.arc(0, 0, 1, 0, math.tau)
    cr.restore()


def _rrect(cr, x, y, w, h, rx, ry):
    cr.new_sub_path()
    for cx, cy, a in ((x + w - rx, y + ry, -math.pi / 2), (x + w - rx, y + h - ry, 0),
                      (x + rx, y + h - ry, math.pi / 2), (x + rx, y + ry, math.pi)):
        cr.save()
        cr.translate(cx, cy)
        cr.scale(rx, ry)
        cr.arc(0, 0, 1, a, a + math.pi / 2)
        cr.restore()
    cr.close_path()


def _quad(cr, cx, cy, x, y):
    x0, y0 = cr.get_current_point()
    cr.curve_to(x0 + (cx - x0) * 2 / 3, y0 + (cy - y0) * 2 / 3, x + (cx - x) * 2 / 3, y + (cy - y) * 2 / 3, x, y)


def _fill_stroke(cr, fill, width=2.0):
    if isinstance(fill, cairo.Pattern):
        cr.set_source(fill)
    else:
        cr.set_source_rgba(*fill)
    cr.fill_preserve()
    cr.set_source_rgba(*INK)
    cr.set_line_width(width)
    cr.stroke()


def _line(cr, x1, y1, x2, y2, color=INK, width=2.0):
    cr.move_to(x1, y1)
    cr.line_to(x2, y2)
    cr.set_source_rgba(*color)
    cr.set_line_width(width)
    cr.stroke()


def _buddy(size, *, bob=0.0, squash=1.0, lean=0.0, eyes="open", mouth="smile", feet=((0, 0), (0, 0)),
           arms=(12, 12), leaf=0.0, aura=0.0, orb=0.0, ring=0.0, sparkles=(), zzz=(), lines=(), jitter=(0, 0)):
    """إطار وحد لشخصية بسيطة: كرة نعناعية بعيون كبيرة وورقة فوق راسها."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    cr.scale(size / 100.0, size / 100.0)                 # نرسم على شبكة 100×100
    cr.translate(*jitter)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    bw, bh = 60.0 / math.sqrt(squash), 52.0 * squash
    bottom = 92.0 - bob
    cy = bottom - bh / 2

    if aura > 0:                                         # هالة طاقة
        r = 24 + 24 * aura
        g = cairo.RadialGradient(50, cy, 0, 50, cy, r)
        g.add_color_stop_rgba(0, 0.69, 0.57, 1, 0.63 * aura)
        g.add_color_stop_rgba(0.65, 0.47, 0.8, 1, 0.35 * aura)
        g.add_color_stop_rgba(1, 0.47, 0.8, 1, 0)
        cr.set_source(g)
        cr.arc(50, cy, r, 0, math.tau)
        cr.fill()
    for x1, y1, x2, y2, a in lines:                      # خطوط السرعة
        _line(cr, x1, y1, x2, y2, (0.59, 0.67, 0.8, a), 2.2)
    if feet is not None:
        for side, (dx, dy) in zip((-1, 1), feet):
            _ellipse(cr, 50 + side * 13 + dx, bottom + 2 + dy, 8.5, 5)
            _fill_stroke(cr, _hex("#2C8C74"))

    cr.save()                                            # الجسم والوجه واليدين
    cr.translate(50, bottom)
    cr.rotate(math.radians(lean))
    _rrect(cr, -bw / 2, -bh, bw, bh, bw * 0.45, bh * 0.5)
    grad = cairo.LinearGradient(0, -bh, 0, 0)
    grad.add_color_stop_rgba(0, *_hex("#86EBCD"))
    grad.add_color_stop_rgba(1, *_hex("#3DB894"))
    _fill_stroke(cr, grad, 2.4)
    _ellipse(cr, -bw * 0.22, -bh * 0.8, bw * 0.12, bh * 0.07)
    cr.set_source_rgba(1, 1, 1, 0.37)
    cr.fill()
    cr.save()                                            # الورقة
    cr.translate(0, -bh + 1)
    cr.rotate(math.radians(leaf))
    _line(cr, 0, 0, 0, -6)
    cr.move_to(0, -6)
    cr.curve_to(4, -14, 14, -13, 15, -9)
    cr.curve_to(11, -4, 4, -3, 0, -6)
    cr.close_path()
    _fill_stroke(cr, _hex("#9BE375"))
    cr.restore()
    _face(cr, bw, bh, eyes, mouth)
    if arms is not None:
        for side, ang in zip((-1, 1), arms):
            cr.save()
            cr.translate(side * (bw / 2 - 3), -bh * 0.42)
            cr.rotate(math.radians(ang if side < 0 else -ang))
            _rrect(cr, -4, -1, 8, 14, 4, 4)
            _fill_stroke(cr, _hex("#4CCAA6"))
            cr.restore()
    cr.restore()

    if orb > 0:                                          # كرة طاقة
        ox, oy, r = 50 + bw / 2 + 9, bottom - bh * 0.45, (2 + 7 * orb) * 1.8
        g = cairo.RadialGradient(ox, oy, 0, ox, oy, r)
        g.add_color_stop_rgba(0, 1, 1, 1, 1)
        g.add_color_stop_rgba(0.45, 0.59, 0.88, 1, 0.9)
        g.add_color_stop_rgba(1, 0.59, 0.88, 1, 0)
        cr.set_source(g)
        cr.arc(ox, oy, r, 0, math.tau)
        cr.fill()
    if ring > 0:                                         # موجة
        _ellipse(cr, 50, cy, 26 + 22 * ring, 26 + 20 * ring)
        cr.set_source_rgba(0.73, 0.61, 1, 1 - ring)
        cr.set_line_width(0.6 + 3.5 * (1 - ring))
        cr.stroke()
    for x, y, s, a in sparkles:                          # نجوم
        cr.move_to(x, y - s)
        for px, py in ((x + s * .28, y - s * .28), (x + s, y), (x + s * .28, y + s * .28), (x, y + s),
                       (x - s * .28, y + s * .28), (x - s, y), (x - s * .28, y - s * .28)):
            cr.line_to(px, py)
        cr.close_path()
        cr.set_source_rgba(1, 0.84, 0.29, max(0.0, min(1.0, a)))
        cr.fill()
    if zzz:                                              # Zzz
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        for x, y, s, a in zzz:
            cr.set_font_size(s)
            cr.move_to(x, y)
            cr.set_source_rgba(0.35, 0.44, 0.63, max(0.0, min(1.0, a)))
            cr.show_text("z")
    surf.flush()
    return surf


def _face(cr, bw, bh, eyes, mouth):
    ey = -bh * 0.58
    for ex in (-bw * 0.16 + 3, bw * 0.16 + 3):          # العيون تنظر لليمين شوي
        if eyes in ("open", "focus", "wide"):
            big = eyes == "wide"
            _ellipse(cr, ex, ey, 5.6 if big else 4.6, 7.0 if big else 6.0)
            _fill_stroke(cr, (1, 1, 1, 1), 1.6)
            pr = 2.2 if big else 3.0
            _ellipse(cr, ex + (0.5 if big else 1.4), ey + 0.8, pr, pr * 1.25)
            cr.set_source_rgba(*INK)
            cr.fill()
            _ellipse(cr, ex + 2.0, ey - 1.6, 1.1, 1.1)
            cr.set_source_rgba(1, 1, 1, 1)
            cr.fill()
            if eyes == "focus":                          # حواجب جادّة
                inner = 1 if ex < 3 else -1
                _line(cr, ex - 5 * inner, ey - 10, ex + 4 * inner, ey - 7)
        else:
            cr.move_to(ex - 4.5, ey + (1.5 if eyes == "happy" else -0.5))
            if eyes == "happy":
                _quad(cr, ex, ey - 4.5, ex + 4.5, ey + 1.5)
            elif eyes == "closed":
                _quad(cr, ex, ey + 3.5, ex + 4.5, ey - 0.5)
            else:
                cr.line_to(ex + 4.5, ey - 0.5)
            cr.set_source_rgba(*INK)
            cr.set_line_width(2.0)
            cr.stroke()
    for bx in (-bw * 0.30 + 3, bw * 0.30 + 3):          # خدود
        _ellipse(cr, bx, ey + 9, 4.4, 2.5)
        cr.set_source_rgba(1, 0.51, 0.59, 0.41)
        cr.fill()
    mx, my = 3.0, -bh * 0.31
    if mouth == "grin":
        cr.move_to(mx - 5, my - 1.5)
        _quad(cr, mx, my + 6.5, mx + 5, my - 1.5)
        cr.close_path()
        _fill_stroke(cr, _hex("#7A2E3A"), 1.8)
    elif mouth == "open":
        _ellipse(cr, mx, my + 1, 2.6, 3.2)
        _fill_stroke(cr, _hex("#7A2E3A"), 1.8)
    elif mouth == "flat":
        _line(cr, mx - 3, my + 0.5, mx + 3, my + 0.5, INK, 1.8)
    else:
        cr.move_to(mx - 4, my - 1)
        _quad(cr, mx, my + 3.5, mx + 4, my - 1)
        cr.set_source_rgba(*INK)
        cr.set_line_width(1.8)
        cr.stroke()


def placeholder_animations(size):
    tau = math.tau

    def walk(i, run=False):
        n = 6 if run else 8
        s, c = math.sin(tau * i / n), math.cos(tau * i / n)
        amp, lift = (9, 5) if run else (6, 3)
        lines = [(3, 50 + k * 11, 16 - 4 * ((i + k) % 2), 50 + k * 11, 0.7) for k in range(3)] if run else ()
        return _buddy(size, bob=(2.5 if run else 1.2) * abs(s), lean=9 if run else 4,
                      feet=((amp * s, -lift * max(0.0, c)), (-amp * s, -lift * max(0.0, -c))),
                      arms=(20 + 18 * s, 20 - 18 * s), leaf=(-18 if run else -6) - 4 * s, lines=lines)

    def special(i):
        if i < 8:                                        # شحن
            t = i / 7
            return _buddy(size, squash=1 - 0.12 * t, eyes="focus", mouth="flat", arms=(70, 70),
                          aura=0.3 + 0.7 * t, orb=t, jitter=((i % 2) * 0.8 * t, 0), leaf=-6 * t)
        if i < 12:                                       # انطلاق
            t = (i - 8) / 3
            stars = [(50 + 36 * math.cos(a), 58 + 30 * math.sin(a), 4 + 3 * (1 - t), 1 - 0.6 * t)
                     for a in (k * tau / 8 + 0.3 for k in range(8))]
            return _buddy(size, squash=1.12 - 0.08 * t, eyes="wide", mouth="open", arms=(165, 165),
                          aura=1 - 0.6 * t, ring=t, sparkles=stars, leaf=10)
        t = (i - 12) / 3                                 # رجوع
        return _buddy(size, squash=1 + 0.04 * (1 - t), eyes="happy", mouth="grin",
                      arms=(40 - 25 * t, 40 - 25 * t), aura=0.4 * (1 - t))

    def happy(i):
        h = math.sin(math.pi * i / 9)
        stars = [(20, 30, 4, h), (82, 24, 3.4, h), (88, 50, 2.6, h)] if 2 <= i <= 7 else ()
        return _buddy(size, bob=20 * h, squash=0.86 if i in (0, 9) else 1 + 0.08 * h, eyes="happy",
                      mouth="grin", arms=(60 + 100 * h, 60 + 100 * h), sparkles=stars, leaf=12 * h)

    def sleep(i):
        zs = [(64 + 16 * ph, 38 - 28 * ph, 8 + 8 * ph, math.sin(math.pi * ph))
              for ph in ((i / 12 + k / 3) % 1.0 for k in range(3))]
        return _buddy(size, squash=1 + 0.03 * math.sin(tau * i / 12), eyes="closed", mouth="flat",
                      arms=(5, 5), zzz=zs, leaf=-32 + 4 * math.sin(tau * i / 12))

    frames = {
        "idle": (12, 110, lambda i: _buddy(size, squash=1 + 0.025 * math.sin(tau * i / 12),
                                           eyes="blink" if i == 8 else "open", leaf=8 * math.sin(tau * i / 12))),
        "walk": (8, 95, walk),
        "run": (6, 65, lambda i: walk(i, run=True)),
        "fly": (6, 100, lambda i: _buddy(size, bob=3 + 1.5 * math.sin(tau * i / 6), lean=16,
                                         feet=((-4, -2), (-2, -1)), arms=(150, 120), leaf=-28,
                                         lines=[(2 + 3 * ((i + k) % 3), 48 + k * 10, 13 + 3 * ((i + k) % 3),
                                                 48 + k * 10, 0.6) for k in range(3)])),
        "happy": (10, 65, happy),
        "special": (16, 70, special),
        "powerup": (6, 70, lambda i: _buddy(size, squash=0.96 + 0.02 * math.sin(tau * i / 6), eyes="focus",
                                            mouth="grin", arms=(30, 30), aura=0.8 + 0.2 * math.sin(tau * i / 6),
                                            jitter=(0.9 if i % 2 else -0.9, 0),
                                            sparkles=[(22 + (k * 37 + i * 11) % 58, 92 - (k * 23 + i * 9) % 66,
                                                       2.4, 0.85) for k in range(5)])),
        "wave": (12, 85, lambda i: _buddy(size, eyes="happy" if i % 6 < 3 else "open",
                                          mouth="grin" if i % 6 < 3 else "smile",
                                          arms=(15, 150 + 22 * math.sin(tau * i / 6)))),
        "drag": (4, 140, lambda i: _buddy(size, squash=1.12, eyes="wide", mouth="open", arms=(168, 168),
                                          feet=((3 * math.sin(tau * i / 4), 4),) * 2)),
        "fall": (4, 90, lambda i: _buddy(size, eyes="wide", mouth="open", feet=((0, 3), (0, 3)), leaf=20,
                                         arms=(150 + 20 * math.sin(tau * i / 4), 150 - 20 * math.sin(tau * i / 4)),
                                         lines=[(36 + 14 * k, 6 + 3 * ((i + k) % 2), 36 + 14 * k,
                                                 16 + 3 * ((i + k) % 2), 0.6) for k in range(3)])),
        "sleep": (12, 170, sleep),
    }
    return {name: Animation(name, [draw(i) for i in range(n)], [ms] * n, ANIMATIONS[name][0])
            for name, (n, ms, draw) in frames.items()}


# ══════════════════════════════════════════════════════════════════
#  الأصوات — مشغّلات النظام (paplay / pw-play / aplay ...)
# ══════════════════════════════════════════════════════════════════
PLACEHOLDER_TUNES = {   # (التردد, المدة) — نغمات بسيطة لين تضيف أصواتك
    "startup": [(523, .11), (659, .11), (784, .11), (1047, .30)],
    "click":   [(880, .06), (1319, .10)],
    "grab":    [(660, .07), (520, .10)],
    "drop":    [(196, .09), (147, .16)],
    "special": [(392, .07), (523, .07), (659, .07), (784, .07), (1047, .07), (1568, .30)],
    "powerup": [(262, .06), (330, .06), (392, .06), (523, .06), (659, .06), (784, .06), (1047, .22)],
    "sleep":   [(784, .18), (659, .18), (523, .32)],
    "wake":    [(523, .09), (784, .16)],
    "goodbye": [(1047, .12), (784, .12), (659, .12), (523, .34)],
}


def placeholder_sound(event):
    tune = PLACEHOLDER_TUNES.get(event)
    if not tune:
        return None
    path = xdg("XDG_CACHE_HOME", ".cache") / APP_ID / f"{event}.wav"
    if path.is_file():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rate, data = 22050, array("h")
        for freq, dur in tune:
            n = int(rate * dur)
            for k in range(n):
                t = k / rate
                env = min(1.0, k / 88.0) * min(1.0, (n - k) / 220.0) * math.exp(-5.0 * t / dur)
                data.append(int(9000 * env * (math.sin(math.tau * freq * t) + 0.25 * math.sin(math.tau * 2 * freq * t))))
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(data.tobytes())
        return path
    except OSError:
        return None


class SoundBoard:
    PLAYERS = {
        "paplay":  lambda f, v: ["paplay", f"--volume={int(v * 65536)}", f],
        "pw-play": lambda f, v: ["pw-play", f"--volume={v:.2f}", f],
        "ffplay":  lambda f, v: ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(int(v * 100)), f],
        "mpv":     lambda f, v: ["mpv", "--no-video", "--really-quiet", f"--volume={int(v * 100)}", f],
        "cvlc":    lambda f, v: ["cvlc", "--play-and-exit", "--quiet", f],
        "aplay":   lambda f, v: ["aplay", "-q", f],
    }

    def __init__(self, folder, volume, muted):
        self.folder, self.volume, self.muted = folder, volume, muted
        self.files = {ev: self._scan(ev) for ev in SOUND_EVENTS}
        self.fallback = {}
        if CONFIG["placeholder_sounds"]:
            for ev in SOUND_EVENTS:
                path = None if self.files[ev] else placeholder_sound(ev)
                if path:
                    self.fallback[ev] = path
        self.available = [p for p in self.PLAYERS if shutil.which(p)]
        self.proc, self._last = None, {}
        have = [f"{ev}:{len(f)}" for ev, f in self.files.items() if f]
        log("Sounds: " + (" ".join(have) if have else "none yet") + ("  (+ placeholder chimes)" if self.fallback else ""))
        log("Audio player: " + (", ".join(self.available) or "none found (sudo apt install pulseaudio-utils)"))

    def _scan(self, event):
        files = []
        if (self.folder / event).is_dir():
            files += [f for f in (self.folder / event).iterdir() if f.suffix.lower() in AUDIO_EXTS]
        files += [f for f in self.folder.glob(event + ".*") if f.suffix.lower() in AUDIO_EXTS]
        return sorted(files)

    def has(self, event):
        return bool(self.files.get(event))

    def play(self, event):
        if self.muted:
            return False
        choices = self.files.get(event) or ([self.fallback[event]] if event in self.fallback else [])
        if not choices:
            return False
        if len(choices) > 1:                          # ما يكرر نفس الملف مرتين ورا بعض
            choices = [c for c in choices if c != self._last.get(event)]
        path = self._last[event] = random.choice(choices)
        self.stop()
        ext = path.suffix.lower()
        if ext == ".wav":
            order = ("paplay", "pw-play", "aplay", "ffplay", "mpv", "cvlc")
        elif ext in (".ogg", ".oga", ".flac", ".opus"):
            order = ("paplay", "pw-play", "ffplay", "mpv", "cvlc")
        else:
            order = ("ffplay", "mpv", "cvlc", "paplay", "pw-play")
        return self._start(path, [p for p in order if p in self.available])

    def _start(self, path, players):
        players = list(players)
        while players:
            name = players.pop(0)
            try:
                self.proc = subprocess.Popen(self.PLAYERS[name](str(path), self.volume), stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                continue
            if players:     # إذا المشغّل رفض الصيغة (يطلع بخطأ بسرعة) نجرّب اللي بعده
                GLib.timeout_add(400, self._retry_if_failed, self.proc, path, players)
            return True
        return False

    def _retry_if_failed(self, proc, path, players):
        if proc is self.proc and proc.poll() not in (None, 0):
            self._start(path, players)
        return False

    def is_playing(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.is_playing():
            self.proc.terminate()
        self.proc = None


# ══════════════════════════════════════════════════════════════════
#  كم صار لك بعيد عن الكيبورد والماوس؟ (من X11 مباشرة)
# ══════════════════════════════════════════════════════════════════
class _XSSInfo(ctypes.Structure):
    _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int), ("kind", ctypes.c_int),
                ("til_or_since", ctypes.c_ulong), ("idle", ctypes.c_ulong), ("event_mask", ctypes.c_ulong)]


class IdleWatcher:
    def __init__(self):
        self.ok = False
        try:
            x11, xss = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXss.so.1")
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            xss.XScreenSaverQueryExtension.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                                       ctypes.POINTER(ctypes.c_int)]
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XSSInfo)
            xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XSSInfo)]
            dpy = x11.XOpenDisplay(None)
            a, b = ctypes.c_int(), ctypes.c_int()
            if dpy and xss.XScreenSaverQueryExtension(dpy, ctypes.byref(a), ctypes.byref(b)):
                self.dpy, self.root, self.xss = dpy, x11.XDefaultRootWindow(dpy), xss
                self.info, self.ok = xss.XScreenSaverAllocInfo(), True
        except (OSError, AttributeError):
            pass

    def seconds(self):
        if self.ok and self.xss.XScreenSaverQueryInfo(self.dpy, self.root, self.info):
            return self.info.contents.idle / 1000.0
        return None


# ══════════════════════════════════════════════════════════════════
#  الشخصية
# ══════════════════════════════════════════════════════════════════
class Companion(Gtk.Window):
    def __init__(self, anims, sounds, settings):
        popup = CONFIG["window_type"] == "popup"
        super().__init__(type=Gtk.WindowType.POPUP if popup else Gtk.WindowType.TOPLEVEL)
        self.anims, self.sounds, self.settings = anims, sounds, settings
        self.idle_watch = IdleWatcher()

        self.set_title(f"{CONFIG['name']} Companion")
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_resizable(False)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        self.composited = visual is not None and screen.is_composited()
        if self.composited:
            self.set_visual(visual)                   # شفافية حقيقية
        if not popup:
            self.set_type_hint(Gdk.WindowTypeHint.DOCK)
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_accept_focus(False)
            self.set_focus_on_map(False)
            self.stick()                              # يظهر في كل مساحات العمل

        # نافذة بحجم ثابت تكفي كل الحركات، والقدمين عند نقطة ثابتة فيها
        uniq = list({id(a): a for a in anims.values()}.values())
        up = max(a.ay for a in uniq)
        self.W = math.ceil(2 * max(max(a.ax, a.w - a.ax) for a in uniq))
        self.H = max(1, math.ceil(up + max(a.h - a.ay for a in uniq)))
        self.foot = (self.W / 2, up)
        for a in uniq:
            a.layout(*self.foot)
        self.set_size_request(self.W, self.H)
        self.resize(self.W, self.H)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("realize", lambda *_: self._set_cursor("grab"))

        self.fx = self.fy = self.vx = self.vy = 0.0   # (fx, fy) = مكان القدمين على الشاشة
        self.facing, self.bounces, self.render_dy, self.shake = 1, 0, 0.0, False
        self.anim, self.frame, self.frame_t, self.anim_done = anims["idle"], 0, 0.0, False
        self.state, self.state_t, self.state_len = "idle", 0.0, 2.0
        self.target_x, self.waypoints, self.hover_t = 0.0, [], 0.0
        self.after_action = self.after_land = None
        self.sleep_afk = self.quitting = self._paused = self._running = self._done = False
        self.last_interaction = time.monotonic()
        self._press = self._click_id = self._afk_id = self._menu = None
        self._press_foot, self._dragging, self._double, self._samples, self._clicks = (0, 0), False, False, [], []
        self._pos = self._key = None
        self._last = time.monotonic()

    # ───────── الظهور والخروج ─────────
    def appear(self, greet=True):
        """ينزل طايراً من فوق ثم يلوّح — مع صوت البداية."""
        disp = Gdk.Display.get_default()
        area = (disp.get_primary_monitor() or disp.get_monitor(0)).get_workarea()
        margin = self.anims["idle"].w
        lo, hi = area.x + margin, max(area.x + margin, area.x + area.width - margin)
        land_x = random.uniform(lo, hi)
        self.fx = min(max(land_x + random.choice((-1, 1)) * random.uniform(120, 260), lo), hi)
        self.fy = area.y + self.anims["fly"].h + 10
        self.waypoints = [(land_x, area.y + area.height + CONFIG["floor_offset"], 0.0)]
        self.after_land = (lambda: self._do("wave")) if greet else None
        self._enter("fly")
        self._apply(force=True)
        self.show()
        if greet:
            self.sounds.play("startup")
        self._last = time.monotonic()
        if not self._running:
            self._running = True
            GLib.timeout_add(33, self._tick)
        if self._afk_id is None:
            self._afk_id = GLib.timeout_add_seconds(5, self._check_afk)

    def hide_for(self, minutes):
        self._running = False
        self.hide()
        GLib.timeout_add_seconds(int(minutes * 60), lambda: self.appear(greet=False) or False)

    def goodbye(self):
        if self.quitting or not self.get_visible():
            self.quit_now()
            return
        self.quitting = True
        self._cancel_click()
        self.sounds.play("goodbye")
        self._do("wave", then=lambda: self._enter("idle", 3600))
        GLib.timeout_add_seconds(6, lambda: self.quit_now() or False)   # أمان

    def quit_now(self):
        if not self._done:
            self._done = True
            Gtk.main_quit()

    # ───────── الحلقة الرئيسية (مؤقّت ذكي: أبطأ لما يكون واقف = أخف على الجهاز) ─────────
    def _tick(self):
        if not self._running:
            return False
        now = time.monotonic()
        dt, self._last = min(now - self._last, 0.25), now
        self.step(dt)
        if self._running:
            GLib.timeout_add(self._next_delay(), self._tick)
        return False

    def _next_delay(self):
        if self.state in ("walk", "run", "fly", "fall") or self.shake:
            return int(1000 / CONFIG["fps"])           # يتحرك
        if self.anim_done:
            return 100
        return int(max(15, min(150, self.anim.delays[self.frame] - self.frame_t + 1)))  # واقف: مع الإطار الجاي

    def step(self, dt):
        self.state_t += dt
        self._advance_frames(dt * 1000.0)
        getattr(self, "_tick_" + self.state)(dt)
        self._keep_inside()
        self._apply()
        if self.quitting and self.state == "idle" and not self.sounds.is_playing():
            self.quit_now()

    def _advance_frames(self, dt_ms):
        a = self.anim
        if self.anim_done:
            return
        self.frame_t += dt_ms
        while self.frame_t >= a.delays[self.frame]:
            self.frame_t -= a.delays[self.frame]
            if self.frame + 1 < len(a):
                self.frame += 1
            elif a.loop:
                self.frame = 0
            else:
                self.anim_done, self.frame_t = True, 0.0
                break

    # ───────── الحالات ─────────
    def _play(self, name, restart=False):
        anim = self.anims[name]
        if anim is self.anim and not restart and (anim.loop or not self.anim_done):
            return
        self.anim, self.frame, self.frame_t, self.anim_done = anim, 0, 0.0, False

    def _enter(self, state, duration=None):
        self.state, self.state_t, self.render_dy, self.shake = state, 0.0, 0.0, False
        if state == "idle":
            self.state_len = duration if duration is not None else random.uniform(2.0, 5.5)
        elif state == "sleep":
            self.state_len = duration if duration is not None else float("inf")
        if state != "action":
            self._play(state)

    def _do(self, name, sound=None, seconds=None, shake=False, then=None):
        """حركة مرة وحدة (أو لمدة محددة) ثم يرجع للراحة."""
        self._enter("action")
        self._play(name, restart=True)
        self.state_len = seconds if seconds is not None else (2.0 if self.anim.loop else 0.0)
        self.shake, self.after_action = shake, then
        if sound:
            self.sounds.play(sound)

    def _choose_next(self):
        if not self.idle_watch.ok and time.monotonic() - self.last_interaction > CONFIG["sleep_after_minutes"] * 60:
            self.last_interaction = time.monotonic()  # بدون معلومات خمول: قيلولة قصيرة
            self.sleep(random.uniform(45, 120))
            return
        weights = CONFIG["behavior"]
        choice = random.choices(list(weights), weights=list(weights.values()))[0]
        if choice in ("walk", "run"):
            self.walk(run=choice == "run")
        elif choice == "fly":
            self.fly()
        elif choice == "special":
            self.special()
        elif choice == "powerup":
            self.power_up()
        elif choice == "wave":
            self._do("wave")
        elif choice == "sleep":
            self.sleep(random.uniform(15, 40))
        else:
            self._enter("idle")

    def walk(self, run=False):
        lo, hi = self._walls()
        target = self.fx
        for _ in range(12):
            target = random.uniform(lo, hi)
            if abs(target - self.fx) > 100:
                break
        self.target_x = target
        self._enter("run" if run else "walk")

    def fly(self):
        area, (lo, hi) = self._area(), self._walls()
        top = area.y + self.anims["fly"].h + 10
        low = max(top, self._floor() - 140)
        self.waypoints = [(random.uniform(lo, hi), random.uniform(top, low), random.uniform(0.5, 2.5))
                          for _ in range(random.randint(1, 3))]
        self.waypoints.append((random.uniform(lo, hi), self._floor(), 0.0))
        self._enter("fly")

    def special(self):
        self._do("special", sound="special")

    def power_up(self, seconds=None):
        self._do("powerup", sound="powerup", seconds=seconds or random.uniform(2.5, 4.0), shake=True)

    def sleep(self, duration=None, afk=False):
        self.sleep_afk = afk
        self._enter("sleep", duration)
        self.sounds.play("sleep")

    def _wake(self, greet=False):
        self.sleep_afk = False
        self.last_interaction = time.monotonic()
        self.sounds.play("wake")
        self._do("wave" if greet else "happy")

    def _land(self):
        self.fy, self.vx, self.vy = self._floor(), 0.0, 0.0
        nxt, self.after_land = self.after_land, None
        nxt() if nxt else self._enter("idle")

    # كل حالة لها دالة تشتغل كل إطار
    def _tick_idle(self, dt):
        if not self.quitting:
            self._maybe_chatter(dt)
            if self.state_t >= self.state_len:
                self._choose_next()

    def _tick_walk(self, dt):
        self._maybe_chatter(dt)
        dx = self.target_x - self.fx
        step = (CONFIG["run_speed"] if self.state == "run" else CONFIG["walk_speed"]) * dt
        if abs(dx) <= step:
            self.fx = self.target_x
            self._enter("idle")
        else:
            self.facing = 1 if dx > 0 else -1
            self.fx += step * self.facing

    _tick_run = _tick_walk

    def _tick_fly(self, dt):
        if not self.waypoints:
            return self._land()
        x, y, hover = self.waypoints[0]
        dx, dy = x - self.fx, y - self.fy
        dist, step = math.hypot(dx, dy), CONFIG["fly_speed"] * dt
        if dist > step:
            self.fx += dx / dist * step
            self.fy += dy / dist * step
            if abs(dx) > 3:
                self.facing = 1 if dx > 0 else -1
        else:
            self.fx, self.fy = x, y
            self.hover_t += dt
            if self.hover_t >= hover:
                self.waypoints.pop(0)
                self.hover_t = 0.0
                if not self.waypoints:
                    return self._land()
        amp = max(0.0, min(5.0, (self._floor() - self.fy) / 25.0))     # تمايل خفيف في الجو
        self.render_dy = amp * math.sin(self.state_t * 3.2)

    def _tick_action(self, dt):
        finished = self.anim.loop or self.anim_done
        if (self.state_t >= self.state_len and finished) or self.state_t > max(self.state_len, self.anim.duration) + 2:
            nxt, self.after_action = self.after_action, None
            nxt() if nxt else self._enter("idle", random.uniform(1.0, 3.0))

    def _tick_sleep(self, dt):
        if self.state_t >= self.state_len:
            self._wake()

    def _tick_drag(self, dt):
        pass                                           # المكان يتحدد من حركة الماوس

    def _tick_fall(self, dt):
        self.vy += CONFIG["gravity"] * dt
        self.fx += self.vx * dt
        self.fy += self.vy * dt
        lo, hi = self._walls()
        if self.fx < lo:                               # يرتد من حواف الشاشة
            self.fx, self.vx = lo, abs(self.vx) * 0.5
        elif self.fx > hi:
            self.fx, self.vx = hi, -abs(self.vx) * 0.5
        top = self._area().y + self.anim.h
        if self.fy < top:
            self.fy, self.vy = top, abs(self.vy) * 0.3
        if abs(self.vx) > 40:
            self.facing = 1 if self.vx > 0 else -1
        floor = self._floor()
        if self.fy >= floor:
            self.fy = floor
            impact, self.vy = self.vy, 0.0
            if self.bounces == 0 and impact > 450:
                self.sounds.play("drop")
            if impact > 950 and self.bounces < 1:      # نطّة صغيرة بعد الطيحة
                self.bounces += 1
                self.vy, self.vx = -impact * 0.32, self.vx * 0.6
            else:
                self._land()

    def _maybe_chatter(self, dt):
        if random.random() < CONFIG["random_voice_per_hour"] / 3600.0 * dt \
                and self.sounds.has("random") and not self.sounds.is_playing():
            self._do("happy", sound="random")

    def _check_afk(self):
        secs = self.idle_watch.seconds()
        if secs is None or self.quitting or not self._running:
            return True
        if self.state == "sleep" and self.sleep_afk:
            if secs < 4:
                self._wake(greet=True)                 # رجعت! يصحى ويرحّب فيك
        elif secs >= CONFIG["sleep_after_minutes"] * 60 and self.state in ("idle", "walk", "run"):
            self.sleep(afk=True)
        return True

    # ───────── الشاشة والرسم ─────────
    def _area(self):
        disp = Gdk.Display.get_default()
        return disp.get_monitor_at_point(int(self.fx), int(self.fy) - 20).get_workarea()

    def _floor(self):
        area = self._area()
        return float(area.y + area.height + CONFIG["floor_offset"])

    def _walls(self):
        area, half = self._area(), self.anims["idle"].w * 0.35
        return area.x + half, area.x + area.width - half

    def _keep_inside(self):
        if self.state == "drag":
            return
        lo, hi = self._walls()
        self.fx = min(max(self.fx, lo), hi)
        if self.state in ("idle", "walk", "run", "sleep"):
            floor = self._floor()
            if self.fy < floor - 2:
                self.vx = self.vy = 0.0
                self.bounces = 0
                self._enter("fall")
            else:
                self.fy = floor

    def _apply(self, force=False):
        jx, jy = (random.uniform(-2, 2), random.uniform(-1.5, 1.5)) if self.shake else (0.0, 0.0)
        pos = (int(round(self.fx - self.foot[0] + jx)), int(round(self.fy - self.foot[1] + self.render_dy + jy)))
        if force or pos != self._pos:
            self._pos = pos
            self.move(*pos)
        key = (id(self.anim), self.facing, self.frame)
        if force or key != self._key:
            self._key = key
            region = self.anim.regions[self.facing][min(self.frame, len(self.anim) - 1)]
            self.input_shape_combine_region(region)    # النقر على الشفاف يمرّ للي تحت
            if not self.composited:
                self.shape_combine_region(region)      # بدون مؤثرات سطح المكتب: نقص شكل النافذة
            self.queue_draw()

    def _on_draw(self, _widget, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        a, d = self.anim, self.facing
        x, y = a.pos[d]
        if d < 0:                                      # لليسار = نفس الإطار معكوس
            cr.translate(x + a.w, y)
            cr.scale(-1, 1)
        else:
            cr.translate(x, y)
        cr.set_source_surface(a.surfaces[min(self.frame, len(a) - 1)], 0, 0)
        cr.paint()
        return True

    def _set_cursor(self, name):
        win = self.get_window()
        if win is not None:
            win.set_cursor(Gdk.Cursor.new_from_name(self.get_display(), name))

    # ───────── الماوس ─────────
    def _on_press(self, _widget, ev):
        if self.quitting:
            return True
        if ev.type == Gdk.EventType.DOUBLE_BUTTON_PRESS and ev.button == 1:
            self._cancel_click()
            self._double = True
        elif ev.type == Gdk.EventType.BUTTON_PRESS:
            if ev.button == 3:
                self._open_menu(ev)
            elif ev.button == 1:
                self._press, self._press_foot = (ev.x_root, ev.y_root), (self.fx, self.fy)
                self._dragging, self._samples = False, [(time.monotonic(), ev.x_root, ev.y_root)]
        return True

    def _on_motion(self, _widget, ev):
        if self._press is None or self.quitting:
            return True
        x, y = ev.x_root, ev.y_root
        if not self._dragging:
            if abs(x - self._press[0]) + abs(y - self._press[1]) < 6:
                return True
            self._dragging, self._double, self.sleep_afk = True, False, False
            self._cancel_click()
            self._set_cursor("grabbing")
            self.last_interaction = time.monotonic()
            self._enter("drag")
            self.sounds.play("grab")
        self.fx = self._press_foot[0] + x - self._press[0]
        self.fy = self._press_foot[1] + y - self._press[1]
        now = time.monotonic()
        self._samples = [s for s in self._samples if now - s[0] < 0.12] + [(now, x, y)]
        if abs(x - self._samples[0][1]) > 4:
            self.facing = 1 if x > self._samples[0][1] else -1
        self._apply()
        return True

    def _on_release(self, _widget, ev):
        if ev.button != 1 or self.quitting:
            return True
        if self._dragging:                             # رمية: السرعة من آخر حركة للماوس
            self._dragging, self._press = False, None
            self._set_cursor("grab")
            (t0, x0, y0), (t1, x1, y1) = self._samples[0], self._samples[-1]
            dt = max(t1 - t0, 0.01)
            lim = 2200.0
            self.vx = max(-lim, min(lim, (x1 - x0) / dt)) if t1 - t0 > 0.01 else 0.0
            self.vy = max(-lim, min(lim, (y1 - y0) / dt)) if t1 - t0 > 0.01 else 0.0
            self.bounces = 0
            self._enter("fall")
            return True
        self._press = None
        now = time.monotonic()
        self._clicks = [t for t in self._clicks if now - t < 2.5] + [now]
        if len(self._clicks) >= 5:                     # نقرات كثيرة… عصّب!
            self._clicks, self._double = [], False
            self._cancel_click()
            self.last_interaction = now
            self.power_up(3.0)
        elif self._double:
            self._double = False
            self.last_interaction = now
            self._wake() if self.state == "sleep" else self.special()
        else:
            self._cancel_click()
            ms = Gtk.Settings.get_default().props.gtk_double_click_time
            self._click_id = GLib.timeout_add(ms, self._on_single_click)
        return True

    def _on_single_click(self):
        self._click_id = None
        self.last_interaction = time.monotonic()
        self._wake() if self.state == "sleep" else self._do("happy", sound="click")
        return False

    def _cancel_click(self):
        if self._click_id is not None:
            GLib.source_remove(self._click_id)
            self._click_id = None

    def _open_menu(self, event):
        self._cancel_click()
        self._paused = self.state in ("idle", "walk", "run")
        if self._paused:
            self._enter("idle", 3600)                  # يوقف مكانه لين تختار
        menu = Gtk.Menu()

        def add(label, fn, active=None):
            item = Gtk.MenuItem(label=label) if active is None else Gtk.CheckMenuItem(label=label)
            if active is None:
                item.connect("activate", lambda _w: fn())
            else:
                item.set_active(active)
                item.connect("toggled", lambda w: fn(w.get_active()))
            menu.append(item)

        add("امشِ", self.walk)
        add("اركض", lambda: self.walk(run=True))
        add("طِر", self.fly)
        add("الحركة الخاصة", self.special)
        add("اشحن طاقتك", self.power_up)
        add("نَم", self.sleep)
        menu.append(Gtk.SeparatorMenuItem())
        add("كتم الصوت", self._set_muted, self.sounds.muted)
        add("يشتغل مع بدء الجهاز", lambda on: install_autostart() if on else remove_autostart(),
            autostart_file().exists())
        add("اختفِ ١٠ دقائق", lambda: self.hide_for(10))
        menu.append(Gtk.SeparatorMenuItem())
        add("خروج", self.goodbye)
        menu.connect("deactivate", self._menu_closed)
        menu.show_all()
        self._menu = menu                              # نحتفظ فيها لين تنقفل
        menu.popup_at_pointer(event)

    def _menu_closed(self, _menu):
        self.last_interaction = time.monotonic()
        if self._paused and self.state == "idle" and self.state_len >= 3600 and not self.quitting:
            self.state_len = self.state_t + random.uniform(0.5, 1.5)

    def _set_muted(self, muted):
        self.sounds.muted = muted
        if muted:
            self.sounds.stop()
        self.settings["muted"] = muted
        save_settings(self.settings)


# ══════════════════════════════════════════════════════════════════
#  التشغيل التلقائي + الإعدادات + التشغيل
# ══════════════════════════════════════════════════════════════════
def autostart_file():
    return xdg("XDG_CONFIG_HOME", ".config") / "autostart" / f"{APP_ID}.desktop"


def _exec_arg(arg):
    """تنسيق المسار داخل سطر Exec (يدعم المسافات)."""
    if re.search(r"[\s\"'\\<>~|&;$*?#()`]", arg):
        arg = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    return arg.replace("\\", "\\\\").replace("%", "%%")


def install_autostart():
    path = autostart_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(_exec_arg(a) for a in (sys.executable, str(SCRIPT_PATH), "--autostart"))
    path.write_text("[Desktop Entry]\nType=Application\n"
                    f"Name={CONFIG['name']} Desktop Companion\nComment=Animated desktop companion\n"
                    f"Exec={exec_line}\nPath={SCRIPT_DIR}\nTerminal=false\nStartupNotify=false\n"
                    "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=3\n", encoding="utf-8")
    log(f"Autostart enabled: {path}")


def remove_autostart():
    autostart_file().unlink(missing_ok=True)
    log("Autostart disabled.")


def settings_file():
    return xdg("XDG_CONFIG_HOME", ".config") / APP_ID / "settings.json"


def load_settings():
    try:
        return json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        settings_file().parent.mkdir(parents=True, exist_ok=True)
        settings_file().write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def single_instance():
    """يمنع تشغيل نسختين بنفس الوقت."""
    lock_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
    handle = open(lock_dir / f"{APP_ID}.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


def main():
    ap = argparse.ArgumentParser(description=f"{CONFIG['name']} desktop companion for Linux Mint")
    ap.add_argument("--install-autostart", action="store_true", help="start automatically when you log in")
    ap.add_argument("--remove-autostart", action="store_true", help="stop starting automatically")
    ap.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--delay", type=float, default=None, help="seconds to wait before appearing")
    ap.add_argument("--mute", action="store_true", help="start with sound muted")
    ap.add_argument("--assets", default=None, help="use a different assets folder")
    args = ap.parse_args()
    if args.install_autostart:
        install_autostart()
        return 0
    if args.remove_autostart:
        remove_autostart()
        return 0
    if Gdk.Display.get_default() is None:
        sys.exit("No X11 display found — run this from your desktop session.")
    lock = single_instance()
    if lock is None:
        log("Already running.")
        return 0

    Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)    # القائمة من اليمين لليسار
    assets = Path(args.assets).expanduser() if args.assets else SCRIPT_DIR / "assets"
    try:
        (assets / "sprites").mkdir(parents=True, exist_ok=True)
        for ev in SOUND_EVENTS:
            (assets / "sounds" / ev).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    settings = load_settings()
    sounds = SoundBoard(assets / "sounds", CONFIG["volume"], args.mute or settings.get("muted", False))
    pet = Companion(SpriteLibrary(assets / "sprites").load(), sounds, settings)

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda *_: pet.goodbye() or True)   # Ctrl+C
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda *_: pet.quit_now() or False)
    delay = args.delay if args.delay is not None else (4.0 if args.autostart else 0.0)
    GLib.timeout_add(int(delay * 1000), lambda: pet.appear() or False)
    log("Running! Right-click the character for the menu, Ctrl+C here to quit.")
    Gtk.main()
    sounds.stop()
    lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
