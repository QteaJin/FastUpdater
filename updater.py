"""FastUpdater — мини-утилита обновления программ через winget (Tkinter, только stdlib)."""
from __future__ import annotations

import ctypes
import fnmatch
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

# ───────────────────────── Константы и пути ─────────────────────────

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
LEARNED_PATH = BASE_DIR / "learned_admin.json"
LOG_DIR = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "UpdateLogs"

DEFAULT_CONFIG = {
    "exclude": ["Python.Launcher"],
    "admin_required": ["Microsoft.VCRedist.*", "Microsoft.Edge", "Logitech.LogiTune"],
    "include_admin_in_update_all": False,
    "language": "en",
}

PER_PACKAGE_TIMEOUT = 600  # 10 минут на одну программу
CREATE_NO_WINDOW = 0x08000000
ID_RE = re.compile(r"^[A-Za-z0-9.+_-]+$")
SEP_RE = re.compile(r"^-{10,}\s*$")
SPINNER_RE = re.compile(r"^[\s\-\\|/]*$")
PROGRESS_RE = re.compile(r"\d+(\.\d+)?\s*[KMG]?i?B\s*/\s*\d|^\s*\d+\s*%\s*$")
FOOTER_RE = re.compile(r"^\s*\d+\s+\D+\.\s*$")
VERSION_PREFIX_RE = re.compile(r"^([<>~]=?)\s*(.*)$")

# Результат → (тег цвета, подпись в логе; лог всегда на английском). Подпись в таблице: ключ "res_<kind>"
RESULTS = {
    "success": ("ok", "SUCCESS"),
    "error": ("err", "ERROR"),
    "not_applicable": ("muted", "NOT APPLICABLE"),
    "admin_needed": ("warn", "ADMIN NEEDED"),
    "running": ("warn", "ERROR (RUNNING?)"),
    "timeout": ("err", "TIMEOUT"),
    "skipped": ("muted", "SKIPPED"),
}
FAILURE_KINDS = {"error", "timeout", "admin_needed", "running"}


# ───────────────────────── Локализация ─────────────────────────

LANGS = ("en", "ru")
LANG = "en"

STRINGS = {
    "en": {
        "theme": "◐ Theme", "check": "⟳  Check for updates", "update_all": "Update all",
        "update_sel": "⬆  Update selected", "open_log": "Open log", "select_all": "Select all",
        "col_name": "Name", "col_id": "ID", "col_cur": "Current version", "col_avail": "Available version",
        "col_src": "Source", "col_status": "Status",
        "starting": "Starting…", "checking": "Checking for updates…", "nothing_selected": "Nothing selected",
        "nothing_to_update": "Nothing to update",
        "count": "{n} updates",
        "st_available": "available", "st_admin": "admin required", "st_unknown_ver": "version unknown",
        "st_waiting": "waiting", "st_updating": "updating…", "st_admin_skipped": "admin required (skipped)",
        "res_success": "success", "res_error": "error", "res_not_applicable": "not applicable",
        "res_admin_needed": "admin required", "res_running": "may be running, close it and retry",
        "res_timeout": "timeout", "res_skipped": "skipped",
        "done": "Done: updated {ok}, not applicable {na}, failed {failed}",
        "see_log": " — see log for details", "rechecking": " · rechecking list…",
        "remaining": "Updates remaining: {n}", "found": "Updates found: {n}",
        "all_current": "All programs are up to date",
        "nowinget": "winget not found",
        "nowinget_msg": "winget not found.\nInstall \"App Installer\" from the Microsoft Store.",
        "check_timeout": "Timeout while checking for updates (no network?)",
        "parse_fail": "Could not parse winget output — showing raw output",
        "internal_error": "Internal error, see log for details",
        "raw_title": "Raw winget output",
        "lang_btn": "RU",
    },
    "ru": {
        "theme": "◐ Тема", "check": "⟳  Проверить обновления", "update_all": "Обновить все",
        "update_sel": "⬆  Обновить выбранные", "open_log": "Открыть лог", "select_all": "Выбрать все",
        "col_name": "Имя", "col_id": "ID", "col_cur": "Текущая версия", "col_avail": "Доступная версия",
        "col_src": "Источник", "col_status": "Статус",
        "starting": "Запуск…", "checking": "Проверяю обновления…", "nothing_selected": "Ничего не выбрано",
        "nothing_to_update": "Нечего обновлять",
        "count": "{n} обновлений",
        "st_available": "доступно", "st_admin": "нужен админ", "st_unknown_ver": "версия не определена",
        "st_waiting": "ожидает", "st_updating": "обновляется…", "st_admin_skipped": "нужен админ (пропущено)",
        "res_success": "успех", "res_error": "ошибка", "res_not_applicable": "не применимо",
        "res_admin_needed": "нужен админ", "res_running": "возможно, запущена, закройте и повторите",
        "res_timeout": "таймаут", "res_skipped": "пропущено",
        "done": "Готово: обновлено {ok}, не применимо {na}, не удалось {failed}",
        "see_log": " — подробности в логе", "rechecking": " · перепроверяю список…",
        "remaining": "Осталось обновлений: {n}", "found": "Найдено обновлений: {n}",
        "all_current": "Все программы актуальны",
        "nowinget": "winget не найден",
        "nowinget_msg": "winget не найден.\nУстановите «Установщик приложений» (App Installer) из Microsoft Store.",
        "check_timeout": "Таймаут при проверке обновлений (нет сети?)",
        "parse_fail": "Не удалось разобрать вывод winget — показан сырой вывод",
        "internal_error": "Внутренняя ошибка, подробности в логе",
        "raw_title": "Сырой вывод winget",
        "lang_btn": "EN",
    },
}


def t(key: str, **kw) -> str:
    text = STRINGS.get(LANG, STRINGS["en"]).get(key) or STRINGS["en"][key]
    return text.format(**kw) if kw else text


def t_multi(keys: str) -> str:
    """Несколько ключей через «|» → строки через « · »."""
    return " · ".join(t(k) for k in keys.split("|"))


# ───────────────────────── Конфиг ─────────────────────────

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_config() -> dict:
    """Читает config.json; если файла нет — создаёт со значениями по умолчанию."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        return dict(DEFAULT_CONFIG)
    cfg = dict(DEFAULT_CONFIG)
    data = load_json(CONFIG_PATH, {})
    if isinstance(data, dict):
        cfg.update(data)
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_learned() -> set[str]:
    data = load_json(LEARNED_PATH, [])
    return {str(x) for x in data} if isinstance(data, list) else set()


def save_learned(ids: set[str]) -> None:
    LEARNED_PATH.write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=2), encoding="utf-8")


def matches_any(pkg_id: str, patterns) -> bool:
    return any(fnmatch.fnmatch(pkg_id.lower(), str(p).lower()) for p in patterns)


# ───────────────────────── Лог ─────────────────────────

class SessionLog:
    """Читаемый лог за день + failures.jsonl для разбора вместе с Claude."""

    def __init__(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOG_DIR / f"update-{datetime.now():%Y-%m-%d}.log"
        self.failures_path = LOG_DIR / "failures.jsonl"
        self.logger = logging.getLogger("fastupdater")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.path, mode="a", encoding="utf-8")
            handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
            self.logger.addHandler(handler)

    def info(self, msg: str) -> None:
        self.logger.info(msg)

    def exception(self, where: str) -> None:
        self.logger.exception("Exception: %s", where)

    def result(self, pkg: "Pkg", kind: str, rc, lines: list[str], cmd: list[str]) -> None:
        label = RESULTS[kind][1]
        tail = "\n".join("    " + ln for ln in lines[-30:])
        self.info(f"{pkg.id} | {pkg.version} -> {pkg.available} | {label} | return code: {rc}\n{tail}")
        if kind in FAILURE_KINDS:
            rec = {
                "time": datetime.now().isoformat(timespec="seconds"),
                "id": pkg.id, "name": pkg.name, "source": pkg.source,
                "from": pkg.version, "to": pkg.available,
                "result": kind, "return_code": rc, "command": cmd, "output": lines,
            }
            with open(self.failures_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def summary(self, done: int, ok: int, failed: list[str], na: int) -> None:
        self.info(f"=== SESSION SUMMARY: processed {done}, updated {ok}, not applicable {na}, "
                  f"failed {len(failed)}; problem IDs: {', '.join(failed) or '-'} ===")


# ───────────────────────── Модель и парсер ─────────────────────────

@dataclass
class Pkg:
    name: str
    id: str
    version: str
    available: str
    source: str
    version_unknown: bool = False
    admin: bool = False


def clean_lines(text: str) -> list[str]:
    """Убирает перезаписи через \\r, спиннер и строки прогресса."""
    out = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        segs = [s for s in raw.split("\r") if s.strip()]
        line = (segs[-1] if segs else "").rstrip()
        if not line:
            out.append("")
            continue
        if SEP_RE.match(line):
            out.append(line)
            continue
        if "█" in line or "▒" in line or SPINNER_RE.match(line) or PROGRESS_RE.search(line):
            continue
        out.append(line)
    return out


def _find_columns(header: str, rows: list[str]) -> list[int]:
    """Границы колонок = начала слов заголовка, перед которыми во всех строках пробел."""
    cols = []
    for m in re.finditer(r"(?<!\S)\S", header):
        s = m.start()
        if s == 0 or all(len(r) <= s or r[s - 1] == " " for r in rows):
            cols.append(s)
    return cols


def _make_pkg(name: str, pid: str, ver: str, avail: str, src: str) -> Pkg | None:
    name, pid, ver, avail, src = (x.strip() for x in (name, pid, ver, avail, src))
    if not ID_RE.match(pid) or not ver:
        return None
    unknown = False
    m = VERSION_PREFIX_RE.match(ver)
    if m:
        unknown = True
    return Pkg(name or pid, pid, ver, avail, src, version_unknown=unknown)


def _parse_row_by_tokens(line: str) -> Pkg | None:
    """Запасной разбор справа налево: Source, Available, Version, Id, остальное — имя."""
    tok = line.split()
    if len(tok) < 5:
        return None
    src, avail, ver, idx = tok[-1], tok[-2], tok[-3], -3
    if tok[-4] in ("<", ">", "~", "<=", ">="):
        ver, idx = tok[-4] + " " + tok[-3], -4
    if len(tok) + idx - 1 < 0:
        return None
    return _make_pkg(" ".join(tok[:idx - 1]), tok[idx - 1], ver, avail, src)


def parse_winget_table(text: str) -> tuple[list[Pkg], bool]:
    """Парсит вывод `winget upgrade`. Возвращает (пакеты, таблица_найдена)."""
    lines = clean_lines(text)
    pkgs: list[Pkg] = []
    found = False
    i = 0
    while i < len(lines):
        if not SEP_RE.match(lines[i]):
            i += 1
            continue
        header = lines[i - 1] if i > 0 else ""
        if not header.strip():
            i += 1
            continue
        found = True
        rows = []
        j = i + 1
        while j < len(lines) and lines[j] and not SEP_RE.match(lines[j]):
            if not FOOTER_RE.match(lines[j]) or "  " in lines[j]:
                rows.append(lines[j])
            j += 1
        cols = _find_columns(header, rows)
        for r in rows:
            pkg = None
            if len(cols) in (4, 5):
                parts = [r[a:b] for a, b in zip(cols, cols[1:] + [len(r) + 1])]
                parts += [""] * (5 - len(parts))
                pkg = _make_pkg(*parts[:5])
            if pkg is None:
                pkg = _parse_row_by_tokens(r)
            if pkg:
                pkgs.append(pkg)
        i = j
    return pkgs, found


# ───────────────────────── Запуск winget ─────────────────────────

def decode(data) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def run_winget(args: list[str], timeout: int) -> tuple[int | None, str]:
    """Запускает winget без shell. Возвращает (код возврата | None при таймауте, вывод)."""
    cmd = ["winget"] + args
    try:
        p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return p.returncode & 0xFFFFFFFF, decode(p.stdout) + decode(p.stderr)
    except subprocess.TimeoutExpired as e:
        return None, decode(e.stdout) + decode(e.stderr)


def classify(rc: int | None, output: str) -> str:
    """Определяет тип результата установки по коду возврата и тексту."""
    if rc is None:
        return "timeout"
    low = output.lower()
    if rc == 0x8A15002B or "no applicable upgrade found" in low or "no newer package versions" in low:
        return "not_applicable"
    if (rc in (740, 1223) or "requires administrator" in low or "require administrator" in low
            or "administrator privileges" in low or "elevat" in low and "required" in low
            or "cancelled by the user" in low or "canceled by the user" in low):
        return "admin_needed"
    if rc == 0:
        return "success"
    if any(s in low for s in ("currently running", "is running", "in use", "close the application",
                              "being used by another process")):
        return "running"
    return "error"


# ───────────────────────── Тема ─────────────────────────

LIGHT = dict(bg="#f3f4f6", card="#ffffff", fg="#1f2937", muted="#6b7280", accent="#2563eb",
             accent_hover="#1d4ed8", accent_fg="#ffffff", border="#e5e7eb", sel="#dbeafe",
             head="#f9fafb", btn="#e5e7eb", btn_hover="#d1d5db",
             ok="#15803d", err="#b91c1c", warn="#b45309")
DARK = dict(bg="#0f172a", card="#1e293b", fg="#f1f5f9", muted="#94a3b8", accent="#3b82f6",
            accent_hover="#60a5fa", accent_fg="#ffffff", border="#334155", sel="#1e3a8a",
            head="#172033", btn="#334155", btn_hover="#475569",
            ok="#4ade80", err="#f87171", warn="#fbbf24")


def system_is_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


# ───────────────────────── Приложение ─────────────────────────

class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = load_config()
        global LANG
        LANG = self.cfg.get("language") if self.cfg.get("language") in LANGS else "en"
        self.learned = load_learned()
        self.log = SessionLog()
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.pkgs: list[Pkg] = []
        self.checked: dict[str, bool] = {}
        self.last_status: dict[str, tuple[str, str]] = {}  # pid -> (ключ строки, тег)
        self.msg = lambda: t("starting")  # сообщение в строке состояния; пересчитывается при смене языка
        self.final_msg = None
        self.dark = system_is_dark()
        self.pal = DARK if self.dark else LIGHT

        root.title("FastUpdater")
        root.geometry("1100x640")
        root.minsize(860, 460)
        root.report_callback_exception = self._on_tk_error
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)

        self._build_ui()
        self.apply_theme()
        root.after(100, self._poll)
        root.after(200, self.check_updates)
        self.log.info("--- FastUpdater started ---")

    # ----- UI -----

    def _build_ui(self) -> None:
        r = self.root
        self.style = ttk.Style(r)
        self.style.theme_use("clam")

        self.outer = ttk.Frame(r, style="App.TFrame", padding=(20, 16))
        self.outer.pack(fill="both", expand=True)

        head = ttk.Frame(self.outer, style="App.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="FastUpdater", style="Title.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(head, text="", style="Muted.TLabel")
        self.count_lbl.pack(side="left", padx=(14, 0), pady=(6, 0))
        self.btn_theme = ttk.Button(head, style="Ghost.TButton", command=self.toggle_theme)
        self.btn_theme.pack(side="right")
        self.btn_lang = ttk.Button(head, style="Ghost.TButton", command=self.toggle_lang)
        self.btn_lang.pack(side="right", padx=(0, 4))

        bar = ttk.Frame(self.outer, style="App.TFrame")
        bar.pack(fill="x", pady=(14, 10))
        self.btn_check = ttk.Button(bar, style="TButton", command=self.check_updates)
        self.btn_all = ttk.Button(bar, style="TButton", command=self.update_all)
        self.btn_sel = ttk.Button(bar, style="Accent.TButton", command=self.update_selected)
        self.btn_log = ttk.Button(bar, style="Ghost.TButton", command=self.open_log)
        self.btn_check.pack(side="left")
        self.btn_all.pack(side="left", padx=8)
        self.btn_sel.pack(side="left")
        self.btn_log.pack(side="right")
        self.all_var = tk.BooleanVar(value=False)
        self.chk_all = ttk.Checkbutton(bar, variable=self.all_var,
                                       style="App.TCheckbutton", command=self.toggle_all)
        self.chk_all.pack(side="right", padx=16)

        card = ttk.Frame(self.outer, style="Card.TFrame", padding=1)
        card.pack(fill="both", expand=True)
        cols = ("name", "id", "cur", "avail", "src", "status")
        self.tree = ttk.Treeview(card, columns=cols, show="tree headings", selectmode="browse")
        heads = {"#0": ("", 44), "name": ("col_name", 270), "id": ("col_id", 230), "cur": ("col_cur", 130),
                 "avail": ("col_avail", 130), "src": ("col_src", 80), "status": ("col_status", 220)}
        self.head_keys = {c: k for c, (k, _) in heads.items() if k}
        for col, (_key, width) in heads.items():
            self.tree.heading(col, anchor="w")
            self.tree.column(col, width=width, minwidth=40, anchor="w", stretch=(col in ("name", "status")))
        self.tree.column("#0", stretch=False)
        sb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)

        foot = ttk.Frame(self.outer, style="App.TFrame")
        foot.pack(fill="x", pady=(12, 0))
        self.status_var = tk.StringVar()
        ttk.Label(foot, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        self.progress = ttk.Progressbar(foot, style="App.Horizontal.TProgressbar", length=260, mode="determinate")
        self.progress.pack(side="right")
        self.retranslate()

    def say(self, fn) -> None:
        """Показывает сообщение в строке состояния; fn() вызывается снова при смене языка."""
        self.msg = fn
        self.status_var.set(fn())

    def retranslate(self) -> None:
        self.btn_theme.configure(text=t("theme"))
        self.btn_lang.configure(text=t("lang_btn"))
        self.btn_check.configure(text=t("check"))
        self.btn_all.configure(text=t("update_all"))
        self.btn_sel.configure(text=t("update_sel"))
        self.btn_log.configure(text=t("open_log"))
        self.chk_all.configure(text=t("select_all"))
        for col, key in self.head_keys.items():
            self.tree.heading(col, text=t(key))
        for i, pkg in enumerate(self.pkgs):
            if self.tree.exists(str(i)):
                self.tree.set(str(i), "status", t_multi(self._status_key(pkg)[0]))
        self.count_lbl.configure(text=t("count", n=len(self.pkgs)) if self.pkgs else "")
        self.status_var.set(self.msg())

    def toggle_lang(self) -> None:
        global LANG
        LANG = "ru" if LANG == "en" else "en"
        self.cfg["language"] = LANG
        save_config(self.cfg)
        self.retranslate()

    def apply_theme(self) -> None:
        p, s = self.pal, self.style
        self.root.configure(bg=p["bg"])
        s.configure(".", background=p["bg"], foreground=p["fg"], font=("Segoe UI", 10))
        s.configure("App.TFrame", background=p["bg"])
        s.configure("Card.TFrame", background=p["border"])
        s.configure("Title.TLabel", background=p["bg"], foreground=p["fg"], font=("Segoe UI Semibold", 18))
        s.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
        s.configure("App.TCheckbutton", background=p["bg"], foreground=p["fg"], focuscolor=p["bg"])
        s.map("App.TCheckbutton", background=[("active", p["bg"])])
        for name, bg, hov, fg in (("TButton", p["btn"], p["btn_hover"], p["fg"]),
                                  ("Accent.TButton", p["accent"], p["accent_hover"], p["accent_fg"]),
                                  ("Ghost.TButton", p["bg"], p["btn"], p["muted"])):
            s.configure(name, background=bg, foreground=fg, borderwidth=0, focusthickness=0,
                        focuscolor=bg, padding=(16, 8), relief="flat", font=("Segoe UI Semibold", 10))
            s.map(name, background=[("disabled", p["btn"] if name != "Ghost.TButton" else p["bg"]),
                                    ("active", hov)],
                  foreground=[("disabled", p["muted"])])
        s.configure("Treeview", background=p["card"], fieldbackground=p["card"], foreground=p["fg"],
                    borderwidth=0, rowheight=int(34 * self.scale), font=("Segoe UI", 10))
        s.map("Treeview", background=[("selected", p["sel"])], foreground=[("selected", p["fg"])])
        s.configure("Treeview.Heading", background=p["head"], foreground=p["muted"], relief="flat",
                    borderwidth=0, padding=(8, 8), font=("Segoe UI Semibold", 9))
        s.map("Treeview.Heading", background=[("active", p["head"])])
        s.configure("Vertical.TScrollbar", background=p["btn"], troughcolor=p["card"],
                    bordercolor=p["card"], arrowcolor=p["muted"], relief="flat")
        s.configure("App.Horizontal.TProgressbar", background=p["accent"], troughcolor=p["btn"],
                    bordercolor=p["bg"], lightcolor=p["accent"], darkcolor=p["accent"], thickness=8)
        for tag, key in (("ok", "ok"), ("err", "err"), ("warn", "warn"), ("muted", "muted"), ("run", "accent")):
            self.tree.tag_configure(tag, foreground=p[key])
        self._set_titlebar_dark()

    def _set_titlebar_dark(self) -> None:
        """Тёмный/светлый заголовок окна Windows 10/11."""
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            val = ctypes.c_int(1 if self.dark else 0)
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass

    def toggle_theme(self) -> None:
        self.dark = not self.dark
        self.pal = DARK if self.dark else LIGHT
        self.apply_theme()

    # ----- Таблица -----

    def _box(self, iid: str) -> str:
        return "☑" if self.checked.get(iid) else "☐"

    def _status_key(self, pkg: Pkg) -> tuple[str, str]:
        """(ключ строки статуса; несколько через «|», тег цвета)."""
        if pkg.id in self.last_status:
            return self.last_status[pkg.id]
        notes = (["st_admin"] if pkg.admin else []) + (["st_unknown_ver"] if pkg.version_unknown else [])
        return ("|".join(notes) or "st_available", "warn" if notes else "")

    def populate(self, pkgs: list[Pkg]) -> None:
        self.tree.delete(*self.tree.get_children())
        self.pkgs = pkgs
        self.checked = {}
        for i, pkg in enumerate(pkgs):
            iid = str(i)
            self.checked[iid] = not (pkg.admin or pkg.version_unknown)
            key, tag = self._status_key(pkg)
            self.tree.insert("", "end", iid=iid, text=self._box(iid), tags=(tag,) if tag else (),
                             values=(pkg.name, pkg.id, pkg.version, pkg.available, pkg.source, t_multi(key)))
        self.count_lbl.configure(text=t("count", n=len(pkgs)) if pkgs else "")
        self.all_var.set(False)

    def set_status(self, iid: str, key: str, tag: str) -> None:
        if self.tree.exists(iid):
            self.tree.set(iid, "status", t_multi(key))
            self.tree.item(iid, tags=(tag,) if tag else ())
            self.tree.see(iid)

    def _toggle(self, iid: str) -> None:
        if self.busy or not self.tree.exists(iid):
            return
        self.checked[iid] = not self.checked.get(iid)
        self.tree.item(iid, text=self._box(iid))

    def _on_click(self, e) -> None:
        if self.tree.identify_column(e.x) == "#0":
            iid = self.tree.identify_row(e.y)
            if iid:
                self._toggle(iid)

    def _on_space(self, _e) -> None:
        for iid in self.tree.selection():
            self._toggle(iid)

    def toggle_all(self) -> None:
        if self.busy:
            return
        for iid in self.checked:
            self.checked[iid] = self.all_var.get()
            self.tree.item(iid, text=self._box(iid))

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_check, self.btn_all, self.btn_sel, self.chk_all):
            b.configure(state=state)

    # ----- Проверка списка -----

    def check_updates(self) -> None:
        if self.busy:
            return
        self.last_status = {}
        self.set_busy(True)
        self.say(lambda: t("checking"))
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        threading.Thread(target=self._guard(self._check_worker), daemon=True).start()

    def _fetch_list(self):
        """Возвращает ('ok', пакеты) | ('nowinget',) | ('raw', текст) | ('timeout',)."""
        if not shutil.which("winget"):
            return ("nowinget",)
        rc, out = run_winget(["upgrade", "--accept-source-agreements"], 180)
        if rc is None:
            return ("timeout",)
        pkgs, found = parse_winget_table(out)
        if not found:
            lines = [l for l in clean_lines(out) if l.strip()]
            if len(lines) <= 3 and rc in (0, 0x8A150014):
                return ("ok", [])  # «Нет доступных обновлений»
            self.log.info(f"Could not parse winget upgrade output (rc={rc}). Raw output:\n{out}")
            return ("raw", out)
        pkgs = [p for p in pkgs if not matches_any(p.id, self.cfg["exclude"])]
        for p in pkgs:
            p.admin = matches_any(p.id, self.cfg["admin_required"]) or p.id in self.learned
        return ("ok", pkgs)

    def _check_worker(self) -> None:
        self.events.put(("list", self._fetch_list()))

    # ----- Обновление -----

    def _selected(self) -> list[Pkg]:
        return [self.pkgs[int(i)] for i, v in self.checked.items() if v]

    def update_selected(self) -> None:
        pkgs = self._selected()
        if not pkgs:
            self.say(lambda: t("nothing_selected"))
            return
        self._start_update(pkgs, [], False)

    def update_all(self) -> None:
        include_admin = bool(self.cfg.get("include_admin_in_update_all"))
        run = [p for p in self.pkgs if include_admin or not p.admin]
        skipped = [p for p in self.pkgs if p not in run]
        if not run:
            self.say(lambda: t("nothing_to_update"))
            return
        self._start_update(run, skipped, True)

    def _start_update(self, run: list[Pkg], skipped: list[Pkg], recheck: bool) -> None:
        if self.busy:
            return
        # VCRedist — первыми: другим пакетам нужны свежие зависимости (сортировка устойчивая)
        run = sorted(run, key=lambda p: not p.id.lower().startswith("microsoft.vcredist."))
        self.set_busy(True)
        self.progress.configure(mode="determinate", maximum=len(run), value=0)
        for p in run:
            self.set_status(str(self.pkgs.index(p)), "st_waiting", "muted")
        threading.Thread(target=self._guard(self._update_worker), args=(run, skipped, recheck),
                         daemon=True).start()

    def _update_worker(self, run: list[Pkg], skipped: list[Pkg], recheck: bool) -> None:
        index = {id(p): str(i) for i, p in enumerate(self.pkgs)}
        counts = {"ok": 0, "na": 0}
        failed: list[str] = []
        self.log.info(f"=== Update started: {len(run)} programs ===")
        for p in skipped:
            self.log.result(p, "skipped", None, ["admin required, excluded from \"Update all\""], [])
            self.events.put(("status", index[id(p)], p.id, "st_admin_skipped", "warn"))
        if not shutil.which("winget"):
            self.events.put(("fatal", "nowinget"))
            return
        for n, p in enumerate(run, 1):
            iid = index[id(p)]
            self.events.put(("status", iid, p.id, "st_updating", "run"))
            if not ID_RE.match(p.id):
                self.log.result(p, "skipped", None, ["invalid ID, skipped"], [])
                self.events.put(("status", iid, p.id, "res_skipped", RESULTS["skipped"][0]))
                self.events.put(("progress", n))
                continue
            args = ["upgrade", "--id", p.id, "--silent",
                    "--accept-source-agreements", "--accept-package-agreements"]
            if p.source.lower() == "msstore":
                args += ["--source", "msstore"]
            try:
                rc, out = run_winget(args, PER_PACKAGE_TIMEOUT)
            except Exception:
                self.log.exception(f"running winget for {p.id}")
                rc, out = 1, "Exception while running winget (see log)"
            kind = classify(rc, out)
            if kind == "admin_needed" and p.id not in self.learned:
                self.learned.add(p.id)  # автообучение: в следующий раз пометим заранее
                save_learned(self.learned)
            lines = [l for l in clean_lines(out) if l.strip()]
            self.log.result(p, kind, rc, lines, ["winget"] + args)
            if kind == "success":
                counts["ok"] += 1
            elif kind == "not_applicable":
                counts["na"] += 1
            elif kind in FAILURE_KINDS:
                failed.append(p.id)
            self.events.put(("status", iid, p.id, "res_" + kind, RESULTS[kind][0]))
            self.events.put(("progress", n))
        self.log.summary(len(run), counts["ok"], failed, counts["na"])
        def summary() -> str:
            return t("done", ok=counts["ok"], na=counts["na"], failed=len(failed)) + (t("see_log") if failed else "")
        self.events.put(("recheck_done", summary, recheck))
        if recheck:
            self.events.put(("list", self._fetch_list()))

    # ----- События из потоков -----

    def _guard(self, fn):
        """Оборачивает рабочую функцию потока: исключение → лог + сообщение, UI не падает."""
        def wrapper(*a):
            try:
                fn(*a)
            except Exception:
                self.log.exception(fn.__name__)
                self.events.put(("fatal", "internal_error"))
        return wrapper

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            self.log.exception("handling UI event")
        self.root.after(100, self._poll)

    def _handle(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "status":
            _, iid, pid, key, tag = ev
            if tag in ("ok", "err", "warn", "muted") and key != "st_updating":
                self.last_status[pid] = (key, tag)
            self.set_status(iid, key, tag)
            self.say(lambda: f"{pid}: {t(key)}")
        elif kind == "progress":
            self.progress.configure(value=ev[1])
        elif kind == "recheck_done":
            fn, recheck = ev[1], ev[2]
            self.say(lambda: fn() + (t("rechecking") if recheck else ""))
            self.final_msg = ev[1]
            if not recheck:
                self.set_busy(False)
        elif kind == "list":
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            res = ev[1]
            if res[0] == "ok":
                self.populate(res[1])
                msg = self.final_msg
                self.final_msg = None
                n = len(res[1])

                def tail() -> str:
                    if msg:
                        return t("remaining", n=n)
                    return t("found", n=n) if n else t("all_current")
                self.say(lambda: f"{msg()} · {tail()}" if msg else tail())
            elif res[0] == "nowinget":
                self.say(lambda: t("nowinget"))
                messagebox.showerror("FastUpdater", t("nowinget_msg"))
            elif res[0] == "timeout":
                self.say(lambda: t("check_timeout"))
            else:
                self.say(lambda: t("parse_fail"))
                self.show_raw(res[1])
            self.set_busy(False)
        elif kind == "fatal":
            self.progress.stop()
            self.say(lambda: t(ev[1]))
            messagebox.showerror("FastUpdater", t(ev[1]))
            self.set_busy(False)

    # ----- Прочее -----

    def show_raw(self, text: str) -> None:
        win = tk.Toplevel(self.root)
        win.title(t("raw_title"))
        win.geometry("900x500")
        win.configure(bg=self.pal["bg"])
        box = tk.Text(win, wrap="none", bg=self.pal["card"], fg=self.pal["fg"], relief="flat",
                      font=("Consolas", 10), padx=10, pady=10)
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.pack(fill="both", expand=True)

    def open_log(self) -> None:
        try:
            os.startfile(self.log.path if self.log.path.exists() else LOG_DIR)
        except Exception:
            self.log.exception("opening log")

    def _on_tk_error(self, exc, val, tb) -> None:
        self.log.logger.error("Tk callback error", exc_info=(exc, val, tb))


# ───────────────────────── Точка входа ─────────────────────────

def test_parser(path: str) -> None:
    """Самопроверка парсера: python updater.py --test-parser sample_output.txt"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    pkgs, found = parse_winget_table(text)
    print(f"Table found: {found}; packages: {len(pkgs)}")
    for p in pkgs:
        flag = "  [version unknown]" if p.version_unknown else ""
        print(f"  {p.name!r:60} {p.id:32} {p.version:>14} -> {p.available:<14} {p.source}{flag}")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--test-parser":
        test_parser(sys.argv[2])
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
