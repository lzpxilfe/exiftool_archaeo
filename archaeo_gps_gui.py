#!/usr/bin/env python3
"""
archaeo_gps_gui.py — 고고학 현장 사진 GPS·방향 추출 도구 (GUI + CLI 통합)
더블클릭 실행 → GUI 창
인자 전달 실행 → CLI 모드 (기존 archaeo_gps.py 동작 유지)
"""

import sys
import os
from pathlib import Path

# ── Windows 콘솔 UTF-8 ───────────────────────────────────────────────────────
if sys.platform == "win32":
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except AttributeError:
        pass  # GUI 모드에서 stdout.buffer 없을 수 있음

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
    find_exiftool, collect_images, run_exiftool, parse_record,
    write_csv, write_map, build_transformers, CRS_PRESETS,
    HAS_PANDAS, HAS_PYPROJ,
)


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

    root = tk.Tk()
    root.title("ExifTool Archaeo — 고고학 현장 GPS 추출기")
    root.configure(bg=BG)
    root.resizable(True, True)
    root.minsize(700, 580)

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
    tk.Label(header, text="고고학 현장 사진 GPS·방향 추출 도구  v1.0",
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
                 bg=PANEL, fg=ACCENT).grid(row=0, column=0, columnspan=3,
                                            sticky="w", pady=(0, 8))
        return f

    def make_row(parent, label, row, browse_fn=None, browse_text="폴더 선택"):
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 10))
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=50)
        entry.grid(row=row, column=1, sticky="ew", pady=2)
        if browse_fn:
            ttk.Button(parent, text=browse_text, style="Browse.TButton",
                       command=lambda: var.set(browse_fn())).grid(
                       row=row, column=2, padx=(6, 0))
        return var

    # § 섹션 1 — 입력 / 출력
    sec1 = make_section(body, "📂  입력 / 출력", 0)

    def browse_input():
        d = filedialog.askdirectory(title="사진 폴더 선택")
        return d or ""

    def browse_output():
        f = filedialog.asksaveasfilename(
            title="CSV 저장 위치", defaultextension=".csv",
            filetypes=[("CSV 파일", "*.csv"), ("모든 파일", "*.*")])
        return f or ""

    def browse_map():
        f = filedialog.asksaveasfilename(
            title="지도 HTML 저장 위치", defaultextension=".html",
            filetypes=[("HTML 파일", "*.html"), ("모든 파일", "*.*")])
        return f or ""

    v_input  = make_row(sec1, "사진 폴더", 1, browse_input, "폴더 선택")
    v_output = make_row(sec1, "출력 CSV",  2, browse_output, "저장 위치")

    # § 섹션 2 — 설정
    sec2 = make_section(body, "⚙️  설정", 1)

    # CRS 선택
    ttk.Label(sec2, text="좌표계 변환", style="Panel.TLabel").grid(
        row=1, column=0, sticky="w", padx=(0, 10))
    crs_frame = ttk.Frame(sec2, style="Panel.TFrame")
    crs_frame.grid(row=1, column=1, columnspan=2, sticky="w", pady=2)

    CRS_OPTIONS = [
        ("TM중부원점  EPSG:5186  [기본, 한국 표준]", "tm"),
        ("UTM-K  EPSG:5179",                         "utmk"),
        ("UTM Zone 52N  EPSG:32652",                 "utm52n"),
        ("WGS84  EPSG:4326  [변환 없음]",            "wgs84"),
    ]
    v_crs = tk.StringVar(value="tm")
    cb_crs = ttk.Combobox(crs_frame, textvariable=v_crs, width=38,
                          state="readonly",
                          values=[c[0] for c in CRS_OPTIONS])
    cb_crs.current(0)
    cb_crs.pack(side="left")

    # 지도 생성 체크박스 + 경로
    ttk.Label(sec2, text="지도 생성", style="Panel.TLabel").grid(
        row=2, column=0, sticky="w", padx=(0, 10), pady=(6, 0))
    map_frame = ttk.Frame(sec2, style="Panel.TFrame")
    map_frame.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(6, 0))
    map_frame.columnconfigure(1, weight=1)

    v_make_map = tk.BooleanVar(value=True)
    ttk.Checkbutton(map_frame, text="HTML 지도 생성 (Leaflet)",
                    variable=v_make_map,
                    style="TCheckbutton").grid(row=0, column=0, sticky="w")
    v_map_path = tk.StringVar()
    ttk.Entry(map_frame, textvariable=v_map_path, width=32).grid(
        row=0, column=1, sticky="ew", padx=(8, 4))
    ttk.Button(map_frame, text="저장 위치", style="Browse.TButton",
               command=lambda: v_map_path.set(browse_map())).grid(
               row=0, column=2)

    # exiftool 경로
    def browse_exiftool():
        f = filedialog.askopenfilename(
            title="exiftool.exe 선택",
            filetypes=[("실행 파일", "*.exe"), ("모든 파일", "*.*")])
        return f or ""

    v_exiftool = make_row(sec2, "ExifTool 경로", 3, browse_exiftool, "찾아보기")
    # 자동 탐색
    try:
        auto_et = find_exiftool(None)
        v_exiftool.set(auto_et)
    except FileNotFoundError:
        pass
    ttk.Label(sec2, text="(비워두면 자동 탐색 — 스크립트 폴더, PATH 순으로)",
              style="Sub.TLabel", background=PANEL).grid(
              row=4, column=1, sticky="w")

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
        root.after(100, drain_queue)

    root.after(100, drain_queue)

    # ── 실행 로직 ───────────────────────────────────────────────────────────
    run_btn = ttk.Button(btn_frame, text="  ▶  추출 실행  ",
                         style="Run.TButton", command=lambda: start_run())
    run_btn.pack()

    def start_run():
        # 입력 검증
        inp = v_input.get().strip()
        out = v_output.get().strip()
        if not inp:
            messagebox.showwarning("입력 오류", "사진 폴더를 선택해 주세요.")
            return
        if not out:
            messagebox.showwarning("입력 오류", "출력 CSV 저장 위치를 선택해 주세요.")
            return

        # 지도 경로 자동 설정
        map_path = ""
        if v_make_map.get():
            map_path = v_map_path.get().strip()
            if not map_path:
                map_path = str(Path(out).with_suffix(".html"))
                v_map_path.set(map_path)

        # CRS 결정
        crs_label = v_crs.get()
        crs_code = next((c[1] for c in CRS_OPTIONS if c[0] == crs_label), "tm")
        target_crs = CRS_PRESETS.get(crs_code, crs_code)

        et_path_hint = v_exiftool.get().strip() or None

        run_btn.configure(state="disabled")
        progress_var.set(0)
        log_text.configure(state="normal")
        log_text.delete("1.0", "end")
        log_text.configure(state="disabled")

        def worker():
            try:
                # 1. exiftool 탐색
                log("▶ ExifTool 탐색 중…", "info")
                try:
                    et = find_exiftool(et_path_hint)
                    log(f"✔ ExifTool: {et}", "ok")
                except FileNotFoundError as e:
                    log(f"✘ {e}", "err")
                    return

                # 2. 이미지 목록
                log("▶ 이미지 목록 수집 중…", "info")
                try:
                    images = collect_images(inp)
                except FileNotFoundError as e:
                    log(f"✘ {e}", "err")
                    return
                if not images:
                    log(f"✘ 지원 이미지 파일 없음: {inp}", "err")
                    return
                log(f"✔ 이미지 {len(images)}장 발견", "ok")
                progress_var.set(10)

                # 3. EXIF 추출
                log("▶ EXIF 데이터 추출 중… (잠시 기다려 주세요)", "info")
                raw_records = run_exiftool(et, images)
                if not raw_records:
                    log("✘ EXIF 추출 실패", "err")
                    return

                records = [parse_record(r) for r in raw_records]
                gps_ok = sum(1 for r in records if r["Lat_DD"] is not None)
                dir_ok = sum(1 for r in records if r["CamDirection_deg"] is not None)
                log(f"✔ GPS: {gps_ok}/{len(records)}장  방향: {dir_ok}/{len(records)}장", "ok")
                progress_var.set(50)

                # 4. 좌표 변환
                transformer = None
                if HAS_PYPROJ and crs_code != "wgs84":
                    log(f"▶ 좌표 변환: WGS84 → {target_crs}", "info")
                    transformer = build_transformers(target_crs)
                    if transformer:
                        log(f"✔ 좌표 변환 준비 완료", "ok")
                elif not HAS_PYPROJ:
                    log("⚠ pyproj 미설치 → 좌표 변환 생략", "warn")
                progress_var.set(65)

                # 5. CSV 저장
                log(f"▶ CSV 저장: {out}", "info")
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                write_csv(records, out, transformer=transformer, target_crs=target_crs)
                log(f"✔ CSV 저장 완료 ({len(records)}개)", "ok")
                progress_var.set(80)

                # 6. 지도 생성
                if map_path:
                    log(f"▶ 지도 생성: {map_path}", "info")
                    write_map(records, map_path)
                    log(f"✔ 지도 저장 완료", "ok")
                progress_var.set(95)

                # 7. 요약
                log("", "")
                log("─" * 52, "")
                log(f"{'파일명':<22} {'방향(°)':<10} {'방향':<5} {'위도':>11}", "info")
                log("─" * 52, "")
                for r in records:
                    lat_s = f"{r['Lat_DD']:.5f}" if r["Lat_DD"] is not None else "N/A"
                    dir_s = f"{r['CamDirection_deg']:.1f}" if r["CamDirection_deg"] is not None else "N/A"
                    card  = r.get("CamDirection_cardinal", "")
                    fname = r["FileName"][:20]
                    log(f"{fname:<22} {dir_s:<10} {card:<5} {lat_s:>11}", "")
                log("─" * 52, "")
                log("✅ 모든 작업 완료!", "ok")
                progress_var.set(100)

                # 완료 알림 (메인 스레드에서 실행)
                root.after(200, lambda: _done_dialog(out, map_path))

            except Exception as e:
                log(f"✘ 예상치 못한 오류: {e}", "err")
                import traceback
                log(traceback.format_exc(), "err")
            finally:
                root.after(0, lambda: run_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _done_dialog(csv_path, map_path):
        msg = f"CSV 저장 완료:\n{csv_path}"
        if map_path:
            msg += f"\n\n지도 저장 완료:\n{map_path}"
        if messagebox.askyesno("완료!", msg + "\n\n출력 폴더를 열겠습니까?"):
            folder = str(Path(csv_path).parent)
            os.startfile(folder)

    # ── 창 중앙 배치 ────────────────────────────────────────────────────────
    root.update_idletasks()
    w, h = 780, 650
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

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
