"""Small, consistent outline icons drawn from local application-owned SVG paths."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


_PATHS = {
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "brand": '<path d="M7 21h12l-2-3 2-3-3-2 2-4-3 1-2-6-2 6-5-1 3 4-3 2 3 3z"/><circle cx="13" cy="6" r=".7"/>',
    "home": '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    "play": '<path d="m7 4 13 8-13 8z"/>',
    "games": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8M8 15h5"/>',
    "analysis": '<path d="M4 19V5m0 14h17"/><path d="m7 15 4-4 3 2 5-7"/>',
    "training": '<path d="m2 9 10-5 10 5-10 5z"/><path d="M6 11v5c3 3 9 3 12 0v-5M22 9v6"/>',
    "progress": '<path d="M8 4h8v4a4 4 0 0 1-8 0zM8 6H4v2a4 4 0 0 0 4 4m8-6h4v2a4 4 0 0 1-4 4M12 12v5m-5 3h10m-8-3h6"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.6a8 8 0 0 1-1.7 1l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.7-1l-1.7.6-1.4-2.4L5.3 15a8 8 0 0 1 0-2l-1.4-1.1 1.4-2.4 1.7.6a8 8 0 0 1 1.7-1L9 7.3h2.8l.3 1.8a8 8 0 0 1 1.7 1l1.7-.6 1.4 2.4-1.4 1.1a8 8 0 0 1-.1 2z"/>',
    "ai": '<rect x="4" y="7" width="16" height="13" rx="3"/><path d="M12 3v4M9 12h.01M15 12h.01M9 16h6M2 11v5m20-5v5"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.1 0l3-3A5 5 0 0 0 13 2.9l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.1 0l-3 3A5 5 0 0 0 11 21.1l1.7-1.7"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2"/><circle cx="9.5" cy="7" r="4"/><path d="M20 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    "board": '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M9 3v18m6-18v18M3 9h18m-18 6h18"/>',
    "pawn": '<path d="M12 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM9 10h6l-1 4 3 4H7l3-4zM6 21h12"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "win": '<path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H4v2a4 4 0 0 0 4 4m8-6h4v2a4 4 0 0 1-4 4M12 13v4m-4 3h8m-6-3h4"/>',
    "loss": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    "draw": '<circle cx="12" cy="12" r="9"/><path d="M8 10h8m-8 4h8"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>',
    "power": '<path d="M12 2v10"/><path d="M6.2 5.5a8 8 0 1 0 11.6 0"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "sync": '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.6 9A7 7 0 0 1 18 6l2 2M4 16l2 2a7 7 0 0 0 12.4-3"/>',
    "database": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    "puzzle": '<path d="M8 4h3a2 2 0 1 1 4 0h3v5a2 2 0 1 1 0 4v7H8v-5a2 2 0 1 0 0-4z"/>',
}


@lru_cache(maxsize=128)
def icon_pixmap(name: str, size: int = 24, color: str = "#E5E1FF",
                device_pixel_ratio: float = 1.0) -> QPixmap:
    """Render a crisp DPR-aware, single-color outline icon for a QLabel/button."""
    path = _PATHS.get(str(name))
    if path is None:
        raise KeyError(f"Unknown Chess Coach icon: {name}")
    dpr = max(1.0, float(device_pixel_ratio))
    pixel_size = max(1, round(int(size) * dpr))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
           f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" '
           'stroke-linecap="round" stroke-linejoin="round">' + path + '</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap
