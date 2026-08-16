import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


VENDOR_DIRECTORY = Path(__file__).parent / ".vendor"
if VENDOR_DIRECTORY.is_dir():
    sys.path.insert(0, str(VENDOR_DIRECTORY))
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None


APP_DIRECTORY = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ROOT_EXIFTOOL_PATH = APP_DIRECTORY / "exiftool.exe"
LEGACY_EXIFTOOL_PATH = Path(r"D:\exiftool\exiftool.exe")
CONFIG_PATH = Path(os.environ.get("APPDATA", APP_DIRECTORY)) / "MetadataViewerCopier" / "config.json"


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(**updates):
    try:
        config = load_config()
        config.update(updates)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_exiftool_path():
    if ROOT_EXIFTOOL_PATH.is_file():
        return str(ROOT_EXIFTOOL_PATH)
    saved_path = load_config().get("exiftool_path", "")
    if os.path.isfile(saved_path):
        return saved_path
    if LEGACY_EXIFTOOL_PATH.is_file():
        return str(LEGACY_EXIFTOOL_PATH)
    return str(ROOT_EXIFTOOL_PATH)


def save_exiftool_path(path):
    save_config(exiftool_path=path)


def system_prefers_dark_mode():
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def enable_high_dpi():
    """在创建 Tk 窗口前启用 Windows 高 DPI 感知，避免系统放大造成模糊。"""
    if sys.platform != "win32":
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


EXIFTOOL_PATH = load_exiftool_path()
DURATION_TOLERANCE_SECONDS = 0.1
IMAGE_ASPECT_RATIO_TOLERANCE = 0.01
VIDEO_EXTENSIONS = {
    ".3g2", ".3gp", ".asf", ".avi", ".dv", ".f4v", ".flv", ".m2t", ".m2ts", ".m4v",
    ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mxf", ".ogv", ".rm", ".rmvb", ".ts",
    ".vob", ".webm", ".wmv",
}
AUDIO_EXTENSIONS = {
    ".aac", ".aiff", ".alac", ".amr", ".ape", ".au", ".caf", ".dff", ".dsf", ".flac",
    ".m4a", ".m4b", ".mka", ".mp2", ".mp3", ".oga", ".ogg", ".opus", ".ra", ".wav", ".wma",
}
IMAGE_EXTENSIONS = {
    ".arw", ".avif", ".bmp", ".cr2", ".cr3", ".dng", ".gif", ".heic", ".heif", ".ico",
    ".jfif", ".jp2", ".jpeg", ".jpg", ".jxl", ".nef", ".orf", ".pef", ".png", ".psd",
    ".raf", ".rw2", ".tif", ".tiff", ".webp",
}
IMPORTANT_TAGS = [
    "-FileName", "-FileSize", "-Duration", "-ImageWidth", "-ImageHeight",
    "-AvgBitrate", "-Rotation", "-CreateDate", "-ModifyDate", "-TrackCreateDate",
    "-MediaCreateDate", "-Make", "-Model", "-CameraModelName", "-ISO",
    "-ShutterSpeed", "-Aperture", "-ExposureCompensation", "-WhiteBalance",
    "-ColorTemperature", "-FocalLength", "-GPSLatitude", "-GPSLongitude",
    "-GPSAltitude", "-GPSSpeed", "-GPSDateTime", "-Compass", "-Pitch",
    "-Roll", "-Yaw", "-NDFilter", "-LensModel", "-Encoder", "-TimeCode",
    "-HandlerType", "-MajorBrand", "-CompatibleBrands",
]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")


def check_exiftool():
    if os.path.isfile(EXIFTOOL_PATH):
        return
    print(f"错误：未找到 ExifTool：{EXIFTOOL_PATH}")
    print("请从 https://exiftool.org/ 下载，并更新 EXIFTOOL_PATH。")
    raise SystemExit(1)


def run_exiftool(*args):
    return subprocess.run(
        [EXIFTOOL_PATH, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def media_kind(path):
    extension = os.path.splitext(path)[1].lower()
    if extension in VIDEO_EXTENSIONS:
        return "视频"
    if extension in AUDIO_EXTENSIONS:
        return "音频"
    if extension in IMAGE_EXTENSIONS:
        return "图片"
    return "其他"


def is_supported_media_file(path):
    return os.path.isfile(path) and media_kind(path) != "其他"


def find_files(folder, include_all=False):
    files = []
    for root, _, filenames in os.walk(folder):
        files.extend(
            os.path.join(root, name)
            for name in filenames
            if include_all or media_kind(name) != "其他"
        )
    return sorted(files, key=lambda path: path.casefold())


def filter_files_supported_by_exiftool(files, reporter=print):
    reporter(f"正在使用 ExifTool 检查 {len(files)} 个文件……")
    supported_files = []
    for path in files:
        try:
            result = run_exiftool("-s3", "-FileType", path)
        except OSError:
            continue
        if result.returncode == 0 and result.stdout.strip():
            supported_files.append(path)
    return supported_files


def parse_dragged_paths(value):
    value = value.strip()
    if not value:
        return []
    if os.path.exists(value.strip('"')):
        return [value.strip('"')]
    return [match[0] or match[1] for match in re.findall(r'"([^"]+)"|(\S+)', value)]


def collect_media_files(label):
    print(f"\n请拖入{label}（可一次拖入多个；每行可继续添加，直接回车结束）：")
    files = []
    while True:
        value = input("> ").strip()
        if not value:
            break
        for path in parse_dragged_paths(value):
            if is_supported_media_file(path):
                files.append(os.path.abspath(path))
            else:
                print(f"警告：已忽略不存在或非支持媒体格式的文件：{path}")

    unique_files = list(dict.fromkeys(files))
    print(f"已添加 {len(unique_files)} 个{label}。")
    return unique_files


def choose_media_collection(label):
    print(f"\n{label}输入方式：1=拖入媒体文件  2=扫描支持格式的文件夹  3=扫描全部文件（实验性）")
    while True:
        choice = input("请选择 [1]：").strip() or "1"
        if choice == "1":
            return collect_media_files(label)
        if choice in {"2", "3"}:
            folder = input(f"请拖入{label}文件夹：").strip().strip('"')
            if not os.path.isdir(folder):
                print("错误：文件夹不存在，请重新输入。")
                continue
            files = find_files(folder, include_all=choice == "3")
            if choice == "3":
                files = filter_files_supported_by_exiftool(files)
            print(f"找到 {len(files)} 个{label}（含子文件夹）。")
            return files
        print("请输入 1、2 或 3。")


def show_metadata(video_path, show_all=False):
    print(f"\n正在读取：{os.path.basename(video_path)}")
    args = ["-a", "-u", "-G", "-ee"] if show_all else ["-G", *IMPORTANT_TAGS]
    try:
        result = run_exiftool(*args, video_path)
        print(result.stdout)
        if result.returncode != 0:
            print(f"ExifTool 警告：{result.stderr.strip()}")
    except OSError as error:
        print(f"错误：无法读取元数据：{error}")


def show_batch_metadata(sources):
    choice = input("\n批量查看原文件元数据？[回车跳过 / i=关键字段 / all=全部]：").strip().lower()
    if choice not in {"i", "all"}:
        return
    for source in sources:
        show_metadata(source, show_all=choice == "all")


def view_metadata_only(sources):
    choice = input("\n显示方式：1=关键元数据  2=全部元数据 [1]：").strip() or "1"
    for source in sources:
        show_metadata(source, show_all=choice == "2")


def pair_in_order(sources, targets):
    print("\n警告：顺序配对不推荐，请确认两个列表的排序和数量完全一致。")
    if len(sources) != len(targets):
        print(f"错误：数量不一致：原文件 {len(sources)}，目标文件 {len(targets)}。")
        return []
    if not confirm("仍按当前顺序配对吗？"):
        return []
    return list(zip(sources, targets))


def pair_by_filename(sources, targets):
    pairs = []
    available_targets = set(targets)
    unmatched = []

    for source in sources:
        source_name = os.path.splitext(os.path.basename(source))[0].casefold()
        candidates = [
            target for target in available_targets
            if source_name in os.path.splitext(os.path.basename(target))[0].casefold()
        ]
        exact_matches = [
            target for target in candidates
            if os.path.splitext(os.path.basename(target))[0].casefold() == source_name
        ]
        selected = exact_matches[0] if len(exact_matches) == 1 else None
        if selected is None and len(candidates) == 1:
            selected = candidates[0]
        if selected is None:
            unmatched.append((source, len(candidates)))
            continue
        available_targets.remove(selected)
        pairs.append((source, selected))

    if unmatched:
        print("\n警告：以下原文件未自动配对（0 个匹配或多个匹配，已跳过）：")
        for source, count in unmatched:
            print(f"  - {os.path.basename(source)}（候选目标：{count}）")
    print(f"文件名自动配对完成：{len(pairs)} 对。")
    return pairs


def pair_manually(sources, targets):
    pairs = []
    available_targets = list(targets)
    print("\n手动配对：对每个原文件输入目标文件编号；直接回车或输入 0 跳过。")
    for source in sources:
        if not available_targets:
            print("目标文件已全部使用。")
            break
        print(f"\n原文件：{os.path.basename(source)}")
        for index, target in enumerate(available_targets, start=1):
            print(f"  {index}. {target}")
        while True:
            selection = input("目标编号：").strip()
            if not selection or selection == "0":
                break
            if selection.isdigit() and 1 <= int(selection) <= len(available_targets):
                pairs.append((source, available_targets.pop(int(selection) - 1)))
                break
            print("请输入有效编号、0 或直接回车。")
    return pairs


def choose_pairs(sources, targets):
    print("\n配对方式：1=按顺序（不推荐）  2=文件名匹配  3=手动匹配")
    while True:
        choice = input("请选择 [2]：").strip() or "2"
        if choice == "1":
            return pair_in_order(sources, targets)
        if choice == "2":
            return pair_by_filename(sources, targets)
        if choice == "3":
            return pair_manually(sources, targets)
        print("请输入 1、2 或 3。")


def get_duration(video_path):
    try:
        result = run_exiftool("-s3", "-n", "-Duration#", video_path)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        match = re.search(r"[-+]?\d+(?:\.\d+)?", result.stdout)
        return float(match.group()) if match else None
    except (OSError, ValueError):
        return None


def get_image_dimensions(image_path):
    try:
        result = run_exiftool("-s3", "-n", "-ImageWidth#", "-ImageHeight#", image_path)
        if result.returncode != 0:
            return None
        values = [int(float(value)) for value in re.findall(r"\d+(?:\.\d+)?", result.stdout)]
        if len(values) < 2 or 0 in values[:2]:
            return None
        return values[0], values[1]
    except (OSError, ValueError):
        return None


def confirm(message):
    return input(f"{message} [y/N]：").strip().lower() in {"y", "yes"}


def choose_operation():
    while True:
        choice = input("\n功能：1=仅批量查看  2=批量复制元数据 [1]：").strip() or "1"
        if choice in {"1", "2"}:
            return choice
        print("请输入 1 或 2。")


def verify_duration(source, target):
    source_duration = get_duration(source)
    target_duration = get_duration(target)
    if source_duration is None or target_duration is None:
        print(f"警告：无法读取时长：{os.path.basename(source)} -> {os.path.basename(target)}")
        return confirm("无法完成时长校验，仍要复制元数据吗？")

    difference = abs(source_duration - target_duration)
    print(
        f"{os.path.basename(source)} -> {os.path.basename(target)}："
        f"{source_duration:.4f}s / {target_duration:.4f}s，差 {difference:.4f}s"
    )
    if difference <= DURATION_TOLERANCE_SECONDS:
        return True
    return confirm(
        f"警告：时长误差超过 ±{DURATION_TOLERANCE_SECONDS:.1f} 秒，仍要复制元数据吗？"
    )


def verify_image_dimensions(source, target):
    source_size = get_image_dimensions(source)
    target_size = get_image_dimensions(target)
    if source_size is None or target_size is None:
        print(f"警告：无法读取图片尺寸：{os.path.basename(source)} -> {os.path.basename(target)}")
        return confirm("无法完成图片校验，仍要复制元数据吗？")

    source_ratio = max(source_size) / min(source_size)
    target_ratio = max(target_size) / min(target_size)
    difference = abs(source_ratio - target_ratio) / max(source_ratio, target_ratio)
    print(
        f"{os.path.basename(source)} -> {os.path.basename(target)}："
        f"{source_size[0]}x{source_size[1]} / {target_size[0]}x{target_size[1]}，"
        f"宽高比差 {difference:.2%}"
    )
    if difference <= IMAGE_ASPECT_RATIO_TOLERANCE:
        return True
    return confirm("警告：图片宽高比差异超过 1%，仍要复制元数据吗？")


def verify_pairs(pairs):
    approved = []
    print("\n正在校验配对文件……")
    for source, target in pairs:
        source_kind = media_kind(source)
        target_kind = media_kind(target)
        if source_kind != target_kind:
            print(f"警告：文件类型不同：{source_kind} -> {target_kind}")
            is_approved = confirm("类型不同，仍要复制元数据吗？")
        elif source_kind in {"视频", "音频"}:
            is_approved = verify_duration(source, target)
        elif source_kind == "图片":
            is_approved = verify_image_dimensions(source, target)
        else:
            print(f"警告：未定义 {os.path.basename(source)} 的自动校验方法。")
            is_approved = confirm("仍要复制元数据吗？")
        if is_approved:
            approved.append((source, target))
    return approved


def copy_metadata(source, target):
    try:
        result = run_exiftool(
            "-TagsFromFile", source, "-all:all", "-overwrite_original", target
        )
        if result.returncode == 0:
            print(f"已复制：{os.path.basename(target)}")
            return True
        print(f"错误：复制失败：{os.path.basename(target)}\n{result.stderr.strip()}")
        print("提示：此格式可能只支持读取元数据，不支持写入。")
    except OSError as error:
        print(f"系统错误：{error}")
    return False


def cli_main():
    check_exiftool()
    print("\n=== 媒体文件元数据批量查看与复制工具 ===")
    sources = choose_media_collection("原文件")
    if not sources:
        print("错误：没有原文件，程序已退出。")
        return

    if choose_operation() == "1":
        view_metadata_only(sources)
        print("\n查看完成。")
        return

    show_batch_metadata(sources)
    targets = choose_media_collection("目标文件")
    if not targets:
        print("错误：没有目标文件，程序已退出。")
        return

    pairs = choose_pairs(sources, targets)
    if not pairs:
        print("没有可复制的配对，程序已退出。")
        return

    approved_pairs = verify_pairs(pairs)
    if not approved_pairs:
        print("没有通过校验确认的配对，程序已退出。")
        return

    print(f"\n开始复制 {len(approved_pairs)} 对文件的元数据……")
    succeeded = sum(copy_metadata(source, target) for source, target in approved_pairs)
    print(f"\n完成：成功 {succeeded} 对，失败 {len(approved_pairs) - succeeded} 对。")


class MetadataApp:
    def __init__(self):
        enable_high_dpi()
        self.root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
        self.root.title("媒体文件元数据查看与复制")
        self.root.geometry("1180x800")
        self.root.minsize(980, 680)
        self.source_files = []
        self.target_files = []
        self.events = queue.Queue()
        self.running = False
        self.exiftool_var = tk.StringVar(value=EXIFTOOL_PATH)
        self.mode_var = tk.StringVar(value="view")
        self.detail_var = tk.StringVar(value="关键字段")
        self.pairing_var = tk.StringVar(value="按文件名匹配")
        self.scan_all_var = tk.BooleanVar(value=False)
        saved_theme = load_config().get("theme", "跟随系统")
        self.theme_var = tk.StringVar(value=saved_theme if saved_theme in {"跟随系统", "亮色", "暗色"} else "跟随系统")
        self.status_var = tk.StringVar()
        self.style = ttk.Style(self.root)
        self.is_dark = None
        self.apply_theme()
        self._build()
        self.apply_theme(force=True)
        self.update_exiftool_status()
        self.root.after(100, self.process_events)
        self.root.after(3000, self.refresh_system_theme)

    def apply_theme(self, force=False):
        dark = system_prefers_dark_mode() if self.theme_var.get() == "跟随系统" else self.theme_var.get() == "暗色"
        if not force and dark == self.is_dark:
            return
        self.is_dark = dark
        colors = {
            "background": "#1e2128" if dark else "#f4f7fb",
            "surface": "#282d36" if dark else "#ffffff",
            "surface_alt": "#343b47" if dark else "#e9eef6",
            "text": "#edf1f7" if dark else "#1f2937",
            "muted": "#aab4c2" if dark else "#5f6b7a",
            "border": "#465160" if dark else "#d4dce7",
            "accent": "#5b9cf6" if dark else "#2563eb",
            "accent_active": "#78b1ff" if dark else "#1d4ed8",
            "selection": "#3d6da8" if dark else "#cfe1ff",
            "status_ok": "#6cc58a" if dark else "#16803c",
            "status_error": "#ff8d8d" if dark else "#b42318",
        }
        self.colors = colors
        self.root.configure(bg=colors["background"])
        self.style.theme_use("clam")
        self.style.configure(".", background=colors["background"], foreground=colors["text"], font=("Segoe UI", 10))
        self.style.configure("TFrame", background=colors["background"])
        self.style.configure("Header.TFrame", background=colors["surface"])
        self.style.configure("TLabel", background=colors["background"], foreground=colors["text"])
        self.style.configure("Title.TLabel", background=colors["surface"], foreground=colors["text"], font=("Segoe UI Semibold", 18))
        self.style.configure("Subtitle.TLabel", background=colors["surface"], foreground=colors["muted"], font=("Segoe UI", 10))
        self.style.configure("StatusOk.TLabel", foreground=colors["status_ok"])
        self.style.configure("StatusError.TLabel", foreground=colors["status_error"])
        self.style.configure("TLabelframe", background=colors["background"], bordercolor=colors["border"], relief="solid")
        self.style.configure("TLabelframe.Label", background=colors["background"], foreground=colors["text"], font=("Segoe UI Semibold", 10))
        self.style.configure("TButton", background=colors["surface_alt"], foreground=colors["text"], bordercolor=colors["border"], padding=(10, 6))
        self.style.map("TButton", background=[("active", colors["selection"])])
        self.style.configure("Accent.TButton", background=colors["accent"], foreground="#ffffff", bordercolor=colors["accent"], font=("Segoe UI Semibold", 10), padding=(18, 8))
        self.style.map("Accent.TButton", background=[("active", colors["accent_active"]), ("disabled", colors["surface_alt"])])
        self.style.configure("TEntry", fieldbackground=colors["surface"], foreground=colors["text"], bordercolor=colors["border"], padding=6)
        self.style.configure("TCombobox", fieldbackground=colors["surface"], background=colors["surface"], foreground=colors["text"], arrowcolor=colors["text"], padding=4)
        self.style.map("TCombobox", fieldbackground=[("readonly", colors["surface"])], foreground=[("readonly", colors["text"])])
        self.style.configure("TCheckbutton", background=colors["background"], foreground=colors["text"])
        self.style.configure("TRadiobutton", background=colors["background"], foreground=colors["text"])
        if hasattr(self, "log_text"):
            self.log_text.configure(bg=colors["surface"], fg=colors["text"], insertbackground=colors["text"], selectbackground=colors["selection"], relief="flat", highlightthickness=0)
        for name in ("source_listbox", "target_listbox"):
            if hasattr(self, name):
                getattr(self, name).configure(bg=colors["surface"], fg=colors["text"], selectbackground=colors["accent"], selectforeground="#ffffff", relief="flat", highlightthickness=1, highlightbackground=colors["border"], highlightcolor=colors["accent"])

    def on_theme_changed(self, _event=None):
        save_config(theme=self.theme_var.get())
        self.apply_theme(force=True)

    def refresh_system_theme(self):
        if self.theme_var.get() == "跟随系统":
            self.apply_theme()
        self.root.after(3000, self.refresh_system_theme)

    def _build(self):
        container = ttk.Frame(self.root, padding=12)
        container.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)
        container.rowconfigure(4, weight=1)

        header = ttk.Frame(container, style="Header.TFrame", padding=(16, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="媒体文件元数据", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="批量查看、核验与复制", style="Subtitle.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(header, text="主题：", style="Subtitle.TLabel").grid(row=0, column=1, padx=(16, 4), sticky="e")
        theme_box = ttk.Combobox(header, textvariable=self.theme_var, state="readonly", width=10, values=("跟随系统", "亮色", "暗色"))
        theme_box.grid(row=0, column=2, rowspan=2, sticky="e")
        theme_box.bind("<<ComboboxSelected>>", self.on_theme_changed)

        tool_frame = ttk.LabelFrame(container, text="ExifTool", padding=8)
        tool_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        tool_frame.columnconfigure(0, weight=1)
        ttk.Entry(tool_frame, textvariable=self.exiftool_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(tool_frame, text="选择路径", command=self.select_exiftool).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(tool_frame, text="使用根目录副本", command=self.use_root_exiftool).grid(row=0, column=2, padx=(8, 0))
        self.status_label = ttk.Label(tool_frame, textvariable=self.status_var)
        self.status_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        files_frame = ttk.Frame(container)
        files_frame.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        files_frame.columnconfigure(0, weight=1)
        files_frame.columnconfigure(1, weight=1)
        files_frame.rowconfigure(0, weight=1)
        self.source_listbox = self.create_file_panel(files_frame, 0, "原文件", "source")
        self.target_listbox = self.create_file_panel(files_frame, 1, "目标文件（仅复制时需要）", "target")

        options = ttk.LabelFrame(container, text="操作", padding=8)
        options.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Radiobutton(options, text="仅查看元数据", variable=self.mode_var, value="view").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(options, text="复制元数据", variable=self.mode_var, value="copy").grid(row=0, column=1, padx=(16, 0), sticky="w")
        ttk.Label(options, text="查看内容：").grid(row=0, column=2, padx=(24, 4))
        ttk.Combobox(options, textvariable=self.detail_var, state="readonly", width=10,
                     values=("关键字段", "全部元数据")).grid(row=0, column=3)
        ttk.Label(options, text="配对方式：").grid(row=1, column=0, pady=(8, 0), sticky="w")
        ttk.Combobox(options, textvariable=self.pairing_var, state="readonly", width=16,
                     values=("按文件名匹配", "按顺序（不推荐）", "手动匹配")).grid(row=1, column=1, pady=(8, 0), sticky="w")
        ttk.Checkbutton(options, text="扫描文件夹时检查全部文件（较慢）", variable=self.scan_all_var).grid(
            row=1, column=2, columnspan=2, padx=(24, 0), pady=(8, 0), sticky="w"
        )
        self.start_button = ttk.Button(options, text="开始处理", style="Accent.TButton", command=self.start)
        self.start_button.grid(row=0, column=4, rowspan=2, padx=(24, 0), sticky="ns")

        log_frame = ttk.LabelFrame(container, text="结果", padding=6)
        log_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap="word", height=14, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def create_file_panel(self, parent, column, title, kind):
        panel = ttk.LabelFrame(parent, text=f"{title}（可拖入文件或文件夹）", padding=8)
        panel.grid(row=0, column=column, sticky="nsew", padx=(0, 5) if column == 0 else (5, 0))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        listbox = tk.Listbox(panel, selectmode="extended", height=12)
        listbox.grid(row=0, column=0, columnspan=3, sticky="nsew")
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=listbox.yview)
        scrollbar.grid(row=0, column=3, sticky="ns")
        listbox.configure(yscrollcommand=scrollbar.set)
        if DND_FILES:
            listbox.drop_target_register(DND_FILES)
            listbox.dnd_bind("<<Drop>>", lambda event: self.handle_drop(event, kind))
        ttk.Button(panel, text="添加文件", command=lambda: self.add_files(kind)).grid(row=1, column=0, pady=(8, 0), sticky="w")
        ttk.Button(panel, text="添加文件夹", command=lambda: self.add_folder(kind)).grid(row=1, column=1, padx=6, pady=(8, 0))
        ttk.Button(panel, text="清空", command=lambda: self.clear_files(kind)).grid(row=1, column=2, pady=(8, 0), sticky="e")
        return listbox

    def set_exiftool_path(self, path):
        global EXIFTOOL_PATH
        path = os.path.abspath(path)
        self.exiftool_var.set(path)
        EXIFTOOL_PATH = path
        if os.path.isfile(path):
            save_exiftool_path(path)
        self.update_exiftool_status()

    def update_exiftool_status(self):
        path = self.exiftool_var.get().strip()
        if os.path.isfile(path):
            self.status_var.set("ExifTool 已就绪。路径会在下次启动时自动记住。")
            self.status_label.configure(style="StatusOk.TLabel")
        else:
            self.status_var.set(f"未找到 ExifTool。可将 exiftool.exe 复制到：{ROOT_EXIFTOOL_PATH}")
            self.status_label.configure(style="StatusError.TLabel")

    def select_exiftool(self):
        path = filedialog.askopenfilename(
            title="选择 exiftool.exe",
            filetypes=(("ExifTool", "exiftool*.exe"), ("可执行文件", "*.exe"), ("所有文件", "*.*")),
        )
        if path:
            self.set_exiftool_path(path)

    def use_root_exiftool(self):
        self.set_exiftool_path(str(ROOT_EXIFTOOL_PATH))
        if not ROOT_EXIFTOOL_PATH.is_file():
            messagebox.showinfo("未找到 ExifTool", f"请将 exiftool.exe 复制到：\n{ROOT_EXIFTOOL_PATH}")

    def add_files(self, kind):
        patterns = " ".join(f"*{extension}" for extension in sorted(VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS))
        paths = filedialog.askopenfilenames(
            title="选择媒体文件",
            filetypes=(("支持的媒体文件", patterns), ("所有文件", "*.*")),
        )
        self.add_file_paths(kind, [path for path in paths if is_supported_media_file(path)])
        skipped = len(paths) - len([path for path in paths if is_supported_media_file(path)])
        if skipped:
            self.append_log(f"已忽略 {skipped} 个未列入支持格式的文件。\n")

    def add_folder(self, kind):
        folder = filedialog.askdirectory(title="选择文件夹")
        if not folder:
            return
        files = find_files(folder, include_all=self.scan_all_var.get())
        if self.scan_all_var.get():
            self.start_worker(self.scan_folder_worker, kind, files)
        else:
            self.add_file_paths(kind, files)

    def handle_drop(self, event, kind):
        paths = [os.path.abspath(path) for path in self.root.tk.splitlist(event.data)]
        discovered_files, skipped = [], []
        for path in paths:
            if os.path.isdir(path):
                discovered_files.extend(find_files(path, include_all=self.scan_all_var.get()))
            elif os.path.isfile(path):
                if self.scan_all_var.get() or is_supported_media_file(path):
                    discovered_files.append(path)
                else:
                    skipped.append(path)
            else:
                skipped.append(path)

        if discovered_files:
            if self.scan_all_var.get():
                self.start_worker(self.scan_folder_worker, kind, discovered_files)
            else:
                self.add_file_paths(kind, discovered_files)
                self.append_log(f"通过拖放添加了 {len(discovered_files)} 个文件。\n")
        if skipped:
            self.append_log(f"已忽略 {len(skipped)} 个不存在或未列入支持格式的项目。\n")
        return event.action

    def scan_folder_worker(self, kind, files):
        supported_files = filter_files_supported_by_exiftool(
            files, reporter=lambda message: self.log_from_worker(f"{message}\n")
        )
        self.events.put(("add_files", kind, supported_files))

    def add_file_paths(self, kind, paths):
        file_list = self.source_files if kind == "source" else self.target_files
        for path in paths:
            absolute_path = os.path.abspath(path)
            if absolute_path not in file_list:
                file_list.append(absolute_path)
        self.refresh_file_list(kind)

    def clear_files(self, kind):
        if kind == "source":
            self.source_files.clear()
        else:
            self.target_files.clear()
        self.refresh_file_list(kind)

    def refresh_file_list(self, kind):
        files = self.source_files if kind == "source" else self.target_files
        listbox = self.source_listbox if kind == "source" else self.target_listbox
        listbox.delete(0, tk.END)
        for path in files:
            listbox.insert(tk.END, path)

    def append_log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message)
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def log_from_worker(self, message):
        self.events.put(("log", message))

    def filename_pairs(self):
        pairs, available_targets, unmatched = [], list(self.target_files), []
        for source in self.source_files:
            source_name = Path(source).stem.casefold()
            candidates = [target for target in available_targets if source_name in Path(target).stem.casefold()]
            exact_matches = [target for target in candidates if Path(target).stem.casefold() == source_name]
            selected = exact_matches[0] if len(exact_matches) == 1 else (candidates[0] if len(candidates) == 1 else None)
            if selected is None:
                unmatched.append(source)
                continue
            available_targets.remove(selected)
            pairs.append((source, selected))
        if unmatched:
            self.append_log("以下原文件未自动配对：\n" + "\n".join(f"- {Path(path).name}" for path in unmatched) + "\n")
        return pairs

    def manual_pairs(self):
        pairs, available_targets = [], list(self.target_files)
        dialog = tk.Toplevel(self.root)
        dialog.title("手动配对")
        dialog.transient(self.root)
        dialog.grab_set()
        current_index = tk.IntVar(value=0)
        source_label = ttk.Label(dialog, padding=12)
        source_label.grid(row=0, column=0, columnspan=2, sticky="w")
        targets_box = tk.Listbox(dialog, width=85, height=12)
        targets_box.grid(row=1, column=0, columnspan=2, padx=12, sticky="nsew")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        def refresh():
            index = current_index.get()
            if index >= len(self.source_files) or not available_targets:
                dialog.destroy()
                return
            source_label.configure(text=f"原文件 {index + 1}/{len(self.source_files)}：{self.source_files[index]}")
            targets_box.delete(0, tk.END)
            for target in available_targets:
                targets_box.insert(tk.END, target)

        def pair_selected():
            selection = targets_box.curselection()
            if not selection:
                messagebox.showwarning("请选择目标文件", "请选择一个目标文件，或点击跳过。", parent=dialog)
                return
            pairs.append((self.source_files[current_index.get()], available_targets.pop(selection[0])))
            current_index.set(current_index.get() + 1)
            refresh()

        def skip():
            current_index.set(current_index.get() + 1)
            refresh()

        ttk.Button(dialog, text="配对", command=pair_selected).grid(row=2, column=0, padx=12, pady=12, sticky="w")
        ttk.Button(dialog, text="跳过", command=skip).grid(row=2, column=1, padx=12, pady=12, sticky="e")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        refresh()
        self.root.wait_window(dialog)
        return pairs

    def create_pairs(self):
        if self.pairing_var.get() == "按顺序（不推荐）":
            if len(self.source_files) != len(self.target_files):
                messagebox.showerror("数量不一致", "按顺序配对要求原文件和目标文件数量相同。", parent=self.root)
                return []
            if not messagebox.askyesno("顺序配对", "顺序配对不推荐。确认两个列表的顺序完全一致后继续？", parent=self.root):
                return []
            return list(zip(self.source_files, self.target_files))
        if self.pairing_var.get() == "手动匹配":
            return self.manual_pairs()
        return self.filename_pairs()

    def start(self):
        path = self.exiftool_var.get().strip()
        if not os.path.isfile(path):
            self.update_exiftool_status()
            messagebox.showerror("需要 ExifTool", "请选择 exiftool.exe，或将它复制到程序根目录。", parent=self.root)
            return
        self.set_exiftool_path(path)
        if not self.source_files:
            messagebox.showerror("没有原文件", "请先添加原文件或原文件夹。", parent=self.root)
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")
        if self.mode_var.get() == "view":
            self.start_worker(self.view_worker, list(self.source_files))
            return
        if not self.target_files:
            messagebox.showerror("没有目标文件", "复制元数据前请添加目标文件或目标文件夹。", parent=self.root)
            return
        pairs = self.create_pairs()
        if not pairs:
            self.append_log("没有可处理的配对。\n")
            return
        self.append_log(f"已生成 {len(pairs)} 对配对，正在校验并复制……\n")
        self.start_worker(self.copy_worker, pairs)

    def start_worker(self, worker, *args):
        if self.running:
            return
        self.running = True
        self.start_button.configure(state="disabled")
        threading.Thread(target=self.worker_wrapper, args=(worker, *args), daemon=True).start()

    def worker_wrapper(self, worker, *args):
        try:
            worker(*args)
        except Exception as error:
            self.log_from_worker(f"发生未处理错误：{error}\n")
        finally:
            self.events.put(("finished",))

    def view_worker(self, sources):
        args = ["-a", "-u", "-G", "-ee"] if self.detail_var.get() == "全部元数据" else ["-G", *IMPORTANT_TAGS]
        for source in sources:
            try:
                result = run_exiftool(*args, source)
                output = result.stdout or result.stderr or "没有可显示的元数据。"
                self.log_from_worker(f"\n{'=' * 18} {source} {'=' * 18}\n{output}\n")
            except OSError as error:
                self.log_from_worker(f"读取失败：{source}\n{error}\n")
        self.log_from_worker("\n查看完成。\n")

    def ask_confirmation(self, title, message):
        answer, event = {}, threading.Event()
        self.events.put(("confirm", title, message, answer, event))
        event.wait()
        return answer.get("value", False)

    def should_copy_pair(self, source, target):
        source_kind, target_kind = media_kind(source), media_kind(target)
        if source_kind != target_kind:
            self.log_from_worker(f"类型不同：{Path(source).name} ({source_kind}) -> {Path(target).name} ({target_kind})\n")
            return self.ask_confirmation("文件类型不同", "源文件和目标文件类型不同，仍要复制元数据吗？")
        if source_kind in {"视频", "音频"}:
            source_duration, target_duration = get_duration(source), get_duration(target)
            if source_duration is None or target_duration is None:
                return self.ask_confirmation("无法读取时长", f"无法校验：{Path(source).name}\n仍要复制元数据吗？")
            difference = abs(source_duration - target_duration)
            self.log_from_worker(f"{Path(source).name} -> {Path(target).name}：时长差 {difference:.4f}s\n")
            return difference <= DURATION_TOLERANCE_SECONDS or self.ask_confirmation(
                "时长差异", f"{Path(source).name} 与目标文件的时长差为 {difference:.4f} 秒，超过 ±0.1 秒。仍要复制吗？"
            )
        if source_kind == "图片":
            source_size, target_size = get_image_dimensions(source), get_image_dimensions(target)
            if source_size is None or target_size is None:
                return self.ask_confirmation("无法读取图片尺寸", f"无法校验：{Path(source).name}\n仍要复制元数据吗？")
            source_ratio = max(source_size) / min(source_size)
            target_ratio = max(target_size) / min(target_size)
            difference = abs(source_ratio - target_ratio) / max(source_ratio, target_ratio)
            self.log_from_worker(f"{Path(source).name} -> {Path(target).name}：宽高比差 {difference:.2%}\n")
            return difference <= IMAGE_ASPECT_RATIO_TOLERANCE or self.ask_confirmation(
                "图片宽高比差异", f"宽高比差为 {difference:.2%}，超过 1%。仍要复制吗？"
            )
        return self.ask_confirmation("未定义校验", f"未定义 {Path(source).name} 的自动校验方法，仍要复制吗？")

    def copy_worker(self, pairs):
        succeeded, skipped = 0, 0
        for source, target in pairs:
            if not self.should_copy_pair(source, target):
                skipped += 1
                self.log_from_worker(f"已跳过：{Path(target).name}\n")
                continue
            try:
                result = run_exiftool("-TagsFromFile", source, "-all:all", "-overwrite_original", target)
            except OSError as error:
                self.log_from_worker(f"复制失败：{Path(target).name}\n{error}\n")
                continue
            if result.returncode == 0:
                succeeded += 1
                self.log_from_worker(f"已复制：{Path(source).name} -> {Path(target).name}\n")
            else:
                self.log_from_worker(
                    f"复制失败：{Path(target).name}\n{result.stderr}\n提示：该格式可能只支持读取元数据。\n"
                )
        self.log_from_worker(f"\n完成：成功 {succeeded} 对，跳过 {skipped} 对，失败 {len(pairs) - succeeded - skipped} 对。\n")

    def process_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "log":
                    self.append_log(event[1])
                elif event[0] == "add_files":
                    self.add_file_paths(event[1], event[2])
                    self.append_log(f"已添加 {len(event[2])} 个 ExifTool 可读取的文件。\n")
                elif event[0] == "confirm":
                    _, title, message, answer, finished = event
                    answer["value"] = messagebox.askyesno(title, message, parent=self.root)
                    finished.set()
                elif event[0] == "finished":
                    self.running = False
                    self.start_button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def run(self):
        self.root.mainloop()


def main():
    if "--cli" in sys.argv:
        cli_main()
        return
    MetadataApp().run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        if "--cli" in sys.argv:
            print("\n操作已取消。")
    finally:
        if "--cli" in sys.argv:
            try:
                input("\n按回车键退出……")
            except EOFError:
                pass
