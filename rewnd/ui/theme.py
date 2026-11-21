"""Colours and stylesheet. Same identity as klokd and errnd: near-black, hairline borders,
JetBrains Mono for the wordmark and numbers, lime used sparingly for what matters."""

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFontDatabase

# klokd tokens
BASE = "#0D0D0D"
SURFACE = "#1A1A1A"
RAISED = "#2A2A2A"
BORDER = "#2A2A2A"
TEXT = "#F0F0F0"
TEXT_2 = "#888888"
TEXT_3 = "#444444"
TEXT_DIM = "#6E6E6E"   # unselected options on glass, a touch brighter than klokd's muted
ACCENT = "#C8F135"
WARN = "#E8785A"      # klokd "social" coral, used for warnings and errors
INFO = "#5AB4E8"

# aliases used across the widgets
BG = BASE
SURFACE_2 = RAISED
SURFACE_3 = RAISED
MUTED = TEXT_2
FAINT = TEXT_3
REC = ACCENT

OVERLAY_COLOURS = {
    "klokd lime": "#C8F135",
    "errnd lime": "#B6FF3C",
    "white": "#F0F0F0",
    "coral": "#E8785A",
    "violet": "#7C6FE8",
    "sky": "#5AB4E8",
}
ACCENTS = OVERLAY_COLOURS

UI_FONT = "Segoe UI" if sys.platform.startswith("win") else "Inter"
MONO = "JetBrains Mono"

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT_DIR = ASSETS / "fonts"
ICON_DIR = ASSETS / "icons"


def load_fonts():
    for f in FONT_DIR.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))


def qcolor(hex_, alpha=255):
    c = QColor(hex_)
    c.setAlpha(alpha)
    return c


def wordmark_html(size=15):
    return (f'<span style="font-family:\'{MONO}\'; font-weight:700; font-size:{size}px; color:{TEXT};'
            f' letter-spacing:-0.3px;">r<span style="color:{ACCENT};">e</span>wnd</span>')


# Dark glass: translucent fill, brighter top edge fading down the sides.
GLASS_FILL = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,0.070), stop:1 rgba(255,255,255,0.025))"
GLASS_EDGE = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,0.20), stop:0.35 rgba(255,255,255,0.07), stop:1 rgba(255,255,255,0.04))"
GLASS_FIELD = "rgba(255,255,255,0.045)"
HAIRLINE = "rgba(255,255,255,0.07)"


def stylesheet(accent=ACCENT):
    chevron = (ICON_DIR / "chevron-down.svg").as_posix()
    a = QColor(accent)
    lime_soft = f"rgba({a.red()},{a.green()},{a.blue()},0.11)"
    lime_edge = f"rgba({a.red()},{a.green()},{a.blue()},0.45)"
    return f"""
* {{
    font-family: "{UI_FONT}", "Inter";
    font-size: 13px;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QDialog {{ background: {BASE}; }}
QWidget#Page, QWidget#Panel, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 7px; border-radius: 4px; }}

QLabel#Tagline {{ color: {TEXT_2}; font-style: italic; font-size: 13px; }}
QLabel#Muted {{ color: {TEXT_2}; font-size: 12px; }}
QLabel#Faint {{ color: {TEXT_3}; font-size: 11px; }}
QLabel#H1 {{ font-family: "{MONO}"; font-size: 16px; font-weight: 700; letter-spacing: -0.3px; }}
QLabel#H2 {{ font-size: 13px; font-weight: 600; }}
QLabel#Caps {{ color: {TEXT_DIM}; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
QLabel#Stat {{ font-family: "{MONO}"; font-size: 20px; font-weight: 700; color: {TEXT}; }}
QLabel#StatKey {{ font-family: "{MONO}"; font-size: 20px; font-weight: 700; color: {accent}; }}
QLabel#Mono {{ font-family: "{MONO}"; color: {TEXT_2}; font-size: 11px; }}
QLabel#FieldLabel {{ color: {TEXT_2}; font-size: 12px; }}

QFrame#Toast {{ background: rgba(24,24,24,0.94); border: 1px solid {GLASS_EDGE}; border-radius: 14px; }}
QFrame#Card, QFrame#Glass {{ background: {GLASS_FILL}; border: 1px solid {GLASS_EDGE}; border-radius: 14px; }}
QFrame#Section {{ background: transparent; border: none; border-bottom: 1px solid {HAIRLINE}; border-radius: 0; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#TopBar {{ background: rgba(13,13,13,0.55); border-bottom: 1px solid {HAIRLINE}; }}
QFrame#SidePanel {{ background: {GLASS_FILL}; border: 1px solid {GLASS_EDGE}; border-radius: 16px; }}
QFrame#StatusPill {{ background: {GLASS_FIELD}; border: 1px solid {HAIRLINE}; border-radius: 11px; }}

QPushButton {{
    background: {GLASS_FIELD}; border: 1px solid {HAIRLINE}; border-radius: 8px;
    padding: 6px 12px; color: {TEXT_2}; font-size: 12px;
}}
QPushButton:hover {{ color: {TEXT}; border-color: rgba(255,255,255,0.16); background: rgba(255,255,255,0.07); }}
QPushButton:pressed {{ background: rgba(255,255,255,0.03); }}
QPushButton:disabled {{ color: {TEXT_3}; }}
QPushButton#Primary {{ background: {TEXT}; color: {BASE}; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: #ffffff; }}
QPushButton#Primary:disabled {{ background: {RAISED}; color: {TEXT_3}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {TEXT_2}; padding: 5px 8px; }}
QPushButton#Ghost:hover {{ color: {TEXT}; }}
QPushButton#Tab {{
    background: transparent; border: 1px solid transparent; border-radius: 8px; color: {TEXT_3};
    padding: 5px 16px; font-size: 12px;
}}
QPushButton#Tab:hover {{ color: {TEXT_2}; background: transparent; border-color: {HAIRLINE}; }}
QPushButton#Tab:checked {{ color: {TEXT}; background: {GLASS_FIELD}; border-color: rgba(255,255,255,0.12); }}
QFrame#SegBox {{ background: {GLASS_FIELD}; border: 1px solid {HAIRLINE}; border-radius: 8px; }}
QPushButton#Seg {{
    background: transparent; border: none; border-radius: 6px; color: {TEXT_DIM};
    padding: 5px 4px; font-family: "{MONO}"; font-size: 11px; color: {TEXT_DIM};
}}
QPushButton#Seg:hover {{ color: {TEXT_2}; background: transparent; }}
QPushButton#Seg:checked {{ background: {lime_soft}; color: {accent}; }}
QPushButton#Chip {{
    background: {GLASS_FIELD}; border: 1px solid {HAIRLINE}; border-radius: 11px;
    padding: 3px 10px; font-family: "{MONO}"; font-size: 11px; color: {TEXT_DIM};
}}
QPushButton#Chip:hover {{ color: {TEXT_2}; }}
QPushButton#Chip:checked {{ border-color: {lime_edge}; color: {accent}; background: {lime_soft}; }}
QPushButton#SectionHeader {{
    background: transparent; border: none; text-align: left; padding: 14px 2px 10px 2px;
    font-size: 10px; font-weight: 600; letter-spacing: 1px; color: {TEXT_DIM};
}}
QPushButton#SectionHeader:hover {{ color: {TEXT_2}; background: transparent; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {GLASS_FIELD}; border: 1px solid {HAIRLINE}; border-radius: 8px;
    padding: 5px 9px; font-size: 12px; selection-background-color: {RAISED};
}}
QSpinBox, QDoubleSpinBox {{ font-family: "{MONO}"; font-size: 11px; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: rgba(255,255,255,0.16); }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {lime_edge}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: #161616; border: 1px solid rgba(255,255,255,0.10); border-radius: 8px;
    padding: 4px; selection-background-color: {RAISED}; selection-color: {TEXT}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QSlider::groove:horizontal {{ height: 2px; background: rgba(255,255,255,0.10); border-radius: 1px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 1px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; }}

QProgressBar {{ background: rgba(255,255,255,0.08); border: none; border-radius: 2px; height: 4px; color: transparent; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 2px; }}

QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.10); border-radius: 2px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}
QScrollBar:horizontal {{ height: 0; }}

QMessageBox, QInputDialog {{ background: {SURFACE}; }}
QMenu {{ background: #161616; border: 1px solid rgba(255,255,255,0.10); border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 5px 14px; border-radius: 4px; color: {TEXT_2}; }}
QMenu::item:selected {{ background: rgba(255,255,255,0.07); color: {TEXT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
"""
