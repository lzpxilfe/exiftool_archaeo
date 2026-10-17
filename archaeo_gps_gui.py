#!/usr/bin/env python3
"""
archaeo_gps_gui.py — 고고학 현장 사진 GPS·방향 추출 도구 (GUI + CLI 통합)
더블클릭 실행 → GUI 창
인자 전달 실행 → CLI 모드 (기존 archaeo_gps.py 동작 유지)
"""

import json
import os
import sys
from pathlib import Path

# ── Windows 콘솔 UTF-8 ───────────────────────────────────────────────────────
if sys.platform == "win32":
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ── PyInstaller 번들 경로 처리 ───────────────────────────────────────────────
def resource_path(relative_path: str) -> str:
    """PyInstaller 번들 내부/외부 모두 동작하는 리소스 경로 반환."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


# ── 핵심 로직 import (같은 패키지 내 archaeo_gps 모듈) ──────────────────────
# exe 빌드 시 archaeo_gps.py가 함께 번들됨
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys, "_MEIPASS"):
    sys.path.insert(0, sys._MEIPASS)

from archaeo_gps import (
    find_exiftool, collect_images, collect_grouped_images, run_exiftool, parse_record,
    write_csv, write_map, write_geojson, build_transformers, CRS_PRESETS,
    CAMERA_TYPE_DRONE, CAMERA_TYPE_DSLR, MAX_SUMMARY_ROWS,
    HAS_PYPROJ, HAS_PILLOW, THUMB_SIZE_DEFAULT, __version__,
)


def _split_folder_inputs(raw_str: str) -> list[str]:
    """세미콜론(;)으로 구분된 하나 이상의 폴더/파일 경로 문자열을 리스트로 분리한다."""
    if not raw_str:
        return []
    return [part.strip() for part in str(raw_str).split(";") if part.strip()]


# ── 사용자 설정 저장 (마지막 사용 값 기억) ──────────────────────────────────
def _settings_path() -> Path:
    """OS 별 사용자 설정 파일 경로를 반환한다."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "ExifToolArchaeo" / "settings.json"


def load_settings() -> dict:
    """저장된 설정을 읽는다. 없거나 손상되었으면 빈 dict 를 돌려준다."""
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    """설정을 저장한다. 실패해도 프로그램 동작에는 영향을 주지 않는다."""
    try:
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def open_in_file_manager(target: str) -> bool:
    """탐색기/파인더로 폴더를 연다 (윈도우 전용 os.startfile 대체)."""
    import subprocess
    try:
        if sys.platform == "win32":
            os.startfile(target)          # noqa: S606  (윈도우 전용 API)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
        return True
    except Exception:
        return False


# ════════════════════════════════════════════════════════════════════════════
#  GUI 모드
# ════════════════════════════════════════════════════════════════════════════

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
    import threading
    import queue

    # ── 색상 팔레트 ────────────────────────────────────────────────────────
    BG        = "#1a1a2e"
    PANEL     = "#16213e"
    ACCENT    = "#e94560"
    ACCENT2   = "#0f3460"
    TEXT      = "#e2e8f0"
    SUBTEXT   = "#94a3b8"
    ENTRY_BG  = "#0f2044"
    SUCCESS   = "#48bb78"
    WARNING   = "#f6ad55"
    MONO      = ("Consolas", 9)
    FONT_MAIN = ("Segoe UI", 10)
    FONT_BOLD = ("Segoe UI", 10, "bold")
    FONT_H1   = ("Segoe UI", 13, "bold")

    settings = load_settings()

    root = tk.Tk()
    root.withdraw()  # 메인 화면 임시 숨김 (스플래시 표시용)
    root.title("ExifTool Archaeo — 고고학 현장 GPS 추출기")
    root.configure(bg=BG)
    root.resizable(True, True)
    root.minsize(740, 620)

    # ── 스플래시 로딩 화면 ───────────────────────────────────────────────────
    splash = tk.Toplevel(root)
    splash.overrideredirect(True)
    splash.configure(bg=PANEL)
    
    # 스플래시 중앙 정렬 계산
    sw = splash.winfo_screenwidth()
    sh = splash.winfo_screenheight()
    sp_w, sp_h = 420, 160
    sp_x = (sw - sp_w) // 2
    sp_y = (sh - sp_h) // 2
    splash.geometry(f"{sp_w}x{sp_h}+{sp_x}+{sp_y}")
    
    sp_border = tk.Frame(splash, bg=ACCENT, bd=2)
    sp_border.pack(fill="both", expand=True)
    sp_inner = tk.Frame(sp_border, bg=PANEL)
    sp_inner.pack(fill="both", expand=True, padx=2, pady=2)
    
    tk.Label(sp_inner, text="🏛️ ExifTool Archaeo", font=("Segoe UI", 16, "bold"), fg=ACCENT, bg=PANEL).pack(pady=(25, 6))
    tk.Label(sp_inner, text="프로그램을 로딩 중입니다. 잠시만 기다려 주십시오...", font=("Segoe UI", 10), fg=TEXT, bg=PANEL).pack(pady=4)
    tk.Label(sp_inner, text=f"v{__version__} — 고고학 현장 GPS/방향 추출 분석 도구", font=("Segoe UI", 8), fg=SUBTEXT, bg=PANEL).pack(pady=(12, 0))
    splash.update()

    # ── 아이콘 (번들 내 ico 파일 있으면 적용) ─────────────────────────────
    try:
        ico = resource_path("archaeo.ico")
        if os.path.exists(ico):
            root.iconbitmap(ico)
    except Exception:
        pass

    # DPI 스케일 처리 (고해상도 디스플레이)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # ── 스타일 ──────────────────────────────────────────────────────────────
    style = ttk.Style()
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT,
                    fieldbackground=ENTRY_BG, troughcolor=PANEL,
                    insertcolor=TEXT, selectbackground=ACCENT2,
                    selectforeground=TEXT, font=FONT_MAIN)
    style.configure("TFrame",       background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel",       background=BG,    foreground=TEXT,    font=FONT_MAIN)
    style.configure("Sub.TLabel",   background=BG,    foreground=SUBTEXT, font=("Segoe UI", 8))
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT,    font=FONT_MAIN)
    style.configure("TEntry",       fieldbackground=ENTRY_BG, foreground=TEXT,
                    insertcolor=TEXT, borderwidth=0)
    style.configure("TCombobox",    fieldbackground=ENTRY_BG, foreground=TEXT,
                    selectbackground=ACCENT2, arrowcolor=TEXT)
    # readonly 상태의 Combobox 는 clam 기본값이 회색 바탕/회색 글씨라 선택한
    # 좌표계가 거의 보이지 않는다. 상태별 색을 명시해 가독성을 확보한다.
    style.map("TCombobox",
              fieldbackground=[("readonly", ENTRY_BG), ("disabled", PANEL)],
              foreground=[("readonly", TEXT), ("disabled", SUBTEXT)],
              selectbackground=[("readonly", ENTRY_BG)],
              selectforeground=[("readonly", TEXT)],
              background=[("readonly", ACCENT2), ("active", ACCENT2)],
              arrowcolor=[("readonly", TEXT)])
    # 펼쳐지는 목록(Listbox)은 ttk 스타일이 아닌 Tk 옵션으로 지정해야 한다.
    root.option_add("*TCombobox*Listbox.background", ENTRY_BG)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
    style.configure("TCheckbutton", background=BG, foreground=TEXT)
    style.map("TCheckbutton",
              background=[("active", BG)],
              foreground=[("active", ACCENT)])
    style.configure("Run.TButton",
                    background=ACCENT, foreground="white",
                    font=("Segoe UI", 11, "bold"),
                    borderwidth=0, relief="flat", padding=(20, 10))
    style.map("Run.TButton",
              background=[("active", "#c93050"), ("disabled", "#4a4a6a")],
              foreground=[("disabled", SUBTEXT)])
    style.configure("Browse.TButton",
                    background=ACCENT2, foreground=TEXT,
                    font=("Segoe UI", 9), borderwidth=0, padding=(8, 4))
    style.map("Browse.TButton",
              background=[("active", "#1a4a90")])
    style.configure("TProgressbar",
                    troughcolor=PANEL, background=ACCENT,
                    thickness=6, borderwidth=0)
    style.configure("TSeparator", background="#2d3748")

    # ── 루트 레이아웃 ────────────────────────────────────────────────────────
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)

    # ── 헤더 ────────────────────────────────────────────────────────────────
    header = tk.Frame(root, bg=ACCENT2, height=56)
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(1, weight=1)
    header.grid_propagate(False)

    tk.Label(header, text="🏛️", font=("Segoe UI", 20),
             bg=ACCENT2, fg=TEXT).grid(row=0, column=0, padx=(16, 8), pady=10)
    tk.Label(header, text="ExifTool Archaeo",
             font=("Segoe UI", 14, "bold"), bg=ACCENT2, fg=TEXT).grid(
             row=0, column=1, sticky="w")
    tk.Label(header, text=f"고고학 현장 사진 GPS·방향 추출 도구  v{__version__}",
             font=("Segoe UI", 8), bg=ACCENT2, fg=SUBTEXT).grid(
             row=0, column=2, padx=16, sticky="e")

    # ── 본문 ────────────────────────────────────────────────────────────────
    body = ttk.Frame(root)
    body.grid(row=1, column=0, sticky="nsew", padx=20, pady=16)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(5, weight=1)

    # ── 입력 패널 ────────────────────────────────────────────────────────────
    def make_section(parent, title, row):
        f = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        f.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        f.columnconfigure(1, weight=1)
        tk.Label(f, text=title, font=FONT_BOLD,
                 bg=PANEL, fg=ACCENT).grid(row=0, column=0, columnspan=4,
                                            sticky="w", pady=(0, 8))
        return f

    def make_row(parent, label, row, browse_fn=None, browse_text="폴더 선택",
                 add_fn=None, clear_fn=None):
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 10))
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=46)
        entry.grid(row=row, column=1, sticky="ew", pady=2)
        btn_box = ttk.Frame(parent, style="Panel.TFrame")
        btn_box.grid(row=row, column=2, padx=(6, 0), sticky="e")
        if browse_fn:
            ttk.Button(btn_box, text=browse_text, style="Browse.TButton",
                       command=lambda: var.set(browse_fn(var.get()))).pack(side="left", padx=(0, 3))
        if add_fn:
            ttk.Button(btn_box, text="+추가", style="Browse.TButton",
                       command=lambda: var.set(add_fn(var.get()))).pack(side="left", padx=(0, 3))
        if clear_fn:
            ttk.Button(btn_box, text="비우기", style="Browse.TButton",
                       command=lambda: var.set("")).pack(side="left")
        return var

    # § 섹션 1 — 입력 / 출력 (드론 폴더와 DSLR 폴더 분리)
    sec1 = make_section(body, "📂  입력 / 출력 (드론 · DSLR 분리 입력 지원)", 0)

    def _initial_dir(current: str) -> str:
        """대화상자가 마지막으로 쓰던 폴더에서 열리도록 시작 경로를 고른다."""
        parts = _split_folder_inputs(current)
        first_cand = parts[-1] if parts else current
        for cand in (first_cand, settings.get("last_dir", "")):
            if cand:
                p = Path(cand)
                p = p if p.is_dir() else p.parent
                if p.is_dir():
                    return str(p)
        return ""

    def _remember_dir(path: str) -> None:
        if path:
            p = Path(path)
            settings["last_dir"] = str(p if p.is_dir() else p.parent)

    def _browse_folder(title_str: str, current_val: str) -> str:
        d = filedialog.askdirectory(title=title_str,
                                    initialdir=_initial_dir(current_val) or None)
        _remember_dir(d)
        return d or current_val

    def _add_folder(title_str: str, current_val: str) -> str:
        d = filedialog.askdirectory(title=title_str,
                                    initialdir=_initial_dir(current_val) or None)
        _remember_dir(d)
        if not d:
            return current_val
        existing = _split_folder_inputs(current_val)
        if d not in existing:
            existing.append(d)
        return "; ".join(existing)

    def browse_output(_cur: str = ""):
        f = filedialog.asksaveasfilename(
            title="CSV 저장 위치", defaultextension=".csv",
            initialdir=_initial_dir(v_output.get()) or None,
            filetypes=[("CSV 파일", "*.csv"), ("모든 파일", "*.*")])
        _remember_dir(f)
        return f or v_output.get()

    def browse_map():
        f = filedialog.asksaveasfilename(
            title="지도 HTML 저장 위치", defaultextension=".html",
            initialdir=_initial_dir(v_map_path.get() or v_output.get()) or None,
            filetypes=[("HTML 파일", "*.html"), ("모든 파일", "*.*")])
        _remember_dir(f)
        return f or v_map_path.get()

    v_drone_input = make_row(
        sec1, "드론 사진 폴더 (화살표)", 1,
        browse_fn=lambda cur: _browse_folder("드론 사진 폴더 선택", cur),
        browse_text="폴더 선택",
        add_fn=lambda cur: _add_folder("드론 사진 폴더 추가", cur),
        clear_fn=True,
    )
    v_dslr_input = make_row(
        sec1, "DSLR 사진 폴더 (점만)", 2,
        browse_fn=lambda cur: _browse_folder("DSLR 사진 폴더 선택", cur),
        browse_text="폴더 선택",
        add_fn=lambda cur: _add_folder("DSLR 사진 폴더 추가", cur),
        clear_fn=True,
    )
    tk.Label(
        sec1,
        text="💡 드론 폴더는 점+촬영방향 화살표로, DSLR 폴더는 화살표 없이 점으로 표시됩니다. 폴더별로 색상이 자동 구분됩니다.",
        font=("Segoe UI", 8), bg=PANEL, fg=SUBTEXT,
    ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 6))

    v_output = make_row(sec1, "출력 CSV", 4, browse_output, "저장 위치")

    # 이전 버전 설정("input")이 있으면 드론 입력칸에 하위호환 복원
    v_drone_input.set(settings.get("drone_input", settings.get("input", "")))
    v_dslr_input.set(settings.get("dslr_input", ""))
    v_output.set(settings.get("output", ""))

    # § 섹션 2 — 설정
    sec2 = make_section(body, "⚙️  설정", 1)

    # CRS 선택
    ttk.Label(sec2, text="좌표계 변환", style="Panel.TLabel").grid(
        row=1, column=0, sticky="w", padx=(0, 10))
    crs_frame = ttk.Frame(sec2, style="Panel.TFrame")
    crs_frame.grid(row=1, column=1, columnspan=2, sticky="w", pady=2)

    CRS_OPTIONS = [
        # ── 한국 현행 (GRS80) ──────────────────────────────────────────
        ("★ TM중부원점  EPSG:5186  [기본값, 한국 국가 표준]",  "tm"),
        ("TM서부원점   EPSG:5185  [서해안·서부 지역]",        "tm_west"),
        ("TM동부원점   EPSG:5187  [동해안·영남동부]",         "tm_east"),
        ("TM동해원점   EPSG:5188  [울릉도·독도]",             "tm_eastsea"),
        ("UTM-K        EPSG:5179  [국토지리정보원 통합]",      "utmk"),
        ("GRS80 지리좌표 EPSG:4737",                          "grs80_geo"),
        # ── 한국 구좌표 (Bessel 1841) ──────────────────────────────────
        ("구TM중부원점  EPSG:5174  [Bessel, 구 내륙 표준]",   "tm_old"),
        ("구TM서부원점  EPSG:5173  [Bessel]",                 "tm_old_west"),
        ("구TM동부원점  EPSG:5176  [Bessel]",                 "tm_old_east"),
        ("구TM동해원점  EPSG:5177  [Bessel, 구 울릉]",        "tm_old_eastsea"),
        ("수정중부원점  EPSG:5181  [Bessel, 2010 경과조치]",  "tm_mod"),
        ("수정서부원점  EPSG:5182  [Bessel]",                 "tm_mod_west"),
        ("수정동부원점  EPSG:5183  [Bessel]",                 "tm_mod_east"),
        ("Bessel 지리좌표 EPSG:4162",                         "bessel_geo"),
        # ── 국제 / 글로벌 ──────────────────────────────────────────────
        ("UTM Zone 52N  EPSG:32652  [한반도 전역, WGS84]",    "utm52n"),
        ("UTM Zone 51N  EPSG:32651  [서해 일부, WGS84]",      "utm51n"),
        ("Web Mercator  EPSG:3857   [Google·Kakao지도 기준]", "web_mercator"),
        ("WGS84 지리좌표 EPSG:4326  [변환 없음]",             "wgs84"),
    ]
    v_crs = tk.StringVar()
    cb_crs = ttk.Combobox(crs_frame, textvariable=v_crs, width=52,
                          state="readonly",
                          values=[c[0] for c in CRS_OPTIONS])
    # 저장된 좌표계가 있으면 복원, 없으면 첫 항목(TM중부원점)
    _saved_crs = settings.get("crs", "tm")
    _crs_index = next((i for i, c in enumerate(CRS_OPTIONS) if c[1] == _saved_crs), 0)
    cb_crs.current(_crs_index)
    cb_crs.pack(side="left")

    # 지도 생성 체크박스 + 경로
    ttk.Label(sec2, text="지도 생성", style="Panel.TLabel").grid(
        row=2, column=0, sticky="w", padx=(0, 10), pady=(6, 0))
    map_frame = ttk.Frame(sec2, style="Panel.TFrame")
    map_frame.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(6, 0))
    map_frame.columnconfigure(1, weight=1)

    v_make_map = tk.BooleanVar(value=settings.get("make_map", True))
    ttk.Checkbutton(map_frame, text="HTML 지도 생성 (비워두면 CSV와 같은 이름으로 저장)",
                    variable=v_make_map,
                    style="TCheckbutton").grid(row=0, column=0, sticky="w")
    v_map_path = tk.StringVar(value=settings.get("map_path", ""))
    ttk.Entry(map_frame, textvariable=v_map_path, width=28).grid(
        row=0, column=1, sticky="ew", padx=(8, 4))
    ttk.Button(map_frame, text="저장 위치", style="Browse.TButton",
               command=lambda: v_map_path.set(browse_map())).grid(
               row=0, column=2)

    # ── 추가 옵션 ──────────────────────────────────────────────────────────
    ttk.Label(sec2, text="추가 옵션", style="Panel.TLabel").grid(
        row=3, column=0, sticky="nw", padx=(0, 10), pady=(8, 0))
    opt_frame = ttk.Frame(sec2, style="Panel.TFrame")
    opt_frame.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(8, 0))

    v_recursive = tk.BooleanVar(value=settings.get("recursive", False))
    ttk.Checkbutton(opt_frame, text="하위 폴더까지 모두 검색 (하위 폴더별 색상 구분)",
                    variable=v_recursive, style="TCheckbutton").grid(
                    row=0, column=0, sticky="w", padx=(0, 16))

    v_geojson = tk.BooleanVar(value=settings.get("geojson", False))
    ttk.Checkbutton(opt_frame, text="GeoJSON 함께 저장 (QGIS)",
                    variable=v_geojson, style="TCheckbutton").grid(
                    row=0, column=1, sticky="w", padx=(0, 16))

    v_thumbs = tk.BooleanVar(value=settings.get("thumbs", True))
    cb_thumbs = ttk.Checkbutton(opt_frame, text="지도에 사진 썸네일 포함",
                                variable=v_thumbs, style="TCheckbutton")
    cb_thumbs.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
    if not HAS_PILLOW:
        # Pillow 가 없으면 썸네일 자체가 불가능하므로 체크박스를 비활성화한다.
        v_thumbs.set(False)
        cb_thumbs.configure(state="disabled",
                            text="지도에 사진 썸네일 포함 (Pillow 미설치)")

    # ExifTool 경로는 백그라운드에서 자동 탐색하므로 UI에서 제외
    v_exiftool = tk.StringVar()

    def _detect_exiftool_async():
        try:
            found = find_exiftool(None)
        except FileNotFoundError:
            found = ""
        try:
            root.after(0, lambda: v_exiftool.set(found))
        except Exception:
            pass

    threading.Thread(target=_detect_exiftool_async, daemon=True).start()

    # § 섹션 3 — 실행 버튼
    btn_frame = ttk.Frame(body)
    btn_frame.grid(row=2, column=0, pady=(0, 10), sticky="e")

    progress_var = tk.DoubleVar()
    progress_bar = ttk.Progressbar(body, variable=progress_var,
                                   maximum=100, style="TProgressbar", length=400)
    progress_bar.grid(row=3, column=0, sticky="ew", pady=(0, 8))

    # § 섹션 4 — 로그 출력
    log_frame = ttk.Frame(body, style="Panel.TFrame")
    log_frame.grid(row=5, column=0, sticky="nsew", pady=(0, 0))
    log_frame.columnconfigure(0, weight=1)
    log_frame.rowconfigure(1, weight=1)

    tk.Label(log_frame, text="실행 로그", font=FONT_BOLD,
             bg=PANEL, fg=ACCENT).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))

    log_text = scrolledtext.ScrolledText(
        log_frame, wrap="word", height=10,
        bg="#0a0f1e", fg=TEXT, insertbackground=TEXT,
        font=MONO, borderwidth=0, relief="flat",
        selectbackground=ACCENT2)
    log_text.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
    log_text.tag_config("ok",   foreground=SUCCESS)
    log_text.tag_config("warn", foreground=WARNING)
    log_text.tag_config("err",  foreground=ACCENT)
    log_text.tag_config("info", foreground="#63b3ed")

    msg_queue = queue.Queue()

    def log(msg: str, tag=""):
        msg_queue.put((msg, tag))

    def drain_queue():
        try:
            while True:
                msg, tag = msg_queue.get_nowait()
                log_text.configure(state="normal")
                log_text.insert("end", msg + "\n", tag)
                log_text.see("end")
                log_text.configure(state="disabled")
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            root.after(100, drain_queue)
        except tk.TclError:
            return

    root.after(100, drain_queue)

    # ── 실행 로직 ───────────────────────────────────────────────────────────
    run_btn = ttk.Button(btn_frame, text="  ▶  추출 실행  ",
                         style="Run.TButton", command=lambda: start_run())
    run_btn.pack()

    def set_progress(value: float):
        """진행률 갱신 — Tk 변수는 반드시 메인 스레드에서 건드려야 한다."""
        try:
            root.after(0, lambda: progress_var.set(value))
        except Exception:
            pass

    def start_run():
        # 입력 검증 (드론 폴더, DSLR 폴더 중 최소 1개 이상)
        drone_raw = v_drone_input.get().strip()
        dslr_raw = v_dslr_input.get().strip()
        drone_list = _split_folder_inputs(drone_raw)
        dslr_list = _split_folder_inputs(dslr_raw)
        out = v_output.get().strip()

        if not drone_list and not dslr_list:
            messagebox.showwarning("입력 오류", "드론 사진 폴더 또는 DSLR 사진 폴더를 하나 이상 선택해 주세요.")
            return
        for p in drone_list + dslr_list:
            if not Path(p).exists():
                messagebox.showwarning("입력 오류", f"입력 경로를 찾을 수 없습니다:\n{p}")
                return
        if not out:
            messagebox.showwarning("입력 오류", "출력 CSV 저장 위치를 선택해 주세요.")
            return

        # 지도 경로: 입력칸이 비어 있으면 현재 CSV 경로 기준으로 자동 파생하되,
        # 입력칸(v_map_path) 자체는 덮어쓰지 않아 다음번 CSV 경로 변경 시에도 자동 추적되게 한다.
        map_path = ""
        user_map_path = v_map_path.get().strip()
        if v_make_map.get():
            map_path = user_map_path or str(Path(out).with_suffix(".html"))

        geojson_path = ""
        if v_geojson.get():
            geojson_path = str(Path(out).with_suffix(".geojson"))

        recursive = v_recursive.get()
        thumb_size = THUMB_SIZE_DEFAULT if v_thumbs.get() else 0

        # CRS 결정
        crs_label = v_crs.get()
        crs_code = next((c[1] for c in CRS_OPTIONS if c[0] == crs_label), "tm")
        target_crs = CRS_PRESETS.get(crs_code, crs_code)

        et_path_hint = v_exiftool.get().strip() or None

        # 다음 실행을 위해 현재 설정을 저장한다.
        settings.update({
            "drone_input": drone_raw,
            "dslr_input": dslr_raw,
            "output": out,
            "crs": crs_code,
            "make_map": v_make_map.get(),
            "map_path": user_map_path,
            "recursive": recursive,
            "geojson": v_geojson.get(),
            "thumbs": v_thumbs.get(),
        })
        save_settings(settings)

        run_btn.configure(state="disabled")
        set_progress(0)
        log_text.configure(state="normal")
        log_text.delete("1.0", "end")
        log_text.configure(state="disabled")

        def worker():
            try:
                # 1. exiftool 탐색 (워커 스레드에서 수행하여 메인 UI 블로킹 방지)
                log("▶ ExifTool 탐색 중…", "info")
                try:
                    et = find_exiftool(et_path_hint)
                    log(f"✔ ExifTool: {et}", "ok")
                except FileNotFoundError as e:
                    log(f"✘ {e}", "err")
                    root.after(0, lambda msg=str(e): messagebox.showerror("ExifTool 누락", msg))
                    return

                # 2. 이미지 목록 (드론 / DSLR 구분 수집)
                scope = "하위 폴더 포함" if recursive else "선택 폴더만"
                log(f"▶ 이미지 목록 수집 중… ({scope})", "info")
                try:
                    images, meta_by_path = collect_grouped_images(
                        drone_inputs=drone_list,
                        dslr_inputs=dslr_list,
                        recursive=recursive,
                    )
                except FileNotFoundError as e:
                    log(f"✘ {e}", "err")
                    return
                if not images:
                    log(f"✘ 지원 이미지 파일 없음", "err")
                    if not recursive:
                        log("  힌트: '하위 폴더까지 모두 검색' 옵션을 켜 보세요.", "warn")
                    return
                log(f"✔ 이미지 총 {len(images)}장 발견", "ok")
                set_progress(10)

                # 3. EXIF 추출
                log("▶ EXIF 데이터 추출 중… (잠시 기다려 주세요)", "info")
                raw_records, et_errors = run_exiftool(et, images)
                if et_errors:
                    for err in et_errors:
                        log(f"  ⚠ {err}", "warn")
                if not raw_records:
                    log("✘ EXIF 추출 실패", "err")
                    return

                records = []
                for r in raw_records:
                    sf = r.get("SourceFile", "")
                    meta = meta_by_path.get(sf, {})
                    records.append(
                        parse_record(
                            r,
                            camera_type=meta.get("camera_type"),
                            folder_label=meta.get("folder"),
                        )
                    )

                gps_ok = sum(1 for r in records if r["Lat_DD"] is not None)
                drone_cnt = sum(1 for r in records if r.get("CameraType") == CAMERA_TYPE_DRONE)
                dslr_cnt = sum(1 for r in records if r.get("CameraType") == CAMERA_TYPE_DSLR)
                dir_ok = sum(
                    1 for r in records
                    if r.get("CameraType") == CAMERA_TYPE_DRONE and r["CamDirection_deg"] is not None
                )
                log(
                    f"✔ GPS: {gps_ok}/{len(records)}장  |  "
                    f"드론: {drone_cnt}장 (방향 {dir_ok}장)  |  DSLR: {dslr_cnt}장",
                    "ok",
                )
                if gps_ok == 0:
                    log("⚠ GPS 좌표가 있는 사진이 없습니다. 지도는 생성되지 않습니다.", "warn")
                set_progress(45)

                # 4. 좌표 변환
                transformer = None
                used_crs = ""
                if HAS_PYPROJ and crs_code != "wgs84":
                    log(f"▶ 좌표 변환: WGS84 → {target_crs}", "info")
                    transformer = build_transformers(target_crs)
                    if transformer:
                        used_crs = target_crs
                        log("✔ 좌표 변환 준비 완료 (Proj_X=동향 E, Proj_Y=북향 N)", "ok")
                    else:
                        log(f"⚠ 좌표계 '{target_crs}' 초기화 실패 → 변환 없이 진행", "warn")
                elif not HAS_PYPROJ:
                    log("⚠ pyproj 미설치 → 좌표 변환 생략", "warn")
                set_progress(55)

                # 5. CSV 저장
                log(f"▶ CSV 저장: {out}", "info")
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                write_csv(records, out, transformer=transformer, target_crs=used_crs)
                log(f"✔ CSV 저장 완료 ({len(records)}개)", "ok")
                set_progress(65)

                # 6. GeoJSON 저장
                saved_geojson = ""
                if geojson_path:
                    log(f"▶ GeoJSON 저장: {geojson_path}", "info")
                    if write_geojson(records, geojson_path):
                        saved_geojson = geojson_path
                        log("✔ GeoJSON 저장 완료", "ok")
                    else:
                        log("⚠ GPS 데이터가 없어 GeoJSON을 만들지 못했습니다.", "warn")
                set_progress(70)

                # 7. 지도 생성 (썸네일 추출 진행률을 실시간으로 반영)
                saved_map = ""
                if map_path:
                    log(f"▶ 지도 생성: {map_path}", "info")

                    last_pct = [-1]

                    def thumb_progress(done, total):
                        set_progress(70 + 25 * done / total)
                        pct = done * 100 // total
                        if pct // 10 != last_pct[0] // 10:
                            last_pct[0] = pct
                            log(f"  🖼 썸네일 {done}/{total} ({pct}%)", "")

                    if write_map(records, map_path, thumb_size=thumb_size,
                                 progress=thumb_progress if thumb_size > 0 else None,
                                 exiftool_path=et):
                        saved_map = map_path
                        log("✔ 지도 저장 완료", "ok")
                    else:
                        log("⚠ GPS 데이터가 없어 지도를 만들지 못했습니다.", "warn")
                set_progress(95)

                # 8. 요약 (최대 MAX_SUMMARY_ROWS 행까지만 출력하여 대량 사진 시 UI 멈춤 방지)
                log("", "")
                log("─" * 64, "")
                log(f"{'파일명':<20} {'구분/폴더':<16} {'방향(°)':<8} {'방위':<4} {'위도':>11}", "info")
                log("─" * 64, "")
                for r in records[:MAX_SUMMARY_ROWS]:
                    lat_s = f"{r['Lat_DD']:.5f}" if r["Lat_DD"] is not None else "N/A"
                    is_drone = r.get("CameraType") == CAMERA_TYPE_DRONE
                    dir_s = (
                        f"{r['CamDirection_deg']:.1f}"
                        if (is_drone and r.get("CamDirection_deg") is not None)
                        else "-"
                    )
                    card  = (r.get("CamDirection_cardinal", "") or "-") if is_drone else "-"
                    cam_lbl = "드론" if is_drone else "DSLR"
                    grp_s = f"{cam_lbl}:{r.get('Folder', '기본')}"[:15]
                    fname = str(r["FileName"])[:19]
                    log(f"{fname:<20} {grp_s:<16} {dir_s:<8} {card:<4} {lat_s:>11}", "")
                if len(records) > MAX_SUMMARY_ROWS:
                    log(f"  … 외 {len(records) - MAX_SUMMARY_ROWS}개 레코드 (전체 내용은 CSV 참조)", "info")
                log("─" * 64, "")
                log("✅ 모든 작업 완료!", "ok")
                set_progress(100)

                # 완료 알림 (메인 스레드에서 실행)
                root.after(200, lambda: _done_dialog(out, saved_map, saved_geojson))

            except Exception as e:
                log(f"✘ 예상치 못한 오류: {e}", "err")
                import traceback
                log(traceback.format_exc(), "err")
            finally:
                root.after(0, lambda: run_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _done_dialog(csv_path, map_path="", geojson_path=""):
        lines = [f"CSV: {csv_path}"]
        if geojson_path:
            lines.append(f"GeoJSON: {geojson_path}")
        if map_path:
            lines.append(f"지도: {map_path}")
        msg = "저장이 완료되었습니다.\n\n" + "\n".join(lines)
        if messagebox.askyesno("완료!", msg + "\n\n출력 폴더를 열겠습니까?"):
            folder = str(Path(csv_path).parent)
            if not open_in_file_manager(folder):
                messagebox.showinfo("폴더 열기 실패",
                                    f"폴더를 자동으로 열지 못했습니다.\n직접 열어 주세요:\n{folder}")

    # ── 창 중앙 배치 ────────────────────────────────────────────────────────
    root.update_idletasks()
    w, h = 820, 680
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    # 150ms 후 스플래시 창 파괴 및 메인 창 표출
    def show_main():
        try:
            splash.destroy()
        except Exception:
            pass
        root.deiconify()

    root.after(150, show_main)

    # 창을 닫을 때 현재 입력값을 저장해 다음 실행에서 그대로 복원되게 한다.
    def on_close():
        settings.update({
            "drone_input": v_drone_input.get().strip(),
            "dslr_input": v_dslr_input.get().strip(),
            "output": v_output.get().strip(),
            "crs": next((c[1] for c in CRS_OPTIONS if c[0] == v_crs.get()), "tm"),
            "make_map": v_make_map.get(),
            "map_path": v_map_path.get().strip(),
            "recursive": v_recursive.get(),
            "geojson": v_geojson.get(),
            "thumbs": v_thumbs.get(),
        })
        save_settings(settings)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


# ════════════════════════════════════════════════════════════════════════════
#  CLI 모드 (archaeo_gps.py의 main() 그대로 재사용)
# ════════════════════════════════════════════════════════════════════════════

def run_cli():
    from archaeo_gps import main as cli_main
    cli_main()


# ════════════════════════════════════════════════════════════════════════════
#  진입점 — 인자 없으면 GUI, 있으면 CLI
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # sys.argv[0]은 스크립트명이므로 1개 이하면 인자 없음 = GUI
    if len(sys.argv) <= 1:
        run_gui()
    else:
        run_cli()
