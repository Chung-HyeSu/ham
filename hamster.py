#!/usr/bin/env python3
import tkinter as tk
from tkinter import messagebox
from PIL import Image
from rembg import remove as rembg_remove
from pynput import keyboard as kb_module
from collections import deque
import cv2
import sys, time, os, json
import numpy as np
import ctypes
from ctypes import windll, wintypes

_BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_F    = os.path.join(_BASE_DIR, 'config.json')
CACHE_DIR   = os.path.join(_BASE_DIR, 'cache')
VIDEO_PATH  = os.path.join(_BASE_DIR, 'ham.mp4')
CACHE_VER   = 'v7'
SAMPLE_STEP = 2

DEFAULTS = {
    'pet_height':  150,
    'idle_after':  0.8,   # 마지막 키 입력 후 N초 동안 활성 유지
    'max_fps':     60.0,  # 미친 듯이 타이핑 시 최대 fps
    'dance_after': 60.0,  # N초 유휴 시 자동 댄스
    'dance_fps':   14.0,
    'last_x':      None,
    'last_y':      None,
}


def load_cfg():
    cfg = DEFAULTS.copy()
    if os.path.exists(CONFIG_F):
        try:
            cfg.update(json.load(open(CONFIG_F, encoding='utf-8')))
        except Exception:
            pass
    return cfg


def save_cfg(cfg):
    try:
        json.dump(cfg, open(CONFIG_F, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── Windows UpdateLayeredWindow ────────────────────────────────

GWL_EXSTYLE      = -20
WS_EX_LAYERED    = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST     = -1
SWP_NOACTIVATE   = 0x0010
SWP_NOMOVE       = 0x0002
SWP_NOSIZE       = 0x0001
ULW_ALPHA        = 0x00000002
AC_SRC_OVER      = 0x00
AC_SRC_ALPHA     = 0x01
GA_ROOT          = 2


class _POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

class _SIZE(ctypes.Structure):
    _fields_ = [('cx', ctypes.c_long), ('cy', ctypes.c_long)]

class _BLEND(ctypes.Structure):
    _fields_ = [
        ('BlendOp',             ctypes.c_uint8),
        ('BlendFlags',          ctypes.c_uint8),
        ('SourceConstantAlpha', ctypes.c_uint8),
        ('AlphaFormat',         ctypes.c_uint8),
    ]

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize',          wintypes.DWORD),
        ('biWidth',         wintypes.LONG),
        ('biHeight',        wintypes.LONG),
        ('biPlanes',        wintypes.WORD),
        ('biBitCount',      wintypes.WORD),
        ('biCompression',   wintypes.DWORD),
        ('biSizeImage',     wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed',       wintypes.DWORD),
        ('biClrImportant',  wintypes.DWORD),
    ]


def _toplevel_hwnd(tk_id):
    top = windll.user32.GetAncestor(tk_id, GA_ROOT)
    return top if top else tk_id


def _setup_layered(hwnd):
    style = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  style | WS_EX_LAYERED | WS_EX_TOOLWINDOW)
    windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                               SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def draw_layered(hwnd, rgba_img, win_x, win_y):
    w, h = rgba_img.size
    arr  = np.array(rgba_img, dtype=np.uint8)

    a             = arr[:, :, 3:4].astype(np.float32) / 255.0
    bgra          = np.empty((h, w, 4), dtype=np.uint8)
    bgra[:, :, 0] = (arr[:, :, 2] * a[:, :, 0]).astype(np.uint8)
    bgra[:, :, 1] = (arr[:, :, 1] * a[:, :, 0]).astype(np.uint8)
    bgra[:, :, 2] = (arr[:, :, 0] * a[:, :, 0]).astype(np.uint8)
    bgra[:, :, 3] = arr[:, :, 3]
    data = bgra.tobytes()

    hdc_scr = windll.user32.GetDC(None)
    hdc_mem = windll.gdi32.CreateCompatibleDC(hdc_scr)

    bih   = _BITMAPINFOHEADER(40, w, -h, 1, 32, 0, len(data), 0, 0, 0, 0)
    pBits = ctypes.c_void_p()
    hbm   = windll.gdi32.CreateDIBSection(
        hdc_mem, ctypes.byref(bih), 0, ctypes.byref(pBits), None, 0)
    windll.gdi32.SelectObject(hdc_mem, hbm)
    ctypes.memmove(pBits, data, len(data))

    blend = _BLEND(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    dst   = _POINT(win_x, win_y)
    src   = _POINT(0, 0)
    sz    = _SIZE(w, h)

    windll.user32.UpdateLayeredWindow(
        hwnd, hdc_scr,
        ctypes.byref(dst), ctypes.byref(sz),
        hdc_mem, ctypes.byref(src),
        0, ctypes.byref(blend), ULW_ALPHA)

    windll.gdi32.DeleteObject(hbm)
    windll.gdi32.DeleteDC(hdc_mem)
    windll.user32.ReleaseDC(None, hdc_scr)


# ── 프레임 로드 ────────────────────────────────────────────────

def _build_frames(raw, H):
    resized = []
    for img in raw:
        bbox = img.getbbox()
        if bbox:
            x1, y1, x2, y2 = bbox
            img = img.crop((
                max(0, x1 - 4), max(0, y1 - 4),
                min(img.width, x2 + 4), min(img.height, y2 + 16),
            ))
        w, h = img.size
        img = img.resize((int(w * H / h), H), Image.LANCZOS)
        resized.append(img)

    max_w = max(f.size[0] for f in resized)
    max_h = max(f.size[1] for f in resized)
    aligned = []
    for img in resized:
        fw, fh = img.size
        canvas = Image.new('RGBA', (max_w, max_h), (0, 0, 0, 0))
        canvas.paste(img, ((max_w - fw) // 2, max_h - fh), img)
        aligned.append(canvas)

    result = []
    for i in range(len(aligned)):
        result.append(aligned[i])
        next_i = (i + 1) % len(aligned)
        result.append(Image.blend(aligned[i], aligned[next_i], 0.5))

    return result, max_w, max_h


def load_frames(cfg, loader=None):
    os.makedirs(CACHE_DIR, exist_ok=True)
    H          = cfg['pet_height']
    index_path = os.path.join(CACHE_DIR, f'index_{CACHE_VER}.json')

    if os.path.exists(index_path):
        try:
            indices    = json.load(open(index_path, encoding='utf-8'))
            cache_hits = [os.path.join(CACHE_DIR, f'frame_{i}_{CACHE_VER}.png') for i in indices]
            if all(os.path.exists(p) for p in cache_hits):
                raw = [Image.open(p).convert('RGBA') for p in cache_hits]
                return _build_frames(raw, H)
        except Exception:
            pass

    cap     = cv2.VideoCapture(VIDEO_PATH)
    total   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    indices = list(range(0, total, SAMPLE_STEP))
    raw     = []

    for i, fidx in enumerate(indices):
        cache_path = os.path.join(CACHE_DIR, f'frame_{fidx}_{CACHE_VER}.png')
        if os.path.exists(cache_path):
            img = Image.open(cache_path).convert('RGBA')
        else:
            if loader:
                loader.update(f'배경 제거 중... ({i+1}/{len(indices)})')
            cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
            ok, bgr = cap.read()
            if not ok:
                continue
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb).convert('RGBA')
            img = rembg_remove(img, alpha_matting=False)
            img.save(cache_path)
        raw.append(img)

    cap.release()

    if not raw:
        raise ValueError("프레임을 추출하지 못했습니다.")

    json.dump(indices, open(index_path, 'w', encoding='utf-8'))
    return _build_frames(raw, H)


# ── 키보드 리스너 ──────────────────────────────────────────────

_last_key_time = 0.0
_key_times     = deque()   # 최근 키 입력 타임스탬프


def _start_listener():
    def on_press(key):
        global _last_key_time
        now = time.time()
        _last_key_time = now
        _key_times.append(now)
    l = kb_module.Listener(on_press=on_press)
    l.daemon = True
    l.start()
    return l


# ── 로딩 창 ────────────────────────────────────────────────────

_SPINNER_FRAMES = [
    "🐹 ᶠᵒᵒᵗ ᶠᵒᵒᵗ ᶠᵒᵒᵗ",
    "  🐹ᶠᵒᵒᵗ ᶠᵒᵒᵗ ᶠᵒᵒᵗ",
    "    🐹 ᶠᵒᵒᵗᶠᵒᵒᵗᶠᵒᵒᵗ",
    "      🐹ᶠᵒᵒᵗᶠᵒᵒᵗ",
    "        🐹 ···",
    "      🐹ᶠᵒᵒᵗᶠᵒᵒᵗ",
    "    🐹 ᶠᵒᵒᵗᶠᵒᵒᵗᶠᵒᵒᵗ",
    "  🐹ᶠᵒᵒᵗ ᶠᵒᵒᵗ ᶠᵒᵒᵗ",
]

class Loader:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('햄찌 깨우는 중')
        self.root.resizable(False, False)
        self.root.attributes('-topmost', True)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f'360x90+{sw//2-180}+{sh//2-45}')
        self.root.configure(bg='#fff8f0')
        self._spin = tk.Label(self.root, text=_SPINNER_FRAMES[0],
                              font=('Segoe UI Emoji', 13), bg='#fff8f0', pady=4)
        self._spin.pack(fill='x')
        self._msg = tk.Label(self.root, text='준비 중...',
                             font=('Segoe UI', 9), fg='#888', bg='#fff8f0', pady=2)
        self._msg.pack(fill='x')
        self._tick = 0
        self.root.update()
        self._animate()

    def _animate(self):
        self._tick += 1
        self._spin.config(text=_SPINNER_FRAMES[self._tick % len(_SPINNER_FRAMES)])
        self.root.after(120, self._animate)
        self.root.update()

    def update(self, msg):
        self._msg.config(text=msg)
        self.root.update()

    def close(self):
        self.root.destroy()


# ── 데스크탑 펫 ────────────────────────────────────────────────

class HamsterPet:
    TASKBAR_H = 60

    def __init__(self, root, frames, fw, fh, cfg):
        self.root   = root
        self.frames = frames
        self.cfg    = cfg
        self._cur   = 0
        self._last_frame_time = time.time()

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        lx, ly = cfg.get('last_x'), cfg.get('last_y')
        self.x = int(lx if lx is not None else sw - fw - 20)
        self.y = int(ly if ly is not None else sh - self.TASKBAR_H - fh - 5)

        root.geometry(f'{fw}x{fh}+{self.x}+{self.y}')
        root.update()

        self.hwnd = _toplevel_hwnd(root.winfo_id())
        _setup_layered(self.hwnd)
        draw_layered(self.hwnd, self.frames[0], self.x, self.y)

        root.bind('<ButtonPress-1>', self._drag_start)
        root.bind('<B1-Motion>',     self._drag_move)
        root.bind('<Button-3>',      lambda e: self._quit())

        self._poll()

    def _drag_start(self, e):
        self._ox, self._oy = e.x, e.y

    def _drag_move(self, e):
        self.x = self.root.winfo_x() + (e.x - self._ox)
        self.y = self.root.winfo_y() + (e.y - self._oy)
        self.root.geometry(f'+{self.x}+{self.y}')
        draw_layered(self.hwnd, self.frames[self._cur], self.x, self.y)

    def _poll(self):
        now      = time.time()
        idle_sec = now - _last_key_time

        # 1초 밖에 있는 키 입력 제거
        while _key_times and now - _key_times[0] > 1.0:
            _key_times.popleft()

        if idle_sec < self.cfg['idle_after']:
            # 타이핑 중: kps 에 따라 12~max_fps 선형 보간
            # 1kps → 12fps, 10kps → max_fps
            kps = len(_key_times)
            fps = max(12.0, min(kps * 6.0, self.cfg['max_fps']))
        elif idle_sec >= self.cfg['dance_after']:
            fps = self.cfg['dance_fps']
        else:
            fps = None

        if fps and now - self._last_frame_time >= 1.0 / fps:
            self._last_frame_time = now
            self._cur = (self._cur + 1) % len(self.frames)
            draw_layered(self.hwnd, self.frames[self._cur], self.x, self.y)

        self.root.after(16, self._poll)

    def _quit(self):
        self.cfg['last_x'], self.cfg['last_y'] = self.x, self.y
        save_cfg(self.cfg)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ── 진입점 ─────────────────────────────────────────────────────

def main():
    has_cache = os.path.exists(os.path.join(CACHE_DIR, f'index_{CACHE_VER}.json'))
    if not has_cache and not os.path.exists(VIDEO_PATH):
        tk.Tk().withdraw()
        messagebox.showerror('오류', 'ham 폴더 안에 ham.mp4 파일이 필요합니다.')
        sys.exit(1)

    cfg    = load_cfg()
    loader = Loader()

    try:
        loader.update('프레임 준비 중...')
        frames, fw, fh = load_frames(cfg, loader)
    except Exception as e:
        loader.close()
        tk.Tk().withdraw()
        messagebox.showerror('오류', f'처리 실패:\n{e}')
        sys.exit(1)

    loader.close()

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes('-topmost', True)
    root.configure(bg='black')

    listener = _start_listener()
    try:
        HamsterPet(root, frames, fw, fh, cfg).run()
    finally:
        listener.stop()


if __name__ == '__main__':
    main()
