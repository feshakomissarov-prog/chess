"""Small, non-sensitive window/workspace state helpers."""

from __future__ import annotations

from PySide6.QtCore import QRect


def geometry_intersects_available(frame: QRect, available: list[QRect]) -> bool:
    return any(screen.isValid() and frame.intersects(screen) for screen in available)


def reposition_visible_frame(frame: QRect, available: list[QRect]) -> QRect:
    """Fit a partly/off-screen frame into an available logical-pixel screen rectangle."""
    screens = [screen for screen in available if screen.isValid()]
    if not screens:
        return QRect(frame)
    containing = next((screen for screen in screens
                       if screen.left() <= frame.left() and screen.top() <= frame.top()
                       and screen.right() >= frame.right() and screen.bottom() >= frame.bottom()), None)
    if containing is not None:
        return QRect(frame)
    target = max(screens, key=lambda screen: screen.intersected(frame).width()
                 * screen.intersected(frame).height())
    width = min(max(1, frame.width()), target.width())
    height = min(max(1, frame.height()), target.height())
    x = min(max(frame.x(), target.left()), target.right() - width + 1)
    y = min(max(frame.y(), target.top()), target.bottom() - height + 1)
    return QRect(x, y, width, height)
