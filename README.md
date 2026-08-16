# MetadataViewerCopier - 媒体文件元数据批量查看与复制

一个基于 [ExifTool](https://exiftool.org/) 的 Windows 小工具，支持**视频、图片、音频**的元数据批量查看与复制。提供图形界面（支持拖拽）与命令行两种模式。

## 功能

- 拖拽或扫描文件夹添加文件（GUI 支持直接拖入窗口）
- 批量查看元数据（关键字段 / 全部字段）
- 批量复制元数据：按文件名匹配或按顺序配对，复制前可逐对校验确认
- 支持视频、图片、音频常见格式（MP4/MKV/MOV、JPG/PNG/HEIC、MP3/FLAC/WAV 等，含子文件夹递归扫描）
- 暗色 / 亮色主题（可跟随系统）、Windows 高 DPI 适配

## 使用前准备：ExifTool

本工具通过 ExifTool 读写元数据，使用前需准备 exiftool.exe。程序按以下顺序查找：

1. exe（或源码）同目录下的 exiftool.exe
2. 在 GUI 中手动选择并保存的路径（存于 %APPDATA%\MetadataViewerCopier\config.json）
3. 旧默认路径 D:\exiftool\exiftool.exe

从 <https://exiftool.org/> 下载后，推荐直接把 exiftool.exe 放到程序同目录即可。

## 快速开始

### 直接使用（推荐）

到 [Releases](../../releases) 页面下载最新版 MetadataViewerCopier.exe，与 exiftool.exe 放在同一目录后双击运行。

### 从源码运行

无需安装依赖即可直接运行（tkinterdnd2 已随仓库内置在 .vendor 中），需要 Python 3.10+：

```bash
python metadata.py          # 图形界面
python metadata.py --cli    # 命令行模式
```

## 从源码构建 exe

```bash
pip install pyinstaller
pyinstaller MetadataViewerCopier.spec
# 输出位于 dist\MetadataViewerCopier.exe
```

## 目录结构

- metadata.py — 全部源码（GUI + CLI）
- MetadataViewerCopier.spec — PyInstaller 打包配置
- .vendor/tkinterdnd2 — 内置的拖拽库（跨平台二进制）
- MetaVCer.ico — 程序图标

## License

[MIT](LICENSE)
