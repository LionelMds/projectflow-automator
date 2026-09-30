"""Industry theme: design tokens, Qt style sheet, fonts and tinted icons.

The tokens follow the Industry design system (steel accent, square wireframe
objects, Barlow / Barlow Condensed) with a light and a dark mode and four
neutral palettes. Neutral ramps are generated in OKLCH on one lightness scale,
exactly like the mockups, then converted to sRGB for Qt.
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QApplication

if TYPE_CHECKING:
    from projectflow.config import AppearanceConfig

HEADING_FAMILY = "Barlow Condensed"
BODY_FAMILY = "Barlow"

PALETTES: dict[str, tuple[str, float, float]] = {
    # Palette key -> label, OKLCH chroma and hue of the neutral ramp.
    "acier": ("Acier", 0.006, 260),
    "graphite": ("Graphite", 0.0, 0),
    "papier": ("Papier", 0.014, 80),
    "ardoise": ("Ardoise", 0.022, 245),
}
MODES: dict[str, str] = {"light": "Clair", "dark": "Sombre", "system": "Système"}

_NEUTRAL_LIGHTNESS = (0.97, 0.93, 0.87, 0.78, 0.68, 0.58, 0.48, 0.38, 0.28)
_ACCENT_RAMP = (
    "#eef6ff",
    "#d6ebff",
    "#b5d9fd",
    "#94bce3",
    "#749dc4",
    "#597ea3",
    "#416180",
    "#2c455d",
    "#1d2d3d",
)


_SRGB_LINEAR_LIMIT = 0.0031308


def _oklch_to_hex(lightness: float, chroma: float, hue: float) -> str:
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )

    def encode(channel: float) -> int:
        channel = min(1.0, max(0.0, channel))
        if channel <= _SRGB_LINEAR_LIMIT:
            value = 12.92 * channel
        else:
            value = 1.055 * channel ** (1 / 2.4) - 0.055
        return round(value * 255)

    return "#{:02x}{:02x}{:02x}".format(*(encode(channel) for channel in linear))


@dataclass(frozen=True, slots=True)
class Theme:
    mode: str
    palette: str
    tokens: dict[str, str]

    @property
    def is_dark(self) -> bool:
        return self.mode == "dark"

    @property
    def key(self) -> str:
        return f"{self.mode}-{self.palette}"

    def color(self, name: str, alpha: float = 1.0) -> QColor:
        color = QColor(self.tokens[name])
        if alpha < 1.0:
            color.setAlphaF(alpha)
        return color

    def rgba(self, name: str, alpha: float) -> str:
        color = QColor(self.tokens[name])
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {round(alpha * 255)})"


def build_theme(mode: str, palette: str) -> Theme:
    """Return the tokens for a resolved mode ("light" or "dark") and palette key."""
    _label, chroma, hue = PALETTES.get(palette, PALETTES["acier"])
    dark = mode == "dark"

    def ok(lightness: float, chroma_scale: float = 1.0) -> str:
        return _oklch_to_hex(lightness, chroma * chroma_scale, hue)

    tokens: dict[str, str] = {}
    for index, lightness in enumerate(_NEUTRAL_LIGHTNESS):
        step = (index + 1) * 100
        source = 8 - index if dark else index
        tokens[f"neutral-{step}"] = ok(_NEUTRAL_LIGHTNESS[source] if dark else lightness)
        tokens[f"accent-{step}"] = _ACCENT_RAMP[source]
    if dark:
        tokens.update(
            {
                "bg": ok(0.19, 1.2),
                "surface": ok(0.23, 1.2),
                "text": ok(0.94, 0.6),
                "accent": "#749dc4",
                "err": "#f0705e",
                "err-ink": "#f4b3aa",
            },
        )
    else:
        tokens.update(
            {
                "bg": ok(0.96, 1.2),
                "surface": ok(0.93, 1.2),
                "text": ok(0.22, 1.2),
                "accent": "#5980a6",
                "err": "#b42318",
                "err-ink": "#7a1a12",
            },
        )
    return Theme(mode="dark" if dark else "light", palette=palette, tokens=tokens)


_current = build_theme("light", "acier")


def current_theme() -> Theme:
    return _current


class _ThemeNotifier(QObject):
    changed = Signal()


_notifier: _ThemeNotifier | None = None


def theme_notifier() -> _ThemeNotifier:
    global _notifier  # noqa: PLW0603
    if _notifier is None:
        _notifier = _ThemeNotifier()
    return _notifier


def resolve_mode(mode: str) -> str:
    if mode != "system":
        return "dark" if mode == "dark" else "light"
    app = QGuiApplication.instance()
    if isinstance(app, QGuiApplication):
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
    return "light"


_fonts_loaded = False
_system_watch_connected = False
_last_appearance: tuple[str, str] = ("light", "acier")


def load_fonts() -> None:
    global _fonts_loaded  # noqa: PLW0603
    if _fonts_loaded:
        return
    _fonts_loaded = True
    try:
        fonts = resources.files("projectflow.ui.resources").joinpath("fonts")
        for entry in fonts.iterdir():
            if entry.name.endswith(".ttf"):
                QFontDatabase.addApplicationFontFromData(QByteArray(entry.read_bytes()))
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return


def body_font(pixel_size: int = 14, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(BODY_FAMILY)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def heading_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.DemiBold) -> QFont:
    font = QFont(HEADING_FAMILY)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def apply_theme(app: QApplication, appearance: AppearanceConfig | None = None) -> Theme:
    """Style the whole application; call again after the appearance setting changes."""
    global _current, _last_appearance, _system_watch_connected  # noqa: PLW0603
    if appearance is not None:
        _last_appearance = (appearance.mode, appearance.palette)
    mode, palette = _last_appearance
    load_fonts()
    app.setStyle("Fusion")
    _current = build_theme(resolve_mode(mode), palette)
    app.setFont(body_font(14))
    app.setPalette(_qt_palette(_current))
    app.setStyleSheet(style_sheet(_current))
    if not _system_watch_connected:
        _system_watch_connected = True
        app.styleHints().colorSchemeChanged.connect(lambda _scheme: _on_system_scheme(app))
    refresh_icons()
    theme_notifier().changed.emit()
    for widget in app.topLevelWidgets():
        widget.update()
    return _current


def _on_system_scheme(app: QApplication) -> None:
    if _last_appearance[0] == "system":
        apply_theme(app)


def _qt_palette(theme: Theme) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "bg",
        QPalette.ColorRole.Base: "surface",
        QPalette.ColorRole.AlternateBase: "bg",
        QPalette.ColorRole.Button: "bg",
        QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.ToolTipBase: "surface",
        QPalette.ColorRole.ToolTipText: "text",
        QPalette.ColorRole.PlaceholderText: "neutral-600",
        QPalette.ColorRole.Highlight: "accent-200",
        QPalette.ColorRole.HighlightedText: "text",
        QPalette.ColorRole.Link: "accent-700",
        QPalette.ColorRole.Mid: "neutral-400",
        QPalette.ColorRole.Dark: "neutral-600",
        QPalette.ColorRole.Light: "neutral-100",
    }
    for role, token in roles.items():
        palette.setColor(role, theme.color(token))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, theme.color("neutral-600"))
    return palette


# ---------------------------------------------------------------- icons ----

# Lucide icons (https://lucide.dev), stroke width 1.5.
_ICON_PATHS: dict[str, str] = {
    "zap": '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 '
    '13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    "settings": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0'
    "l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1"
    "-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 "
    "2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2"
    " 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0"
    " 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-."
    '43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
    '<path d="M21 3v5h-5"/>'
    '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
    '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 '
    '0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "check-circle": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
    '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
}


def icon_svg(name: str, color: str, stroke_width: float = 1.5) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{_ICON_PATHS[name]}</svg>'
    )


def icon_pixmap(
    name: str, color: QColor | str, size: int = 16, stroke_width: float = 1.5
) -> QPixmap:
    color_name = color.name() if isinstance(color, QColor) else color
    renderer = QSvgRenderer(QByteArray(icon_svg(name, color_name, stroke_width).encode()))
    ratio = 2.0
    image = QImage(round(size * ratio), round(size * ratio), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def icon(name: str, role: str = "text", size: int = 16) -> QIcon:
    return QIcon(icon_pixmap(name, current_theme().color(role), size))


def set_icon(button: QAbstractButton, name: str, role: str = "text", size: int = 15) -> None:
    """Give a button a themed icon that follows later theme changes."""
    button.setProperty("pf_icon", name)
    button.setProperty("pf_icon_role", role)
    button.setIconSize(QSize(size, size))
    button.setIcon(icon(name, role, size))


def refresh_icons() -> None:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    for widget in app.allWidgets():
        name = widget.property("pf_icon")
        if isinstance(widget, QAbstractButton) and isinstance(name, str) and name:
            role = widget.property("pf_icon_role") or "text"
            widget.setIcon(icon(name, str(role), widget.iconSize().width()))


def logo_pixmap(size: int, theme: Theme | None = None) -> QPixmap:
    """Return the ProjectFlow mark as monochrome ink so it sits on either ground."""
    theme = theme or current_theme()
    try:
        data = resources.files("projectflow.ui.resources").joinpath("logo.png").read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return QPixmap()
    source = QImage.fromData(data)
    if source.isNull():
        return QPixmap()
    ratio = 2
    source = source.scaled(
        size * ratio,
        size * ratio,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_ARGB32)
    ink = theme.color("text")
    result = QImage(source.size(), QImage.Format.Format_ARGB32)
    for y in range(source.height()):
        for x in range(source.width()):
            pixel = source.pixelColor(x, y)
            luminance = 0.2126 * pixel.redF() + 0.7152 * pixel.greenF() + 0.0722 * pixel.blueF()
            alpha = min(1.0, max(0.0, (1.0 - luminance) * 1.3)) * pixel.alphaF()
            result.setPixelColor(
                x, y, QColor(ink.red(), ink.green(), ink.blue(), round(alpha * 255))
            )
    pixmap = QPixmap.fromImage(result)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


# ----------------------------------------------------------- style sheet ----


def _asset_dir(theme: Theme) -> Path:
    directory = Path(tempfile.gettempdir()) / "projectflow-theme" / theme.key
    directory.mkdir(parents=True, exist_ok=True)
    assets = {
        "check.svg": icon_svg("check", theme.tokens["bg"], 3.5),
        "check-disabled.svg": icon_svg("check", theme.tokens["neutral-300"], 3.5),
        "chevron-down.svg": icon_svg("chevron-down", theme.tokens["neutral-800"], 2),
        "chevron-up.svg": icon_svg("chevron-up", theme.tokens["neutral-800"], 2),
        "chevron-down-disabled.svg": icon_svg("chevron-down", theme.tokens["neutral-500"], 2),
        "chevron-up-disabled.svg": icon_svg("chevron-up", theme.tokens["neutral-500"], 2),
    }
    for name, content in assets.items():
        path = directory / name
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
    return directory


def style_sheet(theme: Theme) -> str:
    t = theme.tokens
    assets = _asset_dir(theme).as_posix()
    divider = theme.rgba("text", 0.30)
    input_border = theme.rgba("text", 0.34)
    hover_tint = theme.rgba("text", 0.07)
    press_tint = theme.rgba("text", 0.14)
    row_rule = theme.rgba("text", 0.08)
    disabled_text = theme.rgba("text", 0.45)
    disabled_border = theme.rgba("text", 0.14)
    accent_hover = theme.rgba("accent", 0.10)
    accent_press = theme.rgba("accent", 0.18)
    heading = f'"{HEADING_FAMILY}"'
    return f"""
QMainWindow, QDialog, QWizard {{ background: {t["bg"]}; }}
QWidget {{ color: {t["text"]}; }}
QWidget[pfRoot="true"] {{ background: {t["bg"]}; }}
QToolTip {{
  background: {t["surface"]}; color: {t["text"]}; border: 1px solid {divider}; padding: 4px 6px;
}}

/* type roles */
QLabel[role="h1"] {{ font-family: {heading}; font-weight: 600; font-size: 52px; }}
QLabel[role="h2"] {{ font-family: {heading}; font-weight: 600; font-size: 30px; }}
QLabel[role="h3"] {{ font-family: {heading}; font-weight: 600; font-size: 25px; }}
QLabel[role="h4"] {{ font-family: {heading}; font-weight: 600; font-size: 20px; }}
QLabel[role="h5"] {{ font-family: {heading}; font-weight: 600; font-size: 17px; }}
QLabel[role="brand"] {{ font-family: {heading}; font-weight: 600; font-size: 20px; }}
QLabel[role="kicker"] {{ font-size: 11px; color: {t["accent-800"]}; }}
QLabel[role="num"] {{ font-size: 11px; font-weight: 500; color: {t["accent-800"]}; }}
QLabel[role="num-lg"] {{
  font-family: {heading}; font-weight: 600; font-size: 24px; color: {t["accent-800"]};
}}
QLabel[role="caps"] {{ font-size: 11px; color: {t["neutral-800"]}; }}
QLabel[role="field"] {{ font-size: 12px; font-weight: 500; color: {t["neutral-800"]}; }}
QLabel[role="muted"] {{ font-size: 13px; color: {t["neutral-800"]}; }}
QLabel[role="small"] {{ font-size: 12px; color: {t["neutral-800"]}; }}
QLabel[role="accent-text"] {{ font-size: 13px; color: {t["accent-800"]}; }}
QLabel[role="path"] {{ font-size: 13px; }}
QLabel[role="tag-accent"] {{
  background: {t["accent-100"]}; color: {t["accent-800"]}; font-size: 11px; padding: 3px 10px;
}}
QLabel[role="tag-neutral"] {{
  background: {t["neutral-100"]}; color: {t["neutral-800"]}; font-size: 11px; padding: 3px 10px;
}}
QLabel[role="tag-outline"] {{
  border: 1px solid {t["accent"]}; color: {t["accent"]}; font-size: 11px; padding: 2px 9px;
}}
QWidget[role="tagbox"] {{ background: {t["accent-100"]}; }}
QFrame[role="dot"] {{ background: {t["accent"]}; border: 0; }}
QLabel[role="tag-text"] {{ color: {t["accent-800"]}; font-size: 11px; }}
QLabel[role="step-cell"] {{
  font-family: {heading}; font-weight: 600; font-size: 22px; color: {t["neutral-700"]};
  border: 1px solid {divider};
}}
QFrame[role="swatch-next"] {{ background: {t["accent-100"]}; border: 1px solid {t["accent-400"]}; }}
QFrame[role="swatch-dirty"] {{ background: {t["accent-200"]}; border: 1px solid {t["accent"]}; }}
QLabel[role="initials"] {{
  background: {t["neutral-100"]}; color: {t["neutral-800"]}; font-size: 13px; font-weight: 500;
  padding: 5px 12px;
}}
QLabel[tone="success"] {{ color: {t["accent-900"]}; }}
QLabel[tone="error"] {{ color: {t["err-ink"]}; }}
QLabel[tone="danger"] {{ color: {t["err"]}; }}
QLabel a {{ color: {t["accent-700"]}; }}

/* rules */
QFrame[role="rule"] {{ background: {divider}; border: 0; min-height: 1px; max-height: 1px; }}
QFrame[role="vrule"] {{ background: {divider}; border: 0; min-width: 1px; max-width: 1px; }}
QWidget[role="header"], QWidget[role="footer"] {{ background: {t["bg"]}; }}

/* buttons */
QPushButton, QToolButton {{
  font-family: {heading}; font-weight: 600; font-size: 14px; color: {t["text"]};
  background: transparent; border: 1px solid {divider}; border-radius: 0;
  padding: 6px 12px; min-height: 22px;
}}
QToolButton {{ padding: 6px 10px; }}
QPushButton:hover, QToolButton:hover {{ background: {hover_tint}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {press_tint}; }}
QPushButton:disabled, QToolButton:disabled {{
  color: {disabled_text}; border-color: {disabled_border};
}}
QPushButton:focus, QToolButton:focus {{ outline: none; }}
QPushButton[variant="primary"] {{
  background: {t["accent"]}; color: {t["bg"]}; border-color: {t["accent"]}; font-size: 15px;
  padding: 7px 26px;
}}
QPushButton[variant="primary"]:hover {{
  background: {t["accent-600"]}; border-color: {t["accent-600"]};
}}
QPushButton[variant="primary"]:pressed {{ background: {t["accent-700"]}; }}
QPushButton[variant="primary"]:disabled {{
  background: {theme.rgba("accent", 0.45)}; border-color: transparent; color: {t["bg"]};
}}
QPushButton[blueprint="true"] {{ margin: 6px; }}
QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{
  color: {t["accent-700"]}; border-color: transparent; padding: 6px 6px;
}}
QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{
  background: {accent_hover};
}}
QPushButton[variant="ghost"]:pressed {{ background: {accent_press}; }}
QPushButton[variant="ghost"]:disabled {{ color: {disabled_text}; }}
QPushButton[variant="danger"] {{ color: {t["err"]}; border-color: transparent; padding: 6px 6px; }}
QPushButton[variant="danger"]:hover {{ background: {theme.rgba("err", 0.08)}; }}
QPushButton[variant="danger"]:disabled {{ color: {theme.rgba("err", 0.45)}; }}
QPushButton[variant="icon"], QToolButton[variant="icon"] {{
  min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; padding: 0;
}}
QToolButton[variant="icon"]::menu-indicator {{ image: none; width: 0; }}
QPushButton[size="sm"] {{ font-size: 13px; padding: 4px 10px; min-height: 18px; }}
QPushButton[variant="nav"] {{
  font-size: 16px; color: {t["neutral-800"]}; background: transparent; border: 0;
  border-bottom: 2px solid transparent; padding: 0 14px; min-height: 55px;
}}
QPushButton[variant="nav"]:hover {{ color: {t["accent-800"]}; background: transparent; }}
QPushButton[variant="nav"]:checked {{ color: {t["text"]}; border-bottom-color: {t["accent"]}; }}
QPushButton[variant="side"] {{
  font-family: "{BODY_FAMILY}"; font-weight: 400; font-size: 14px; text-align: left;
  border: 0; border-left: 3px solid transparent; padding: 10px 19px;
}}
QPushButton[variant="side"]:hover {{ background: {theme.rgba("text", 0.05)}; }}
QPushButton[variant="side"]:checked {{
  border-left-color: {t["accent"]}; background: {t["accent-100"]};
}}
QPushButton[variant="chip"] {{
  font-family: "{BODY_FAMILY}"; font-weight: 400; font-size: 13px; color: {t["neutral-800"]};
  padding: 4px 10px; min-height: 18px;
}}
QPushButton[variant="chip"]:disabled {{ color: {disabled_text}; border-color: {disabled_border}; }}
QPushButton[variant="chip"]:checked {{
  color: {t["accent-800"]}; background: {t["accent-100"]}; border-color: {t["accent"]};
}}
QPushButton[variant="seg"] {{
  font-family: "{BODY_FAMILY}"; font-weight: 400; font-size: 13px; padding: 6px 14px;
}}
QPushButton[variant="seg"]:checked {{
  background: {t["accent"]}; color: {t["bg"]}; border-color: {t["accent"]};
}}
QPushButton[variant="swatch"] {{
  min-width: 132px; max-width: 132px; min-height: 78px; max-height: 78px;
  padding: 0; border: 0; background: transparent;
}}
QToolButton::menu-button {{ border: 0; border-left: 1px solid {divider}; width: 22px; }}
QToolButton::menu-arrow, QToolButton::menu-indicator {{
  image: url({assets}/chevron-down.svg); width: 12px; height: 12px;
}}

/* inputs */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit[role="input"] {{
  background: {t["surface"]}; color: {t["text"]}; border: 1px solid {input_border};
  border-radius: 0; padding: 6px 10px; min-height: 22px; font-size: 14px;
  selection-background-color: {theme.rgba("accent", 0.30)}; selection-color: {t["text"]};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {theme.rgba("text", 0.5)}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {t["accent"]}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
  color: {disabled_text}; border-color: {disabled_border};
}}
QLineEdit:read-only {{ background: {t["bg"]}; }}
QLineEdit[role="bare"] {{ background: transparent; border: 0; padding: 2px 0; }}
QComboBox {{ padding-right: 4px; }}
QComboBox QLineEdit, QSpinBox QLineEdit {{
  background: transparent; border: 0; padding: 0; min-height: 0; margin: 0;
}}
QComboBox::drop-down {{
  border: 0; width: 26px; subcontrol-origin: padding; subcontrol-position: right;
}}
QComboBox::down-arrow {{ image: url({assets}/chevron-down.svg); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url({assets}/chevron-down-disabled.svg); }}
QComboBox QAbstractItemView {{
  background: {t["surface"]}; color: {t["text"]}; border: 1px solid {divider}; outline: 0;
  selection-background-color: {t["accent-200"]}; selection-color: {t["text"]}; padding: 2px;
}}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{
  subcontrol-origin: border; width: 20px; border: 0; background: transparent;
}}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ image: url({assets}/chevron-up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url({assets}/chevron-down.svg); width: 10px; height: 10px; }}
QSpinBox::up-arrow:disabled {{ image: url({assets}/chevron-up-disabled.svg); }}
QSpinBox::down-arrow:disabled {{ image: url({assets}/chevron-down-disabled.svg); }}

QCheckBox, QRadioButton {{ spacing: 9px; font-size: 14px; background: transparent; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {disabled_text}; }}
QCheckBox::indicator {{
  width: 13px; height: 13px; border: 2px solid {t["neutral-700"]}; background: transparent;
}}
QCheckBox::indicator:hover {{ border-color: {t["accent"]}; }}
QCheckBox::indicator:checked {{
  border-color: {t["accent"]}; background: {t["accent"]}; image: url({assets}/check.svg);
}}
QCheckBox::indicator:disabled {{ border-color: {t["neutral-400"]}; }}
QCheckBox::indicator:checked:disabled {{
  background: {t["neutral-400"]}; image: url({assets}/check-disabled.svg);
}}
QRadioButton::indicator {{
  width: 11px; height: 11px; border: 2px solid {t["neutral-700"]}; border-radius: 7px;
}}
QRadioButton::indicator:checked {{ border-color: {t["accent"]}; background: {t["accent"]}; }}

/* text areas */
QTextEdit, QPlainTextEdit {{
  background: {t["surface"]}; border: 1px solid {input_border}; border-radius: 0;
  selection-background-color: {theme.rgba("accent", 0.30)}; selection-color: {t["text"]};
}}
QTextEdit[role="journal"] {{ background: transparent; border: 0; font-size: 13px; }}

/* lists and tables */
QListWidget {{ background: transparent; border: 0; outline: 0; font-size: 14px; }}
QListWidget::item {{
  border: 1px solid {divider}; padding: 7px 10px; margin: 0 0 6px 0; color: {t["text"]};
}}
QListWidget::item:hover {{ background: {theme.rgba("text", 0.04)}; }}
QListWidget::item:selected {{
  border-color: {t["accent"]}; background: {t["accent-100"]}; color: {t["text"]};
}}
QListWidget[role="rail"]::item {{
  border: 0; border-left: 2px solid transparent; margin: 0; padding: 7px 10px;
}}
QListWidget[role="rail"]::item:selected {{ border-left-color: {t["accent"]}; }}
QTableView {{
  background: {t["bg"]}; alternate-background-color: {t["bg"]}; border: 1px solid {divider};
  gridline-color: transparent; outline: 0; font-size: 14px;
  selection-background-color: {t["accent-200"]}; selection-color: {t["text"]};
}}
QTableView::item {{ border-bottom: 1px solid {row_rule}; padding: 0 8px; }}
QTableView::item:selected {{ background: {t["accent-200"]}; color: {t["text"]}; }}
QHeaderView {{ background: {t["bg"]}; border: 0; }}
QHeaderView::section {{
  background: {t["bg"]}; color: {t["neutral-800"]}; font-size: 11px; font-weight: 500;
  border: 0; border-bottom: 1px solid {divider}; padding: 8px 10px;
}}
QTableCornerButton::section {{ background: {t["bg"]}; border: 0; }}

/* containers */
QTabWidget::pane {{ border: 0; }}
QScrollArea {{ background: transparent; border: 0; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QStackedWidget {{ background: transparent; }}
QGroupBox {{
  border: 1px solid {divider}; margin-top: 22px; padding: 12px 10px 10px 10px;
  font-family: {heading}; font-weight: 600; font-size: 16px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 0; padding: 0 0 4px 0; }}
QGroupBox[flat="true"] {{ border: 0; margin: 0; padding: 0; }}
QGroupBox::indicator {{ width: 0; height: 0; }}
QProgressBar {{ background: {t["accent-200"]}; border: 0; max-height: 3px; min-height: 3px; }}
QProgressBar::chunk {{ background: {t["accent"]}; }}
QMenu {{ background: {t["surface"]}; border: 1px solid {divider}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; }}
QMenu::item:selected {{ background: {t["accent-100"]}; color: {t["text"]}; }}
QMenu::separator {{ height: 1px; background: {divider}; margin: 4px 6px; }}
QMenuBar {{ background: {t["bg"]}; }}
QMenuBar::item:selected {{ background: {t["accent-100"]}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {t["neutral-400"]}; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {t["neutral-500"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMessageBox QLabel {{ font-size: 14px; }}
QDialogButtonBox QPushButton {{ min-width: 72px; }}
"""
