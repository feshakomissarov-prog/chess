"""Compact full-move notation table shared by game review screens."""

from __future__ import annotations

import chess
from decimal import Decimal, ROUND_HALF_UP
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QWidget, QVBoxLayout,
)


_SEVERITY = {
    "good": ("✓", "Хороший ход"),
    # Avoid chess NAG punctuation whose conventional meanings differ from
    # the product's classifier labels (! is a good move; ?! is dubious).
    "inaccuracy": ("△", "Неточность"),
    "mistake": ("✕", "Ошибка"),
    "blunder": ("‼", "Зевок"),
    "strong": ("★", "Подтверждённый сильный ход"),
}
_SEVERITY_COLORS = {
    "good": "#58a978", "inaccuracy": "#d1a638", "mistake": "#dd7a36",
    "blunder": "#d95050", "strong": "#4aa6d8",
}


def _score_tenth(white_cp: int) -> str:
    score = (Decimal(int(white_cp)) / Decimal(100)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP)
    return format(score, "+.1f")


class _PlyTable(QTableWidget):
    wheel_navigate = Signal(int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._wheel_accum = 0

    def wheelEvent(self, event):  # noqa: N802 - Qt API
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y() or event.pixelDelta().y() * 3
        self._wheel_accum += delta
        steps = int(self._wheel_accum / 120)
        if steps:
            self._wheel_accum -= steps * 120
            for _ in range(min(8, abs(steps))):
                self.wheel_navigate.emit(-1 if steps > 0 else 1)
        event.accept()


class MoveTable(QWidget):
    """One row per full move; each SAN cell retains its exact ply identity."""

    ply_selected = Signal(int)
    wheel_navigate = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = _PlyTable(0, 3, self)
        self.table.wheel_navigate.connect(self.wheel_navigate)
        self.table.setHorizontalHeaderLabels(("№", "Белые", "Чёрные"))
        self.table.setAccessibleName("Нотация партии по полным ходам")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setMinimumWidth(0)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column, label in enumerate(("Номер полного хода", "Белые", "Чёрные")):
            self.table.horizontalHeaderItem(column).setToolTip(
                label if column == 0 else
                f"{label}; краткая оценка после хода указана за белых, если доступна.")
        self.table.setAccessibleDescription(
            "Одна строка на полный ход; оценка после хода приводится с перспективы белых. "
            "Изменение оценки доступно отдельно для выбранного полухода.")
        self.table.cellClicked.connect(self._cell_clicked)
        self._moves = []
        self._selected_ply: int | None = None
        layout.addWidget(self.table)

    def set_rows(self, moves, *, scores=None, enabled=True, show_severity=True):
        """Moves contain ply, SAN, mover and optional severity; scores are post-ply dicts."""
        self._moves = list(moves)
        self.table.setRowCount(0)
        rows: dict[int, int] = {}
        for move in moves:
            ply = int(move.ply if hasattr(move, "ply") else move["ply"])
            mover = str(move.mover if hasattr(move, "mover") else move["mover"])
            san = str(move.san if hasattr(move, "san") else move["san"])
            severity = getattr(move, "severity", None) or (move.get("severity") if isinstance(move, dict) else None)
            if not show_severity:
                severity = None
            before_fen = getattr(move, "before_fen", None)
            if before_fen is None and isinstance(move, dict):
                before_fen = move.get("before_fen")
            try:
                number = chess.Board(before_fen).fullmove_number if before_fen else (ply + 1) // 2
            except ValueError:
                number = (ply + 1) // 2
            if number not in rows:
                row = self.table.rowCount()
                rows[number] = row
                self.table.insertRow(row)
                number_item = QTableWidgetItem(str(number))
                number_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                number_item.setData(Qt.ItemDataRole.UserRole, max(0, ply - 1))
                self.table.setItem(row, 0, number_item)
                for col in (1, 2):
                    self.table.setItem(row, col, QTableWidgetItem(""))
            row = rows[number]
            column = 1 if mover == "white" else 2
            score = (scores or {}).get(ply)
            label = san
            if score is not None:
                cp = score.get("white_cp")
                mate = score.get("mate_white")
                if mate is not None:
                    score_text = f"M{abs(int(mate))}" if int(mate) > 0 else f"−M{abs(int(mate))}"
                elif cp is not None:
                    score_text = _score_tenth(cp)
                else:
                    score_text = None
                if score_text:
                    label += f" {score_text}"
            if severity in _SEVERITY:
                icon, description = _SEVERITY[severity]
                label = f"{icon} {label}"
                base_label = f"{icon} {san}"
                item = QTableWidgetItem(label)
                item.setToolTip(description)
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, f"{description}: {san}")
                item.setForeground(QColor(_SEVERITY_COLORS[severity]))
            else:
                item = QTableWidgetItem(label)
                base_label = san
                if severity == "unknown":
                    item.setToolTip("Не оценено")
                    item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,
                                 f"Не оценено: {san}")
                elif severity:
                    item.setToolTip(f"Класс хода: {severity}")
                    item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,
                                 f"Класс хода не распознан: {san}")
            item.setData(Qt.ItemDataRole.UserRole, ply)
            item.setData(Qt.ItemDataRole.UserRole + 1, base_label)
            if score is not None:
                item.setToolTip((item.toolTip() + "\n" if item.toolTip() else "") +
                                "Оценка позиции после хода; перспектива белых. Δ показана отдельно.")
            if not enabled:
                item.setToolTip("Оценки и анализ скрыты до проверки безопасности.")
            self.table.setItem(row, column, item)

    def append_move(self, move, *, score=None, enabled=True):
        """Append one confirmed ply without rebuilding existing full-move rows."""
        self._moves.append(move)
        ply = int(move.ply if hasattr(move, "ply") else move["ply"])
        mover = str(move.mover if hasattr(move, "mover") else move["mover"])
        san = str(move.san if hasattr(move, "san") else move["san"])
        before_fen = getattr(move, "before_fen", None)
        if before_fen is None and isinstance(move, dict):
            before_fen = move.get("before_fen")
        try:
            number = chess.Board(before_fen).fullmove_number if before_fen else (ply + 1) // 2
        except ValueError:
            number = (ply + 1) // 2
        row = next((r for r in range(self.table.rowCount())
                    if self.table.item(r, 0).text() == str(number)), None)
        if row is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
            number_item = QTableWidgetItem(str(number))
            number_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            number_item.setData(Qt.ItemDataRole.UserRole, max(0, ply - 1))
            self.table.setItem(row, 0, number_item)
            self.table.setItem(row, 1, QTableWidgetItem(""))
            self.table.setItem(row, 2, QTableWidgetItem(""))
        column = 1 if mover == "white" else 2
        label = san
        if score is not None:
            cp, mate = score.get("white_cp"), score.get("mate_white")
            score_text = (f"M{abs(int(mate))}" if mate is not None and int(mate) > 0 else
                          f"−M{abs(int(mate))}" if mate is not None else
                          _score_tenth(cp) if cp is not None else None)
            if score_text:
                label += f" {score_text}"
        severity = getattr(move, "severity", None) or (
            move.get("severity") if isinstance(move, dict) else None)
        if severity in _SEVERITY:
            icon, description = _SEVERITY[severity]
            item = QTableWidgetItem(f"{icon} {label}")
            base_label = f"{icon} {san}"
            item.setToolTip(description)
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, f"{description}: {san}")
            item.setForeground(QColor(_SEVERITY_COLORS[severity]))
        else:
            item = QTableWidgetItem(label)
            base_label = san
            if severity == "unknown":
                item.setToolTip("Не оценено")
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,
                             f"Не оценено: {san}")
            elif severity:
                item.setToolTip(f"Класс хода: {severity}")
        item.setData(Qt.ItemDataRole.UserRole, ply)
        item.setData(Qt.ItemDataRole.UserRole + 1, base_label)
        if score is not None:
            item.setToolTip((item.toolTip() + "\n" if item.toolTip() else "") +
                            "Оценка позиции после хода, перспектива белых.")
        if not enabled:
            item.setToolTip("Оценки и анализ скрыты до проверки безопасности.")
        self.table.setItem(row, column, item)

    def count(self):
        return len(self._moves)

    def item(self, ply_index: int):
        """Compatibility lookup by zero-based ply index used by UI integrations."""
        ply = int(ply_index) + 1
        for row in range(self.table.rowCount()):
            for column in (1, 2):
                cell = self.table.item(row, column)
                if cell is not None and cell.data(Qt.ItemDataRole.UserRole) == ply:
                    return cell
        return None

    def viewport(self):
        return self.table.viewport()

    def visualItemRect(self, item):
        return self.table.visualItemRect(item)

    def model(self):
        return self.table.model()

    def verticalScrollBar(self):
        return self.table.verticalScrollBar()

    def currentItem(self):
        return self.table.currentItem()

    def setCurrentItem(self, item):
        self.table.setCurrentItem(item)

    def clear(self):
        self._moves.clear()
        self.table.setRowCount(0)

    def blockSignals(self, block):
        return self.table.blockSignals(block)

    def _cell_clicked(self, row: int, column: int):
        item = self.table.item(row, column)
        if column == 0:
            self.ply_selected.emit(int(item.data(Qt.ItemDataRole.UserRole) or 0))
        elif item is not None:
            ply = item.data(Qt.ItemDataRole.UserRole)
            if ply is not None:
                self.ply_selected.emit(int(ply))

    def select_ply(self, ply: int):
        ply = int(ply)
        self._selected_ply = ply
        if ply == 0:
            self.table.clearSelection()
            self.table.setCurrentItem(None)
            self.table.scrollToTop()
            QTimer.singleShot(0, self._scroll_to_start_if_selected)
            return
        for row in range(self.table.rowCount()):
            for column in (1, 2):
                item = self.table.item(row, column)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == ply:
                    self.table.setCurrentItem(item)
                    self.table.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)
                    # Showing an asynchronously loaded review can trigger a
                    # splitter resize after selection. Repeat the scroll once
                    # the queued layout pass has settled, fenced by ply so an
                    # older callback cannot move a newer selection.
                    QTimer.singleShot(0, lambda selected=ply: self._scroll_selected_ply(selected))
                    return
        self.table.clearSelection()

    def _scroll_to_start_if_selected(self):
        if self._selected_ply == 0:
            self.table.scrollToTop()

    def _scroll_selected_ply(self, ply: int):
        current = self.table.currentItem()
        if self._selected_ply != int(ply) or current is None \
                or current.data(Qt.ItemDataRole.UserRole) != int(ply):
            return
        item = self.item_by_ply(ply)
        if item is not None:
            self.table.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)

    def setCurrentRow(self, ply: int):
        """Compatibility helper for callers that used the former one-ply list."""
        self.ply_selected.emit(int(ply))
        self.select_ply(ply)

    def item(self, ply: int):
        """Return a move cell by its historical ply index for integrations/tests."""
        return self.item_by_ply(int(ply) + 1)

    def item_by_ply(self, ply: int):
        for row in range(self.table.rowCount()):
            for column in (1, 2):
                cell = self.table.item(row, column)
                if cell is not None and cell.data(Qt.ItemDataRole.UserRole) == int(ply):
                    return cell
        return None

    def set_score(self, ply: int, *, white_cp: int | None = None,
                  mate_white: int | None = None, source: str = "", depth: int | None = None) -> bool:
        """Attach a post-move evaluation to the exact SAN cell, if present."""
        item = self.item_by_ply(ply)
        if item is None:
            return False
        if mate_white is not None:
            value = f"M{abs(int(mate_white))}" if int(mate_white) > 0 else f"−M{abs(int(mate_white))}"
        elif white_cp is not None:
            value = _score_tenth(white_cp)
        else:
            return False
        base = item.data(Qt.ItemDataRole.UserRole + 1) or item.text()
        item.setText(f"{base}   {value}")
        item.setToolTip(f"Оценка позиции после этого хода, перспектива белых · "
                        f"{source or 'источник неизвестен'}"
                        + (f" · глубина {depth}" if depth is not None else ""))
        return True
