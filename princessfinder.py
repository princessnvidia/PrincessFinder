import sys
import shutil
import subprocess
import zipfile
import tarfile
import gzip
import os
import plistlib
import time
from urllib.parse import unquote, quote, urlparse
from pathlib import Path
from datetime import datetime

from PyQt6.QtCore import Qt, QRectF, QUrl, QRect, QSize, QPointF, QMimeData, QTimer, QTimer, QObject, QThread, pyqtSignal, QFileSystemWatcher
from PyQt6.QtGui import QIcon, QPainter, QColor, QFont, QPixmap, QTextOption, QImageReader, QTransform, QDrag
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QSlider, QLineEdit, QMenu, QInputDialog
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

try:
    from PyQt6.QtPdf import QPdfDocument
    from PyQt6.QtPdfWidgets import QPdfView
    HAS_PDF = True
except Exception:
    HAS_PDF = False


LEFT = QColor(51, 51, 59, 76)
RIGHT = QColor(51, 51, 59, 76)
PANE_BG = QColor(51, 51, 59, 76)
PANE_LINE = QColor(255, 255, 255, 28)
MODAL_BG = QColor(32, 32, 39, 76)
WHITE = QColor(255, 255, 255)
DIM = QColor(210, 210, 220, 160)
SELECT = QColor(120, 130, 150, 38)
DIVIDER = QColor(255, 255, 255, 42)
SIDEBAR_DIVIDER = QColor(255, 255, 255, 42)

# Police plus complète que "monospace" pour éviter les rectangles blancs
# sur certains caractères spéciaux dans les noms de fichiers.
UI_FONT_FAMILY = "DejaVu Sans"
MONO_FONT_FAMILY = "DejaVu Sans Mono"


def themed_icon(*names):
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QIcon.fromTheme("unknown")


def load_high_quality_pixmap(path):
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return QPixmap(str(path))
    return QPixmap.fromImage(image)


def rotated_svg_icon(svg_path, angle=0, size=22):
    renderer = QSvgRenderer(str(svg_path))

    if not renderer.isValid():
        return QIcon()

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.translate(size / 2, size / 2)
    painter.rotate(angle)
    renderer.render(painter, QRectF(-size / 2 + 3, -size / 2 + 3, size - 6, size - 6))
    painter.end()

    return QIcon(pixmap)


class PreviewWindow(QWidget):
    def __init__(self, path, parent=None):
        super().__init__(parent)

        self.path = path
        self.player = None
        self.audio = None
        self.video = None
        self.slider = None
        self.play_btn = None
        self.time_label = None
        self.user_is_sliding = False
        self.is_cleaning = False
        self.pdf = None
        self.pdf_view = None
        self.pdf_page = 0
        self.pdf_page_label = None
        self.pdf_prev_btn = None
        self.pdf_next_btn = None

        self.setWindowTitle(path.name)
        self.resize(900, 650)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.setStyleSheet("""
            QWidget { color: white; font-size: 15px; }
            QLabel { color: white; }

            QTextEdit {
                background-color: rgba(20, 20, 26, 76);
                color: white;
                border: 1px solid rgba(255, 255, 255, 35);
                border-radius: 12px;
                padding: 12px;
            }

            QPushButton {
                background-color: rgba(255, 255, 255, 35);
                color: white;
                border: 1px solid rgba(255, 255, 255, 45);
                border-radius: 10px;
                padding: 8px 16px;
            }

            QPushButton:hover {
                background-color: rgba(255, 255, 255, 55);
            }

            QPushButton:pressed,
            QPushButton:checked,
            QPushButton:focus {
                background-color: rgba(255, 255, 255, 35);
                border: 1px solid rgba(255, 255, 255, 45);
                outline: none;
            }

            QSlider::groove:horizontal {
                height: 6px;
                background: rgba(255, 255, 255, 35);
                border-radius: 3px;
            }

            QSlider::handle:horizontal {
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
                background: rgba(255, 255, 255, 210);
            }

            QVideoWidget {
                background-color: black;
                border-radius: 12px;
            }
        """)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(22, 22, 22, 22)
        self.layout.setSpacing(14)

        if path.is_dir():
            self.setup_folder_view(path)
            return

        suffix = path.suffix.lower()

        if suffix in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"]:
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pix = load_high_quality_pixmap(path)
            label.setPixmap(
                pix.scaled(
                    840,
                    560,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.layout.addWidget(label)

        elif suffix in [
            ".txt", ".md", ".py", ".html", ".css", ".js", ".json",
            ".xml", ".sh", ".conf", ".ini", ".log", ".csv"
        ]:
            text = QTextEdit()
            text.setReadOnly(True)
            text.setPlainText(path.read_text(errors="replace"))
            self.layout.addWidget(text)

        elif suffix in [".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"]:
            title = QLabel(path.name)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout.addWidget(title)
            self.setup_media_player(path, video=False, autoplay=True)

        elif suffix in [".mp4", ".mkv", ".mov", ".avi", ".webm"]:
            self.video = QVideoWidget(self)
            self.layout.addWidget(self.video, 1)
            self.setup_media_player(path, video=True, autoplay=True)

        elif suffix == ".pdf":
            self.setup_pdf_view(path)

        else:
            self.setup_generic_file_view(path)

    def setup_folder_view(self, path):
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(themed_icon("folder").pixmap(256, 256))

        title = QLabel(path.name)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)

        try:
            children = list(path.iterdir())
            folder_count = sum(1 for child in children if child.is_dir())
            file_count = sum(1 for child in children if child.is_file())
            parent = self.parent()
            folder_size = parent.format_size(parent.path_size(path)) if parent else ""
            info = QLabel(f"{folder_count} dossier(s)\n{file_count} fichier(s)\n{folder_size}")
        except Exception:
            info = QLabel("Accès refusé")

        info.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.layout.addStretch()
        self.layout.addWidget(icon_label)
        self.layout.addWidget(title)
        self.layout.addWidget(info)
        self.layout.addStretch()

    def setup_generic_file_view(self, path):
        parent = self.parent()
        icon = parent.icon_for_path(path) if parent else themed_icon("unknown", "text-x-generic")

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(icon.pixmap(220, 220))

        title = QLabel(path.name)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)

        suffix = path.suffix.lower()
        info = QLabel(
            f"Type : {suffix[1:].upper() if suffix else 'Fichier'}\n"
            f"Taille : {self.format_file_size(path)}"
        )
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.layout.addStretch()
        self.layout.addWidget(icon_label)
        self.layout.addWidget(title)
        self.layout.addWidget(info)
        self.layout.addStretch()

    def format_file_size(self, path):
        try:
            size = path.stat().st_size
        except Exception:
            return ""

        units = ["o", "Ko", "Mo", "Go", "To"]
        value = float(size)
        unit = 0

        while value >= 1024 and unit < len(units) - 1:
            value /= 1024
            unit += 1

        return f"{value:.1f} {units[unit]}" if unit else f"{int(value)} o"

    def setup_pdf_view(self, path):
        if not HAS_PDF:
            info = QLabel("Aperçu PDF indisponible.\n\nIl manque probablement le module PyQt6 PDF.")
            info.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout.addWidget(info)
            return

        try:
            self.pdf = QPdfDocument(self)
            status = self.pdf.load(str(path))

            if self.pdf.pageCount() <= 0:
                info = QLabel(f"Impossible d'afficher ce PDF :\n\n{path.name}")
                info.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.layout.addWidget(info)
                return

            arrow_path = Path.home() / "Applications/PrincessFinder/assets/arrow.svg"

            self.pdf_prev_btn = QPushButton()
            self.pdf_next_btn = QPushButton()

            self.pdf_prev_btn.setFixedSize(44, 34)
            self.pdf_next_btn.setFixedSize(44, 34)

            self.pdf_prev_btn.setIcon(rotated_svg_icon(arrow_path, 180, 24))
            self.pdf_next_btn.setIcon(rotated_svg_icon(arrow_path, 0, 24))

            self.pdf_prev_btn.setIconSize(QSize(22, 22))
            self.pdf_next_btn.setIconSize(QSize(22, 22))

            self.pdf_page_label = QLabel()
            self.pdf_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            controls = QHBoxLayout()
            controls.setContentsMargins(0, 0, 0, 0)
            controls.setSpacing(12)
            controls.addStretch()
            controls.addWidget(self.pdf_prev_btn)
            controls.addWidget(self.pdf_page_label)
            controls.addWidget(self.pdf_next_btn)
            controls.addStretch()

            self.layout.addLayout(controls)

            self.pdf_view = QPdfView(self)
            self.pdf_view.setDocument(self.pdf)

            try:
                self.pdf_view.setPageMode(QPdfView.PageMode.SinglePage)
            except Exception:
                pass

            try:
                self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitInView)
            except Exception:
                pass

            self.layout.addWidget(self.pdf_view, 1)

            self.pdf_prev_btn.clicked.connect(lambda: self.change_pdf_page(-1))
            self.pdf_next_btn.clicked.connect(lambda: self.change_pdf_page(1))

            self.pdf_page = 0
            self.jump_pdf_page(0)

        except Exception as e:
            info = QLabel(f"Erreur pendant l'ouverture du PDF :\n\n{path.name}\n\n{e}")
            info.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout.addWidget(info)

    def jump_pdf_page(self, page):
        if not self.pdf or not self.pdf_view:
            return

        page_count = self.pdf.pageCount()

        if page_count <= 0:
            return

        self.pdf_page = max(0, min(page, page_count - 1))

        try:
            navigator = self.pdf_view.pageNavigator()
            navigator.jump(self.pdf_page, QPointF(0, 0))
        except Exception:
            pass

        self.update_pdf_controls()

    def change_pdf_page(self, delta):
        self.jump_pdf_page(self.pdf_page + delta)

    def update_pdf_controls(self):
        if not self.pdf:
            return

        page_count = self.pdf.pageCount()

        if self.pdf_page_label:
            self.pdf_page_label.setText(f"{self.pdf_page + 1} / {page_count}")

        if self.pdf_prev_btn:
            self.pdf_prev_btn.setEnabled(self.pdf_page > 0)

        if self.pdf_next_btn:
            self.pdf_next_btn.setEnabled(self.pdf_page < page_count - 1)

    def setup_media_player(self, path, video=False, autoplay=True):
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)

        self.player.setAudioOutput(self.audio)

        if video and self.video:
            self.player.setVideoOutput(self.video)

        self.player.setSource(QUrl.fromLocalFile(str(path)))

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)

        self.play_btn = QPushButton("Pause" if autoplay else "Play")
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        controls = QHBoxLayout()
        controls.addWidget(self.play_btn)
        controls.addWidget(self.time_label)

        self.layout.addWidget(self.slider)
        self.layout.addLayout(controls)

        self.play_btn.clicked.connect(self.toggle_play)
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.playbackStateChanged.connect(self.update_play_button)

        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderReleased.connect(self.on_slider_released)
        self.slider.sliderMoved.connect(self.on_slider_moved)

        if autoplay:
            self.player.play()

    def toggle_play(self):
        if not self.player:
            return

        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def update_play_button(self, state):
        if not self.play_btn:
            return

        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setText("Pause")
        else:
            self.play_btn.setText("Play")

    def update_duration(self, duration):
        if self.slider:
            self.slider.setRange(0, duration)
        self.refresh_time_label()

    def update_position(self, position):
        if self.slider and not self.user_is_sliding:
            self.slider.setValue(position)
        self.refresh_time_label()

    def on_slider_pressed(self):
        self.user_is_sliding = True

    def on_slider_released(self):
        self.user_is_sliding = False
        if self.player and self.slider:
            self.player.setPosition(self.slider.value())

    def on_slider_moved(self, value):
        self.refresh_time_label(value)

    def refresh_time_label(self, preview_position=None):
        if not self.player or not self.time_label:
            return

        position = preview_position if preview_position is not None else self.player.position()
        duration = self.player.duration()
        self.time_label.setText(f"{self.format_time(position)} / {self.format_time(duration)}")

    def format_time(self, ms):
        seconds = max(0, int(ms / 1000))
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    def cleanup_media(self):
        if self.is_cleaning:
            return

        self.is_cleaning = True

        if self.player:
            try:
                self.player.stop()
                self.player.setVideoOutput(None)
                self.player.setAudioOutput(None)
                self.player.setSource(QUrl())
            except Exception:
                pass

        self.player = None
        self.audio = None
        self.video = None

    def paintEvent(self, event):
        p = QPainter(self)

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        p.setBrush(MODAL_BG)
        p.setPen(QColor(255, 255, 255, 35))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 22, 22)

    def manual_refresh_now(self):
        self.kio_phone_discovery_last = 0.0
        self.refresh_locations(force=True)
        self.size_cache.clear()

        if hasattr(self, "metadata_cache"):
            self.metadata_cache.clear()

        if hasattr(self, "kio_list_cache"):
            self.kio_list_cache.clear()

        if self.is_downloads_mode():
            self.rebuild_rows()
            self.selected_row = min(self.selected_row, max(0, len(self.rows) - 1))

            if self.rows:
                self.set_table_single_selection(self.selected_row)
        else:
            self.rebuild_columns()

        self.directory_snapshot = self.current_directory_snapshot()
        self.update_realtime_watchers()
        self.update()

    def keyPressEvent(self, event):
        parent = self.parent()

        if self.pdf is not None and self.pdf_view is not None:
            if event.key() == Qt.Key.Key_Left:
                self.change_pdf_page(-1)
                event.accept()
                return

            if event.key() == Qt.Key.Key_Right:
                self.change_pdf_page(1)
                event.accept()
                return

        if event.key() == Qt.Key.Key_Down:
            if parent:
                parent.navigate_preview(1)
            event.accept()
            return

        if event.key() == Qt.Key.Key_Up:
            if parent:
                parent.navigate_preview(-1)
            event.accept()
            return

        if event.key() in [Qt.Key.Key_Escape, Qt.Key.Key_Space]:
            self.close()
            if parent:
                parent.raise_()
                parent.activateWindow()
                parent.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            event.accept()
            return

        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.cleanup_media()

        parent = self.parent()
        if parent:
            parent.preview = None
            parent.raise_()
            parent.activateWindow()
            parent.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

        super().closeEvent(event)



class GlobalSearchWorker(QObject):
    finished = pyqtSignal(object)
    progress = pyqtSignal(object)

    def __init__(self, query, pinned_filter, limit):
        super().__init__()
        self.query = query or ""
        self.pinned_filter = pinned_filter or ""
        self.limit = int(limit or 1200)
        self.cancel_requested = False
        self.started_at = time.monotonic()

    def cancel(self):
        self.cancel_requested = True

    def normalize_search_token(self, text):
        token = (text or "").strip().lower()
        aliases = {
            "images": "image", "photo": "image", "photos": "image",
            "picture": "image", "pictures": "image",
            "video": "vidéo", "videos": "vidéo", "vidéos": "vidéo",
            "audios": "audio", "musique": "audio", "musiques": "audio",
            "son": "audio", "sons": "audio",
            "archives": "archive", "zip": "archive",
            "dossiers": "dossier", "folder": "dossier", "folders": "dossier",
            "fichiers": "fichier", "file": "fichier", "files": "fichier",
            "documents": "document", "doc": "document", "texte": "document", "text": "document",
            "apps": "application", "app": "application", "applications": "application",
            "programme": "application", "programmes": "application",
            "scripts": "code", "dev": "code",
            "police": "font", "polices": "font", "font": "font", "fonts": "font",
        }
        return aliases.get(token, token)

    def search_category_extensions(self):
        return {
            "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".heic", ".heif", ".avif", ".ico"},
            "vidéo": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v"},
            "audio": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aiff", ".aif"},
            "pdf": {".pdf"},
            "archive": {".zip", ".gz", ".tar", ".tgz", ".tar.gz", ".xz", ".bz2", ".7z", ".rar", ".zst"},
            "document": {".txt", ".md", ".rtf", ".doc", ".docx", ".odt", ".pages", ".csv", ".tsv", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp", ".key"},
            "code": {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss", ".json", ".xml", ".sh", ".bash", ".zsh", ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java", ".php", ".rb", ".lua", ".sql", ".yaml", ".yml", ".toml", ".ini", ".conf"},
            "application": {".exe", ".msi", ".appimage", ".deb", ".rpm", ".flatpak", ".flatpakref", ".snap", ".desktop", ".run", ".bin"},
            "font": {".ttf", ".otf", ".woff", ".woff2", ".eot"},
            "disque": {".iso", ".img", ".dmg"},
        }

    def should_skip_dir(self, path):
        name = path.name.lower()
        path_str = str(path)

        skip_exact = {
            ".cache", ".git", "__pycache__", "node_modules",
            ".venv", "venv", "env", ".npm", ".cargo", ".rustup",
            ".gradle", ".local/share/Trash", "Trash",
        }

        if name in skip_exact:
            return True

        skip_parts = [
            "/.cache/", "/.local/share/Trash/", "/node_modules/",
            "/__pycache__/", "/.git/", "/.venv/", "/.npm/",
            "/.cargo/", "/.rustup/", "/.gradle/",
        ]

        return any(part in path_str for part in skip_parts)

    def item_matches(self, path, name_lower, is_dir, is_file):
        pinned = self.normalize_search_token(self.pinned_filter)
        query = self.normalize_search_token(self.query)

        def matches_token(token):
            if not token:
                return True

            if token == "dossier":
                return is_dir

            if token == "fichier":
                return is_file

            categories = self.search_category_extensions()
            suffix = path.suffix.lower()
            full_name = path.name.lower()

            if token in categories:
                extensions = categories[token]

                if ".tar.gz" in extensions and full_name.endswith(".tar.gz"):
                    return is_file

                return is_file and suffix in extensions

            if token.startswith("."):
                return is_file and (suffix == token or full_name.endswith(token))

            if is_file and len(token) <= 10 and " " not in token:
                if suffix == f".{token}" or full_name.endswith(f".{token}"):
                    return True

            return token in name_lower

        if pinned and not matches_token(pinned):
            return False

        if query and not matches_token(query):
            return False

        return True

    def run(self):
        results = []
        seen = set()
        scanned = 0

        roots = [Path.home()]

        try:
            for root in roots:
                if self.cancel_requested:
                    break

                if not root.exists() or not root.is_dir():
                    continue

                stack = [root]

                while stack and len(results) < self.limit:
                    if self.cancel_requested:
                        break

                    current = stack.pop()

                    try:
                        with os.scandir(current) as entries:
                            for entry in entries:
                                if self.cancel_requested or len(results) >= self.limit:
                                    break

                                scanned += 1
                                name = entry.name

                                if name.startswith("."):
                                    continue

                                path = Path(entry.path)

                                try:
                                    is_dir = entry.is_dir(follow_symlinks=False)
                                    is_file = entry.is_file(follow_symlinks=False)
                                except Exception:
                                    continue

                                if is_dir and not self.should_skip_dir(path):
                                    stack.append(path)

                                if self.item_matches(path, name.lower(), is_dir, is_file):
                                    key = str(path)

                                    if key not in seen:
                                        seen.add(key)
                                        results.append(path)

                    except Exception:
                        continue

                    if scanned % 1500 == 0:
                        self.progress.emit({
                            "count": len(results),
                            "scanned": scanned,
                            "elapsed": time.monotonic() - self.started_at,
                        })

        finally:
            elapsed = time.monotonic() - self.started_at

            try:
                results = sorted(results, key=lambda p: (not p.is_dir(), p.name.lower()))
            except Exception:
                pass

            self.finished.emit({
                "cancelled": self.cancel_requested,
                "results": results,
                "count": len(results),
                "scanned": scanned,
                "elapsed": elapsed,
                "query": self.query,
                "pinned_filter": self.pinned_filter,
            })


class FileOperationWorker(QObject):
    finished = pyqtSignal(object)
    progress = pyqtSignal(object)

    def __init__(self, operation, sources, target_dir, context):
        super().__init__()
        self.operation = operation
        self.sources = [Path(source) for source in sources]
        self.target_dir = Path(target_dir) if target_dir is not None else None
        self.context = context or {}
        self.total_bytes = 0
        self.done_bytes = 0
        self.started_at = time.monotonic()
        self.last_progress_at = 0.0
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True

    def unique_destination(self, parent, name):
        candidate = parent / name

        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix

        if candidate.is_dir() and not suffix:
            stem = candidate.name
            suffix = ""

        index = 2

        while True:
            new_name = f"{stem} copie {index}{suffix}"
            candidate = parent / new_name

            if not candidate.exists():
                return candidate

            index += 1

    def format_size(self, size):
        units = ["o", "Ko", "Mo", "Go", "To"]
        value = float(max(0, size))
        unit = 0

        while value >= 1024 and unit < len(units) - 1:
            value /= 1024
            unit += 1

        return f"{value:.1f} {units[unit]}" if unit else f"{int(value)} o"

    def format_eta(self, seconds):
        if seconds is None or seconds < 0:
            return "calcul…"

        seconds = int(seconds)

        if seconds < 60:
            return f"{seconds}s"

        minutes = seconds // 60
        seconds = seconds % 60

        if minutes < 60:
            return f"{minutes}m {seconds:02d}s"

        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours}h {minutes:02d}m"

    def path_total_size(self, path):
        try:
            if path.is_symlink():
                return 0

            if path.is_file():
                return path.stat().st_size

            if not path.is_dir():
                return 0

            total = 0
            stack = [path]

            while stack:
                if self.cancel_requested:
                    return total

                current = stack.pop()

                try:
                    with os.scandir(current) as entries:
                        for entry in entries:
                            try:
                                if entry.is_symlink():
                                    continue

                                if entry.is_file(follow_symlinks=False):
                                    total += entry.stat(follow_symlinks=False).st_size
                                elif entry.is_dir(follow_symlinks=False):
                                    stack.append(Path(entry.path))
                            except Exception:
                                continue
                except Exception:
                    continue

            return total

        except Exception:
            return 0

    def compute_total_bytes(self):
        total = 0

        if self.operation in {"copy", "move"}:
            for source in self.sources:
                if source is not None and source.exists():
                    total += self.path_total_size(source)

        elif self.operation in {"trash", "delete"}:
            for source in self.sources:
                if source is not None and source.exists():
                    total += self.path_total_size(source)

        self.total_bytes = max(0, total)
        self.done_bytes = 0
        self.started_at = time.monotonic()
        self.last_progress_at = 0.0
        self.emit_progress(force=True)

    def emit_progress(self, force=False, current_name=""):
        now = time.monotonic()

        if not force and now - self.last_progress_at < 0.12:
            return

        self.last_progress_at = now

        elapsed = max(0.001, now - self.started_at)
        speed = self.done_bytes / elapsed if self.done_bytes > 0 else 0

        if speed > 0 and self.total_bytes > 0:
            eta = max(0, (self.total_bytes - self.done_bytes) / speed)
        else:
            eta = None

        percent = 0

        if self.total_bytes > 0:
            percent = min(100, max(0, int((self.done_bytes / self.total_bytes) * 100)))

        if self.cancel_requested:
            verb = "Annulation"
        elif self.operation == "copy":
            verb = "Copie"
        elif self.operation == "move":
            verb = "Déplacement"
        elif self.operation == "trash":
            verb = "Corbeille"
        elif self.operation == "delete":
            verb = "Suppression"
        else:
            verb = "Opération"

        self.progress.emit({
            "operation": self.operation,
            "verb": verb,
            "done": self.done_bytes,
            "total": self.total_bytes,
            "done_text": self.format_size(self.done_bytes),
            "total_text": self.format_size(self.total_bytes),
            "speed_text": f"{self.format_size(speed)}/s" if speed > 0 else "calcul…",
            "eta_text": self.format_eta(eta),
            "percent": percent,
            "current_name": current_name,
        })

    def copy_file_with_progress(self, source, destination):
        if self.cancel_requested:
            return False

        destination.parent.mkdir(parents=True, exist_ok=True)

        try:
            file_size = source.stat().st_size
        except Exception:
            file_size = 0

        chunk_size = 1024 * 1024 * 4

        try:
            with source.open("rb") as src_file:
                with destination.open("wb") as dst_file:
                    while True:
                        if self.cancel_requested:
                            break

                        chunk = src_file.read(chunk_size)

                        if not chunk:
                            break

                        dst_file.write(chunk)
                        self.done_bytes += len(chunk)
                        self.emit_progress(current_name=source.name)

            if self.cancel_requested:
                try:
                    destination.unlink()
                except Exception:
                    pass

                self.emit_progress(force=True, current_name=source.name)
                return False

        except Exception:
            try:
                if destination.exists():
                    destination.unlink()
            except Exception:
                pass

            raise

        try:
            shutil.copystat(source, destination, follow_symlinks=False)
        except Exception:
            pass

        if file_size == 0:
            self.emit_progress(force=True, current_name=source.name)

        return True

    def copy_dir_with_progress(self, source, destination):
        destination.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copystat(source, destination, follow_symlinks=False)
        except Exception:
            pass

        stack = [(source, destination)]

        while stack:
            if self.cancel_requested:
                return False

            src_dir, dst_dir = stack.pop()

            try:
                with os.scandir(src_dir) as entries:
                    for entry in entries:
                        src_child = Path(entry.path)
                        dst_child = dst_dir / entry.name

                        try:
                            if entry.is_symlink():
                                # On recrée le lien symbolique sans suivre la cible.
                                try:
                                    target = os.readlink(src_child)
                                    os.symlink(target, dst_child)
                                except Exception:
                                    pass

                            elif entry.is_dir(follow_symlinks=False):
                                dst_child.mkdir(parents=True, exist_ok=True)

                                try:
                                    shutil.copystat(src_child, dst_child, follow_symlinks=False)
                                except Exception:
                                    pass

                                stack.append((src_child, dst_child))

                            elif entry.is_file(follow_symlinks=False):
                                if not self.copy_file_with_progress(src_child, dst_child):
                                    return False

                        except Exception:
                            continue

            except Exception:
                continue

        return not self.cancel_requested

    def copy_path_to_destination(self, source, destination):
        if self.cancel_requested:
            return False

        if source.is_dir():
            ok = self.copy_dir_with_progress(source, destination)

            if not ok or self.cancel_requested:
                try:
                    if destination.exists():
                        shutil.rmtree(destination)
                except Exception:
                    pass

                return False

            return True

        return self.copy_file_with_progress(source, destination)

    def move_path_to_destination(self, source, destination):
        if self.cancel_requested:
            return False

        # rename instantané si même filesystem : pas de vraie progression possible,
        # mais c'est quasi immédiat.
        try:
            if source.is_file() and self.target_dir is not None:
                source_dev = source.parent.stat().st_dev
                target_dev = self.target_dir.stat().st_dev

                if source_dev == target_dev:
                    shutil.move(str(source), str(destination))
                    self.done_bytes += self.path_total_size(destination)
                    self.emit_progress(force=True, current_name=source.name)
                    return True
        except Exception:
            pass

        # Cross-device ou dossier : copie progressive puis suppression source.
        ok = self.copy_path_to_destination(source, destination)

        if not ok or self.cancel_requested:
            return False

        try:
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()
        except Exception:
            pass

        return True

    def delete_path_with_progress(self, source):
        size = self.path_total_size(source)

        if source.is_dir():
            shutil.rmtree(source)
        else:
            source.unlink()

        self.done_bytes += size
        self.emit_progress(force=True, current_name=source.name)

    def run(self):
        result = {
            "operation": self.operation,
            "target_dir": self.target_dir,
            "paths": [],
            "context": self.context,
            "errors": [],
        }

        self.compute_total_bytes()

        try:
            if self.operation in {"copy", "move"}:
                if self.target_dir is None or not self.target_dir.is_dir():
                    self.finished.emit(result)
                    return

                for source in self.sources:
                    if self.cancel_requested:
                        result["cancelled"] = True
                        break

                    try:
                        if source is None or not source.exists():
                            continue

                        if source.is_dir():
                            try:
                                source_resolved = source.resolve()
                                target_resolved = self.target_dir.resolve()

                                if target_resolved == source_resolved or source_resolved in target_resolved.parents:
                                    continue
                            except Exception:
                                pass

                        if self.operation == "move":
                            try:
                                if source.parent.resolve() == self.target_dir.resolve():
                                    continue
                            except Exception:
                                pass

                        destination = self.unique_destination(self.target_dir, source.name)

                        if self.operation == "copy":
                            ok = self.copy_path_to_destination(source, destination)
                        else:
                            ok = self.move_path_to_destination(source, destination)

                        if self.cancel_requested:
                            result["cancelled"] = True
                            break

                        if ok:
                            result["paths"].append(destination)

                    except Exception as e:
                        result["errors"].append(str(e))

            elif self.operation == "trash":
                trash_dir = Path.home() / ".local/share/Trash/files"
                trash_dir.mkdir(parents=True, exist_ok=True)

                for source in self.sources:
                    if self.cancel_requested:
                        result["cancelled"] = True
                        break

                    try:
                        if source is None or not source.exists():
                            continue

                        size = self.path_total_size(source)
                        destination = self.unique_destination(trash_dir, source.name)
                        shutil.move(str(source), str(destination))
                        self.done_bytes += size
                        self.emit_progress(force=True, current_name=source.name)
                        result["paths"].append(destination)

                    except Exception as e:
                        result["errors"].append(str(e))

            elif self.operation == "delete":
                for source in self.sources:
                    if self.cancel_requested:
                        result["cancelled"] = True
                        break

                    try:
                        if source is None or not source.exists():
                            continue

                        self.delete_path_with_progress(source)
                        result["paths"].append(source)

                    except Exception as e:
                        result["errors"].append(str(e))

        finally:
            self.emit_progress(force=True)
            self.finished.emit(result)


class PrincessFinder(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("PrincessFinder")
        self.resize(1200, 650)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)

        self.sidebar_w = 220
        self.search_h = 34
        self.header_h = 30
        self.row_h = 30

        self.scroll = 0
        self.hscroll = 0
        self.hscroll_stuck_to_right = True

        self.sort_mode = "Date de l'ajout"
        self.sort_reverse = True

        self.open_dirs = set()
        self.selected_row = 0

        # Sélection multiple :
        # - Téléchargements : indexes de lignes dans self.rows
        # - Colonnes : chemins sélectionnés, pour garder la sélection malgré le tri/refresh
        self.selected_table_rows = set()
        self.selected_column_paths = set()
        self.selection_anchor_table = None
        self.selection_anchor_column = None
        self.selection_anchor_row = None

        self.preview = None
        self.size_cache = {}
        self.metadata_cache = {}
        self.folder_size_enabled = False
        self.deep_media_metadata_enabled = False
        self.auto_refresh_enabled = True
        self.realtime_refresh_enabled = True
        self.realtime_refresh_pending = False
        self.max_list_items_per_dir = 900
        self.max_snapshot_items_per_dir = 120
        self.max_kio_items_per_dir = 300
        self.kio_list_cache = {}
        self.kio_list_cache_ttl = 30.0

        self.column_paths = []
        self.column_items = []
        self.column_selected = []
        self.column_scrolls = []
        self.column_widths_custom = []
        self.active_column = 0
        self.column_selection_enabled = True
        self.disable_next_column_auto_selection = False

        self.resizing_browser_column = None
        self.resize_start_x = 0
        self.resize_start_width = 0

        self.text_preview_cache = {}
        self.pixmap_preview_cache = {}

        self.rename_editor = QLineEdit(self)
        self.rename_editor.hide()
        self.rename_editor.setFrame(False)
        self.rename_editor.setStyleSheet("""
            QLineEdit {
                background-color: rgba(235, 235, 245, 235);
                color: #202026;
                border: 1px solid rgba(255, 255, 255, 90);
                border-radius: 5px;
                padding-left: 4px;
                padding-right: 4px;
                selection-background-color: rgba(120, 130, 150, 120);
            }
        """)
        self.rename_editor.returnPressed.connect(self.finish_rename)
        self.rename_editor.editingFinished.connect(self.finish_rename_if_focus_lost)
        self.rename_target_path = None
        self.rename_column = None
        self.rename_row = None
        self.rename_committing = False

        self.drag_start_position = None
        self.drag_start_path = None
        self.sidebar_drop_index = None
        self.folder_drop_target = None

        self.operation_status_text = ""
        self.operation_status_visible = False
        self.file_operation_thread = None
        self.file_operation_worker = None

        self.search_query = ""
        self.search_pinned_filter = ""
        self.global_search_enabled = True
        self.global_search_active = False
        self.global_search_limit = 1200
        self.global_search_thread = None
        self.global_search_worker = None
        self.perf_log_path = Path.home() / ".cache/princessfinder_perf.log"
        self.last_locations_refresh = 0.0

        self.search_editor = QLineEdit(self)
        self.search_editor.setPlaceholderText("Rechercher... Entrée = partout")
        self.search_editor.setFixedHeight(24)
        self.search_editor.setStyleSheet("""
            QLineEdit {
                background-color: rgba(235, 235, 245, 42);
                color: white;
                border: 1px solid rgba(255, 255, 255, 38);
                border-radius: 8px;
                padding-left: 10px;
                padding-right: 10px;
                selection-background-color: rgba(120, 130, 150, 120);
            }

            QLineEdit:focus {
                background-color: rgba(235, 235, 245, 58);
                border: 1px solid rgba(255, 255, 255, 70);
            }
        """)
        self.search_editor.textChanged.connect(self.on_search_text_changed)
        self.search_editor.returnPressed.connect(self.run_global_search_from_editor)

        self.search_pin_button = QPushButton("Épingler", self)
        self.search_pin_button.setFixedHeight(24)
        self.search_pin_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(235, 235, 245, 34);
                color: white;
                border: 1px solid rgba(255, 255, 255, 34);
                border-radius: 8px;
                padding-left: 10px;
                padding-right: 10px;
            }

            QPushButton:hover {
                background-color: rgba(235, 235, 245, 54);
            }

            QPushButton:pressed {
                background-color: rgba(235, 235, 245, 70);
            }
        """)
        self.search_pin_button.clicked.connect(self.pin_or_unpin_search_filter)
        self.position_search_widgets()

        self.cancel_operation_button = QPushButton("Annuler", self)
        self.cancel_operation_button.hide()
        self.cancel_operation_button.setFixedHeight(26)
        self.cancel_operation_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(235, 80, 95, 90);
                color: white;
                border: 1px solid rgba(255, 255, 255, 55);
                border-radius: 9px;
                padding-left: 12px;
                padding-right: 12px;
            }

            QPushButton:hover {
                background-color: rgba(235, 80, 95, 130);
            }

            QPushButton:pressed {
                background-color: rgba(235, 80, 95, 170);
            }
        """)
        self.cancel_operation_button.clicked.connect(self.cancel_file_operation)

        self.arrow_svg = QSvgRenderer(
            str(Path.home() / "Applications/PrincessFinder/assets/arrow.svg")
        )

        self.icons = {
            "folder": themed_icon("folder"),
            "desktop": themed_icon("user-desktop", "desktop"),
            "documents": themed_icon("folder-documents", "folder-document", "folder"),
            "downloads": themed_icon("folder-download", "folder-downloads", "folder"),
            "music": themed_icon("folder-music", "audio-x-generic", "folder"),
            "pictures": themed_icon("folder-pictures", "folder-image", "image-x-generic", "folder"),
            "videos": themed_icon("folder-videos", "video-x-generic", "folder"),
            "trash": themed_icon("user-trash", "trash-empty", "user-trash-full"),

            "image": themed_icon("image-x-generic", "image-png", "image-jpeg"),
            "audio": themed_icon("audio-x-generic", "audio-x-wav", "audio-mpeg"),
            "video": themed_icon("video-x-generic", "video-mp4", "video-x-matroska"),
            "document": themed_icon("text-x-generic", "x-office-document"),
            "pdf": themed_icon("application-pdf", "x-office-pdf", "application-x-pdf"),
            "archive": themed_icon("package-x-generic", "application-zip", "application-x-compressed-tar"),
            "package": themed_icon("application-x-deb", "package-x-generic", "application-x-executable"),
            "font": themed_icon("font-x-generic", "application-x-font-ttf", "preferences-desktop-font"),
            "code": themed_icon("text-x-script", "text-x-python", "application-x-code", "text-x-generic"),
            "config": themed_icon("text-x-generic-template", "text-x-generic"),
            "spreadsheet": themed_icon("x-office-spreadsheet", "text-csv", "text-x-generic"),
            "presentation": themed_icon("x-office-presentation", "x-office-document"),
            "word": themed_icon("x-office-document", "application-msword"),
            "executable": themed_icon("application-x-executable", "application-x-shellscript"),
            "disk": themed_icon("drive-harddisk", "media-flash", "application-x-iso"),
            "file": themed_icon("unknown", "text-x-generic"),
            "new_folder": themed_icon("folder-new", "folder", "document-new"),
            "new_file": themed_icon("document-new", "text-x-generic", "unknown"),
            "paste": themed_icon("edit-paste", "folder-new", "document-new"),
            "copy": themed_icon("edit-copy", "edit-paste", "document-new"),
            "move_trash": themed_icon("user-trash", "edit-delete", "trash-empty"),
            "delete_permanent": themed_icon("edit-delete", "edit-delete-shred", "user-trash-full", "user-trash"),
            "extract": themed_icon("archive-extract", "application-zip", "package-x-generic"),
            "wallpaper": themed_icon("preferences-desktop-wallpaper", "image-x-generic", "preferences-desktop"),
            "open_vscode": themed_icon("com.visualstudio.code", "code", "text-x-script", "application-x-executable"),
            "open_with": themed_icon("document-open", "application-x-executable", "system-run"),
            "app_kate": themed_icon("org.kde.kate", "kate", "text-editor", "accessories-text-editor"),
            "app_vlc": themed_icon("vlc", "org.videolan.VLC", "video-x-generic"),
            "app_gimp": themed_icon("gimp", "org.gimp.GIMP", "image-x-generic"),
            "app_krita": themed_icon("krita", "org.kde.krita", "image-x-generic"),
            "app_libreoffice": themed_icon("libreoffice-startcenter", "libreoffice-writer", "x-office-document"),
            "drive": themed_icon("drive-removable-media-usb", "drive-removable-media", "drive-harddisk-usb", "drive-harddisk"),
            "phone": themed_icon("phone", "smartphone", "multimedia-player", "camera-photo", "drive-removable-media-usb"),
            "eject": themed_icon("media-eject", "drive-removable-media-eject", "window-close"),
        }

        self.extension_icons = {
            ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
            ".webp": "image", ".bmp": "image", ".svg": "image", ".tif": "image",
            ".tiff": "image", ".ico": "image",

            ".mp3": "audio", ".wav": "audio", ".flac": "audio", ".ogg": "audio",
            ".m4a": "audio", ".aac": "audio", ".opus": "audio", ".aiff": "audio",
            ".aif": "audio",

            ".mp4": "video", ".mkv": "video", ".mov": "video", ".avi": "video",
            ".webm": "video", ".wmv": "video", ".flv": "video", ".m4v": "video",

            ".pdf": "pdf",

            ".zip": "archive", ".gz": "archive", ".tar": "archive", ".tgz": "archive",
            ".xz": "archive", ".bz2": "archive", ".7z": "archive", ".rar": "archive",
            ".zst": "archive", ".appimage": "package",

            ".deb": "package", ".rpm": "package", ".flatpak": "package", ".snap": "package",

            ".ttf": "font", ".otf": "font", ".woff": "font", ".woff2": "font", ".eot": "font",

            ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code",
            ".tsx": "code", ".html": "code", ".css": "code", ".scss": "code",
            ".json": "code", ".xml": "code", ".sh": "code", ".bash": "code",
            ".zsh": "code", ".c": "code", ".cpp": "code", ".h": "code",
            ".hpp": "code", ".rs": "code", ".go": "code", ".java": "code",
            ".php": "code", ".rb": "code", ".lua": "code", ".sql": "code",

            ".txt": "document", ".md": "document", ".rtf": "document", ".log": "document",
            ".doc": "word", ".docx": "word", ".odt": "word",

            ".csv": "spreadsheet", ".tsv": "spreadsheet", ".xls": "spreadsheet",
            ".xlsx": "spreadsheet", ".ods": "spreadsheet",

            ".ppt": "presentation", ".pptx": "presentation", ".odp": "presentation",

            ".conf": "config", ".ini": "config", ".yaml": "config", ".yml": "config",
            ".toml": "config",

            ".exe": "executable", ".bin": "executable", ".run": "executable",

            ".iso": "disk", ".img": "disk", ".dmg": "disk",
        }

        self.preview_text_exts = {
            ".txt", ".md", ".py", ".html", ".css", ".js", ".json",
            ".xml", ".sh", ".conf", ".ini", ".log", ".csv", ".yaml",
            ".yml", ".toml", ".sql"
        }

        self.image_exts = {
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
            ".svg", ".heic", ".heif", ".avif", ".ico"
        }

        self.audio_exts = {
            ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aiff", ".aif"
        }

        self.video_exts = {
            ".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v"
        }

        self.base_locations = [
            ("Bureau", Path.home() / "Bureau", self.icons["desktop"], "base"),
            ("Documents", Path.home() / "Documents", self.icons["documents"], "base"),
            ("Musiques", Path.home() / "Musique", self.icons["music"], "base"),
            ("Images", Path.home() / "Images", self.icons["pictures"], "base"),
            ("Vidéos", Path.home() / "Vidéos", self.icons["videos"], "base"),
            ("Applications", Path.home() / "Applications", self.icons["applications"] if "applications" in self.icons else self.icons["documents"], "base"),
            ("Téléchargements", Path.home() / "Téléchargements", self.icons["downloads"], "base"),
            ("Corbeille", Path.home() / ".local/share/Trash/files", self.icons["trash"], "trash"),
        ]

        self.locations = []
        self.external_drive_paths = []
        self.external_phone_paths = []
        self.kio_phone_urls = {}
        self.kio_virtual_urls = {}
        self.kio_virtual_dirs = set()
        self.kio_phone_discovery_cache = []
        self.kio_phone_discovery_last = 0.0
        self.kio_phone_discovery_ttl = 20.0
        self.refresh_locations(force=True)

        self.startup_target_path = None

        self.current_location = self.downloads_location_index()
        self.root_path = self.locations[self.current_location][1]

        startup_path = self.startup_path_from_arguments()

        if startup_path is not None:
            self.startup_target_path = startup_path
            self.current_location = self.location_index_for_path(startup_path)
            self.root_path = self.locations[self.current_location][1]

        self.locations_timer = QTimer(self)
        self.locations_timer.setInterval(30000)
        self.locations_timer.timeout.connect(self.refresh_locations_live)
        self.locations_timer.start()

        self.directory_snapshot = None
        self.suppress_auto_file_preview_until_click = False

        self.files_timer = QTimer(self)
        self.files_timer.setInterval(15000)
        self.files_timer.timeout.connect(self.live_refresh_files)

        if self.auto_refresh_enabled:
            self.files_timer.start()

        self.realtime_watcher = QFileSystemWatcher(self)
        self.realtime_watcher.directoryChanged.connect(self.on_realtime_directory_changed)
        self.realtime_watcher.fileChanged.connect(self.on_realtime_file_changed)

        self.realtime_refresh_timer = QTimer(self)
        self.realtime_refresh_timer.setSingleShot(True)
        self.realtime_refresh_timer.setInterval(350)
        self.realtime_refresh_timer.timeout.connect(self.apply_realtime_refresh)

        self.rows = []
        self.table_width_ratios = [0.52, 0.14, 0.16, 0.18]

        self.rebuild_rows()
        self.set_table_single_selection(self.selected_row)
        self.rebuild_columns()
        self.apply_startup_target_path()
        self.directory_snapshot = self.current_directory_snapshot()
        self.update_realtime_watchers()

    def perf_log(self, label, elapsed, extra=""):
        try:
            if elapsed < 0.75:
                return

            self.perf_log_path.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with self.perf_log_path.open("a", encoding="utf-8") as f:
                f.write(f"{stamp} | {label} | {elapsed:.3f}s | {extra}\n")
        except Exception:
            pass

    def refresh_ui_later(self, delay_ms=0):
        QTimer.singleShot(delay_ms, self.update)

    def startup_path_from_arguments(self):
        if len(sys.argv) < 2:
            return None

        raw = sys.argv[1].strip()

        if not raw:
            return None

        if raw.startswith("file://"):
            parsed = urlparse(raw)
            raw = unquote(parsed.path)

        path = Path(raw).expanduser()

        try:
            path = path.resolve()
        except Exception:
            pass

        if path.exists() and path.is_file():
            path = path.parent

        if path.exists() and path.is_dir():
            return path

        return None

    def location_index_for_path(self, path):
        # Si le dossier ouvert correspond à un onglet connu OU se trouve dedans,
        # on garde l'onglet parent sélectionné.
        #
        # Exemple :
        #   ~/Bureau/MonDossier  -> onglet Bureau sélectionné
        #   ~/Documents/Projet   -> onglet Documents sélectionné
        best_index = None
        best_depth = -1

        try:
            resolved_path = path.resolve()
        except Exception:
            resolved_path = path

        for i, (_name, location_path, _icon, kind) in enumerate(self.locations):
            if kind == "temp":
                continue

            try:
                resolved_location = location_path.resolve()
            except Exception:
                resolved_location = location_path

            try:
                if resolved_path == resolved_location or resolved_location in resolved_path.parents:
                    depth = len(resolved_location.parts)

                    if depth > best_depth:
                        best_depth = depth
                        best_index = i
            except Exception:
                if location_path == path:
                    best_index = i
                    break

        if best_index is not None:
            return best_index

        # Fallback seulement si le dossier n'est dans aucun onglet connu :
        # on ajoute un onglet temporaire.
        self.locations.append(
            (path.name or str(path), path, self.icons["folder"], "temp")
        )

        return len(self.locations) - 1

    def apply_startup_target_path(self):
        target = getattr(self, "startup_target_path", None)

        if target is None:
            return

        try:
            target = target.resolve()
            root = self.root_path.resolve()
        except Exception:
            root = self.root_path

        # Si le dossier ouvert est dans l'onglet courant, on garde l'onglet comme racine
        # et on sélectionne le dossier dans la première colonne.
        try:
            if target == root:
                self.startup_target_path = None
                return

            if root not in target.parents:
                self.startup_target_path = None
                return

            relative_parts = target.relative_to(root).parts
        except Exception:
            self.startup_target_path = None
            return

        if not relative_parts:
            self.startup_target_path = None
            return

        current_parent = root
        active_col = 0

        for part in relative_parts:
            wanted = current_parent / part

            if not (0 <= active_col < len(self.column_items)):
                break

            items = self.column_items[active_col]

            try:
                row = items.index(wanted)
            except ValueError:
                break

            self.column_selected[active_col] = row
            self.set_column_single_selection(wanted, active_col, row)
            self.ensure_row_visible_in_column(active_col, row)

            # Ouvre la colonne suivante pour ce dossier, sans ouvrir automatiquement
            # son premier enfant. On veut arriver sur le dossier lui-même.
            self.open_path_in_next_column(wanted, active_col, auto_child=False)

            current_parent = wanted
            active_col += 1

        self.active_column = max(0, min(active_col - 1, len(self.column_items) - 1))
        self.pin_horizontal_scroll_to_right()
        self.startup_target_path = None
        self.update()

    def prune_stale_file_preview_columns(self):
        if self.is_downloads_mode():
            return

        if not self.column_paths:
            return

        keep_count = len(self.column_paths)

        for i, column_path in enumerate(self.column_paths):
            if i == 0:
                continue

            if self.is_kio_virtual_path(column_path):
                continue

            # Si une colonne pointe vers un fichier supprimé/déplacé,
            # elle ne doit plus rester affichée.
            if column_path.is_file():
                if not column_path.exists():
                    keep_count = i
                    break

                # Une colonne fichier est valide seulement si le fichier est
                # encore présent dans la colonne précédente.
                previous_items = self.column_items[i - 1] if i - 1 < len(self.column_items) else []

                if column_path not in previous_items:
                    keep_count = i
                    break

            # Si une colonne dossier n'existe plus, on coupe aussi.
            elif not column_path.exists():
                keep_count = i
                break

        if keep_count < len(self.column_paths):
            self.column_paths = self.column_paths[:keep_count]
            self.column_items = self.column_items[:keep_count]
            self.column_selected = self.column_selected[:keep_count]
            self.column_scrolls = self.column_scrolls[:keep_count]
            self.active_column = max(0, min(self.active_column, len(self.column_items) - 1))

            self.selected_column_paths = {
                path for path in self.selected_column_paths if path.exists()
            }

    def update_realtime_watchers(self):
        if not getattr(self, "realtime_refresh_enabled", False):
            return

        if not hasattr(self, "realtime_watcher"):
            return

        try:
            old_dirs = self.realtime_watcher.directories()
            old_files = self.realtime_watcher.files()

            if old_dirs:
                self.realtime_watcher.removePaths(old_dirs)

            if old_files:
                self.realtime_watcher.removePaths(old_files)
        except Exception:
            pass

        dirs = []
        files = []

        for directory in self.watched_directories():
            try:
                if self.is_kio_virtual_path(directory):
                    continue

                if directory.exists() and directory.is_dir():
                    dirs.append(str(directory))
            except Exception:
                continue

        if not self.is_downloads_mode():
            for path in getattr(self, "column_paths", []):
                try:
                    if not self.is_kio_virtual_path(path) and path.exists() and path.is_file():
                        files.append(str(path))
                except Exception:
                    continue

        dirs = sorted(set(dirs))[:64]
        files = sorted(set(files))[:64]

        try:
            if dirs:
                self.realtime_watcher.addPaths(dirs)

            if files:
                self.realtime_watcher.addPaths(files)
        except Exception:
            pass

    def on_realtime_directory_changed(self, _path):
        self.schedule_realtime_refresh()

    def on_realtime_file_changed(self, _path):
        self.schedule_realtime_refresh()

    def schedule_realtime_refresh(self):
        if not getattr(self, "realtime_refresh_enabled", False):
            return

        if getattr(self, "file_operation_thread", None) is not None:
            return

        if getattr(self, "global_search_thread", None) is not None:
            return

        if hasattr(self, "rename_editor") and self.rename_editor.isVisible():
            return

        if any(self.is_kio_virtual_path(path) for path in getattr(self, "column_paths", [])):
            return

        self.realtime_refresh_pending = True

        if hasattr(self, "realtime_refresh_timer"):
            self.realtime_refresh_timer.start()

    def apply_realtime_refresh(self):
        if not getattr(self, "realtime_refresh_pending", False):
            return

        self.realtime_refresh_pending = False
        self.live_refresh_files()
        self.update_realtime_watchers()

    def watched_directories(self):
        directories = set()

        if self.is_downloads_mode():
            directories.add(self.root_path)

            for path, _depth in self.rows:
                if path.is_dir() and path in self.open_dirs:
                    directories.add(path)

            return directories

        for path in self.column_paths:
            if self.is_kio_virtual_path(path):
                continue

            if path.is_dir():
                directories.add(path)
            elif path.exists():
                directories.add(path.parent)

        return directories

    def directory_state(self, path):
        # Snapshot léger : on évite de stat() des milliers de fichiers toutes les 5s.
        # On prend juste un échantillon des noms + un compteur approximatif.
        try:
            children = []
            count = 0

            for child in path.iterdir():
                if child.name.startswith("."):
                    continue

                count += 1

                if count > self.max_snapshot_items_per_dir:
                    children.append(("__TOO_MANY_ITEMS__", count))
                    break

                children.append((child.name, child.is_dir()))

            return tuple(sorted(children))

        except Exception:
            return tuple()

    def current_directory_snapshot(self):
        snapshot = {}

        for directory in self.watched_directories():
            try:
                key = str(directory.resolve())
            except Exception:
                key = str(directory)

            snapshot[key] = self.directory_state(directory)

        return snapshot

    def live_refresh_files(self):
        if not (
            getattr(self, "auto_refresh_enabled", False)
            or getattr(self, "realtime_refresh_enabled", False)
        ):
            return

        if self.rename_editor.isVisible():
            return

        if getattr(self, "file_operation_thread", None) is not None:
            return

        if hasattr(self, "search_editor") and self.search_editor.hasFocus():
            return

        # Les téléphones MTP/KIO peuvent être très lents : pas de rescan automatique.
        if any(self.is_kio_virtual_path(path) for path in getattr(self, "column_paths", [])):
            return

        new_snapshot = self.current_directory_snapshot()

        if new_snapshot == self.directory_snapshot:
            return

        selected_paths = self.current_selected_paths()
        selected_path = self.selected_path()

        self.size_cache.clear()
        self.metadata_cache.clear()
        self.text_preview_cache.clear()
        self.pixmap_preview_cache.clear()

        if (
            getattr(self, "suppress_auto_file_preview_until_click", False)
            and not self.is_downloads_mode()
            and len(self.column_paths) > 1
            and self.column_paths[-1].is_file()
        ):
            self.column_paths = self.column_paths[:-1]
            self.column_items = self.column_items[:-1]
            self.column_selected = self.column_selected[:-1]
            self.column_scrolls = self.column_scrolls[:-1]
            self.active_column = max(0, len(self.column_items) - 1)

        if self.is_downloads_mode():
            previous_selected_row = self.selected_row

            self.rebuild_rows()

            restored_rows = set()

            for i, (path, _depth) in enumerate(self.rows):
                if path in selected_paths:
                    restored_rows.add(i)

            if restored_rows:
                self.selected_table_rows = restored_rows
                self.selected_row = min(restored_rows)
            elif selected_path:
                for i, (path, _depth) in enumerate(self.rows):
                    if path == selected_path:
                        self.selected_row = i
                        self.set_table_single_selection(i)
                        break
                else:
                    self.selected_row = min(previous_selected_row, max(0, len(self.rows) - 1))
                    if self.rows:
                        self.set_table_single_selection(self.selected_row)
            elif self.rows:
                self.selected_row = min(previous_selected_row, len(self.rows) - 1)
                self.set_table_single_selection(self.selected_row)

            self.ensure_selected_visible()

        else:
            old_column_paths = list(self.column_paths)
            old_active_column = self.active_column
            old_selected_paths = set(self.selected_column_paths)

            for col_index, column_path in enumerate(old_column_paths):
                if col_index >= len(self.column_items):
                    break

                if column_path.is_dir():
                    self.column_items[col_index] = self.safe_children_by_name(column_path)

                    if col_index < len(self.column_selected):
                        selected_row = self.column_selected[col_index]
                        self.column_selected[col_index] = min(
                            selected_row,
                            max(0, len(self.column_items[col_index]) - 1)
                        )

            self.prune_stale_file_preview_columns()

            self.selected_column_paths = {
                path for path in old_selected_paths if path.exists()
            }

            if selected_path and selected_path.exists():
                restored = False

                for col_index, items in enumerate(self.column_items):
                    if selected_path in items:
                        row = items.index(selected_path)
                        self.column_selected[col_index] = row
                        self.active_column = col_index
                        self.ensure_row_visible_in_column(col_index, row)
                        restored = True
                        break

                if not restored:
                    self.active_column = min(old_active_column, max(0, len(self.column_items) - 1))
            else:
                self.active_column = min(old_active_column, max(0, len(self.column_items) - 1))

            self.prune_stale_file_preview_columns()
            self.pin_horizontal_scroll_to_right()

        self.directory_snapshot = self.current_directory_snapshot()
        self.update_realtime_watchers()
        self.update()

    def discover_external_drives(self):
        user_name = Path.home().name

        candidates = []

        # Emplacements classiques des montages utilisateurs.
        roots = [
            Path("/media") / user_name,
            Path("/run/media") / user_name,
            Path("/mnt"),
        ]

        for root in roots:
            if not root.exists() or not root.is_dir():
                continue

            try:
                for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                    if child.name.startswith("."):
                        continue

                    if child.exists() and child.is_dir():
                        candidates.append(child)

            except Exception:
                continue

        # Détection plus fiable : liste des points de montage réels.
        # Ça attrape les disques montés par KDE même si leur chemin varie.
        try:
            result = subprocess.run(
                ["findmnt", "-rn", "-o", "TARGET,SOURCE,FSTYPE"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )

            system_prefixes = (
                "/",
                "/boot",
                "/boot/efi",
                "/home",
                "/proc",
                "/sys",
                "/dev",
                "/run",
                "/tmp",
                "/var",
                "/snap",
                "/flatpak",
            )

            allowed_prefixes = (
                f"/media/{user_name}/",
                f"/run/media/{user_name}/",
                "/mnt/",
            )

            for line in result.stdout.splitlines():
                parts = line.split()

                if len(parts) < 1:
                    continue

                target = parts[0]
                target_path = Path(target)

                if not target_path.exists() or not target_path.is_dir():
                    continue

                # On garde en priorité les montages dans les chemins utilisateurs.
                if target.startswith(allowed_prefixes):
                    candidates.append(target_path)
                    continue

                # Fallback : certains montages externes peuvent apparaître ailleurs,
                # mais on évite les montages système évidents.
                if target in system_prefixes:
                    continue

                if any(target.startswith(prefix + "/") for prefix in system_prefixes if prefix != "/"):
                    continue

                if target.startswith("/media/") or target.startswith("/run/media/"):
                    candidates.append(target_path)

        except Exception:
            pass

        drives = []
        seen = set()

        for path in candidates:
            try:
                resolved = str(path.resolve())
            except Exception:
                resolved = str(path)

            if resolved in seen:
                continue

            # Évite d'ajouter les dossiers système ou le home comme volumes.
            try:
                if path == Path.home() or Path.home() in path.parents and not str(path).startswith(("/media/", "/run/media/", "/mnt/")):
                    continue
            except Exception:
                pass

            seen.add(resolved)
            drives.append(path)

        return sorted(drives, key=lambda p: p.name.lower())

    def clean_android_mount_name(self, path):
        name = path.name

        if name.startswith("mtp:host="):
            name = name.replace("mtp:host=", "")

        name = unquote(name)
        name = name.replace("_", " ").replace("%20", " ").strip()

        if not name:
            return "Android"

        if len(name) > 28 or ":" in name:
            return "Android"

        return name

    def discover_android_devices(self):
        uid = os.getuid()
        candidates = []

        # Cas où MTP est exposé comme vrai dossier local par GVFS.
        gvfs_root = Path(f"/run/user/{uid}/gvfs")

        if gvfs_root.exists() and gvfs_root.is_dir():
            try:
                for child in sorted(gvfs_root.iterdir(), key=lambda p: p.name.lower()):
                    if child.name.startswith("mtp:") and child.is_dir():
                        candidates.append(child)
            except Exception:
                pass

        # Cas KDE kio-fuse si disponible.
        run_user = Path(f"/run/user/{uid}")

        try:
            for kio_root in run_user.glob("kio-fuse-*"):
                if not kio_root.is_dir():
                    continue

                for child in sorted(kio_root.iterdir(), key=lambda p: p.name.lower()):
                    lowered = child.name.lower()

                    if child.is_dir() and (
                        lowered.startswith("mtp:")
                        or "mtp" in lowered
                        or "android" in lowered
                    ):
                        candidates.append(child)
        except Exception:
            pass

        phones = []
        seen = set()

        for path in candidates:
            try:
                resolved = str(path.resolve())
            except Exception:
                resolved = str(path)

            if resolved in seen:
                continue

            seen.add(resolved)
            phones.append(path)

        return sorted(phones, key=lambda p: self.clean_android_mount_name(p).lower())

    def kio_client_command(self):
        for command in ("kioclient6", "kioclient5"):
            if self.command_exists(command):
                return command

        return None

    def discover_kio_mtp_phones(self, force=False):
        now = time.monotonic()

        if (
            not force
            and self.kio_phone_discovery_cache
            and now - self.kio_phone_discovery_last < self.kio_phone_discovery_ttl
        ):
            return list(self.kio_phone_discovery_cache)

        command = self.kio_client_command()

        if not command:
            return []

        try:
            result = subprocess.run(
                [command, "ls", "mtp:/"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1.5,
                check=False,
            )
        except Exception:
            if self.kio_phone_discovery_cache:
                return list(self.kio_phone_discovery_cache)

            return []

        phones = []

        for line in result.stdout.splitlines():
            name = line.strip()

            if not name or name == "." or name == "..":
                continue

            url = "mtp:/" + quote(name)
            fake_path = Path(f"/__princessfinder_kio_mtp__/{name}")

            phones.append((name, fake_path, url))

        self.kio_phone_discovery_cache = list(phones)
        self.kio_phone_discovery_last = now

        return phones

    def is_phone_location(self, index):
        return self.location_kind(index) in {"phone", "phone_kio"}

    def is_removable_location(self, index):
        return self.location_kind(index) in {"drive", "phone", "phone_kio"}

    def is_kio_virtual_path(self, path):
        try:
            return str(path).startswith("/__princessfinder_kio_mtp__/")
        except Exception:
            return False

    def kio_url_for_path(self, path):
        return self.kio_virtual_urls.get(str(path), "")

    def kio_path_is_dir(self, path):
        if not self.is_kio_virtual_path(path):
            return False

        if str(path) in self.kio_virtual_dirs:
            return True

        # Heuristique : dans MTP, les dossiers ont souvent peu/pas d'extension.
        # On garde ça pour permettre de descendre dans DCIM, Download, Pictures, etc.
        return path.suffix == ""

    def open_kio_phone(self, index):
        if not (0 <= index < len(self.locations)):
            return

        name, path, _icon, kind = self.locations[index]

        if kind != "phone_kio":
            return

        url = self.kio_phone_urls.get(str(path), "")

        if not url:
            return

        self.current_location = index
        self.root_path = path
        self.open_dirs.clear()
        self.scroll = 0
        self.hscroll = 0
        self.hscroll_stuck_to_right = True
        self.selected_row = 0
        self.clear_multi_selection()
        self.active_column = 0
        self.size_cache.clear()
        self.metadata_cache.clear()
        self.text_preview_cache.clear()
        self.pixmap_preview_cache.clear()
        self.close_preview()

        self.kio_virtual_urls[str(path)] = url
        self.kio_virtual_dirs.add(str(path))

        self.column_selection_enabled = False
        self.disable_next_column_auto_selection = True
        self.rebuild_columns()
        self.update_realtime_watchers()
        self.update()

    def open_kio_url_external(self, url):
        if not url:
            return

        command = self.kio_client_command()

        if command:
            try:
                subprocess.Popen(
                    [command, "exec", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                pass

        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def list_kio_children(self, path):
        url = self.kio_url_for_path(path)

        if not url:
            return []

        now = time.monotonic()
        cache_key = str(path)
        cached = self.kio_list_cache.get(cache_key)

        if cached:
            cached_time, cached_children = cached

            if now - cached_time < self.kio_list_cache_ttl:
                return list(cached_children)

        command = self.kio_client_command()

        if not command:
            return []

        try:
            result = subprocess.run(
                [command, "ls", url],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3.0,
                check=False,
            )
        except Exception:
            return []

        children = []
        count = 0

        for line in result.stdout.splitlines():
            name = line.strip()

            if not name or name in {".", ".."}:
                continue

            if name.startswith("Listing "):
                continue

            count += 1

            if count > self.max_kio_items_per_dir:
                break

            fake_child = path / name
            child_url = url.rstrip("/") + "/" + quote(name)

            self.kio_virtual_urls[str(fake_child)] = child_url

            if "." not in name:
                self.kio_virtual_dirs.add(str(fake_child))

            children.append(fake_child)

        self.kio_list_cache[cache_key] = (now, tuple(children))
        return children

    def kio_file_type(self, path):
        if self.kio_path_is_dir(path):
            return "Dossier Android"

        suffix = path.suffix.lower()

        if suffix:
            return self.file_type_for_suffix(suffix)

        return "Fichier Android"

    def refresh_locations(self, force=False):
        current_path = None

        if hasattr(self, "locations") and self.locations and hasattr(self, "current_location"):
            if 0 <= self.current_location < len(self.locations):
                current_path = self.locations[self.current_location][1]

        self.external_drive_paths = self.discover_external_drives()
        self.external_phone_paths = self.discover_android_devices()
        self.kio_phone_urls = {}

        self.locations = list(self.base_locations)

        for drive_path in self.external_drive_paths:
            self.locations.append(
                (drive_path.name, drive_path, self.icons["drive"], "drive")
            )

        for phone_path in self.external_phone_paths:
            self.locations.append(
                (self.clean_android_mount_name(phone_path), phone_path, self.icons["phone"], "phone")
            )

        for phone_name, fake_path, kio_url in self.discover_kio_mtp_phones(force=force):
            # Si un vrai dossier local existe déjà pour ce téléphone, pas besoin de doublon KIO.
            already_local = False

            for local_phone in self.external_phone_paths:
                if self.clean_android_mount_name(local_phone).lower() == phone_name.lower():
                    already_local = True
                    break

            if already_local:
                continue

            self.kio_phone_urls[str(fake_path)] = kio_url
            self.kio_virtual_urls[str(fake_path)] = kio_url
            self.kio_virtual_dirs.add(str(fake_path))
            self.locations.append(
                (phone_name, fake_path, self.icons["phone"], "phone_kio")
            )

        if current_path is not None:
            known_paths = [path for _name, path, _icon, _kind in self.locations]

            inside_known_location = False

            for known_path in known_paths:
                try:
                    resolved_current = current_path.resolve()
                    resolved_known = known_path.resolve()

                    if resolved_current == resolved_known or resolved_known in resolved_current.parents:
                        inside_known_location = True
                        break
                except Exception:
                    continue

            if (
                not inside_known_location
                and current_path not in known_paths
                and current_path.exists()
                and current_path.is_dir()
            ):
                self.locations.append(
                    (current_path.name or str(current_path), current_path, self.icons["folder"], "temp")
                )

            for i, (_name, path, _icon, _kind) in enumerate(self.locations):
                if path == current_path:
                    self.current_location = i
                    break
            else:
                self.current_location = min(self.current_location, max(0, len(self.locations) - 1))

    def refresh_locations_live(self):
        if getattr(self, "file_operation_thread", None) is not None:
            return

        if getattr(self, "global_search_thread", None) is not None:
            return

        if hasattr(self, "search_editor") and self.search_editor.hasFocus():
            return

        if hasattr(self, "rename_editor") and self.rename_editor.isVisible():
            return

        now = time.monotonic()

        if now - getattr(self, "last_locations_refresh", 0.0) < 25.0:
            return

        self.last_locations_refresh = now
        started = time.monotonic()

        old_paths = [path for _name, path, _icon, _kind in self.locations]
        current_path = None

        if 0 <= self.current_location < len(self.locations):
            current_path = self.locations[self.current_location][1]

        self.refresh_locations()

        new_paths = [path for _name, path, _icon, _kind in self.locations]

        elapsed = time.monotonic() - started
        self.perf_log("refresh_locations_live", elapsed)

        if old_paths == new_paths:
            return

        if current_path in new_paths:
            self.current_location = new_paths.index(current_path)
        else:
            self.current_location = min(self.current_location, max(0, len(self.locations) - 1))

            if 0 <= self.current_location < len(self.locations):
                self.root_path = self.locations[self.current_location][1]

        self.update()

    def location_kind(self, index):
        if 0 <= index < len(self.locations):
            return self.locations[index][3]

        return None

    def is_drive_location(self, index):
        return self.location_kind(index) == "drive"

    def is_trash_mode(self):
        if not (0 <= self.current_location < len(self.locations)):
            return False

        try:
            return self.locations[self.current_location][3] == "trash"
        except Exception:
            return False

    def downloads_location_index(self):
        downloads_path = Path.home() / "Téléchargements"

        for i, (name, path, _icon, _kind) in enumerate(self.locations):
            try:
                if path.resolve() == downloads_path.resolve():
                    return i
            except Exception:
                if name == "Téléchargements":
                    return i

        return min(5, max(0, len(self.locations) - 1))

    def is_downloads_mode(self):
        if not (0 <= self.current_location < len(self.locations)):
            return False

        name, path, _icon, _kind = self.locations[self.current_location]
        downloads_path = Path.home() / "Téléchargements"

        try:
            return path.resolve() == downloads_path.resolve()
        except Exception:
            return name == "Téléchargements"

    def is_file_preview_column(self, col_index):
        if not (0 <= col_index < len(self.column_items)):
            return False

        items = self.column_items[col_index]

        return (
            len(items) == 1
            and items[0].is_file()
            and self.column_paths[col_index].is_file()
        )

    def paintEvent(self, event):
        p = QPainter(self)

        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        p.fillRect(0, 0, self.width(), self.height(), RIGHT)

        p.save()
        p.setClipRect(
            QRect(
                int(self.sidebar_w),
                0,
                int(max(0, self.width() - self.sidebar_w)),
                int(self.height())
            )
        )

        if self.is_downloads_mode():
            self.draw_table_view(p)
        else:
            self.draw_column_browser(p)

        p.restore()

        self.draw_sidebar(p)

        if hasattr(self, "search_editor"):
            self.search_editor.raise_()
            self.search_pin_button.raise_()

        if hasattr(self, "cancel_operation_button"):
            self.cancel_operation_button.raise_()

        self.draw_operation_status(p)

    def position_cancel_operation_button(self):
        if not hasattr(self, "cancel_operation_button"):
            return

        button_w = 92
        button_h = 26
        x = self.width() - button_w - 28
        y = self.height() - button_h - 30
        self.cancel_operation_button.setGeometry(x, y, button_w, button_h)

    def cancel_file_operation(self):
        worker = getattr(self, "file_operation_worker", None)

        if worker is None:
            return

        try:
            worker.cancel()
        except Exception:
            pass

        self.set_operation_status("Annulation en cours…")
        self.cancel_operation_button.setEnabled(False)

    def set_operation_status(self, text):
        self.operation_status_text = text
        self.operation_status_visible = bool(text)

        if hasattr(self, "cancel_operation_button"):
            active = getattr(self, "file_operation_worker", None) is not None
            self.position_cancel_operation_button()
            self.cancel_operation_button.setVisible(active)
            self.cancel_operation_button.setEnabled(active)

        self.update()

    def clear_operation_status(self):
        self.operation_status_text = ""
        self.operation_status_visible = False

        if hasattr(self, "cancel_operation_button"):
            self.cancel_operation_button.hide()
            self.cancel_operation_button.setEnabled(True)

        self.update()

    def start_file_operation(self, label, operation, sources, target_dir=None, context=None):
        if getattr(self, "file_operation_thread", None) is not None:
            return

        valid_sources = [
            Path(source)
            for source in sources
            if source is not None and Path(source).exists()
        ]

        if not valid_sources:
            return

        self.set_operation_status(label)

        thread = QThread(self)
        worker = FileOperationWorker(operation, valid_sources, target_dir, context or {})
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self.update_file_operation_progress)
        worker.finished.connect(self.finish_file_operation)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self.file_operation_thread = thread
        self.file_operation_worker = worker
        self.position_cancel_operation_button()
        self.cancel_operation_button.show()
        self.cancel_operation_button.setEnabled(True)
        thread.start()

    def update_file_operation_progress(self, info):
        if not isinstance(info, dict):
            return

        verb = info.get("verb", "Opération")
        percent = info.get("percent", 0)
        done_text = info.get("done_text", "")
        total_text = info.get("total_text", "")
        speed_text = info.get("speed_text", "calcul…")
        eta_text = info.get("eta_text", "calcul…")
        current_name = info.get("current_name", "")

        if info.get("total", 0) > 0:
            text = f"{verb}… {percent}% · {done_text}/{total_text} · {speed_text} · reste {eta_text}"
        else:
            text = f"{verb}… calcul de la taille…"

        if current_name:
            text += f" · {current_name[:36]}"

        self.set_operation_status(text)

    def finish_file_operation(self, result):
        self.file_operation_thread = None
        self.file_operation_worker = None

        operation = result.get("operation")
        cancelled = bool(result.get("cancelled"))
        paths = result.get("paths", [])

        if hasattr(self, "cancel_operation_button"):
            self.cancel_operation_button.hide()
            self.cancel_operation_button.setEnabled(True)
        context = result.get("context", {})
        target_dir = result.get("target_dir")

        self.sidebar_drop_index = None
        self.folder_drop_target = None
        self.clear_multi_selection()
        self.size_cache.clear()
        self.metadata_cache.clear()
        self.text_preview_cache.clear()
        self.pixmap_preview_cache.clear()

        if operation in {"copy", "move"}:
            if context.get("kind") == "drop":
                target_location_index = context.get("target_location_index", self.current_location)
                self.suppress_auto_file_preview_until_click = True
                self.refresh_after_drop(target_dir, paths, target_location_index)

            elif context.get("kind") == "paste":
                col_index = context.get("col_index", 0)

                if paths:
                    self.refresh_column_after_new_item(target_dir, paths[-1], col_index)
                else:
                    self.directory_snapshot = self.current_directory_snapshot()
                    self.update()

        elif operation in {"trash", "delete"}:
            if self.is_downloads_mode():
                self.rebuild_rows()
                self.selected_row = min(self.selected_row, max(0, len(self.rows) - 1))

                if self.rows:
                    self.set_table_single_selection(self.selected_row)
                    self.ensure_selected_visible()

            else:
                col_index = context.get("col_index", self.active_column)
                parent_dirs = context.get("parent_dirs", [])

                parent_dir = Path(parent_dirs[0]) if parent_dirs else None

                if parent_dir is not None:
                    parent_col = None

                    for i, column_path in enumerate(self.column_paths):
                        if column_path == parent_dir and not self.is_file_preview_column(i):
                            parent_col = i
                            break

                    if parent_col is None:
                        parent_col = max(0, min(col_index, len(self.column_paths) - 1))

                    if 0 <= parent_col < len(self.column_items):
                        refreshed = self.safe_children_by_name(parent_dir)

                        self.column_paths = self.column_paths[:parent_col + 1]
                        self.column_items = self.column_items[:parent_col + 1]
                        self.column_selected = self.column_selected[:parent_col + 1]
                        self.column_scrolls = self.column_scrolls[:parent_col + 1]

                        self.column_paths[parent_col] = parent_dir
                        self.column_items[parent_col] = refreshed

                        if refreshed:
                            self.column_selected[parent_col] = min(
                                self.column_selected[parent_col],
                                len(refreshed) - 1
                            )
                            self.set_column_single_selection(
                                refreshed[self.column_selected[parent_col]],
                                parent_col,
                                self.column_selected[parent_col]
                            )
                        else:
                            self.column_selected[parent_col] = 0

                        self.active_column = parent_col
                        self.pin_horizontal_scroll_to_right()

        self.directory_snapshot = self.current_directory_snapshot()

        if cancelled:
            self.set_operation_status("Opération annulée")
            QTimer.singleShot(1200, self.clear_operation_status)
        else:
            self.clear_operation_status()

        self.update()

    def content_top(self):
        return self.search_h + self.header_h

    def position_search_widgets(self):
        if not hasattr(self, "search_editor"):
            return

        button_w = 106
        editor_w = 180
        y = 5
        x = max(self.sidebar_w + 12, self.width() - editor_w - button_w - 18)

        self.search_editor.setGeometry(x, y, editor_w, 24)
        self.search_pin_button.setGeometry(x + editor_w + 8, y, button_w, 24)

    def search_is_active(self):
        return bool(self.search_query.strip() or self.search_pinned_filter.strip())

    def normalize_search_token(self, text):
        token = text.strip().lower()
        aliases = {
            "images": "image",
            "photo": "image",
            "photos": "image",
            "picture": "image",
            "pictures": "image",

            "video": "vidéo",
            "videos": "vidéo",
            "vidéos": "vidéo",

            "audios": "audio",
            "musique": "audio",
            "musiques": "audio",
            "son": "audio",
            "sons": "audio",

            "archives": "archive",
            "zip": "archive",

            "dossiers": "dossier",
            "folder": "dossier",
            "folders": "dossier",

            "fichiers": "fichier",
            "file": "fichier",
            "files": "fichier",

            "documents": "document",
            "doc": "document",
            "texte": "document",
            "text": "document",

            "apps": "application",
            "app": "application",
            "applications": "application",
            "programme": "application",
            "programmes": "application",

            "scripts": "code",
            "dev": "code",

            "police": "font",
            "polices": "font",
            "font": "font",
            "fonts": "font",
        }
        return aliases.get(token, token)

    def search_category_extensions(self):
        return {
            "image": {
                ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
                ".svg", ".heic", ".heif", ".avif", ".ico"
            },
            "vidéo": {
                ".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v"
            },
            "audio": {
                ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aiff", ".aif"
            },
            "pdf": {
                ".pdf"
            },
            "archive": {
                ".zip", ".gz", ".tar", ".tgz", ".tar.gz", ".xz", ".bz2", ".7z", ".rar", ".zst"
            },
            "document": {
                ".txt", ".md", ".rtf", ".doc", ".docx", ".odt", ".pages",
                ".csv", ".tsv", ".xls", ".xlsx", ".ods",
                ".ppt", ".pptx", ".odp", ".key"
            },
            "code": {
                ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
                ".json", ".xml", ".sh", ".bash", ".zsh", ".c", ".cpp", ".h",
                ".hpp", ".rs", ".go", ".java", ".php", ".rb", ".lua", ".sql",
                ".yaml", ".yml", ".toml", ".ini", ".conf"
            },
            "application": {
                ".exe", ".msi", ".appimage", ".deb", ".rpm", ".flatpak",
                ".flatpakref", ".snap", ".desktop", ".run", ".bin"
            },
            "font": {
                ".ttf", ".otf", ".woff", ".woff2", ".eot"
            },
            "disque": {
                ".iso", ".img", ".dmg"
            },
        }

    def path_has_suffix_in_category(self, path, extensions):
        name = path.name.lower()
        suffix = path.suffix.lower()

        if ".tar.gz" in extensions and name.endswith(".tar.gz"):
            return True

        return suffix in extensions

    def search_filter_matches(self, path, token):
        token = self.normalize_search_token(token)

        if not token:
            return True

        if token == "dossier":
            return path.is_dir() or self.kio_path_is_dir(path)

        if token == "fichier":
            if self.is_kio_virtual_path(path):
                return not self.kio_path_is_dir(path)
            return path.is_file()

        categories = self.search_category_extensions()

        is_file_like = (self.is_kio_virtual_path(path) and not self.kio_path_is_dir(path)) or path.is_file()

        if token in categories:
            return is_file_like and self.path_has_suffix_in_category(path, categories[token])

        if token.startswith("."):
            return is_file_like and self.path_has_suffix_in_category(path, {token})

        # Si on tape png/jpg/exe sans point, on traite aussi comme extension.
        if is_file_like and len(token) <= 10 and " " not in token:
            if self.path_has_suffix_in_category(path, {f".{token}"}):
                return True

        file_type = self.file_type(path).lower()

        return token in path.name.lower() or token in file_type

    def search_name_matches(self, path, query):
        query = self.normalize_search_token(query)

        if not query:
            return True

        if not self.search_pinned_filter:
            return self.search_filter_matches(path, query)

        return query in path.name.lower()

    def path_matches_search(self, path):
        pinned = self.normalize_search_token(self.search_pinned_filter)
        query = self.normalize_search_token(self.search_query)

        if pinned and not self.search_filter_matches(path, pinned):
            return False

        if query and not self.search_name_matches(path, query):
            return False

        return True

    def filter_paths_for_search(self, paths):
        if not self.search_is_active():
            return list(paths)

        return [
            path
            for path in paths
            if self.path_matches_search(path)
        ]

    def global_search_roots(self):
        # Recherche dans tout l'utilisateur visible.
        return [Path.home()]

    def should_skip_global_search_dir(self, path):
        name = path.name.lower()
        path_str = str(path)

        # On ignore seulement les énormes dossiers techniques/caches.
        skip_exact = {
            ".cache",
            ".git",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
            "env",
        }

        if name in skip_exact:
            return True

        skip_parts = [
            "/.cache/",
            "/.local/share/Trash/",
            "/node_modules/",
            "/__pycache__/",
            "/.git/",
            "/.venv/",
        ]

        return any(part in path_str for part in skip_parts)

    def global_search_item_matches(self, path, name_lower, is_dir, is_file):
        pinned = self.normalize_search_token(self.search_pinned_filter)
        query = self.normalize_search_token(self.search_query)

        def matches_token(token):
            if not token:
                return True

            if token == "dossier":
                return is_dir

            if token == "fichier":
                return is_file

            categories = self.search_category_extensions()

            suffix = path.suffix.lower()
            full_name = path.name.lower()

            if token in categories:
                extensions = categories[token]

                if ".tar.gz" in extensions and full_name.endswith(".tar.gz"):
                    return is_file

                return is_file and suffix in extensions

            if token.startswith("."):
                return is_file and (suffix == token or full_name.endswith(token))

            # extension tapée sans point : png, py, exe...
            if is_file and len(token) <= 10 and " " not in token:
                if suffix == f".{token}" or full_name.endswith(f".{token}"):
                    return True

            return token in name_lower

        if pinned and not matches_token(pinned):
            return False

        if query and not matches_token(query):
            return False

        return True

    def global_search_paths(self):
        if not self.global_search_enabled or not self.global_search_active:
            return []

        if not (self.search_query.strip() or self.search_pinned_filter.strip()):
            return []

        results = []
        seen = set()

        for root in self.global_search_roots():
            if not root.exists() or not root.is_dir():
                continue

            stack = [root]

            while stack and len(results) < self.global_search_limit:
                current = stack.pop()

                try:
                    with os.scandir(current) as entries:
                        for entry in entries:
                            if len(results) >= self.global_search_limit:
                                break

                            name = entry.name

                            # On ne montre pas les fichiers cachés, mais on ne bloque pas tout le home.
                            if name.startswith("."):
                                continue

                            path = Path(entry.path)

                            try:
                                is_dir = entry.is_dir(follow_symlinks=False)
                                is_file = entry.is_file(follow_symlinks=False)
                            except Exception:
                                continue

                            if is_dir and not self.should_skip_global_search_dir(path):
                                stack.append(path)

                            if self.global_search_item_matches(path, name.lower(), is_dir, is_file):
                                key = str(path)

                                if key not in seen:
                                    seen.add(key)
                                    results.append(path)

                except Exception:
                    continue

        # Dossiers d'abord, puis nom.
        return sorted(results, key=lambda p: (not p.is_dir(), p.name.lower()))

    def refresh_after_search_change(self):
        started = time.monotonic()

        if self.global_search_enabled and self.global_search_active:
            self.start_global_search()
            return

        if self.is_downloads_mode():
            self.rebuild_rows()
            self.selected_row = min(self.selected_row, max(0, len(self.rows) - 1))

            if self.rows:
                self.set_table_single_selection(self.selected_row)
            else:
                self.clear_multi_selection()
        else:
            self.column_selection_enabled = False
            self.disable_next_column_auto_selection = True
            self.rebuild_columns()

        self.directory_snapshot = self.current_directory_snapshot()
        self.update_realtime_watchers()
        self.update()
        self.perf_log("refresh_after_search_change", time.monotonic() - started)


    def cancel_global_search(self):
        worker = getattr(self, "global_search_worker", None)

        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass

    def start_global_search(self):
        if not (self.search_query.strip() or self.search_pinned_filter.strip()):
            return

        self.cancel_global_search()

        # Affiche une colonne vide immédiatement pour ne jamais bloquer l'interface.
        self.column_paths = [Path.home()]
        self.column_items = [[]]
        self.column_selected = [0]
        self.column_scrolls = [0]
        self.column_widths_custom = [
            max(430, int((self.width() - self.sidebar_w) * 0.55))
        ]
        self.active_column = 0
        self.column_selection_enabled = False
        self.selected_table_rows = set()
        self.selected_column_paths = set()
        self.hscroll = 0
        self.clamp_hscroll()
        self.set_operation_status("Recherche utilisateur…")
        self.update()

        thread = QThread(self)
        worker = GlobalSearchWorker(
            self.search_query,
            self.search_pinned_filter,
            self.global_search_limit,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self.update_global_search_progress)
        worker.finished.connect(self.finish_global_search)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self.global_search_thread = thread
        self.global_search_worker = worker
        thread.start()

    def update_global_search_progress(self, info):
        if not isinstance(info, dict):
            return

        count = info.get("count", 0)
        scanned = info.get("scanned", 0)
        self.operation_status_text = f"Recherche… {count} résultat(s), {scanned} éléments scannés"
        self.operation_status_visible = True
        self.update()

    def finish_global_search(self, result):
        self.global_search_thread = None
        self.global_search_worker = None

        if not isinstance(result, dict):
            self.clear_operation_status()
            return

        if result.get("cancelled"):
            return

        results = result.get("results", [])

        self.column_paths = [Path.home()]
        self.column_items = [results]
        self.column_selected = [0]
        self.column_scrolls = [0]
        self.column_widths_custom = [
            max(430, int((self.width() - self.sidebar_w) * 0.55))
        ]
        self.active_column = 0
        self.column_selection_enabled = bool(results)
        self.selected_table_rows = set()
        self.selected_column_paths = set()

        if results:
            self.set_column_single_selection(results[0], 0, 0)
        else:
            self.clear_multi_selection()

        self.hscroll = 0
        self.clamp_hscroll()
        self.directory_snapshot = self.current_directory_snapshot()
        self.update_realtime_watchers()
        self.update()

        count = result.get("count", len(results))
        scanned = result.get("scanned", 0)
        elapsed = float(result.get("elapsed", 0.0) or 0.0)

        self.set_operation_status(f"{count} résultat(s)")
        QTimer.singleShot(900, self.clear_operation_status)
        self.perf_log("global_search", elapsed, f"query={result.get('query', '')!r} scanned={scanned} count={count}")

    def on_search_text_changed(self, text):
        self.search_query = text.strip()

        # Anti-lag : pendant la frappe, recherche locale seulement.
        self.global_search_active = False
        self.cancel_global_search()
        self.refresh_after_search_change()

    def run_global_search_from_editor(self):
        # Entrée = recherche complète dans /home/vio, mais dans un thread.
        self.search_query = self.search_editor.text().strip()

        if not (self.search_query or self.search_pinned_filter):
            return

        self.global_search_active = True
        self.refresh_after_search_change()

    def pin_or_unpin_search_filter(self):
        token = self.search_editor.text().strip()

        if token:
            self.search_pinned_filter = token
            self.search_editor.clear()
        elif self.search_pinned_filter:
            self.search_pinned_filter = ""

        self.global_search_active = False
        self.cancel_global_search()
        self.refresh_search_pin_button()
        self.refresh_after_search_change()

    def refresh_search_pin_button(self):
        if not hasattr(self, "search_pin_button"):
            return

        if self.search_pinned_filter:
            label = self.search_pinned_filter
            self.search_pin_button.setText(f"✕ {label[:9]}")
            self.search_editor.setPlaceholderText(f"{self.search_pinned_filter} · nom...")
        else:
            self.search_pin_button.setText("Épingler")
            self.search_editor.setPlaceholderText("Rechercher... Entrée = partout")

    def draw_operation_status(self, p):
        if not getattr(self, "operation_status_visible", False):
            return

        text = getattr(self, "operation_status_text", "")

        if not text:
            return

        font = QFont(UI_FONT_FAMILY, 10)
        font.setBold(True)
        p.setFont(font)

        metrics = p.fontMetrics()
        padding_x = 18
        text_w = metrics.horizontalAdvance(text)
        box_w = min(max(230, text_w + padding_x * 2), max(260, self.width() - self.sidebar_w - 40))
        box_h = 42
        button_space = 112 if getattr(self, "file_operation_worker", None) is not None else 0
        x = self.width() - box_w - 22 - button_space
        y = self.height() - box_h - 22

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(255, 255, 255, 55))
        p.setBrush(QColor(32, 32, 39, 150))
        p.drawRoundedRect(QRectF(x, y, box_w, box_h), 14, 14)
        p.setPen(WHITE)
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, box_w - padding_x * 2)
        p.drawText(QRectF(x + padding_x, y, box_w - padding_x * 2, box_h), Qt.AlignmentFlag.AlignVCenter, elided)
        p.restore()

    def draw_sidebar(self, p):
        # Z-index visuel de la barre gauche :
        # on remplace vraiment les pixels de la zone sidebar au lieu de
        # dessiner une couche transparente par-dessus les colonnes.
        # Sinon les noms des fichiers/colonnes restent visibles sous la sidebar.
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(0, 0, self.sidebar_w, self.height(), LEFT)
        p.restore()

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        font = QFont(UI_FONT_FAMILY, 10)
        p.setFont(font)
        p.setPen(WHITE)

        previous_kind = None

        for i, (name, path, icon, kind) in enumerate(self.locations):
            y = i * self.row_h

            if kind in {"drive", "phone", "phone_kio"} and previous_kind not in {"drive", "phone", "phone_kio"}:
                p.fillRect(12, y + 1, self.sidebar_w - 24, 1, QColor(255, 255, 255, 28))

            if i == self.current_location:
                p.fillRect(0, y, self.sidebar_w, self.row_h, SELECT)

            if i == self.sidebar_drop_index:
                p.fillRect(0, y, self.sidebar_w, self.row_h, QColor(255, 255, 255, 42))

            f = QFont(font)
            f.setBold(i == self.current_location)
            p.setFont(f)

            icon.paint(p, 10, y + 6, 18, 18)

            label_w = self.sidebar_w - 72 if kind in {"drive", "phone", "phone_kio"} else self.sidebar_w - 44
            label = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, max(40, label_w))
            p.setPen(WHITE)
            p.drawText(38, y + 20, label)

            if kind == "drive":
                self.icons["eject"].paint(p, self.sidebar_w - 30, y + 7, 16, 16)

            previous_kind = kind

        p.fillRect(self.sidebar_w - 1, 0, 1, self.height(), SIDEBAR_DIVIDER)

    def draw_table_view(self, p):
        font = QFont(UI_FONT_FAMILY, 10)
        p.setFont(font)
        p.setPen(WHITE)

        x0 = self.sidebar_w
        widths = self.table_column_widths()
        headers = ["Nom", "Taille", "Type", "Date de l'ajout"]
        top = self.content_top()

        p.save()
        p.setClipRect(
            QRect(
                int(self.sidebar_w),
                int(top),
                int(max(0, self.width() - self.sidebar_w)),
                int(max(0, self.height() - top))
            )
        )

        y = top - self.scroll

        for index, (path, depth) in enumerate(self.rows):
            if y + self.row_h >= top and y <= self.height():
                if index == self.selected_row or index in self.selected_table_rows:
                    p.fillRect(self.sidebar_w, y, self.width() - self.sidebar_w, self.row_h, SELECT)

                if path == self.folder_drop_target:
                    p.fillRect(self.sidebar_w, y, self.width() - self.sidebar_w, self.row_h, QColor(255, 255, 255, 42))

                self.draw_table_row(p, path, depth, x0, y, widths)

            y += self.row_h

        p.restore()

        x = x0 + 6
        header_y = self.search_h

        for col_index, (name, w) in enumerate(zip(headers, widths)):
            f = QFont(font)
            f.setBold(name == self.sort_mode)
            p.setFont(f)

            header_padding = 12 if name != 'Nom' else 0
            p.drawText(x + header_padding, header_y + 18, name)

            if name == self.sort_mode and self.arrow_svg.isValid():
                p.save()
                p.translate(x + w - 10, header_y + 13)
                p.rotate(270 if self.sort_reverse else 90)
                self.arrow_svg.render(p, QRectF(-4, -4, 8, 8))
                p.restore()

            if col_index < len(widths) - 1:
                p.fillRect(x + w - 2, header_y, 1, self.height() - header_y, DIVIDER)

            x += w

    def draw_column_browser(self, p):
        start_x = self.sidebar_w - self.hscroll
        x = start_x
        top = self.content_top()

        for col_index, items in enumerate(self.column_items):
            width = self.column_width(col_index)

            if x + width >= self.sidebar_w and x <= self.width():
                p.fillRect(x, 0, width, self.height(), PANE_BG)
                p.fillRect(x + width - 1, self.search_h, 1, self.height() - self.search_h, PANE_LINE)

                if self.is_file_preview_column(col_index):
                    self.draw_file_preview_column(p, items[0], x, width, col_index)
                else:
                    p.save()
                    p.setClipRect(
                        QRect(
                            int(x),
                            int(top),
                            int(width),
                            int(max(0, self.height() - top))
                        )
                    )
                    self.draw_column_items(p, col_index, x, width, items)
                    p.restore()

                title = self.column_paths[col_index].name or str(self.column_paths[col_index])

                if self.global_search_active and col_index == 0 and self.global_search_enabled:
                    title = "Recherche utilisateur"

                self.draw_column_title(p, x, width, title)

            x += width

    def draw_column_title(self, p, x, width, title):
        font = QFont(UI_FONT_FAMILY, 9)
        font.setBold(True)
        p.setFont(font)
        p.setPen(WHITE)

        title_text = title

        if self.search_pinned_filter:
            title_text = f"{title}  ·  {self.search_pinned_filter}"

        text = p.fontMetrics().elidedText(title_text, Qt.TextElideMode.ElideRight, max(20, width - 20))
        p.drawText(x + 10, self.search_h + 18, text)

    def draw_column_items(self, p, col_index, x, width, items):
        font = QFont(UI_FONT_FAMILY, 10)
        p.setFont(font)

        top = self.content_top()
        y = top - self.column_scrolls[col_index]

        for row, path in enumerate(items):
            if y + self.row_h >= top and y <= self.height():
                if (
                    (
                        self.column_selection_enabled
                        and col_index == self.active_column
                        and row == self.column_selected[col_index]
                    )
                    or path in self.selected_column_paths
                ):
                    p.fillRect(x, y, width, self.row_h, SELECT)

                if path == self.folder_drop_target:
                    p.fillRect(x, y, width, self.row_h, QColor(255, 255, 255, 42))

                icon = self.icon_for_path(path)
                icon.paint(p, x + 10, y + 6, 18, 18)

                name_x = x + 38
                name_w = width - 72
                name = p.fontMetrics().elidedText(path.name, Qt.TextElideMode.ElideRight, max(20, name_w))

                p.setPen(WHITE)
                p.drawText(name_x, y + 20, name)

                if path.is_dir() or self.kio_path_is_dir(path):
                    p.setPen(DIM)
                    p.drawText(x + width - 24, y + 20, "›")

            y += self.row_h

    def draw_file_preview_column(self, p, path, x, width, col_index):
        if self.is_kio_virtual_path(path):
            content_x = x + 18
            content_w = max(20, width - 36)
            top = self.content_top() + 18
            bottom = self.height() - 18
            content_h = max(20, bottom - top)

            if col_index == self.active_column:
                p.fillRect(x, self.content_top(), width, self.height() - self.content_top(), SELECT)

            self.draw_static_file_preview(
                p,
                path,
                content_x,
                top,
                content_w,
                content_h,
                subtitle=f"{self.file_type(path)} · Entrée pour ouvrir via Android"
            )
            return

        suffix = path.suffix.lower()
        content_x = x + 18
        content_w = max(20, width - 36)
        top = self.content_top() + 18
        bottom = self.height() - 18
        content_h = max(20, bottom - top)

        if col_index == self.active_column:
            p.fillRect(x, self.content_top(), width, self.height() - self.content_top(), SELECT)

        if suffix in self.image_exts:
            self.draw_image_preview(p, path, content_x, top, content_w, content_h)
            return

        if suffix == ".svg":
            self.draw_svg_preview(p, path, content_x, top, content_w, content_h)
            return

        if suffix in self.preview_text_exts:
            self.draw_text_preview(p, path, content_x, top, content_w, content_h)
            return

        if suffix in self.audio_exts:
            self.draw_static_media_preview(p, path, content_x, top, content_w, content_h, media_type="audio")
            return

        if suffix in self.video_exts:
            self.draw_static_media_preview(p, path, content_x, top, content_w, content_h, media_type="video")
            return

        if suffix == ".pdf":
            self.draw_static_file_preview(p, path, content_x, top, content_w, content_h, subtitle="PDF · Espace pour ouvrir")
            return

        self.draw_static_file_preview(
            p,
            path,
            content_x,
            top,
            content_w,
            content_h,
            subtitle=f"{self.file_type(path)} · Espace pour ouvrir"
        )

    def draw_image_preview(self, p, path, x, y, w, h):
        key = str(path)
        pix = self.pixmap_preview_cache.get(key)

        if pix is None:
            pix = load_high_quality_pixmap(path)
            self.pixmap_preview_cache[key] = pix

        if pix.isNull():
            self.draw_static_file_preview(p, path, x, y, w, h, subtitle="Image illisible")
            return

        title_h = 74
        image_rect = QRect(x, y, w, max(40, h - title_h))

        scaled = pix.scaled(
            image_rect.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        img_x = image_rect.x() + (image_rect.width() - scaled.width()) // 2
        img_y = image_rect.y() + (image_rect.height() - scaled.height()) // 2

        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawPixmap(img_x, img_y, scaled)
        p.restore()

        self.draw_preview_title(p, path, x, y + h - title_h + 10, w, self.file_type(path))

    def draw_svg_preview(self, p, path, x, y, w, h):
        renderer = QSvgRenderer(str(path))

        if not renderer.isValid():
            self.draw_static_file_preview(p, path, x, y, w, h, subtitle="SVG illisible")
            return

        title_h = 74
        image_rect = QRectF(x, y, w, max(40, h - title_h))
        default_size = renderer.defaultSize()

        if default_size.width() <= 0 or default_size.height() <= 0:
            target = image_rect
        else:
            scale = min(image_rect.width() / default_size.width(), image_rect.height() / default_size.height())
            tw = default_size.width() * scale
            th = default_size.height() * scale
            target = QRectF(
                image_rect.x() + (image_rect.width() - tw) / 2,
                image_rect.y() + (image_rect.height() - th) / 2,
                tw,
                th,
            )

        renderer.render(p, target)
        self.draw_preview_title(p, path, x, y + h - title_h + 10, w, self.file_type(path))

    def draw_text_preview(self, p, path, x, y, w, h):
        text = self.preview_text(path)

        p.setPen(QColor(255, 255, 255, 45))
        p.drawRoundedRect(QRectF(x, y, w, h), 12, 12)

        font = QFont(MONO_FONT_FAMILY, 9)
        p.setFont(font)
        p.setPen(WHITE)

        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

        rect = QRectF(x + 12, y + 12, w - 24, h - 72)
        p.drawText(rect, text, option)

        self.draw_preview_title(p, path, x + 12, y + h - 48, w - 24, self.file_type(path))

    def draw_static_media_preview(self, p, path, x, y, w, h, media_type):
        icon = self.icons["audio"] if media_type == "audio" else self.icons["video"]
        icon_size = min(150, max(72, w // 3))

        icon_x = x + (w - icon_size) // 2
        icon_y = y + max(20, (h - icon_size) // 2 - 70)

        icon.paint(p, icon_x, icon_y, icon_size, icon_size)

        label = "Audio · Espace pour ouvrir le lecteur" if media_type == "audio" else "Vidéo · Espace pour ouvrir le lecteur"

        self.draw_fake_player(p, x, icon_y + icon_size + 28, w)
        self.draw_preview_title(p, path, x, icon_y + icon_size + 84, w, label)

    def draw_fake_player(self, p, x, y, w):
        p.setPen(QColor(255, 255, 255, 60))
        p.setBrush(QColor(255, 255, 255, 25))
        p.drawRoundedRect(QRectF(x + 20, y, w - 40, 6), 3, 3)

        p.setBrush(QColor(255, 255, 255, 180))
        p.drawEllipse(QRectF(x + 20, y - 5, 16, 16))

        p.setPen(WHITE)
        p.setFont(QFont(UI_FONT_FAMILY, 9))
        p.drawText(QRectF(x, y + 18, w, 24), Qt.AlignmentFlag.AlignCenter, "Play / Pause dans la modale")

    def draw_static_file_preview(self, p, path, x, y, w, h, subtitle):
        icon = self.icon_for_path(path)
        icon_size = min(170, max(80, w // 3))

        icon_x = x + (w - icon_size) // 2
        icon_y = y + max(20, (h - icon_size) // 2 - 50)

        icon.paint(p, icon_x, icon_y, icon_size, icon_size)
        self.draw_preview_title(p, path, x, icon_y + icon_size + 30, w, subtitle)

    def draw_preview_title(self, p, path, x, y, w, subtitle):
        title_font = QFont(UI_FONT_FAMILY, 11)
        title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(WHITE)

        title = p.fontMetrics().elidedText(path.name, Qt.TextElideMode.ElideRight, max(20, w))
        p.drawText(QRectF(x, y, w, 26), Qt.AlignmentFlag.AlignCenter, title)

        p.setFont(QFont(UI_FONT_FAMILY, 9))
        p.setPen(DIM)

        info = f"{subtitle}\n{self.file_size(path)}"
        p.drawText(QRectF(x, y + 30, w, 52), Qt.AlignmentFlag.AlignCenter, info)

    def preview_text(self, path):
        key = str(path)

        if key in self.text_preview_cache:
            return self.text_preview_cache[key]

        try:
            text = path.read_text(errors="replace")
        except Exception as e:
            text = f"Impossible de lire ce fichier :\n\n{e}"

        if len(text) > 5000:
            text = text[:5000] + "\n\n…"

        self.text_preview_cache[key] = text
        return text

    def draw_arrow(self, p, x, y, opened):
        if not self.arrow_svg.isValid():
            return

        p.save()
        p.translate(x + 8, y + self.row_h / 2)

        if opened:
            p.rotate(90)

        self.arrow_svg.render(p, QRectF(-4, -4, 8, 8))
        p.restore()

    def draw_table_row(self, p, path, depth, x0, y, widths):
        x = x0 + 6
        depth_px = depth * 24

        arrow_x = x + depth_px
        icon_x = arrow_x + 20

        if path.is_dir():
            self.draw_arrow(p, arrow_x, y, path in self.open_dirs)

        self.icon_for_path(path).paint(p, icon_x, y + 6, 18, 18)

        name_x = icon_x + 26
        name_w = widths[0] - depth_px - 50
        name = p.fontMetrics().elidedText(path.name, Qt.TextElideMode.ElideRight, max(20, name_w))

        p.drawText(name_x, y + 20, name)

        table_left_padding = 12
        table_right_padding = 16

        x += widths[0]
        size_text_w = max(20, widths[1] - table_left_padding - table_right_padding)
        size_text = p.fontMetrics().elidedText(
            self.file_size(path),
            Qt.TextElideMode.ElideRight,
            size_text_w
        )
        p.drawText(x + table_left_padding, y + 20, size_text)

        x += widths[1]
        type_text_w = max(20, widths[2] - table_left_padding - table_right_padding)
        type_text = p.fontMetrics().elidedText(
            self.file_type(path),
            Qt.TextElideMode.ElideRight,
            type_text_w
        )
        p.drawText(x + table_left_padding, y + 20, type_text)

        x += widths[2]
        date_text_w = max(20, widths[3] - table_left_padding - table_right_padding)
        date_text = p.fontMetrics().elidedText(
            self.date_added(path),
            Qt.TextElideMode.ElideRight,
            date_text_w
        )
        p.drawText(x + table_left_padding, y + 20, date_text)

    def table_column_widths(self):
        total = self.width() - self.sidebar_w - 12
        return [int(total * ratio) for ratio in self.table_width_ratios]

    def column_width(self, index):
        while index >= len(self.column_widths_custom):
            self.column_widths_custom.append(max(260, int((self.width() - self.sidebar_w) * 0.32)))
        return self.column_widths_custom[index]

    def sidebar_index_at_y(self, y):
        index = y // self.row_h

        if 0 <= index < len(self.locations):
            return index

        return None

    def sidebar_drop_target_for_index(self, index):
        if index is None or not (0 <= index < len(self.locations)):
            return None

        path = self.locations[index][1]

        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        if path.exists() and path.is_dir():
            return path

        return None

    def event_local_paths(self, event):
        mime = event.mimeData()
        paths = []

        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())

                    if path.exists():
                        paths.append(path)

        elif mime.hasText():
            for line in mime.text().splitlines():
                value = line.strip().strip('"').strip("'")

                if value.startswith("file://"):
                    value = QUrl(value).toLocalFile()

                path = Path(value).expanduser()

                if path.exists():
                    paths.append(path)

        unique = []
        seen = set()

        for path in paths:
            try:
                key = str(path.resolve())
            except Exception:
                key = str(path)

            if key not in seen:
                seen.add(key)
                unique.append(path)

        return unique

    def dragEnterEvent(self, event):
        if self.event_local_paths(event):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                event.setDropAction(Qt.DropAction.CopyAction)
            else:
                event.setDropAction(Qt.DropAction.MoveAction)

            event.accept()
            return

        event.ignore()

    def dragMoveEvent(self, event):
        pos = event.position().toPoint()
        x = pos.x()
        y = pos.y()

        self.sidebar_drop_index = None
        self.folder_drop_target = None

        if not self.event_local_paths(event):
            self.update()
            event.ignore()
            return

        target_dir, sidebar_index, folder_target = self.drop_target_at_position(x, y)

        if target_dir is None:
            self.update()
            event.ignore()
            return

        if sidebar_index is not None:
            self.sidebar_drop_index = sidebar_index

        if folder_target is not None:
            self.folder_drop_target = folder_target

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.setDropAction(Qt.DropAction.CopyAction)
        else:
            event.setDropAction(Qt.DropAction.MoveAction)

        event.accept()
        self.update()

    def dragLeaveEvent(self, event):
        self.sidebar_drop_index = None
        self.folder_drop_target = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        pos = event.position().toPoint()
        x = pos.x()
        y = pos.y()

        target_dir, sidebar_index, folder_target = self.drop_target_at_position(x, y)

        if target_dir is None:
            self.sidebar_drop_index = None
            self.folder_drop_target = None
            self.update()
            event.ignore()
            return

        sources = self.event_local_paths(event)

        if not sources:
            self.sidebar_drop_index = None
            self.folder_drop_target = None
            self.update()
            event.ignore()
            return

        copy_mode = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        target_location_index = sidebar_index if sidebar_index is not None else self.current_location

        self.sidebar_drop_index = None
        self.folder_drop_target = None

        self.start_file_operation(
            "Copie en cours…" if copy_mode else "Déplacement en cours…",
            "copy" if copy_mode else "move",
            sources,
            target_dir,
            {
                "kind": "drop",
                "target_location_index": target_location_index,
            },
        )

        event.setDropAction(
            Qt.DropAction.CopyAction if copy_mode else Qt.DropAction.MoveAction
        )
        event.accept()

    def drop_target_at_position(self, x, y):
        # 1) Barre latérale : drop vers l'onglet ciblé.
        if x < self.sidebar_w:
            index = self.sidebar_index_at_y(y)
            target_dir = self.sidebar_drop_target_for_index(index)

            if target_dir is not None:
                return target_dir, index, None

            return None, None, None

        # 2) Vue Téléchargements :
        # - sur un dossier : drop dedans
        # - dans le vide : drop dans le dossier courant Téléchargements
        if self.is_downloads_mode():
            if y < self.content_top():
                return self.root_path, None, None

            row_index = (y - self.content_top() + self.scroll) // self.row_h

            if 0 <= row_index < len(self.rows):
                row_y = self.content_top() - self.scroll + row_index * self.row_h

                if row_y <= y <= row_y + self.row_h:
                    path = self.rows[row_index][0]

                    if path.is_dir():
                        return path, None, path

                    return self.root_path, None, None

            return self.root_path, None, None

        # 3) Vue colonnes.
        col_index, _local_x = self.column_at_x(x)

        if col_index is None:
            # Si on est dans la zone à droite des colonnes, drop dans la dernière colonne dossier connue.
            for path in reversed(self.column_paths):
                if path.is_dir():
                    return path, None, None
                if path.exists():
                    return path.parent, None, None

            return self.root_path, None, None

        # Colonne d'aperçu fichier : drop dans son dossier parent.
        if self.is_file_preview_column(col_index):
            if self.column_items[col_index]:
                return self.column_items[col_index][0].parent, None, None

            return self.root_path, None, None

        column_dir = self.column_paths[col_index]

        if not column_dir.is_dir():
            column_dir = column_dir.parent

        # Header ou vide : drop dans le dossier de la colonne.
        if y < self.content_top():
            return column_dir, None, None

        items = self.column_items[col_index]
        row = (y - self.content_top() + self.column_scrolls[col_index]) // self.row_h

        if 0 <= row < len(items):
            row_y = self.content_top() - self.column_scrolls[col_index] + row * self.row_h

            if row_y <= y <= row_y + self.row_h:
                path = items[row]

                if path.is_dir():
                    return path, None, path

                return column_dir, None, None

        return column_dir, None, None

    def drop_folder_at_position(self, x, y):
        target_dir, _sidebar_index, folder_target = self.drop_target_at_position(x, y)

        if folder_target is not None:
            return folder_target

        return target_dir

    def refresh_after_drop(self, target_dir, moved_paths, target_location_index):
        dropped_on_sidebar = (
            target_location_index is not None
            and 0 <= target_location_index < len(self.locations)
            and target_location_index != self.current_location
        )

        if dropped_on_sidebar:
            self.current_location = target_location_index
            self.root_path = self.locations[self.current_location][1]
            self.open_dirs.clear()
            self.scroll = 0
            self.hscroll = 0
            self.active_column = 0
            self.clear_multi_selection()

        if self.is_downloads_mode():
            self.rebuild_rows()
            self.selected_row = min(self.selected_row, max(0, len(self.rows) - 1))

            if self.rows:
                self.set_table_single_selection(self.selected_row)
                self.ensure_selected_visible()

        else:
            # Très important :
            # après un drop, on refresh l'onglet/dossier mais on ne sélectionne PAS
            # automatiquement le fichier déplacé, sinon ça ouvre une colonne d'aperçu.
            self.rebuild_columns()

            # Si rebuild_columns a ouvert automatiquement la 2e colonne sur le premier élément,
            # on la garde seulement si c'est un dossier déjà présent. Si c'est un fichier déplacé,
            # on coupe les colonnes à droite.
            if len(self.column_paths) > 1:
                second_path = self.column_paths[1]

                if second_path.is_file():
                    self.column_paths = self.column_paths[:1]
                    self.column_items = self.column_items[:1]
                    self.column_selected = self.column_selected[:1]
                    self.column_scrolls = self.column_scrolls[:1]
                    self.active_column = 0

            # Si on a drop dans un dossier visible de l'onglet courant, on ouvre ce dossier,
            # mais pas le fichier déplacé.
            if not dropped_on_sidebar and target_dir is not None:
                try:
                    root = self.root_path.resolve()
                    target_resolved = target_dir.resolve()

                    if target_resolved != root and root in target_resolved.parents:
                        self.startup_target_path = target_dir
                        self.apply_startup_target_path()

                        # Coupe l'aperçu fichier si apply_startup_target_path en a recréé un.
                        if len(self.column_paths) > 0 and self.column_paths[-1].is_file():
                            self.column_paths = self.column_paths[:-1]
                            self.column_items = self.column_items[:-1]
                            self.column_selected = self.column_selected[:-1]
                            self.column_scrolls = self.column_scrolls[:-1]
                            self.active_column = max(0, len(self.column_items) - 1)
                except Exception:
                    pass

        self.pin_horizontal_scroll_to_right()
        self.directory_snapshot = self.current_directory_snapshot()
        self.update_realtime_watchers()
        self.update()

    def refresh_after_sidebar_drop(self, target_dir, moved_paths, target_location_index):
        self.refresh_after_drop(target_dir, moved_paths, target_location_index)

    def path_at_position(self, x, y):
        if x < self.sidebar_w:
            return None

        if self.is_downloads_mode():
            if y < self.content_top():
                return None

            row_index = (y - self.content_top() + self.scroll) // self.row_h

            if not (0 <= row_index < len(self.rows)):
                return None

            row_y = self.content_top() - self.scroll + row_index * self.row_h

            if not (row_y <= y <= row_y + self.row_h):
                return None

            return self.rows[row_index][0]

        col_index, _local_x = self.column_at_x(x)

        if col_index is None:
            return None

        if self.is_file_preview_column(col_index):
            if self.column_items[col_index]:
                return self.column_items[col_index][0]
            return None

        if y < self.content_top():
            return None

        items = self.column_items[col_index]
        row = (y - self.content_top() + self.column_scrolls[col_index]) // self.row_h

        if not (0 <= row < len(items)):
            return None

        row_y = self.content_top() - self.column_scrolls[col_index] + row * self.row_h

        if not (row_y <= y <= row_y + self.row_h):
            return None

        return items[row]

    def start_drag_for_path(self, path):
        if path is None:
            return

        if self.is_kio_virtual_path(path):
            # Les URLs mtp:/ KDE ne sont pas des chemins locaux drag-and-drop fiables.
            # On les ouvre via KIO au double/entrée/espace plutôt que de générer un faux file://.
            return

        if not path.exists():
            return

        selected_paths = self.current_selected_paths()

        if path not in selected_paths:
            selected_paths = [path]

        selected_paths = [
            selected_path
            for selected_path in selected_paths
            if selected_path is not None and selected_path.exists()
        ]

        if not selected_paths:
            return

        urls = [QUrl.fromLocalFile(str(selected_path)) for selected_path in selected_paths]
        uri_list = "\r\n".join(url.toString() for url in urls) + "\r\n"
        plain_text = "\n".join(str(selected_path) for selected_path in selected_paths)

        mime = QMimeData()
        mime.setUrls(urls)
        mime.setText(plain_text)
        mime.setData("text/uri-list", uri_list.encode("utf-8"))
        mime.setData(
            "x-special/gnome-copied-files",
            ("copy\n" + uri_list).encode("utf-8")
        )
        mime.setData("application/x-kde-cutselection", b"0")

        drag = QDrag(self)
        drag.setMimeData(mime)

        icon_path = selected_paths[0]
        icon = self.icon_for_path(icon_path)
        pixmap = icon.pixmap(48, 48)

        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPointF(8, 8).toPoint())

        # CopyAction par défaut pour les apps externes comme VS Code.
        # PrincessFinder accepte toujours MoveAction en interne dans dragMove/dropEvent.
        drag.exec(
            Qt.DropAction.CopyAction
            | Qt.DropAction.MoveAction
            | Qt.DropAction.LinkAction,
            Qt.DropAction.CopyAction
        )

    def show_drive_context_menu(self, global_pos, index):
        if not self.is_removable_location(index):
            return

        name, path, _icon, kind = self.locations[index]

        menu = QMenu(self)
        self.style_context_menu(menu)

        icon = self.icons["phone"] if kind in {"phone", "phone_kio"} else self.icons["drive"]
        open_action = menu.addAction(icon, "Ouvrir")

        eject_action = None

        if kind == "drive":
            eject_action = menu.addAction(self.icons["eject"], "Éjecter")

        chosen = menu.exec(global_pos)

        if chosen == open_action:
            if kind == "phone_kio":
                self.open_kio_phone(index)
                return

            self.current_location = index
            self.root_path = path
            self.open_dirs.clear()
            self.scroll = 0
            self.hscroll = 0
            self.selected_row = 0
            self.clear_multi_selection()
            self.active_column = 0
            self.size_cache.clear()
            self.text_preview_cache.clear()
            self.pixmap_preview_cache.clear()
            self.close_preview()
            self.rebuild_columns()
            self.update()

        elif eject_action is not None and chosen == eject_action:
            self.eject_drive(index)

    def eject_drive(self, index):
        if not self.is_drive_location(index):
            return

        _name, path, _icon, _kind = self.locations[index]

        device = None
        parent_device = None

        try:
            result = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE", "--target", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            device = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
        except Exception:
            device = None

        # Si findmnt renvoie un /dev/sdXN, on tente de retrouver le disque parent /dev/sdX
        # pour power-off proprement après démontage.
        if device and device.startswith("/dev/"):
            try:
                pk = subprocess.run(
                    ["lsblk", "-no", "PKNAME", device],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    check=False,
                )
                parent_name = pk.stdout.strip().splitlines()[0] if pk.stdout.strip() else ""

                if parent_name:
                    parent_device = f"/dev/{parent_name}"
            except Exception:
                parent_device = None

        success = False

        # 1) Tentative KDE/GVFS/GIO avec le point de montage.
        try:
            result = subprocess.run(
                ["gio", "mount", "-e", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            success = result.returncode == 0
        except Exception:
            success = False

        # 2) Fallback udisksctl : démonte la partition.
        if not success and device:
            try:
                unmount = subprocess.run(
                    ["udisksctl", "unmount", "-b", device],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                success = unmount.returncode == 0
            except Exception:
                success = False

        # 3) Si possible, coupe aussi l'alimentation du disque parent.
        if device:
            power_target = parent_device or device

            try:
                subprocess.run(
                    ["udisksctl", "power-off", "-b", power_target],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                pass

        if self.current_location == index:
            self.current_location = self.downloads_location_index() if self.locations else 0

        self.refresh_locations()

        if 0 <= self.current_location < len(self.locations):
            self.root_path = self.locations[self.current_location][1]
        else:
            self.current_location = 0
            self.root_path = self.locations[0][1]

        if self.is_downloads_mode():
            self.rebuild_rows()
        else:
            self.rebuild_columns()

        self.update()

    def show_table_context_menu(self, global_pos, x, y):
        self.cancel_rename()

        target_dir = self.root_path

        if y < self.content_top():
            self.show_empty_folder_context_menu(global_pos, target_dir, 0)
            return

        row_index = (y - self.content_top() + self.scroll) // self.row_h

        clicked_on_item = False
        target_path = None

        if 0 <= row_index < len(self.rows):
            row_y = self.content_top() - self.scroll + row_index * self.row_h

            if row_y <= y <= row_y + self.row_h:
                clicked_on_item = True
                target_path = self.rows[row_index][0]

        if clicked_on_item and target_path is not None:
            self.selected_row = row_index
            self.ensure_selected_visible()
            self.show_file_context_menu(global_pos, target_path, 0)
            self.update()
            return

        self.show_empty_folder_context_menu(global_pos, target_dir, 0)

    def show_column_context_menu(self, global_pos, x, y):
        self.cancel_rename()

        col_index, local_x = self.column_at_x(x)

        if col_index is None:
            return

        if self.is_file_preview_column(col_index):
            target_path = self.column_items[col_index][0]
            self.active_column = col_index
            self.pin_horizontal_scroll_to_right()
            self.show_file_context_menu(global_pos, target_path, col_index)
            return

        if y < self.content_top():
            target_dir = self.column_paths[col_index]

            if not target_dir.is_dir():
                target_dir = target_dir.parent

            self.show_empty_folder_context_menu(global_pos, target_dir, col_index)
            return

        items = self.column_items[col_index]
        row = (y - self.content_top() + self.column_scrolls[col_index]) // self.row_h
        clicked_on_item = False
        target_path = None

        if 0 <= row < len(items):
            row_y = self.content_top() - self.column_scrolls[col_index] + row * self.row_h

            if row_y <= y <= row_y + self.row_h:
                clicked_on_item = True
                target_path = items[row]

        if clicked_on_item and target_path is not None:
            self.select_column_row_like_keyboard(col_index, row)
            self.active_column = col_index
            self.show_file_context_menu(global_pos, target_path, col_index)
            return

        target_dir = self.column_paths[col_index]

        if not target_dir.is_dir():
            target_dir = target_dir.parent

        self.show_empty_folder_context_menu(global_pos, target_dir, col_index)

    def show_empty_folder_context_menu(self, global_pos, target_dir, col_index):
        if not target_dir.is_dir():
            return

        menu = QMenu(self)
        self.style_context_menu(menu)

        new_folder_action = menu.addAction(self.icons["new_folder"], "Nouveau dossier")
        new_file_action = menu.addAction(self.icons["new_file"], "Nouveau fichier texte")
        menu.addSeparator()

        paste_action = menu.addAction(self.icons["paste"], "Coller")

        can_paste = self.clipboard_has_pasteable_files()
        paste_action.setEnabled(can_paste)

        menu.addSeparator()
        folder_size_action = menu.addAction(
            self.icons["folder"],
            "Activer taille des dossiers" if not self.folder_size_enabled else "Désactiver taille des dossiers"
        )
        refresh_action = menu.addAction(self.icons["open_with"], "Rafraîchir maintenant")
        auto_refresh_action = menu.addAction(
            self.icons["open_with"],
            "Activer refresh temps réel" if not self.realtime_refresh_enabled else "Désactiver refresh temps réel"
        )

        chosen = menu.exec(global_pos)

        if chosen == new_folder_action:
            self.create_new_folder_in_column(target_dir, col_index)
        elif chosen == new_file_action:
            self.create_new_text_file_in_column(target_dir, col_index)
        elif chosen == paste_action:
            self.paste_clipboard_into_column(target_dir, col_index)
        elif chosen == folder_size_action:
            self.folder_size_enabled = not self.folder_size_enabled
            self.size_cache.clear()

            if self.is_downloads_mode():
                self.rebuild_rows()
            else:
                self.rebuild_columns()

            self.update()

        elif chosen == refresh_action:
            self.manual_refresh_now()

        elif chosen == auto_refresh_action:
            self.realtime_refresh_enabled = not self.realtime_refresh_enabled
            self.auto_refresh_enabled = self.realtime_refresh_enabled

            if self.auto_refresh_enabled:
                self.files_timer.start()
                self.update_realtime_watchers()
            else:
                self.files_timer.stop()

                try:
                    dirs = self.realtime_watcher.directories()
                    files = self.realtime_watcher.files()

                    if dirs:
                        self.realtime_watcher.removePaths(dirs)

                    if files:
                        self.realtime_watcher.removePaths(files)
                except Exception:
                    pass

    def show_file_context_menu(self, global_pos, target_path, col_index):
        if target_path is None:
            return

        if self.is_kio_virtual_path(target_path):
            menu = QMenu(self)
            self.style_context_menu(menu)
            open_action = menu.addAction(self.icons["phone"], "Ouvrir via Android")
            chosen = menu.exec(global_pos)

            if chosen == open_action:
                if self.kio_path_is_dir(target_path):
                    self.open_path_in_next_column(target_path, col_index, auto_child=False)
                    self.pin_horizontal_scroll_to_right()
                    self.update()
                else:
                    self.open_kio_url_external(self.kio_url_for_path(target_path))
            return

        if not target_path.exists():
            return

        selected_paths = self.current_selected_paths()

        if target_path not in selected_paths:
            selected_paths = [target_path]

        menu = QMenu(self)
        self.style_context_menu(menu)

        label_suffix = f" ({len(selected_paths)})" if len(selected_paths) > 1 else ""

        copy_action = menu.addAction(self.icons["copy"], f"Copier{label_suffix}")

        paste_action = menu.addAction(self.icons["paste"], "Coller")
        paste_action.setEnabled(self.clipboard_has_pasteable_files())

        open_with_actions = {}

        if len(selected_paths) == 1:
            open_with_menu = menu.addMenu(self.icons["open_with"], "Ouvrir avec")
            self.style_context_menu(open_with_menu)

            for label, icon_key, command in self.open_with_candidates_for_path(target_path):
                action = open_with_menu.addAction(self.icons.get(icon_key, self.icons["open_with"]), label)
                open_with_actions[action] = command

            if open_with_actions:
                open_with_menu.addSeparator()

            other_action = open_with_menu.addAction(self.icons["open_with"], "Autre…")
            open_with_actions[other_action] = "__choose__"

        extract_action = None

        if len(selected_paths) == 1 and self.is_extractable_archive(target_path):
            extract_action = menu.addAction(
                self.icons["extract"],
                "Extraire ici"
            )

        if self.is_trash_mode():
            trash_action = None
            delete_permanent_action = menu.addAction(
                self.icons["delete_permanent"],
                f"Supprimer définitivement{label_suffix}"
            )
        else:
            delete_permanent_action = None
            trash_action = menu.addAction(
                self.icons["move_trash"],
                f"Déplacer dans la corbeille{label_suffix}"
            )

        wallpaper_desktop_action = None
        wallpaper_lock_action = None
        wallpaper_both_action = None

        if self.is_wallpaper_image(target_path):
            menu.addSeparator()

            wallpaper_menu = menu.addMenu(self.icons["wallpaper"], "Définir comme fond d’écran")
            self.style_context_menu(wallpaper_menu)

            wallpaper_desktop_action = wallpaper_menu.addAction(
                self.icons["wallpaper"],
                "Bureaux"
            )
            wallpaper_lock_action = wallpaper_menu.addAction(
                self.icons["wallpaper"],
                "Écran de verrouillage"
            )
            wallpaper_both_action = wallpaper_menu.addAction(
                self.icons["wallpaper"],
                "Les deux"
            )

        chosen = menu.exec(global_pos)

        if chosen == copy_action:
            self.copy_paths_to_clipboard(selected_paths)
        elif chosen == paste_action:
            self.paste_clipboard_from_context_target(target_path, col_index)
        elif chosen in open_with_actions:
            command = open_with_actions[chosen]

            if command == "__choose__":
                self.open_path_with_application_chooser(target_path)
            else:
                self.open_path_with_command(target_path, command)
        elif chosen == extract_action:
            self.extract_archive_here(target_path, col_index)
        elif chosen == trash_action:
            self.move_paths_to_trash(selected_paths, col_index)
        elif chosen == delete_permanent_action:
            self.delete_paths_permanently(selected_paths, col_index)
        elif chosen == wallpaper_desktop_action:
            self.set_image_as_wallpaper(target_path, desktop=True, lockscreen=False)
        elif chosen == wallpaper_lock_action:
            self.set_image_as_wallpaper(target_path, desktop=False, lockscreen=True)
        elif chosen == wallpaper_both_action:
            self.set_image_as_wallpaper(target_path, desktop=True, lockscreen=True)

    def style_context_menu(self, menu):
        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(40, 40, 48, 178);
                color: white;
                border: 1px solid rgba(255, 255, 255, 45);
                border-radius: 8px;
                padding: 6px;
            }

            QMenu::item {
                padding: 7px 24px 7px 26px;
                border-radius: 6px;
            }

            QMenu::item:selected {
                background-color: rgba(120, 130, 150, 80);
            }

            QMenu::icon {
                padding-left: 6px;
            }
        """)

    def command_exists(self, command):
        try:
            result = subprocess.run(
                ["which", command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False

    def open_with_candidates_for_path(self, path):
        suffix = path.suffix.lower() if path and not self.is_kio_virtual_path(path) and path.is_file() else ""
        candidates = []

        # VS Code : utile pour dossiers, code, texte, projets.
        if path and (path.is_dir() or suffix in self.preview_text_exts or suffix in {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
            ".json", ".xml", ".sh", ".c", ".cpp", ".h", ".hpp", ".rs",
            ".go", ".java", ".php", ".rb", ".lua", ".sql"
        }):
            candidates.append(("VS Code", "open_vscode", "code"))
            candidates.append(("VSCodium", "open_vscode", "codium"))

        # Kate : texte/code.
        if path and (suffix in self.preview_text_exts or suffix in {
            ".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".sh"
        }):
            candidates.append(("Kate", "app_kate", "kate"))

        # VLC : audio/vidéo.
        if path and (suffix in self.audio_exts or suffix in self.video_exts):
            candidates.append(("VLC", "app_vlc", "vlc"))

        # Images.
        if path and suffix in self.image_exts.union({".svg", ".gif", ".webp"}):
            candidates.append(("GIMP", "app_gimp", "gimp"))
            candidates.append(("Krita", "app_krita", "krita"))

        # Documents.
        if path and suffix in {
            ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods",
            ".ppt", ".pptx", ".odp", ".csv"
        }:
            candidates.append(("LibreOffice", "app_libreoffice", "libreoffice"))

        # Fallbacks utiles pour tout fichier.
        candidates.append(("Ouvrir normalement", "open_with", "xdg-open"))

        filtered = []
        seen_commands = set()

        for label, icon_key, command in candidates:
            if command in seen_commands:
                continue

            if command == "xdg-open" or self.command_exists(command):
                filtered.append((label, icon_key, command))
                seen_commands.add(command)

        return filtered

    def open_path_with_command(self, path, command):
        if path is None or not path.exists():
            return

        try:
            subprocess.Popen(
                [command, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def open_path_with_application_chooser(self, path):
        if path is None or not path.exists():
            return

        # KDE : affiche le dialogue "Ouvrir avec…" natif.
        kde_commands = [
            ["kdialog", "--title", "Ouvrir avec", "--getopenfilename", str(path.parent)],
        ]

        # Plus utile : ouvrir les propriétés/association via kde-open5/kioclient si dispo.
        # En pratique, xdg-open reste le fallback fiable.
        try:
            subprocess.Popen(
                ["kde-open5", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass

        try:
            subprocess.Popen(
                ["kioclient5", "exec", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass

        try:
            subprocess.Popen(
                ["kioclient6", "exec", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass

        self.open_path_with_command(path, "xdg-open")

    def open_path_with_vscode(self, path):
        # Compatibilité avec l'ancien appel direct.
        for command in ["code", "codium", "code-insiders"]:
            if self.command_exists(command):
                self.open_path_with_command(path, command)
                return

    def is_wallpaper_image(self, path):
        if path is None or not path.is_file():
            return False

        return path.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".bmp",
            ".gif",
        }

    def set_image_as_wallpaper(self, path, desktop=True, lockscreen=False):
        if not self.is_wallpaper_image(path):
            return

        image_path = str(path.resolve())

        if desktop:
            self.set_plasma_desktop_wallpaper(image_path)

        if lockscreen:
            self.set_plasma_lockscreen_wallpaper(image_path)

    def set_plasma_desktop_wallpaper(self, image_path):
        # KDE Plasma : applique l'image à tous les bureaux/écrans disponibles.
        script = f"""
            var allDesktops = desktops();

            for (var i = 0; i < allDesktops.length; i++) {{
                var desktop = allDesktops[i];
                desktop.wallpaperPlugin = "org.kde.image";
                desktop.currentConfigGroup = Array("Wallpaper", "org.kde.image", "General");
                desktop.writeConfig("Image", "file://{image_path}");
            }}
        """

        try:
            subprocess.run(
                ["qdbus", "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

        try:
            subprocess.run(
                ["dbus-send", "--session", "--dest=org.kde.plasmashell", "--type=method_call",
                 "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", f"string:{script}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

    def set_plasma_lockscreen_wallpaper(self, image_path):
        # KDE Plasma stocke l'image du verrouillage dans kscreenlockerrc.
        # On met à jour les deux variantes courantes selon les versions Plasma.
        try:
            subprocess.run(
                [
                    "kwriteconfig6",
                    "--file",
                    "kscreenlockerrc",
                    "--group",
                    "Greeter",
                    "--group",
                    "Wallpaper",
                    "--group",
                    "org.kde.image",
                    "--group",
                    "General",
                    "--key",
                    "Image",
                    f"file://{image_path}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

        try:
            subprocess.run(
                [
                    "kwriteconfig6",
                    "--file",
                    "kscreenlockerrc",
                    "--group",
                    "Greeter",
                    "--group",
                    "Wallpaper",
                    "--group",
                    "org.kde.image",
                    "--group",
                    "General",
                    "--key",
                    "PreviewImage",
                    f"file://{image_path}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

        try:
            subprocess.run(
                [
                    "kwriteconfig5",
                    "--file",
                    "kscreenlockerrc",
                    "--group",
                    "Greeter",
                    "--group",
                    "Wallpaper",
                    "--group",
                    "org.kde.image",
                    "--group",
                    "General",
                    "--key",
                    "Image",
                    f"file://{image_path}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

    def is_extractable_archive(self, path):
        if path is None or not path.is_file():
            return False

        name = path.name.lower()

        return (
            name.endswith(".zip")
            or name.endswith(".gz")
            or name.endswith(".tar.gz")
            or name.endswith(".tgz")
        )

    def archive_output_dir(self, archive_path):
        name = archive_path.name
        lowered = name.lower()

        if lowered.endswith(".tar.gz"):
            base = name[:-7]
        elif lowered.endswith(".tgz"):
            base = name[:-4]
        elif lowered.endswith(".zip"):
            base = name[:-4]
        elif lowered.endswith(".gz"):
            base = name[:-3]
        else:
            base = archive_path.stem

        base = base.strip() or "Archive extraite"
        return self.unique_paste_destination(archive_path.parent, base)

    def extract_archive_here(self, archive_path, col_index):
        if not self.is_extractable_archive(archive_path):
            return

        output_dir = self.archive_output_dir(archive_path)

        self.set_operation_status("Extraction en cours…")

        try:
            output_dir.mkdir(parents=True, exist_ok=False)
            lowered = archive_path.name.lower()

            if lowered.endswith(".zip"):
                with zipfile.ZipFile(archive_path, "r") as archive:
                    archive.extractall(output_dir)

            elif lowered.endswith(".tar.gz") or lowered.endswith(".tgz"):
                with tarfile.open(archive_path, "r:gz") as archive:
                    archive.extractall(output_dir)

            elif lowered.endswith(".gz"):
                inner_name = archive_path.name[:-3].strip() or archive_path.stem
                output_file = output_dir / inner_name

                with gzip.open(archive_path, "rb") as source:
                    with output_file.open("wb") as target:
                        shutil.copyfileobj(source, target)

            else:
                return

        except Exception:
            try:
                if output_dir.exists():
                    if output_dir.is_dir():
                        shutil.rmtree(output_dir)
                    else:
                        output_dir.unlink()
            except Exception:
                pass
            self.clear_operation_status()
            return

        self.size_cache.clear()
        self.metadata_cache.clear()
        self.text_preview_cache.clear()
        self.pixmap_preview_cache.clear()

        parent_dir = archive_path.parent

        if self.is_downloads_mode():
            self.rebuild_rows()

            for i, (row_path, _depth) in enumerate(self.rows):
                if row_path == output_dir:
                    self.selected_row = i
                    self.set_table_single_selection(i)
                    self.ensure_selected_visible()
                    break

        else:
            parent_col = None

            for i, column_path in enumerate(self.column_paths):
                if column_path == parent_dir and not self.is_file_preview_column(i):
                    parent_col = i
                    break

            if parent_col is None:
                parent_col = max(0, min(col_index, len(self.column_paths) - 1))

            if 0 <= parent_col < len(self.column_items):
                refreshed = self.safe_children_by_name(parent_dir)

                self.column_paths = self.column_paths[:parent_col + 1]
                self.column_items = self.column_items[:parent_col + 1]
                self.column_selected = self.column_selected[:parent_col + 1]
                self.column_scrolls = self.column_scrolls[:parent_col + 1]

                self.column_paths[parent_col] = parent_dir
                self.column_items[parent_col] = refreshed

                row = refreshed.index(output_dir) if output_dir in refreshed else 0

                self.column_selected[parent_col] = row
                self.active_column = parent_col
                self.set_column_single_selection(output_dir, parent_col, row)
                self.ensure_row_visible_in_column(parent_col, row)

        self.directory_snapshot = self.current_directory_snapshot()
        self.update_realtime_watchers()
        self.update()
        self.clear_operation_status()

    def delete_paths_permanently(self, paths, col_index):
        paths = [
            path
            for path in paths
            if path is not None and path.exists()
        ]

        if not paths:
            return

        parent_dirs = []
        seen = set()

        for path in paths:
            key = str(path.parent)

            if key not in seen:
                seen.add(key)
                parent_dirs.append(key)

        self.start_file_operation(
            "Suppression en cours…",
            "delete",
            paths,
            None,
            {
                "col_index": col_index,
                "parent_dirs": parent_dirs,
            },
        )

    def current_paste_target_dir(self):
        if self.is_downloads_mode():
            return self.root_path

        if not self.column_paths:
            return self.root_path

        if self.is_file_preview_column(self.active_column):
            path = self.column_items[self.active_column][0]

            if path.exists():
                return path.parent

            return self.root_path

        if 0 <= self.active_column < len(self.column_paths):
            path = self.column_paths[self.active_column]

            if path.is_dir():
                return path

            if path.exists():
                return path.parent

        return self.root_path

    def paste_clipboard_from_context_target(self, target_path, col_index):
        if target_path is None:
            target_dir = self.current_paste_target_dir()
        elif target_path.is_dir():
            # Clic droit sur un dossier : on colle dedans.
            target_dir = target_path
        else:
            # Clic droit sur un fichier : on colle dans son dossier parent.
            target_dir = target_path.parent

        if not target_dir or not target_dir.is_dir():
            return

        self.paste_clipboard_into_column(target_dir, col_index)

    def copy_selected_to_clipboard(self):
        paths = self.current_selected_paths()
        self.copy_paths_to_clipboard(paths)

    def paste_clipboard_into_current_location(self):
        target_dir = self.current_paste_target_dir()

        if not target_dir or not target_dir.is_dir():
            return

        col_index = self.active_column if not self.is_downloads_mode() else 0
        self.paste_clipboard_into_column(target_dir, col_index)

    def copy_paths_to_clipboard(self, paths):
        paths = [path for path in paths if path is not None and path.exists()]

        if not paths:
            return

        clipboard = QApplication.clipboard()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        mime.setText("\n".join(str(path) for path in paths))
        clipboard.setMimeData(mime)

    def move_paths_to_trash(self, paths, col_index):
        paths = [path for path in paths if path is not None and path.exists()]

        if not paths:
            return

        parent_dirs = []
        seen = set()

        for path in paths:
            key = str(path.parent)

            if key not in seen:
                seen.add(key)
                parent_dirs.append(key)

        self.start_file_operation(
            "Déplacement vers la corbeille…",
            "trash",
            paths,
            None,
            {
                "col_index": col_index,
                "parent_dirs": parent_dirs,
            },
        )

    def clipboard_source_paths(self):
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()

        paths = []

        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())

                    if path.exists():
                        paths.append(path)

        elif mime.hasText():
            raw_text = mime.text().strip()

            for line in raw_text.splitlines():
                value = line.strip().strip('"').strip("'")

                if value.startswith("file://"):
                    value = QUrl(value).toLocalFile()

                path = Path(value).expanduser()

                if path.exists():
                    paths.append(path)

        # Déduplique en gardant l'ordre.
        unique = []
        seen = set()

        for path in paths:
            key = str(path.resolve())

            if key not in seen:
                seen.add(key)
                unique.append(path)

        return unique

    def clipboard_has_pasteable_files(self):
        return len(self.clipboard_source_paths()) > 0

    def unique_paste_destination(self, parent, name):
        candidate = parent / name

        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix

        if candidate.is_dir() and not suffix:
            stem = candidate.name
            suffix = ""

        index = 2

        while True:
            new_name = f"{stem} copie {index}{suffix}"
            candidate = parent / new_name

            if not candidate.exists():
                return candidate

            index += 1

    def copy_path_to_destination(self, source, destination):
        if source.is_dir():
            shutil.copytree(source, destination, symlinks=True)
        else:
            shutil.copy2(source, destination)

    def paste_clipboard_into_column(self, target_dir, col_index):
        if not target_dir.is_dir():
            return

        sources = self.clipboard_source_paths()

        if not sources:
            return

        self.start_file_operation(
            "Copie en cours…",
            "copy",
            sources,
            target_dir,
            {
                "kind": "paste",
                "col_index": col_index,
            },
        )

    def unique_child_path(self, parent, base_name, suffix=""):
        candidate = parent / f"{base_name}{suffix}"

        if not candidate.exists():
            return candidate

        index = 2

        while True:
            candidate = parent / f"{base_name} {index}{suffix}"

            if not candidate.exists():
                return candidate

            index += 1

    def ask_new_item_name(self, title, label, default_name):
        name, ok = QInputDialog.getText(
            self,
            title,
            label,
            text=default_name,
        )

        if not ok:
            return None

        name = name.strip()

        if not name or "/" in name or name.startswith("."):
            return None

        return name

    def create_new_folder_in_column(self, target_dir, col_index):
        if not target_dir.is_dir():
            return

        default_path = self.unique_child_path(target_dir, "Nouveau dossier")
        name = self.ask_new_item_name(
            "Nouveau dossier",
            "Nom du dossier :",
            default_path.name,
        )

        if not name:
            return

        new_path = target_dir / name

        if new_path.exists():
            return

        try:
            new_path.mkdir()
        except Exception:
            return

        self.refresh_column_after_new_item(target_dir, new_path, col_index)

    def create_new_text_file_in_column(self, target_dir, col_index):
        if not target_dir.is_dir():
            return

        default_path = self.unique_child_path(target_dir, "Nouveau fichier", ".txt")
        name = self.ask_new_item_name(
            "Nouveau fichier texte",
            "Nom du fichier :",
            default_path.name,
        )

        if not name:
            return

        new_path = target_dir / name

        if new_path.exists():
            return

        try:
            new_path.write_text("", encoding="utf-8")
        except Exception:
            return

        self.refresh_column_after_new_item(target_dir, new_path, col_index)

    def refresh_column_after_new_item(self, target_dir, new_path, fallback_col_index):
        self.size_cache.clear()
        self.metadata_cache.clear()

        if self.is_downloads_mode():
            self.rebuild_rows()

            for i, (row_path, _depth) in enumerate(self.rows):
                if row_path == new_path:
                    self.selected_row = i
                    break

            self.ensure_selected_visible()
            self.update()
            return

        col_index = None

        for i, column_path in enumerate(self.column_paths):
            if column_path == target_dir and not self.is_file_preview_column(i):
                col_index = i
                break

        if col_index is None:
            col_index = fallback_col_index

        if not (0 <= col_index < len(self.column_items)):
            return

        refreshed = self.safe_children_by_name(target_dir)
        self.column_paths[col_index] = target_dir
        self.column_items[col_index] = refreshed

        try:
            row = refreshed.index(new_path)
        except ValueError:
            row = 0

        self.column_selected[col_index] = row
        self.column_scrolls[col_index] = 0
        self.active_column = col_index
        self.ensure_row_visible_in_column(col_index, row)

        # Comme une navigation clavier : on coupe la droite, puis on affiche
        # seulement la première colonne liée au nouvel élément.
        self.open_path_in_next_column(new_path, col_index, auto_child=False)
        self.pin_horizontal_scroll_to_right()
        self.update_realtime_watchers()
        self.update()

    def clear_multi_selection(self):
        self.selected_table_rows = set()
        self.selected_column_paths = set()
        self.selection_anchor_table = None
        self.selection_anchor_column = None
        self.selection_anchor_row = None

    def set_table_single_selection(self, row):
        self.column_selection_enabled = True
        self.selected_column_paths = set()
        self.selected_table_rows = {row}
        self.selection_anchor_column = None
        self.selection_anchor_row = None
        self.selection_anchor_table = row
        self.selected_row = row

    def extend_table_selection_to(self, row):
        if not self.rows:
            return

        if self.selection_anchor_table is None:
            self.selection_anchor_table = self.selected_row

        start = max(0, min(self.selection_anchor_table, row))
        end = min(len(self.rows) - 1, max(self.selection_anchor_table, row))

        self.selected_table_rows = set(range(start, end + 1))
        self.selected_row = row
        self.ensure_selected_visible()

    def toggle_table_row_selection(self, row):
        if not (0 <= row < len(self.rows)):
            return

        if row in self.selected_table_rows and len(self.selected_table_rows) > 1:
            self.selected_table_rows.remove(row)
        else:
            self.selected_table_rows.add(row)

        self.selected_row = row
        self.selection_anchor_table = row

    def table_selected_paths(self):
        if self.is_downloads_mode():
            return [
                self.rows[i][0]
                for i in sorted(self.selected_table_rows)
                if 0 <= i < len(self.rows)
            ]

        return []

    def set_column_single_selection(self, path, col_index, row):
        self.column_selection_enabled = True
        self.selected_table_rows = set()
        self.selected_column_paths = {path}
        self.selection_anchor_table = None
        self.selection_anchor_column = col_index
        self.selection_anchor_row = row

    def extend_column_selection_to(self, col_index, row):
        if not (0 <= col_index < len(self.column_items)):
            return

        items = self.column_items[col_index]

        if not items:
            return

        if self.selection_anchor_column != col_index or self.selection_anchor_row is None:
            self.selection_anchor_column = col_index
            self.selection_anchor_row = self.column_selected[col_index]

        start = max(0, min(self.selection_anchor_row, row))
        end = min(len(items) - 1, max(self.selection_anchor_row, row))

        self.selected_column_paths = {
            items[i]
            for i in range(start, end + 1)
        }

        self.column_selected[col_index] = row
        self.active_column = col_index
        self.ensure_row_visible_in_column(col_index, row)

    def toggle_column_path_selection(self, path, col_index, row):
        if path in self.selected_column_paths and len(self.selected_column_paths) > 1:
            self.selected_column_paths.remove(path)
        else:
            self.selected_column_paths.add(path)

        self.active_column = col_index
        self.column_selected[col_index] = row
        self.selection_anchor_column = col_index
        self.selection_anchor_row = row

    def column_selected_paths(self):
        existing = []

        for path in self.selected_column_paths:
            if self.is_kio_virtual_path(path) or path.exists():
                existing.append(path)

        return existing

    def current_selected_paths(self):
        if self.is_downloads_mode():
            selected = self.table_selected_paths()

            if selected:
                return selected

            path = self.selected_path()
            return [path] if path else []

        selected = self.column_selected_paths()

        if selected:
            return selected

        path = self.selected_path()
        return [path] if path else []

    def mousePressEvent(self, event):
        self.setFocus()

        x = int(event.position().x())
        y = int(event.position().y())

        if event.button() == Qt.MouseButton.LeftButton and x >= self.sidebar_w:
            self.drag_start_position = event.position().toPoint()
            self.drag_start_path = self.path_at_position(x, y)
        else:
            self.drag_start_position = None
            self.drag_start_path = None

        if event.button() == Qt.MouseButton.RightButton and x >= self.sidebar_w:
            if self.is_downloads_mode():
                self.show_table_context_menu(event.globalPosition().toPoint(), x, y)
            else:
                self.show_column_context_menu(event.globalPosition().toPoint(), x, y)
            return

        if x < self.sidebar_w:
            self.cancel_rename()
            self.refresh_locations()
            index = y // self.row_h

            if event.button() == Qt.MouseButton.RightButton:
                if 0 <= index < len(self.locations) and self.is_removable_location(index):
                    self.show_drive_context_menu(event.globalPosition().toPoint(), index)
                return

            if (
                event.button() == Qt.MouseButton.LeftButton
                and 0 <= index < len(self.locations)
                and self.is_drive_location(index)
                and x >= self.sidebar_w - 40
            ):
                self.eject_drive(index)
                return

            if 0 <= index < len(self.locations) and self.location_kind(index) == "phone_kio":
                self.current_location = index
                self.open_kio_phone(index)
                self.update()
                return

            if 0 <= index < len(self.locations):
                self.current_location = index
                self.root_path = self.locations[index][1]
                self.open_dirs.clear()
                self.scroll = 0
                self.hscroll = 0
                self.hscroll_stuck_to_right = True
                self.selected_row = 0
                self.clear_multi_selection()
                self.active_column = 0
                self.size_cache.clear()
                self.text_preview_cache.clear()
                self.pixmap_preview_cache.clear()
                self.close_preview()

                if self.is_downloads_mode():
                    self.column_selection_enabled = True
                    self.sort_mode = "Date de l'ajout"
                    self.sort_reverse = True
                    self.rebuild_rows()
                else:
                    self.column_selection_enabled = False
                    self.disable_next_column_auto_selection = True
                    self.rebuild_columns()

                self.update()

            return

        if self.is_downloads_mode():
            self.mouse_press_table(x, y)
        else:
            self.mouse_press_columns(x, y)

    def mouse_press_table(self, x, y):
        if y < self.search_h:
            return

        if y < self.content_top():
            self.cancel_rename()

            rel_x = x - self.sidebar_w - 6
            acc = 0
            widths = self.table_column_widths()
            headers = ["Nom", "Taille", "Type", "Date de l'ajout"]

            for name, w in zip(headers, widths):
                if acc <= rel_x < acc + w:
                    if self.sort_mode == name:
                        self.sort_reverse = not self.sort_reverse
                    else:
                        self.sort_mode = name
                        self.sort_reverse = False

                    if self.sort_mode == "Taille":
                        self.folder_size_enabled = True
                        self.size_cache.clear()

                    self.rebuild_rows()
                    self.update()
                    return

                acc += w

            return

        row_index = (y - self.content_top() + self.scroll) // self.row_h

        if not (0 <= row_index < len(self.rows)):
            self.cancel_rename()
            self.clear_multi_selection()
            self.update()
            return

        was_selected = self.selected_row == row_index
        path, depth = self.rows[row_index]

        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.toggle_table_row_selection(row_index)
            self.update()
            return

        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
            self.toggle_table_row_selection(row_index)
            self.update()
            return

        keep_existing_multi_selection = (
            row_index in self.selected_table_rows
            and len(self.selected_table_rows) > 1
        )

        self.selected_row = row_index

        row_y = self.content_top() - self.scroll + row_index * self.row_h

        widths = self.table_column_widths()
        table_x = self.sidebar_w + 6
        depth_px = depth * 24
        arrow_x = table_x + depth_px
        icon_x = arrow_x + 20
        name_x = icon_x + 26
        name_w = widths[0] - depth_px - 50

        clicked_name = (
            name_x <= x <= name_x + max(20, name_w)
            and row_y <= y <= row_y + self.row_h
        )

        # Comme dans les autres onglets :
        # clic sur le nom déjà sélectionné = renommage inline.
        if was_selected and clicked_name:
            self.start_rename(path, None, row_index, name_x, row_y, name_w)
            return

        self.cancel_rename()

        arrow_left = self.sidebar_w + 6 + depth * 24
        arrow_right = arrow_left + 18

        if path.is_dir() and arrow_left <= x <= arrow_right:
            if not keep_existing_multi_selection:
                self.set_table_single_selection(row_index)
            self.toggle_path(path)

        else:
            if not keep_existing_multi_selection:
                self.set_table_single_selection(row_index)

        self.update()

    def mouse_press_columns(self, x, y):
        divider = self.column_divider_at(x)

        if divider is not None:
            self.cancel_rename()
            self.resizing_browser_column = divider
            self.resize_start_x = x
            self.resize_start_width = self.column_width(divider)
            return

        col_index, local_x = self.column_at_x(x)

        if col_index is None:
            self.cancel_rename()
            self.clear_multi_selection()
            self.update()
            return

        # Clic sur colonne d'aperçu fichier = sélection unique du fichier affiché.
        # Clic normal ailleurs que Maj/Ctrl doit donc annuler l'ancienne sélection multiple.
        if self.is_file_preview_column(col_index):
            self.cancel_rename()
            self.active_column = col_index

            if self.column_items[col_index]:
                self.selected_column_paths = {self.column_items[col_index][0]}
                self.selection_anchor_column = col_index
                self.selection_anchor_row = 0
            else:
                self.clear_multi_selection()

            self.pin_horizontal_scroll_to_right()
            self.update()
            return

        if y < self.content_top():
            self.cancel_rename()
            self.active_column = col_index
            self.clear_multi_selection()
            self.update()
            return

        items = self.column_items[col_index]
        row = (y - self.content_top() + self.column_scrolls[col_index]) // self.row_h

        if not (0 <= row < len(items)):
            self.cancel_rename()
            self.active_column = col_index
            self.clear_multi_selection()
            self.update()
            return

        row_y = self.content_top() - self.column_scrolls[col_index] + row * self.row_h
        column_x = self.column_screen_x(col_index)
        col_w = self.column_width(col_index)

        # Si on clique dans la colonne mais sous/après la vraie ligne visuelle,
        # on considère que c'est du vide et on désélectionne tout.
        if not (row_y <= y <= row_y + self.row_h):
            self.cancel_rename()
            self.active_column = col_index
            self.clear_multi_selection()
            self.update()
            return

        path = items[row]

        name_x = column_x + 38
        name_w = col_w - 72

        # Toute la ligne est maintenant cliquable/sélectionnable,
        # pas seulement le titre ou l'icône.
        was_selected = (
            col_index == self.active_column
            and self.column_selected[col_index] == row
        )

        # Pour éviter de renommer en cliquant n'importe où sur la ligne,
        # le renommage reste déclenché seulement par un second clic sur le titre.
        text_width = self.fontMetrics().horizontalAdvance(path.name)
        visible_name_w = max(20, min(name_w, text_width + 12))

        clicked_name = (
            name_x <= x <= name_x + visible_name_w
            and row_y <= y <= row_y + self.row_h
        )

        # Si l'élément est déjà sélectionné, un clic sur son titre active le renommage.
        # Ça marche après sélection par souris OU par navigation clavier.
        if was_selected and clicked_name:
            self.start_rename(path, col_index, row, name_x, row_y, name_w)
            return

        self.cancel_rename()

        self.column_selection_enabled = True
        self.suppress_auto_file_preview_until_click = False

        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.toggle_column_path_selection(path, col_index, row)
            self.update()
            return

        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
            self.toggle_column_path_selection(path, col_index, row)
            self.update()
            return

        keep_existing_multi_selection = (
            path in self.selected_column_paths
            and len(self.selected_column_paths) > 1
        )

        if keep_existing_multi_selection:
            self.active_column = col_index
            self.column_selected[col_index] = row
            self.ensure_row_visible_in_column(col_index, row)
            self.update()
            return

        # Clic normal sans Maj/Ctrl sur un élément hors sélection = nouvelle sélection unique.
        self.selected_column_paths = set()
        self.select_column_row_like_keyboard(col_index, row)
        self.update()

    def column_screen_x(self, col_index):
        return self.sidebar_w - self.hscroll + sum(
            self.column_width(i) for i in range(col_index)
        )

    def start_rename(self, path, col_index, row, name_x, row_y, name_w):
        if not path.exists():
            return

        self.rename_target_path = path
        self.rename_column = col_index
        self.rename_row = row
        self.rename_committing = False

        if col_index is None:
            editor_w = max(80, name_w)
        else:
            editor_w = max(80, min(name_w, self.column_width(col_index) - 48))

        self.rename_editor.setText(path.name)
        self.rename_editor.setGeometry(
            int(name_x - 2),
            int(row_y + 4),
            int(editor_w),
            int(self.row_h - 8),
        )
        self.rename_editor.show()
        self.rename_editor.raise_()
        self.rename_editor.setFocus(Qt.FocusReason.MouseFocusReason)
        self.rename_editor.selectAll()

    def cancel_rename(self):
        if not hasattr(self, "rename_editor"):
            return

        self.rename_committing = False
        self.rename_target_path = None
        self.rename_column = None
        self.rename_row = None
        self.rename_editor.hide()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def finish_rename_if_focus_lost(self):
        if self.rename_editor.isVisible() and not self.rename_committing:
            self.finish_rename()

    def finish_rename(self):
        if not self.rename_editor.isVisible() or self.rename_target_path is None:
            return

        if self.rename_committing:
            return

        self.rename_committing = True

        old_path = self.rename_target_path
        col_index = self.rename_column
        table_row = self.rename_row
        new_name = self.rename_editor.text().strip()

        self.rename_editor.hide()

        if not new_name or new_name == old_path.name:
            self.cancel_rename()
            return

        # On évite les séparateurs de chemin dans un nom de fichier Unix.
        if "/" in new_name:
            self.cancel_rename()
            return

        new_path = old_path.with_name(new_name)

        if new_path.exists():
            self.cancel_rename()
            return

        try:
            old_path.rename(new_path)
        except Exception:
            self.cancel_rename()
            return

        self.rename_target_path = None
        self.rename_column = None
        self.rename_row = None
        self.rename_committing = False
        self.size_cache.clear()
        self.metadata_cache.clear()

        if self.is_downloads_mode() or col_index is None:
            self.rebuild_rows()

            # Retrouve et resélectionne l'élément renommé dans la vue Téléchargements.
            for i, (row_path, _depth) in enumerate(self.rows):
                if row_path == new_path:
                    self.selected_row = i
                    break
            else:
                self.selected_row = min(
                    table_row if table_row is not None else 0,
                    max(0, len(self.rows) - 1)
                )

            self.ensure_selected_visible()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            self.update()
            return

        if col_index is not None and 0 <= col_index < len(self.column_paths):
            parent_path = new_path.parent

            # Rafraîchit la colonne parente et resélectionne le fichier/dossier renommé.
            if self.column_paths[col_index] == parent_path:
                refreshed = self.safe_children_by_name(parent_path)
                self.column_items[col_index] = refreshed

                try:
                    new_row = refreshed.index(new_path)
                except ValueError:
                    new_row = 0

                self.column_selected[col_index] = new_row
                self.active_column = col_index
                self.ensure_row_visible_in_column(col_index, new_row)

                # Reconstruit une seule colonne à droite, comme une navigation clavier.
                self.open_path_in_next_column(new_path, col_index, auto_child=False)

        self.pin_horizontal_scroll_to_right()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.update()

    def select_column_row_like_keyboard(self, col_index, row):
        self.cancel_rename()
        self.column_selection_enabled = True

        if not (0 <= col_index < len(self.column_items)):
            return

        items = self.column_items[col_index]

        if not (0 <= row < len(items)):
            return

        # On garde seulement les colonnes jusqu'à celle cliquée.
        # Toute prévisualisation/branche plus à droite disparaît avant reconstruction.
        self.column_paths = self.column_paths[:col_index + 1]
        self.column_items = self.column_items[:col_index + 1]
        self.column_selected = self.column_selected[:col_index + 1]
        self.column_scrolls = self.column_scrolls[:col_index + 1]

        self.column_selected[col_index] = row
        self.active_column = col_index
        self.ensure_row_visible_in_column(col_index, row)

        path = self.column_items[col_index][row]

        # Une seule colonne immédiate à droite, exactement comme quand on se déplace
        # avec ↑/↓ dans une colonne déjà ouverte.
        if path.is_dir() or self.kio_path_is_dir(path):
            self.column_paths.append(path)
            self.column_items.append(self.safe_children_by_name(path))
        else:
            self.column_paths.append(path)
            self.column_items.append([path])

        self.column_selected.append(0)
        self.column_scrolls.append(0)
        self.pin_horizontal_scroll_to_right()

    def mouseMoveEvent(self, event):
        x = int(event.position().x())
        y = int(event.position().y())

        if self.resizing_browser_column is not None:
            delta = x - self.resize_start_x
            new_width = max(180, self.resize_start_width + delta)
            self.column_widths_custom[self.resizing_browser_column] = new_width
            self.ensure_column_visible(self.active_column)
            self.update()
            return

        if event.buttons() & Qt.MouseButton.LeftButton:
            if self.drag_start_position is not None:
                distance = (event.position().toPoint() - self.drag_start_position).manhattanLength()

                if distance >= QApplication.startDragDistance():
                    drag_path = self.drag_start_path

                    # Sécurité :
                    # si le chemin n'a pas été mémorisé au mousePress,
                    # on le recalcule sous la souris au moment du drag.
                    if drag_path is None:
                        drag_path = self.path_at_position(x, y)

                    if drag_path is not None and drag_path.exists():
                        self.start_drag_for_path(drag_path)

                    self.drag_start_position = None
                    self.drag_start_path = None
                    return

        if not self.is_downloads_mode() and self.column_divider_at(x) is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self.resizing_browser_column = None
        self.drag_start_position = None
        self.drag_start_path = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def column_divider_at(self, x):
        if x < self.sidebar_w:
            return None

        pos = self.sidebar_w - self.hscroll

        for i in range(len(self.column_items)):
            pos += self.column_width(i)

            if abs(x - pos) <= 5:
                return i

        return None

    def column_at_x(self, x):
        if x < self.sidebar_w:
            return None, None

        pos = self.sidebar_w - self.hscroll

        for i in range(len(self.column_items)):
            width = self.column_width(i)

            if pos <= x < pos + width:
                return i, x - pos

            pos += width

        return None, None

    def select_all_current_view(self):
        if self.is_downloads_mode():
            self.select_all_table_rows()
        else:
            self.select_all_active_column_items()

        self.update()

    def select_all_table_rows(self):
        if not self.rows:
            return

        self.column_selection_enabled = True
        self.selected_column_paths = set()
        self.selected_table_rows = set(range(len(self.rows)))
        self.selected_row = 0
        self.selection_anchor_table = 0
        self.selection_anchor_column = None
        self.selection_anchor_row = None
        self.ensure_selected_visible()

    def select_all_active_column_items(self):
        if not self.column_items:
            return

        col_index = self.active_column

        if not (0 <= col_index < len(self.column_items)):
            col_index = 0

        if self.is_file_preview_column(col_index):
            if self.column_items[col_index]:
                self.column_selection_enabled = True
                self.selected_table_rows = set()
                self.selected_column_paths = {self.column_items[col_index][0]}
                self.selection_anchor_column = col_index
                self.selection_anchor_row = 0
            return

        items = [
            path
            for path in self.column_items[col_index]
            if path is not None and path.exists()
        ]

        if not items:
            return

        self.column_selection_enabled = True
        self.selected_table_rows = set()
        self.selected_column_paths = set(items)
        self.column_selected[col_index] = 0
        self.active_column = col_index
        self.selection_anchor_table = None
        self.selection_anchor_column = col_index
        self.selection_anchor_row = 0
        self.ensure_row_visible_in_column(col_index, 0)

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key.Key_Escape
            and getattr(self, "file_operation_worker", None) is not None
        ):
            self.cancel_file_operation()
            event.accept()
            return

        if event.key() == Qt.Key.Key_F5:
            self.manual_refresh_now()
            event.accept()
            return

        if self.rename_editor.isVisible():
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_rename()
                event.accept()
                return

            if event.key() in [Qt.Key.Key_Return, Qt.Key.Key_Enter]:
                self.finish_rename()
                event.accept()
                return

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_F:
                self.global_search_active = False
                self.search_editor.setFocus(Qt.FocusReason.ShortcutFocusReason)
                self.search_editor.selectAll()
                event.accept()
                return

            if event.key() == Qt.Key.Key_A:
                self.select_all_current_view()
                event.accept()
                return

            if event.key() == Qt.Key.Key_C:
                self.copy_selected_to_clipboard()
                event.accept()
                return

            if event.key() == Qt.Key.Key_V:
                self.paste_clipboard_into_current_location()
                event.accept()
                return

        if self.is_downloads_mode():
            self.key_table(event)
        else:
            self.key_columns(event)

    def key_table(self, event):
        if not self.rows:
            return

        key = event.key()

        if key == Qt.Key.Key_Down:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                new_row = min(self.selected_row + 1, len(self.rows) - 1)
                self.extend_table_selection_to(new_row)
            else:
                self.move_selection(1)
                self.set_table_single_selection(self.selected_row)
            self.update()

        elif key == Qt.Key.Key_Up:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                new_row = max(self.selected_row - 1, 0)
                self.extend_table_selection_to(new_row)
            else:
                self.move_selection(-1)
                self.set_table_single_selection(self.selected_row)
            self.update()

        elif key == Qt.Key.Key_Right:
            path = self.rows[self.selected_row][0]

            if path.is_dir() and path not in self.open_dirs:
                self.open_dirs.add(path)
                self.rebuild_rows()
                self.update()

        elif key == Qt.Key.Key_Left:
            path = self.rows[self.selected_row][0]

            if path.is_dir() and path in self.open_dirs:
                self.open_dirs.remove(path)
                self.rebuild_rows()
                self.update()

        elif key == Qt.Key.Key_Space:
            self.toggle_preview()

        elif key == Qt.Key.Key_Escape:
            self.close_preview()

    def key_columns(self, event):
        if not self.column_items:
            return

        key = event.key()

        if key == Qt.Key.Key_Down:
            if not self.column_selection_enabled:
                if self.column_items and self.column_items[0]:
                    self.column_selection_enabled = True
                    self.select_column_row_like_keyboard(0, 0)
            elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if not self.is_file_preview_column(self.active_column) and self.column_items[self.active_column]:
                    new_row = min(
                        self.column_selected[self.active_column] + 1,
                        len(self.column_items[self.active_column]) - 1
                    )
                    self.extend_column_selection_to(self.active_column, new_row)
            else:
                self.move_column_selection(1)

                if not self.is_file_preview_column(self.active_column) and self.column_items[self.active_column]:
                    path = self.column_items[self.active_column][self.column_selected[self.active_column]]
                    self.set_column_single_selection(path, self.active_column, self.column_selected[self.active_column])

            self.pin_horizontal_scroll_to_right()
            self.update()

        elif key == Qt.Key.Key_Up:
            if not self.column_selection_enabled:
                if self.column_items and self.column_items[0]:
                    self.column_selection_enabled = True
                    self.select_column_row_like_keyboard(0, 0)
            elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if not self.is_file_preview_column(self.active_column) and self.column_items[self.active_column]:
                    new_row = max(
                        self.column_selected[self.active_column] - 1,
                        0
                    )
                    self.extend_column_selection_to(self.active_column, new_row)
            else:
                self.move_column_selection(-1)

                if not self.is_file_preview_column(self.active_column) and self.column_items[self.active_column]:
                    path = self.column_items[self.active_column][self.column_selected[self.active_column]]
                    self.set_column_single_selection(path, self.active_column, self.column_selected[self.active_column])

            self.pin_horizontal_scroll_to_right()
            self.update()

        elif key == Qt.Key.Key_Right:
            self.enter_selected_column_item()

        elif key == Qt.Key.Key_Left:
            if self.active_column > 0:
                self.active_column -= 1
                self.column_paths = self.column_paths[:self.active_column + 1]
                self.column_items = self.column_items[:self.active_column + 1]
                self.column_selected = self.column_selected[:self.active_column + 1]
                self.column_scrolls = self.column_scrolls[:self.active_column + 1]
                self.pin_horizontal_scroll_to_right()
                self.update()

        elif key == Qt.Key.Key_Space:
            self.toggle_preview()

        elif key == Qt.Key.Key_Escape:
            self.close_preview()

    def move_column_selection(self, delta):
        if not self.column_items:
            return

        if self.is_file_preview_column(self.active_column):
            return

        items = self.column_items[self.active_column]

        if not items:
            return

        row = max(
            0,
            min(self.column_selected[self.active_column] + delta, len(items) - 1)
        )

        self.select_column_row_like_keyboard(self.active_column, row)

    def enter_selected_column_item(self):
        self.suppress_auto_file_preview_until_click = False

        if not self.column_items:
            return

        if self.is_file_preview_column(self.active_column):
            return

        items = self.column_items[self.active_column]

        if not items:
            return

        path = items[self.column_selected[self.active_column]]

        if not (path.is_dir() or self.kio_path_is_dir(path)):
            if self.is_kio_virtual_path(path):
                self.open_kio_url_external(self.kio_url_for_path(path))
                return

            self.open_path_in_next_column(path, self.active_column, auto_child=False)
            self.pin_horizontal_scroll_to_right()
            self.update()
            return

        self.open_path_in_next_column(path, self.active_column, auto_child=True)
        self.active_column = min(self.active_column + 1, len(self.column_items) - 1)
        self.pin_horizontal_scroll_to_right()
        self.update_realtime_watchers()
        self.update()

    def open_path_in_next_column(self, path, col_index, auto_child=False):
        if (
            getattr(self, "suppress_auto_file_preview_until_click", False)
            and path is not None
            and path.is_file()
        ):
            self.column_paths = self.column_paths[:col_index + 1]
            self.column_items = self.column_items[:col_index + 1]
            self.column_selected = self.column_selected[:col_index + 1]
            self.column_scrolls = self.column_scrolls[:col_index + 1]
            return

        self.column_paths = self.column_paths[:col_index + 1]
        self.column_items = self.column_items[:col_index + 1]
        self.column_selected = self.column_selected[:col_index + 1]
        self.column_scrolls = self.column_scrolls[:col_index + 1]

        if path.is_dir() or self.kio_path_is_dir(path):
            children = self.safe_children_by_name(path)

            self.column_paths.append(path)
            self.column_items.append(children)
            self.column_selected.append(0)
            self.column_scrolls.append(0)

            if auto_child and children:
                first_child = children[0]

                self.column_paths.append(first_child)

                if first_child.is_dir() or self.kio_path_is_dir(first_child):
                    self.column_items.append(self.safe_children_by_name(first_child))
                else:
                    self.column_items.append([first_child])

                self.column_selected.append(0)
                self.column_scrolls.append(0)

        else:
            self.column_paths.append(path)
            self.column_items.append([path])
            self.column_selected.append(0)
            self.column_scrolls.append(0)

        self.pin_horizontal_scroll_to_right()

    def rebuild_columns(self):
        self.sort_mode = "Nom"
        self.sort_reverse = False

        root_children = self.safe_children_by_name(self.root_path)

        self.column_paths = [self.root_path]
        self.column_items = [root_children]
        self.column_selected = [0]
        self.column_scrolls = [0]
        self.column_widths_custom = [
            max(260, int((self.width() - self.sidebar_w) * 0.32))
        ]
        self.active_column = 0

        if self.disable_next_column_auto_selection:
            self.disable_next_column_auto_selection = False
            self.selected_column_paths = set()
            self.selection_anchor_column = None
            self.selection_anchor_row = None
            self.hscroll = 0
            self.clamp_hscroll()
            return

        self.column_selection_enabled = bool(root_children)

        if root_children:
            first_child = root_children[0]
            self.set_column_single_selection(first_child, 0, 0)

        self.hscroll = 0
        self.clamp_hscroll()

    def priority_name_sort_key(self, path):
        name = path.name
        lowered = name.lower()

        # Priorité aux noms qui commencent par des caractères "symboles"
        # utilisés pour forcer un dossier/fichier en haut.
        # On inclut aussi quelques caractères de remplacement/carrés courants.
        priority_chars = {
            "<",
            "/",
            "□",
            "▢",
            "■",
            "▣",
            "�",
            "􀀀",
        }

        first_char = name[:1]

        if first_char in priority_chars:
            priority = 0
        else:
            priority = 1

        return (priority, lowered)

    def safe_children_by_name(self, path):
        if self.is_kio_virtual_path(path):
            children = [
                p for p in self.list_kio_children(path)[:self.max_list_items_per_dir]
                if not p.name.startswith(".")
            ]

            children = self.filter_paths_for_search(children)

            return sorted(
                children,
                key=lambda p: p.name.lower()
            )

        try:
            children = []
            count = 0

            # os.scandir est beaucoup plus rapide que Path.iterdir()+stat sur gros dossiers.
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue

                    count += 1

                    if count > self.max_list_items_per_dir and not self.search_is_active():
                        break

                    children.append(Path(entry.path))

            children = self.filter_paths_for_search(children)

            # En mode colonnes, tri nom simple uniquement = fluide.
            return sorted(children, key=lambda p: p.name.lower())

        except Exception:
            return []

    def move_selection(self, delta):
        if not self.rows:
            return

        self.selected_row = max(0, min(self.selected_row + delta, len(self.rows) - 1))
        self.ensure_selected_visible()

    def move_selection_for_preview(self, delta):
        if self.is_downloads_mode():
            if not self.rows:
                return False

            new_index = max(0, min(self.selected_row + delta, len(self.rows) - 1))

            if new_index == self.selected_row:
                return False

            self.selected_row = new_index
            self.ensure_selected_visible()
            self.update()
            return True

        if not self.column_items:
            return False

        if self.is_file_preview_column(self.active_column):
            return False

        items = self.column_items[self.active_column]

        if not items:
            return False

        new_index = max(0, min(self.column_selected[self.active_column] + delta, len(items) - 1))

        if new_index == self.column_selected[self.active_column]:
            return False

        self.column_selected[self.active_column] = new_index
        self.ensure_row_visible_in_column(self.active_column, new_index)
        self.update()
        return True

    def toggle_preview(self):
        if self.preview is not None:
            self.close_preview()
            return

        self.open_preview_for_selected()

    def selected_path(self):
        if self.is_downloads_mode():
            if not self.rows:
                return None
            return self.rows[self.selected_row][0]

        if not self.column_items:
            return None

        if not self.column_selection_enabled:
            return None

        if self.is_file_preview_column(self.active_column):
            return self.column_items[self.active_column][0]

        items = self.column_items[self.active_column]

        if not items:
            return None

        return items[self.column_selected[self.active_column]]

    def open_preview_for_selected(self):
        path = self.selected_path()

        if path is None:
            return

        if self.is_kio_virtual_path(path):
            if self.kio_path_is_dir(path):
                if self.active_column < len(self.column_items):
                    self.open_path_in_next_column(path, self.active_column, auto_child=False)
                    self.pin_horizontal_scroll_to_right()
                    self.update()
            else:
                self.open_kio_url_external(self.kio_url_for_path(path))
            return

        self.preview = PreviewWindow(path, parent=self)
        self.preview.show()
        self.preview.raise_()
        self.preview.activateWindow()
        self.preview.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def navigate_preview(self, delta):
        if self.preview is None:
            return

        if not self.move_selection_for_preview(delta):
            return

        self.close_preview()
        self.open_preview_for_selected()

    def close_preview(self):
        if self.preview is None:
            return

        preview = self.preview
        self.preview = None

        try:
            preview.cleanup_media()
            preview.close()
        except RuntimeError:
            pass

        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def ensure_selected_visible(self):
        y = self.selected_row * self.row_h
        visible_bottom = self.scroll + self.height() - self.content_top()

        if y < self.scroll:
            self.scroll = y

        elif y + self.row_h > visible_bottom:
            self.scroll = y + self.row_h - (self.height() - self.content_top())

        self.clamp_scroll()

    def ensure_row_visible_in_column(self, col_index, row):
        y = row * self.row_h
        visible_bottom = self.column_scrolls[col_index] + self.height() - self.content_top()

        if y < self.column_scrolls[col_index]:
            self.column_scrolls[col_index] = y

        elif y + self.row_h > visible_bottom:
            self.column_scrolls[col_index] = y + self.row_h - (self.height() - self.content_top())

        self.clamp_column_scroll(col_index)

    def ensure_column_visible(self, col_index):
        # Dans le navigateur en colonnes, on garde toujours le scroll horizontal
        # collé sur son extrémité droite dès qu'une navigation change les barres.
        # Ça évite de devoir rescroller manuellement pour retrouver la dernière colonne.
        self.pin_horizontal_scroll_to_right()

    def pin_horizontal_scroll_to_right(self, activate=True):
        if activate:
            self.hscroll_stuck_to_right = True

        total_w = sum(self.column_width(i) for i in range(len(self.column_items)))
        visible_w = self.width() - self.sidebar_w
        self.hscroll = max(0, total_w - visible_w)
        self.clamp_hscroll()

    def keep_horizontal_scroll_stuck_if_needed(self):
        if self.hscroll_stuck_to_right:
            self.pin_horizontal_scroll_to_right(activate=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_search_widgets()
        self.position_cancel_operation_button()

        if not self.is_downloads_mode():
            self.keep_horizontal_scroll_stuck_if_needed()

    def wheelEvent(self, event):
        x = int(event.position().x())

        if self.is_downloads_mode():
            self.scroll -= int(event.angleDelta().y() / 3)
            self.clamp_scroll()
            self.update()
            return

        if not self.column_items:
            return

        dx = event.angleDelta().x()
        dy = event.angleDelta().y()

        # Scroll horizontal au trackpad / deux doigts gauche-droite.
        if dx != 0 and abs(dx) >= abs(dy):
            self.hscroll -= int(dx / 3)
            self.clamp_hscroll()
            self.update()
            return

        # Shift + molette verticale = scroll horizontal.
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.hscroll -= int(dy / 3)
            self.clamp_hscroll()
            self.update()
            return

        # Important :
        # on scrolle la colonne sous la souris, pas seulement la colonne active.
        # Avant, si une barre de droite était pleine de haut en bas mais pas active,
        # la molette donnait l'impression de ne rien faire.
        col_index, _local_x = self.column_at_x(x)

        if col_index is None:
            col_index = self.active_column

        if self.is_file_preview_column(col_index):
            # Les colonnes d'aperçu dessinées au QPainter ne sont pas scrollables.
            # On garde le comportement précédent pour éviter les mouvements inutiles.
            return

        if not (0 <= col_index < len(self.column_items)):
            return

        self.active_column = col_index
        self.column_scrolls[col_index] -= int(dy / 3)
        self.clamp_column_scroll(col_index)
        self.update()

    def clamp_scroll(self):
        max_scroll = max(0, len(self.rows) * self.row_h - (self.height() - self.content_top()))
        self.scroll = max(0, min(self.scroll, max_scroll))

    def clamp_column_scroll(self, col_index):
        if not (0 <= col_index < len(self.column_items)):
            return

        max_scroll = max(0, len(self.column_items[col_index]) * self.row_h - (self.height() - self.content_top()))
        self.column_scrolls[col_index] = max(0, min(self.column_scrolls[col_index], max_scroll))

    def clamp_hscroll(self):
        total_w = sum(self.column_width(i) for i in range(len(self.column_items)))
        visible_w = self.width() - self.sidebar_w
        max_scroll = max(0, total_w - visible_w)
        self.hscroll = max(0, min(self.hscroll, max_scroll))

    def toggle_path(self, path):
        if path in self.open_dirs:
            self.open_dirs.remove(path)
        else:
            self.open_dirs.add(path)

        self.rebuild_rows()
        self.selected_row = min(self.selected_row, max(0, len(self.rows) - 1))

    def rebuild_rows(self):
        self.rows = []
        self.build_rows(self.root_path)
        self.selected_row = min(self.selected_row, max(0, len(self.rows) - 1))
        self.clamp_scroll()

    def build_rows(self, path, depth=0):
        for child in self.safe_children(path):
            self.rows.append((child, depth))

            if child.is_dir() and child in self.open_dirs:
                self.build_rows(child, depth + 1)

    def safe_children(self, path):
        if self.is_kio_virtual_path(path):
            children = [
                p for p in self.list_kio_children(path)[:self.max_list_items_per_dir]
                if not p.name.startswith(".")
            ]

            children = self.filter_paths_for_search(children)

            return sorted(children, key=lambda p: p.name.lower(), reverse=self.sort_reverse)

        try:
            children = []
            count = 0

            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue

                    count += 1

                    # Très important :
                    # sur les énormes dossiers, on ne charge pas tout d'un coup.
                    # Utilise la recherche pour viser un fichier précis.
                    if count > self.max_list_items_per_dir and not self.search_is_active():
                        break

                    children.append(Path(entry.path))

            children = self.filter_paths_for_search(children)

            if self.sort_mode == "Nom":
                return sorted(children, key=lambda p: p.name.lower(), reverse=self.sort_reverse)

            # Sur gros dossiers, Date/Taille/Type peuvent être coûteux.
            # On autorise quand la liste est petite, sinon on garde le nom.
            if len(children) >= self.max_list_items_per_dir:
                return sorted(children, key=lambda p: p.name.lower(), reverse=self.sort_reverse)

            return sorted(children, key=self.sort_key, reverse=self.sort_reverse)

        except Exception:
            return []

    def apple_metadata_timestamp(self, path):
        # Essaie de lire les métadonnées macOS préservées en extended attributes.
        # La plus utile ici est kMDItemDateAdded : la date d'ajout dans Finder.
        attr_names = [
            "com.apple.metadata:kMDItemDateAdded",
            "user.com.apple.metadata:kMDItemDateAdded",
            "com.apple.metadata:kMDItemDownloadedDate",
            "user.com.apple.metadata:kMDItemDownloadedDate",
        ]

        for attr_name in attr_names:
            try:
                raw = os.getxattr(str(path), attr_name)
            except Exception:
                continue

            try:
                value = plistlib.loads(raw)
            except Exception:
                continue

            try:
                if isinstance(value, list) and value:
                    value = value[0]

                if hasattr(value, "timestamp"):
                    return value.timestamp()

                if isinstance(value, str):
                    try:
                        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        pass
            except Exception:
                continue

        return None

    def sidecar_appledouble_timestamp(self, path):
        # Quand des fichiers viennent de macOS vers un disque non-APFS/HFS,
        # macOS peut créer des fichiers AppleDouble "._NomDuFichier".
        # Ils peuvent contenir des métadonnées, mais leur format complet est lourd.
        # Ici on utilise au moins leur mtime comme meilleur indice si présent.
        try:
            sidecar = path.parent / f"._{path.name}"

            if sidecar.exists():
                return sidecar.stat().st_mtime
        except Exception:
            pass

        return None

    def media_metadata_timestamp(self, path):
        # Pour les photos/vidéos, exiftool donne souvent la vraie date d'origine.
        # On l'utilise seulement si exiftool est installé, sans bloquer si absent.
        if not path.is_file():
            return None

        suffix = path.suffix.lower()

        if suffix not in self.image_exts.union(self.video_exts).union({".heic", ".heif"}):
            return None

        try:
            result = subprocess.run(
                [
                    "exiftool",
                    "-s3",
                    "-d",
                    "%Y-%m-%d %H:%M:%S",
                    "-DateTimeOriginal",
                    "-CreateDate",
                    "-MediaCreateDate",
                    str(path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1.5,
                check=False,
            )
        except Exception:
            return None

        for line in result.stdout.splitlines():
            value = line.strip()

            if not value:
                continue

            try:
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                continue

        return None

    def metadata_timestamp(self, path):
        # Fast mode :
        # Ne lance jamais exiftool automatiquement pendant le tri/affichage.
        # Sur gros dossiers, exiftool par fichier peut bloquer toute l'app.
        try:
            key = str(path)
        except Exception:
            key = repr(path)

        cached = self.metadata_cache.get(key)

        if cached is not None:
            return cached

        # 1) xattrs macOS rapides si présentes.
        timestamp = self.apple_metadata_timestamp(path)

        if timestamp:
            self.metadata_cache[key] = timestamp
            return timestamp

        # 2) AppleDouble rapide si présent.
        timestamp = self.sidecar_appledouble_timestamp(path)

        if timestamp:
            self.metadata_cache[key] = timestamp
            return timestamp

        # 3) exiftool seulement si activé manuellement plus tard.
        if getattr(self, "deep_media_metadata_enabled", False):
            timestamp = self.media_metadata_timestamp(path)

            if timestamp:
                self.metadata_cache[key] = timestamp
                return timestamp

        # 4) mtime Linux, très rapide et généralement préservé par copie.
        try:
            stat = path.stat()
            timestamp = getattr(stat, "st_mtime", 0) or getattr(stat, "st_ctime", 0)
        except Exception:
            timestamp = 0

        self.metadata_cache[key] = timestamp
        return timestamp

    def sort_key(self, path):
        if self.is_kio_virtual_path(path):
            if self.sort_mode == "Type":
                return (self.file_type(path), path.name.lower())

            return (path.name.lower(),)

        if self.sort_mode == "Taille":
            if path.is_dir() and not self.folder_size_enabled:
                return (-1, path.name.lower())

            return (self.path_size(path), path.name.lower())

        if self.sort_mode == "Type":
            return (self.file_type(path), path.name.lower())

        if self.sort_mode == "Date de l'ajout":
            return (self.metadata_timestamp(path), path.name.lower())

        return (path.name.lower(),)

    def path_size(self, path):
        try:
            key = str(path)
        except Exception:
            key = repr(path)

        if key in self.size_cache:
            return self.size_cache[key]

        if path.is_file():
            try:
                size = path.stat().st_size
            except Exception:
                size = 0

            self.size_cache[key] = size
            return size

        if not path.is_dir():
            self.size_cache[key] = 0
            return 0

        total = 0
        stack = [path]

        while stack:
            current = stack.pop()

            try:
                for child in current.iterdir():
                    try:
                        if child.is_symlink():
                            continue

                        if child.is_file():
                            total += child.stat().st_size

                        elif child.is_dir():
                            stack.append(child)

                    except Exception:
                        continue

            except Exception:
                continue

        self.size_cache[key] = total
        return total

    def format_size(self, size):
        units = ["o", "Ko", "Mo", "Go", "To"]

        value = float(size)
        unit = 0

        while value >= 1024 and unit < len(units) - 1:
            value /= 1024
            unit += 1

        return f"{value:.1f} {units[unit]}" if unit else f"{int(value)} o"

    def icon_for_path(self, path):
        if self.is_kio_virtual_path(path):
            if self.kio_path_is_dir(path):
                return self.icons["folder"]

            suffix = path.suffix.lower()
            icon_key = self.extension_icons.get(suffix, "file")
            return self.icons.get(icon_key, self.icons["file"])

        if path.is_dir():
            return self.icons["folder"]

        suffix = path.suffix.lower()
        icon_key = self.extension_icons.get(suffix, "file")

        return self.icons.get(icon_key, self.icons["file"])

    def file_type(self, path):
        if self.is_kio_virtual_path(path):
            return self.kio_file_type(path)

        if path.is_dir():
            return "Dossier"

        suffix = path.suffix.lower()
        return self.file_type_for_suffix(suffix)

    def file_type_for_suffix(self, suffix):
        type_names = {
            ".png": "Image PNG", ".jpg": "Image JPEG", ".jpeg": "Image JPEG",
            ".gif": "Image GIF", ".webp": "Image WEBP", ".bmp": "Image BMP",
            ".svg": "Image SVG",

            ".mp3": "Audio MP3", ".wav": "Audio WAV", ".flac": "Audio FLAC",
            ".ogg": "Audio OGG", ".m4a": "Audio M4A", ".aac": "Audio AAC",

            ".mp4": "Vidéo MP4", ".mkv": "Vidéo MKV", ".mov": "Vidéo MOV",
            ".avi": "Vidéo AVI", ".webm": "Vidéo WEBM",

            ".pdf": "PDF",

            ".zip": "Archive ZIP", ".gz": "Archive GZ", ".tar": "Archive TAR",
            ".tgz": "Archive TGZ", ".xz": "Archive XZ", ".bz2": "Archive BZ2",
            ".7z": "Archive 7Z", ".rar": "Archive RAR",

            ".deb": "Paquet DEB", ".rpm": "Paquet RPM", ".appimage": "AppImage",

            ".ttf": "Police TTF", ".otf": "Police OTF",
            ".woff": "Police WOFF", ".woff2": "Police WOFF2",

            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".html": "HTML", ".css": "CSS", ".json": "JSON",
            ".xml": "XML", ".sh": "Script Shell",

            ".txt": "Texte", ".md": "Markdown", ".doc": "Document Word",
            ".docx": "Document Word", ".odt": "Document",

            ".csv": "Tableur CSV", ".xls": "Tableur Excel",
            ".xlsx": "Tableur Excel", ".ods": "Tableur",

            ".iso": "Image disque", ".img": "Image disque",
        }

        return type_names.get(suffix, suffix[1:].upper() if suffix else "Fichier")

    def file_size(self, path):
        if self.is_kio_virtual_path(path):
            return ""

        if path.is_dir():
            if not self.folder_size_enabled:
                return "—"

            return self.format_size(self.path_size(path))

        try:
            size = path.stat().st_size
        except Exception:
            return ""

        return self.format_size(size)

    def date_added(self, path):
        timestamp = self.metadata_timestamp(path)

        if not timestamp:
            return ""

        try:
            return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y")
        except Exception:
            return ""

QApplication.setApplicationName("PrincessFinder")
QApplication.setDesktopFileName("princessfinder")

app = QApplication(sys.argv)
app.setFont(QFont(UI_FONT_FAMILY, 10))

window = PrincessFinder()
window.show()

sys.exit(app.exec())
