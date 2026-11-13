"""M4.6 design tokens and a single Qt stylesheet for the desktop shell."""

from __future__ import annotations

from dataclasses import dataclass, replace


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
VISUAL_TOKENS = replace(
    TOKENS, app="#0F1322", sidebar="#15192A", surface="#191F30",
    raised="#232B3E", hover="#343C54", border="#303950", text="#F5F6FD",
    secondary="#AEB8CF", muted="#9AA6BF", accent="#AA9BEF",
    accent_hover="#BBACFF", accent_pressed="#9F8BE8", on_accent="#16142A",
    success="#79E599", danger="#FF737C", warning="#F0BE55",
)


def stylesheet(t: Theme = TOKENS) -> str:
    radius = 16 if t is VISUAL_TOKENS else 12
    selected = "#39365E" if t is VISUAL_TOKENS else t.raised
    visual_sidebar = f"""
    QFrame#appSidebar QPushButton#navItem {{
        background: transparent; font-size: 18px; border-radius: 12px;
    }}
    QFrame#appSidebar QPushButton#navItem:hover {{ background: {t.hover}; }}
    QFrame#appSidebar QPushButton#navItem[active="true"] {{ background: {selected}; }}
    QFrame#appSidebar QLabel#sidebarBrandText {{ font-size: 23px; font-weight: 700; }}
    QFrame#appSidebar QLabel#sidebarProfile {{ font-size: 17px; font-weight: 600; }}
    """ if t is VISUAL_TOKENS else ""
    return f"""
    QWidget {{ background: {t.app}; color: {t.text}; font-size: {16 if t is VISUAL_TOKENS else 14}px; }}
    QMainWindow, QDialog {{ background: {t.app}; }}
    QLabel {{ background: transparent; }}
    QStatusBar#inlineNoticeBar {{ background: {t.sidebar}; color: {t.secondary}; border-top: 1px solid {t.border}; }}
    QStatusBar#inlineNoticeBar::item {{ border: none; }}
    QFrame#appSidebar {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
    QWidget#sidebarBrand QLabel#sidebarBrandText {{ font-size: 18px; font-weight: 700; }}
    QLabel#sidebarProfile {{ color: {t.secondary}; }}
    QFrame#card, QFrame#metricCard, QFrame#gameCard {{
        background: {t.surface}; border: 1px solid {t.border}; border-radius: {radius}px;
    }}
    QWidget#dashboardDetailsColumn, QWidget#dashboardInfoColumn,
    QWidget#dashboardInfoRow, QWidget#dashboardAnalysisMetrics,
    QWidget#dashboardGraphColumn,
    QWidget#homeEvaluationGraph, QWidget#homePuzzleRatingGraph {{
        background: transparent;
    }}
    QLabel#secondary, QLabel#muted, QLabel#connectionStatus {{ color: {t.secondary}; }}
    QLabel#pageTitle {{ font-size: {30 if t is VISUAL_TOKENS else 22}px; font-weight: 700; }}
    QPushButton, QToolButton {{
        background: {t.raised}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 8px; padding: 9px 14px; min-height: 22px;
    }}
    QPushButton:hover, QToolButton:hover {{ background: {t.hover}; }}
    QToolButton#positionPiece:checked {{ background: {t.accent}; border-color: {t.accent}; }}
    QWidget#appHeader QPushButton {{ padding-top: 2px; padding-bottom: 2px; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {t.accent_pressed}; color: {t.on_accent}; }}
    QPushButton:focus, QToolButton:focus {{ border: 2px solid {t.accent}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {t.muted}; background: {t.surface}; }}
    QPushButton#primaryAction {{ background: {t.accent}; color: {t.on_accent}; font-weight: 700; }}
    QPushButton#primaryAction:hover {{ background: {t.accent_hover}; }}
    QFrame#playModeCard {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: {radius}px; }}
    QFrame#playModeCard:hover {{ background: {t.hover}; }}
    QFrame#playModeCard[selected="true"] {{ background: {selected}; border: 2px solid {t.accent}; }}
    QFrame#playModeCard:focus {{ border: 2px solid {t.accent}; }}
      QLabel#playModeIcon {{ background: {selected}; color: {t.accent}; border-radius: 12px;
          font-size: 25px; font-weight: 700; }}
      QLabel#playModeSelectedMark {{ background: {t.accent}; color: {t.on_accent};
          border-radius: 17px; font-size: 19px; font-weight: 700; }}
      QRadioButton {{ background: {t.surface}; border: 1px solid {t.border};
          padding: 9px 12px; spacing: 6px; }}
      QRadioButton:first-child {{ border-top-left-radius: 7px; border-bottom-left-radius: 7px; }}
      QRadioButton:last-child {{ border-top-right-radius: 7px; border-bottom-right-radius: 7px; }}
      QRadioButton:checked {{ background: {selected}; color: {t.accent}; font-weight: 700; }}
    QFrame#disabledAnalysisCard {{ background: {t.sidebar}; border: 1px solid {t.border}; border-radius: {radius}px; }}
    QLabel#analysisCardIcon {{ background: {selected}; color: {t.accent}; border-radius: 10px; font-size: 23px; font-weight: 700; }}
    QLabel#analysisStatus, QLabel#breadcrumb {{ color: {t.accent}; }}
    QPushButton#navItem {{ text-align: left; padding: 10px 12px; min-height: 22px; border: none; }}
    QPushButton#navItem[active="true"] {{ background: {selected}; border-left: 3px solid {t.accent}; }}
    QLineEdit, QTextEdit, QComboBox, QSpinBox, QListWidget, QTableWidget {{
        background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 6px; padding: 5px;
        selection-background-color: {t.accent}; selection-color: {t.on_accent};
    }}
    QListWidget::item {{ padding: 5px; }}
    QListWidget::item:hover {{ background: {t.hover}; }}
    QHeaderView::section {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border}; padding: 6px; }}
    QGroupBox {{ background: {t.surface}; border: 1px solid {t.border};
        border-radius: {14 if t is VISUAL_TOKENS else 8}px;
        margin-top: 12px; padding: 10px 10px 6px 10px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px;
        color: {t.secondary}; background: {t.app}; }}
    QTabWidget::pane {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; top: -1px; }}
    QTabBar::tab {{ background: {t.raised}; color: {t.secondary}; border: 1px solid {t.border};
        border-top-left-radius: 8px; border-top-right-radius: 8px; padding: 9px 16px; min-height: 24px; }}
    QTabBar::tab:selected {{ background: {selected}; color: {t.text}; border-color: {t.accent}; }}
    QTabBar::tab:hover:!selected {{ background: {t.hover}; color: {t.text}; }}
    QTabBar::tab:disabled {{ color: {t.muted}; }}
    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px;
        border: 1px solid {t.border}; background: {t.surface}; border-radius: 4px; }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {t.accent};
        border-color: {t.accent}; }}
    QComboBox QAbstractItemView {{ background: {t.surface}; color: {t.text};
        border: 1px solid {t.border}; selection-background-color: {selected}; }}
    QProgressBar {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 7px; min-height: 18px; text-align: center; }}
    QProgressBar::chunk {{ background: {t.success}; border-radius: 6px; }}
    QScrollBar:vertical {{ background: {t.app}; width: 12px; }}
    QScrollBar::handle:vertical {{ background: {t.hover}; min-height: 24px; border-radius: 5px; }}
    QToolTip {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border}; }}
    {visual_sidebar}
    """


def relative_luminance(hex_color: str) -> float:
    values = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                for value in values]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first: str, second: str) -> float:
    high, low = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)
