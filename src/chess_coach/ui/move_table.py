"""Compact full-move notation table shared by game review screens."""

from __future__ import annotations

import chess
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem, QWidget, QVBoxLayout


_SEVERITY = {
    "unknown": ("?", "Ход не классифицирован"),
    "good": ("✓", "Хороший ход"),
    "inaccuracy": ("!", "Неточность"),
    "mistake": ("?!", "Ошибка"),
    "blunder": ("??", "Зевок"),
    "strong": ("★", "Подтверждённый сильный ход"),
}


class MoveTable(QWidget):
    """One row per full move; each SAN cell retains its exact ply identity."""

    ply_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(("№", "Белые", "Чёрные"))
        self.table.setAccessibleName("Нотация партии по полным ходам")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellClicked.connect(self._cell_clicked)
        self._moves = []
        layout.addWidget(self.table)

    def set_rows(self, moves, *, scores=None, enabled=True):
        """Moves contain ply, SAN, mover and optional severity; scores are post-ply dicts."""
        self._moves = list(moves)
        self.table.setRowCount(0)
        rows: dict[int, int] = {}
        for move in moves:
            ply = int(move.ply if hasattr(move, "ply") else move["ply"])
            mover = str(move.mover if hasattr(move, "mover") else move["mover"])
            san = str(move.san if hasattr(move, "san") else move["san"])
            severity = getattr(move, "severity", None) or (move.get("severity") if isinstance(move, dict) else None)
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
                    score_text = f"{int(cp) / 100:+.1f}"
                else:
                    score_text = None
                if score_text:
                    label += f"   {score_text} · белые"
            if severity in _SEVERITY:
                icon, description = _SEVERITY[severity]
                label = f"{icon} {label}"
                item = QTableWidgetItem(label)
                item.setToolTip(description)
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, f"{description}: {san}")
            else:
                item = QTableWidgetItem(label)
                if severity:
                    item.setToolTip(f"Класс хода: {severity}")
                    item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,
                                 f"Класс хода не распознан: {san}")
            item.setData(Qt.ItemDataRole.UserRole, ply)
            if score is not None:
                item.setToolTip((item.toolTip() + "\n" if item.toolTip() else "") +
                                "Оценка позиции после хода, перспектива белых.")
            if not enabled:
                item.setToolTip("Оценки и анализ скрыты до проверки безопасности.")
            self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()

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
                          f"{int(cp) / 100:+.1f}" if cp is not None else None)
            if score_text:
                label += f"   {score_text} · белые"
        severity = getattr(move, "severity", None) or (
            move.get("severity") if isinstance(move, dict) else None)
        if severity in _SEVERITY:
            icon, description = _SEVERITY[severity]
            item = QTableWidgetItem(f"{icon} {label}")
            item.setToolTip(description)
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, f"{description}: {san}")
        else:
            item = QTableWidgetItem(label)
            if severity:
                item.setToolTip(f"Класс хода: {severity}")
        item.setData(Qt.ItemDataRole.UserRole, ply)
        if score is not None:
            item.setToolTip((item.toolTip() + "\n" if item.toolTip() else "") +
                            "Оценка позиции после хода, перспектива белых.")
        if not enabled:
            item.setToolTip("Оценки и анализ скрыты до проверки безопасности.")
        self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()

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
        for row in range(self.table.rowCount()):
            for column in (1, 2):
                item = self.table.item(row, column)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == ply:
                    self.table.setCurrentItem(item)
                    return
        self.table.clearSelection()

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
