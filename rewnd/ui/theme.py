"""Themes and the stylesheet.

Three themes share one layout:
  black  the klokd/errnd identity: flat deep black, hairlines, lime used sparingly
  light  its inverse: flat white, the same hairlines and accent
  aero   Frutiger Aero glass: sky gradient, soft bubbles, glossy translucent panels

The module-level colour names (BASE, TEXT, ACCENT, ...) always hold the active theme;
call apply() to switch. Widgets read them at paint time, so a switch repaints cleanly.
"""

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFontDatabase

UI_FONT = "Segoe UI" if sys.platform.startswith("win") else "Inter"
MONO = "JetBrains Mono"
ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT_DIR = ASSETS / "fonts"
ICON_DIR = ASSETS / "icons"

WARN = "#E8785A"      # klokd "social" coral, used for warnings and errors
INFO = "#5AB4E8"
DEFAULT_ACCENT = "#C8F135"

PRESETS = {
    "klokd lime": "#C8F135",
    "errnd lime": "#B6FF3C",
    "aero green": "#45C43A",
    "sky": "#36A9F2",
    "white": "#F0F0F0",
    "coral": "#E8785A",
    "violet": "#7C6FE8",
    "pink": "#FF6FB5",
}
OVERLAY_COLOURS = PRESETS
ACCENTS = PRESETS

THEMES = {
    "black": dict(
        BASE="#000000", SURFACE="#0D0D0D", RAISED="#161616", BORDER="#1F1F1F", FIELD="#111111",
        HOVER_EDGE="#333333", TEXT="#F0F0F0", TEXT_2="#888888", TEXT_3="#444444", TEXT_DIM="#6E6E6E",
        TOGGLE_OFF="#333333", RING="#262626", DARK=True,
    ),
    "light": dict(
        BASE="#FFFFFF", SURFACE="#F6F6F5", RAISED="#ECECEA", BORDER="#E3E3E1", FIELD="#FFFFFF",
        HOVER_EDGE="#C9C9C6", TEXT="#0D0D0D", TEXT_2="#5E5E5E", TEXT_3="#B4B4B0", TEXT_DIM="#868683",
        TOGGLE_OFF="#C9C9C6", RING="#E3E3E1", DARK=False,
    ),
    "aero": dict(
        BASE="#DDF1FF", SURFACE="rgba(255,255,255,0.62)", RAISED="rgba(255,255,255,0.80)",
        BORDER="rgba(110,160,205,0.40)", FIELD="rgba(255,255,255,0.70)", HOVER_EDGE="rgba(60,140,210,0.65)",
        TEXT="#0B2A43", TEXT_2="#3E5C75", TEXT_3="#9DB4C7", TEXT_DIM="#5F7D95",
        TOGGLE_OFF="#9DB4C7", RING="#B9D7EE", DARK=False,
    ),
}
THEME_NAMES = list(THEMES)

# Active values (filled by apply)
NAME = "black"
BASE = SURFACE = RAISED = BORDER = FIELD = HOVER_EDGE = TEXT = TEXT_2 = TEXT_3 = TEXT_DIM = ""
TOGGLE_OFF = RING = ""
DARK = True
ACCENT = DEFAULT_ACCENT          # fills, dots, icons
ACCENT_TEXT = DEFAULT_ACCENT     # accent used as text on the base colour (darkened on light themes)
ON_ACCENT = "#000000"            # text drawn on an accent fill


def _luminance(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def readable_on_light(hex_):
    """Darken a colour until it reads on white (a lime accent becomes klokd's accent-dark)."""
    c = QColor(hex_)
    h, s, v, _ = c.getHsvF()
    while _luminance(c) > 0.18 and v > 0.05:
        v -= 0.03
        c = QColor.fromHsvF(max(0.0, h), s, v)
    return c.name()


def apply(name="black", accent=DEFAULT_ACCENT):
    global NAME, DARK, ACCENT, ACCENT_TEXT, ON_ACCENT
    name = name if name in THEMES else "black"
    NAME = name
    g = globals()
    for k, v in THEMES[name].items():
        g[k] = v
    DARK = THEMES[name]["DARK"]
    ACCENT = QColor(accent).name() if QColor(accent).isValid() else DEFAULT_ACCENT
    ACCENT_TEXT = ACCENT if DARK else readable_on_light(ACCENT)
    ON_ACCENT = "#000000" if _luminance(QColor(ACCENT)) > 0.35 else "#FFFFFF"


apply()


def load_fonts():
    for f in FONT_DIR.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))


def qcolor(spec, alpha=None):
    """QColor from '#hex' or 'rgba(r,g,b,a)'."""
    if spec.startswith("rgba"):
        r, g, b, a = [x.strip() for x in spec[5:-1].split(",")]
        c = QColor(int(r), int(g), int(b))
        c.setAlphaF(float(a))
    else:
        c = QColor(spec)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def icon(name, colour=None):
    """An SVG icon from assets/icons recoloured for the active theme."""
    from PySide6.QtGui import QIcon, QPixmap
    svg = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    svg = svg.replace("#E9EDF2", colour or TEXT_2).replace("#8B94A1", colour or TEXT_2)
    pm = QPixmap()
    pm.loadFromData(svg.encode("utf-8"), "SVG")
    return QIcon(pm)


def wordmark_html(size=15):
    return (f'<span style="font-family:\'{MONO}\'; font-weight:700; font-size:{size}px; color:{TEXT};'
            f' letter-spacing:-0.3px;">r<span style="color:{ACCENT_TEXT};">e</span>wnd</span>')


def tagline_html():
    return f'your hours, <span style="color:{ACCENT_TEXT};">rewound.</span>'


def _gloss(top, mid, low, bottom):
    """The Aero gloss: a bright upper half with a hard edge at the middle."""
    return (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:0.49 {mid}, "
            f"stop:0.5 {low}, stop:1 {bottom})")


def stylesheet():
    chevron = (ICON_DIR / "chevron-down.svg").as_posix()
    a = QColor(ACCENT)
    acc, acc_t, on_acc = ACCENT, ACCENT_TEXT, ON_ACCENT
    if NAME == "aero":
        panel = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,0.86), "
                 "stop:0.06 rgba(255,255,255,0.66), stop:1 rgba(255,255,255,0.46))")
        panel_edge = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,1.0), "
                      "stop:1 rgba(120,175,220,0.55))")
        button = _gloss("rgba(255,255,255,0.98)", "rgba(240,248,255,0.92)", "rgba(214,234,250,0.92)",
                        "rgba(236,246,255,0.96)")
        button_hover = _gloss("#ffffff", "#f4faff", "#d2ecff", "#eaf6ff")
        primary = _gloss(a.lighter(135).name(), a.lighter(112).name(), a.name(), a.lighter(118).name())
        primary_hover = _gloss(a.lighter(145).name(), a.lighter(122).name(), a.lighter(106).name(),
                               a.lighter(126).name())
        seg_on = _gloss("#ffffff", "#f7fbff", "#e2f1fc", "#f2f9ff")
        topbar = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,0.78), "
                  "stop:1 rgba(255,255,255,0.45))")
        menu = "#F4FAFF"
        toast = "rgba(255,255,255,0.94)"
        radius, pill = 12, 13
        seg_on_text = TEXT
        tab_on_bg = seg_on
        primary_edge = a.darker(115).name()
        groove = "rgba(110,160,205,0.30)"
    else:
        panel, panel_edge = SURFACE, BORDER
        button, button_hover = FIELD, RAISED
        primary = acc
        primary_hover = a.lighter(108).name() if DARK else a.darker(106).name()
        seg_on = RAISED
        topbar = BASE
        menu = SURFACE
        toast = SURFACE
        radius, pill = 10, 11
        seg_on_text = acc_t
        tab_on_bg = BASE
        primary_edge = acc
        groove = RAISED
    window_bg = "#DDF1FF" if NAME == "aero" else BASE
    handle = TEXT if DARK else "#FFFFFF"
    handle_edge = TEXT if DARK else HOVER_EDGE
    scroll_handle = RAISED if DARK else TEXT_3

    return f"""
* {{
    font-family: "{UI_FONT}", "Inter";
    font-size: 13px;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QDialog {{ background: {window_bg}; }}
QWidget#Page, QWidget#Panel, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {menu}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 7px; border-radius: 4px; }}

QLabel#Tagline {{ color: {TEXT_2}; font-style: italic; font-size: 13px; }}
QLabel#Muted {{ color: {TEXT_2}; font-size: 12px; }}
QLabel#Faint {{ color: {TEXT_3}; font-size: 11px; }}
QLabel#H1 {{ font-family: "{MONO}"; font-size: 16px; font-weight: 700; letter-spacing: -0.3px; }}
QLabel#H2 {{ font-size: 13px; font-weight: 600; }}
QLabel#Caps {{ color: {TEXT_DIM}; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
QLabel#Stat {{ font-family: "{MONO}"; font-size: 20px; font-weight: 700; color: {TEXT}; }}
QLabel#StatKey {{ font-family: "{MONO}"; font-size: 20px; font-weight: 700; color: {acc_t}; }}
QLabel#Mono {{ font-family: "{MONO}"; color: {TEXT_2}; font-size: 11px; }}
QLabel#FieldLabel {{ color: {TEXT_2}; font-size: 12px; }}
QLabel#Warn {{ color: {WARN}; font-size: 11px; }}
QLabel#StatSmall {{ font-family: "{MONO}"; font-size: 14px; font-weight: 700; color: {TEXT}; }}
QFrame#ProfileCard {{ background: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px; }}

QFrame#Toast {{ background: {toast}; border: 1px solid {BORDER}; border-left: 2px solid {acc}; border-radius: {radius}px; }}
QFrame#Card, QFrame#Glass {{ background: {panel}; border: 1px solid {panel_edge}; border-radius: {radius}px; }}
QFrame#Section {{ background: transparent; border: none; border-bottom: 1px solid {BORDER}; border-radius: 0; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#TopBar {{ background: {topbar}; border-bottom: 1px solid {BORDER}; }}
QFrame#SidePanel {{ background: {panel}; border: 1px solid {panel_edge}; border-radius: {radius}px; }}
QFrame#StatusPill {{ background: {FIELD}; border: 1px solid {BORDER}; border-radius: {pill}px; }}
QFrame#ColourPopup {{ background: {menu}; border: 1px solid {BORDER}; border-radius: {radius}px; }}

QPushButton {{
    background: {button}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 12px; color: {TEXT_2}; font-size: 12px;
}}
QPushButton:hover {{ color: {TEXT}; border-color: {HOVER_EDGE}; background: {button_hover}; }}
QPushButton:pressed {{ background: {RAISED}; }}
QPushButton:disabled {{ color: {TEXT_3}; }}
QPushButton#Primary {{ background: {primary}; color: {on_acc}; border: 1px solid {primary_edge}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {primary_hover}; }}
QPushButton#Primary:disabled {{ background: {RAISED}; color: {TEXT_3}; border-color: {BORDER}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {TEXT_2}; padding: 5px 8px; }}
QPushButton#Ghost:hover {{ color: {TEXT}; background: transparent; }}
QPushButton#Tab {{
    background: transparent; border: 1px solid transparent; border-radius: 8px; color: {TEXT_DIM};
    padding: 5px 16px; font-size: 12px;
}}
QPushButton#Tab:hover {{ color: {TEXT_2}; background: transparent; border-color: {BORDER}; }}
QPushButton#Tab:checked {{ color: {acc_t}; background: {tab_on_bg}; border-color: {BORDER}; }}
QFrame#SegBox {{ background: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px; }}
QPushButton#Seg {{
    background: transparent; border: none; border-radius: 6px; color: {TEXT_DIM};
    padding: 5px 4px; font-family: "{MONO}"; font-size: 11px;
}}
QPushButton#Seg:hover {{ color: {TEXT_2}; background: transparent; }}
QPushButton#Seg:checked {{ background: {seg_on}; color: {seg_on_text}; }}
QPushButton#Chip {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 11px;
    padding: 3px 10px; font-family: "{MONO}"; font-size: 11px; color: {TEXT_DIM};
}}
QPushButton#Chip:hover {{ color: {TEXT_2}; }}
QPushButton#Chip:checked {{ border-color: {acc}; color: {acc_t}; background: {FIELD}; }}
QPushButton#SectionHeader {{
    background: transparent; border: none; text-align: left; padding: 14px 2px 10px 2px;
    font-size: 10px; font-weight: 600; letter-spacing: 1px; color: {TEXT_DIM};
}}
QPushButton#SectionHeader:hover {{ color: {TEXT_2}; background: transparent; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 5px 9px; font-size: 12px; selection-background-color: {RAISED}; selection-color: {TEXT};
}}
QSpinBox, QDoubleSpinBox, QTimeEdit {{ font-family: "{MONO}"; font-size: 11px; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QTimeEdit:hover {{ border-color: {HOVER_EDGE}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus {{ border-color: {acc}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {menu}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 4px; selection-background-color: {RAISED}; selection-color: {TEXT}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QTimeEdit::up-button, QTimeEdit::down-button {{ width: 0; border: none; }}

QSlider::groove:horizontal {{ height: 2px; background: {groove}; border-radius: 1px; }}
QSlider::sub-page:horizontal {{ background: {acc}; border-radius: 1px; }}
QSlider::handle:horizontal {{ background: {handle}; border: 1px solid {handle_edge};
    width: 10px; height: 10px; margin: -5px 0; border-radius: 6px; }}

QProgressBar {{ background: {groove}; border: none; border-radius: 2px; height: 4px; color: transparent; }}
QProgressBar::chunk {{ background: {acc}; border-radius: 2px; }}

QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {scroll_handle}; border-radius: 2px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}
QScrollBar:horizontal {{ height: 0; }}

QMessageBox, QInputDialog {{ background: {menu}; }}
QMenu {{ background: {menu}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 5px 14px; border-radius: 4px; color: {TEXT_2}; }}
QMenu::item:selected {{ background: {RAISED}; color: {TEXT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
"""
