"""Colours and the app-wide stylesheet."""

from pathlib import Path

from PySide6.QtGui import QColor, QFontDatabase

BG = "#0A0C0F"
SURFACE = "#111419"
SURFACE_2 = "#171B21"
SURFACE_3 = "#1F242C"
BORDER = "#242A33"
TEXT = "#E9EDF2"
MUTED = "#8B94A1"
FAINT = "#5A6371"
REC = "#FF4D5E"
WARN = "#FFB547"

ACCENTS = {
    "Mint": "#5CF2B4",
    "Pink": "#FF6FB5",
    "Amber": "#FFB547",
    "Sky": "#5CC8FF",
    "Violet": "#A98BFF",
    "White": "#F2F4F7",
}

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


def stylesheet(accent="#5CF2B4"):
    a = QColor(accent)
    accent_dim = f"rgba({a.red()},{a.green()},{a.blue()},0.14)"
    accent_mid = f"rgba({a.red()},{a.green()},{a.blue()},0.35)"
    return f"""
* {{
    font-family: "Inter";
    font-size: 13px;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QDialog, #Root {{ background: {BG}; }}
QWidget#Page, QWidget#Panel, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}

QLabel#Wordmark {{ font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }}
QLabel#Tagline, QLabel#Muted {{ color: {MUTED}; }}
QLabel#Faint {{ color: {FAINT}; font-size: 12px; }}
QLabel#H1 {{ font-size: 22px; font-weight: 700; letter-spacing: -0.4px; }}
QLabel#H2 {{ font-size: 13px; font-weight: 600; }}
QLabel#Caps {{ color: {FAINT}; font-size: 10px; font-weight: 700; letter-spacing: 1.4px; }}
QLabel#Stat {{ font-family: "JetBrains Mono"; font-size: 20px; font-weight: 700; }}
QLabel#Mono {{ font-family: "JetBrains Mono"; color: {MUTED}; font-size: 12px; }}
QLabel#FieldLabel {{ color: {MUTED}; font-size: 12px; }}

QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#Section {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#TopBar {{ background: {BG}; border-bottom: 1px solid {BORDER}; }}
QFrame#StatusPill {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 13px; }}

QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: {SURFACE_3}; border-color: #303744; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; }}
QPushButton#Primary {{ background: {accent}; color: #06110C; border: none; }}
QPushButton#Primary:hover {{ background: {a.lighter(112).name()}; }}
QPushButton#Primary:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton#Danger {{ color: {REC}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {MUTED}; padding: 6px 8px; }}
QPushButton#Ghost:hover {{ color: {TEXT}; background: {SURFACE_2}; }}
QPushButton#Tab {{
    background: transparent; border: none; border-radius: 8px; color: {MUTED};
    padding: 7px 16px; font-weight: 600;
}}
QPushButton#Tab:hover {{ color: {TEXT}; }}
QPushButton#Tab:checked {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton#Seg {{
    background: transparent; border: none; border-radius: 7px; color: {MUTED};
    padding: 6px 4px; font-weight: 600; font-size: 12px;
}}
QPushButton#Seg:hover {{ color: {TEXT}; }}
QPushButton#Seg:checked {{ background: {accent_dim}; color: {accent}; }}
QFrame#SegBox {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px; }}
QPushButton#Chip {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 13px;
    padding: 4px 11px; font-size: 12px; font-weight: 600; color: {MUTED};
}}
QPushButton#Chip:hover {{ color: {TEXT}; }}
QPushButton#Chip:checked {{ border-color: {accent_mid}; color: {accent}; background: {accent_dim}; }}
QPushButton#SectionHeader {{
    background: transparent; border: none; text-align: left; padding: 12px 14px;
    font-size: 13px; font-weight: 700;
}}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {accent_mid};
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: #333a46; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {accent_mid}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{(ICON_DIR / 'chevron-down.svg').as_posix()}"); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 4px; selection-background-color: {SURFACE_3}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: white; }}

QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #2c333d; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}
QScrollBar:horizontal {{ height: 0; }}

QMessageBox {{ background: {SURFACE}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
"""
