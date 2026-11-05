"""M4.6 design tokens and a single Qt stylesheet for the desktop shell."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    app: str = "#14171C"
    sidebar: str = "#191C22"
    surface: str = "#1F232A"
    raised: str = "#272C34"
    hover: str = "#30353E"
    border: str = "#353A43"
    text: str = "#F0EBE3"
    secondary: str = "#B8BDC7"
    muted: str = "#A0A6B1"
    accent: str = "#C6A477"
    accent_hover: str = "#D6B88C"
    accent_pressed: str = "#AA885D"
    on_accent: str = "#181B20"
    success: str = "#6F9D83"
    danger: str = "#CF7272"
    warning: str = "#D2AA69"
    info: str = "#8EAEC8"
    board_light: str = "#C9B79D"
    board_dark: str = "#78675C"
    board_selection: str = "#E4BF80"
    board_last_move: str = "#D9C47F"


TOKENS = Theme()


def stylesheet() -> str:
    t = TOKENS
    return f"""
    QWidget {{ background: {t.app}; color: {t.text}; font-size: 14px; }}
    QMainWindow, QDialog {{ background: {t.app}; }}
    QLabel {{ background: transparent; }}
    QStatusBar#inlineNoticeBar {{ background: {t.sidebar}; color: {t.secondary}; border-top: 1px solid {t.border}; }}
    QStatusBar#inlineNoticeBar::item {{ border: none; }}
    QFrame#appSidebar {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
    QFrame#card, QFrame#metricCard, QFrame#gameCard {{
        background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px;
    }}
    QLabel#secondary, QLabel#muted, QLabel#connectionStatus {{ color: {t.secondary}; }}
    QLabel#pageTitle {{ font-size: 22px; font-weight: 700; }}
    QPushButton, QToolButton {{
        background: {t.raised}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 8px; padding: 9px 14px; min-height: 22px;
    }}
    QPushButton:hover, QToolButton:hover {{ background: {t.hover}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {t.accent_pressed}; color: {t.on_accent}; }}
    QPushButton:focus, QToolButton:focus {{ border: 2px solid {t.accent}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {t.muted}; background: {t.surface}; }}
    QPushButton#primaryAction {{ background: {t.accent}; color: {t.on_accent}; font-weight: 700; }}
    QPushButton#primaryAction:hover {{ background: {t.accent_hover}; }}
    QPushButton#navItem {{ text-align: left; padding: 10px 12px; min-height: 22px; border: none; }}
    QPushButton#navItem[active="true"] {{ background: {t.raised}; border-left: 3px solid {t.accent}; }}
    QLineEdit, QTextEdit, QComboBox, QSpinBox, QListWidget, QTableWidget {{
        background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 6px; padding: 5px;
        selection-background-color: {t.accent}; selection-color: {t.on_accent};
    }}
    QListWidget::item {{ padding: 5px; }}
    QListWidget::item:hover {{ background: {t.hover}; }}
    QHeaderView::section {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border}; padding: 6px; }}
    QGroupBox {{ border: 1px solid {t.border}; border-radius: 8px; margin-top: 10px; padding-top: 8px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
    QScrollBar:vertical {{ background: {t.app}; width: 12px; }}
    QScrollBar::handle:vertical {{ background: {t.hover}; min-height: 24px; border-radius: 5px; }}
    QToolTip {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border}; }}
    """


def relative_luminance(hex_color: str) -> float:
    values = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                for value in values]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first: str, second: str) -> float:
    high, low = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)
