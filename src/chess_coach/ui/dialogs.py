"""Standalone Lichess and analysis dialogs; re-exported by chess_coach.window."""

from __future__ import annotations

from pathlib import Path
import os
import platform
import threading
import shiboken6
import chess
from PySide6.QtCore import Qt, Signal, QUrl, QProcess, QSettings, QTimer
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QLineEdit, QPushButton,
    QRadioButton, QStackedWidget, QVBoxLayout, QSpinBox, QMessageBox,
    QWidget, QApplication, QFileDialog, QFrame, QGroupBox, QScrollArea, QSlider, QTabWidget,
)
from ..diagnostics import anonymized_report, logs_directory
from ..engine_manager import discover_stockfish
from ..uci import popen_uci_hidden
from ..lichess.analysis import AnalysisOptions
from ..lichess.repository import GameRepository, SCHEMA_VERSION
from .sounds import DEFAULT_SOUND_SELECTIONS, SOUND_CATALOG, selected_sound_ids


class OnlineGameDialog(QDialog):
    def __init__(self, title: str, include_level: bool = False, parent=None,
                 defaults: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(450 if include_level else 400)
        form = QVBoxLayout(self)
        self.color = QComboBox()
        self.color.addItem("Случайный", "random")
        self.color.addItem("Белыми", "white")
        self.color.addItem("Чёрными", "black")
        form.addWidget(QLabel("Ваш цвет"))
        form.addWidget(self.color)
        self.clock = QComboBox()
        self.clock.addItem("Без часов", (None, 0))
        self.clock.addItem("3 + 2", (180, 2))
        self.clock.addItem("10 + 0", (600, 0))
        self.clock.addItem("15 + 10", (900, 10))
        defaults = defaults or {}
        color_index = self.color.findData(defaults.get("color"))
        if color_index >= 0:
            self.color.setCurrentIndex(color_index)
        clock_value = (defaults.get("clock_limit"), int(defaults.get("clock_increment") or 0))
        clock_index = next((index for index in range(self.clock.count())
                            if self.clock.itemData(index) == clock_value), -1)
        if clock_index >= 0:
            self.clock.setCurrentIndex(clock_index)
        form.addWidget(QLabel("Контроль времени"))
        form.addWidget(self.clock)
        self.rated = QCheckBox("Рейтинговая партия (изменит рейтинг)")
        if not include_level:
            form.addWidget(self.rated)
        self.level = QComboBox()
        for level in range(1, 9):
            self.level.addItem(f"Уровень {level}", level)
        if include_level:
            form.addWidget(QLabel("Сила Lichess AI"))
            self.level_buttons = QButtonGroup(self)
            self.level_buttons.setExclusive(True)
            levels = QGridLayout()
            levels.setHorizontalSpacing(8)
            levels.setVerticalSpacing(6)
            for level in range(1, 9):
                button = QRadioButton(str(level))
                button.setAccessibleName(f"Уровень Lichess AI {level}")
                button.setStyleSheet("QRadioButton { border: 1px solid #46505c; border-radius: 6px; "
                                     "padding: 9px 18px; font-size: 14px; font-weight: 700; } "
                                     "QRadioButton:checked { background: #174e41; border-color: #55b894; }")
                self.level_buttons.addButton(button, level)
                levels.addWidget(button, (level - 1) // 4, (level - 1) % 4)
                button.toggled.connect(lambda checked, value=level:
                                       self.level.setCurrentIndex(value - 1) if checked else None)
            self.level_buttons.button(1).setChecked(True)
            level_index = self.level.findData(defaults.get("level", 1))
            if level_index >= 0:
                self.level_buttons.button(int(defaults.get("level", 1))).setChecked(True)
            form.addLayout(levels)
            self.level.setVisible(False)
        else:
            self.level_buttons = None
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Начать")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addWidget(buttons)

    def selection(self):
        return self.color.currentData(), *self.clock.currentData(), self.level.currentData(), self.rated.isChecked()


class BatchAnalysisDialog(QDialog):
    """Explicit controls for the bounded recent-first Stockfish queue."""

    run_requested = Signal(int, int, int, bool, bool, bool)
    pause_requested = Signal()
    cancel_requested = Signal()
    engine_settings_requested = Signal()
    worker_progress = Signal(str)
    worker_queue_status = Signal(str)
    worker_run_status = Signal(str)
    worker_finished = Signal(bool, str)
    diagnostics_requested = Signal()
    lichess_inspector_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Анализ партий")
        self.setMinimumWidth(440)
        self._readiness = False
        self._running = False
        self._paused = False
        self._active_queue_parameters: tuple[int, int] | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Анализ запускается только по кнопке и только для завершённых стандартных партий. "
            "Перед каждой партией Lichess проверяется заново; неизвестное состояние останавливает очередь."))
        self.engine_status = QLabel("Движок не найден. Откройте настройки, чтобы найти или установить Stockfish.")
        self.engine_status.setWordWrap(True)
        layout.addWidget(self.engine_status)
        self.account_status = QLabel("Аккаунт Lichess не подключён; fair-play preflight недоступен.")
        self.account_status.setWordWrap(True)
        layout.addWidget(self.account_status)
        self.engine_settings_button = QPushButton("Настроить Stockfish…")
        self.engine_settings_button.clicked.connect(self.engine_settings_requested.emit)
        layout.addWidget(self.engine_settings_button)
        self.lichess_inspector_button = QPushButton("Готовые данные Lichess…")
        self.lichess_inspector_button.clicked.connect(self.lichess_inspector_requested.emit)
        layout.addWidget(self.lichess_inspector_button)
        self.settings_group = QGroupBox("Параметры анализа")
        self.settings_group.setCheckable(True)
        self.settings_group.setChecked(False)
        group_layout = QVBoxLayout(self.settings_group)
        self.settings_content = QWidget()
        self.settings_content.setVisible(False)
        self.settings_group.toggled.connect(self.settings_content.setVisible)
        group_layout.addWidget(self.settings_content)
        settings_layout = QVBoxLayout(self.settings_content)
        settings_layout.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(self.settings_group)
        settings_layout.addWidget(QLabel(
            "Проанализировать две последние недостающие завершённые партии. "
            "Партии с готовой Lichess-оценкой, уже завершённым заданием или неподдерживаемым PGN пропускаются."))
        form = QGridLayout()
        self.limit = QSpinBox(); self.limit.setRange(1, 10_000); self.limit.setValue(2)
        self.nodes = QSpinBox(); self.nodes.setRange(1000, 10_000_000); self.nodes.setSingleStep(10_000); self.nodes.setGroupSeparatorShown(True); self.nodes.setValue(20_000)
        self.nodes.setSuffix(" узлов / позицию")
        self.hash_mb = QSpinBox(); self.hash_mb.setRange(16, 128); self.hash_mb.setSingleStep(16); self.hash_mb.setValue(64)
        self.retry_failed = QCheckBox("Повторить ранее завершившиеся ошибкой/отменённые задания")
        self.selective_review = QCheckBox()
        self.budget_note = QLabel()
        self.budget_note.setWordWrap(True)
        def update_budget_note(value: int) -> None:
            high_budget = value > 30_000
            description = ("Пользовательский высокий бюджет; анализ начнётся только по кнопке. "
                           if high_budget else "Ограниченный бюджет одного локального прохода. ")
            description += (f"Пример 20 партий × 120 позиций: {value:,} × 120 × 20 узлов; "
                            "это оценка вычислительной работы, не времени исполнения.").replace(",", " ")
            self.budget_note.setText(description)
            prefix = "основного" if high_budget else "быстрого"
            self.selective_review.setText(
                f"После {prefix} прохода выборочно перепроверить критические позиции "
                "(до 12 на игру / 100 всего)")
        self.nodes.valueChanged.connect(update_budget_note)
        self.nodes.valueChanged.emit(self.nodes.value())
        settings_layout.addWidget(self.budget_note)
        form.addWidget(QLabel("Максимум партий за один запуск"), 0, 0); form.addWidget(self.limit, 0, 1)
        form.addWidget(QLabel("Узлов на позицию"), 1, 0); form.addWidget(self.nodes, 1, 1)
        form.addWidget(QLabel("Hash (МБ)"), 2, 0); form.addWidget(self.hash_mb, 2, 1)
        settings_layout.addLayout(form)
        settings_layout.addWidget(self.retry_failed)
        settings_layout.addWidget(self.selective_review)
        self.queue_label = QLabel("Очередь ещё не загружена")
        self.queue_label.setWordWrap(True)
        layout.addWidget(self.queue_label)
        self.run_label = QLabel("В текущем запуске: 0 заданий")
        self.run_label.setWordWrap(True)
        layout.addWidget(self.run_label)
        self.progress_label = QLabel("Движок не запущен")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)
        controls = QHBoxLayout()
        self.start_button = QPushButton("Проанализировать 2 последние недостающие")
        self.start_button.clicked.connect(self._emit_start_request)
        self.start_all_button = QPushButton("Все недостающие (фоновая очередь)")
        self.start_all_button.setObjectName("primaryAction")
        self.start_all_button.clicked.connect(lambda: self.run_requested.emit(
            0, self.nodes.value(), self.hash_mb.value(), False, self.retry_failed.isChecked(),
            self.selective_review.isChecked()))
        self.resume_button = QPushButton("Продолжить очередь")
        # A persistent queue may have been paused by a previous application run,
        # but no queue may start before engine/account readiness is visible.
        self.start_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.limit.valueChanged.connect(lambda count: self.start_button.setText(
            f"Проанализировать {count} последние недостающие"))
        self.resume_button.clicked.connect(self._emit_resume_request)
        self.pause_button = QPushButton("Пауза")
        self.pause_button.setEnabled(False)
        self.pause_button.setVisible(False)
        self.pause_button.clicked.connect(self.pause_requested.emit)
        self.cancel_button = QPushButton("Отменить очередь")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._confirm_cancel)
        controls.addWidget(self.start_all_button)
        controls.addWidget(self.start_button); controls.addWidget(self.resume_button)
        controls.addWidget(self.pause_button); controls.addWidget(self.cancel_button)
        layout.addLayout(controls)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)
        self.worker_progress.connect(self.progress_label.setText)
        self.worker_queue_status.connect(self.queue_label.setText)
        self.worker_run_status.connect(self.run_label.setText)
        self.worker_finished.connect(self._worker_finished)
        self.diagnostics_button = QPushButton("Открыть папку диагностических логов")
        self.diagnostics_button.clicked.connect(self.diagnostics_requested.emit)
        settings_layout.addWidget(self.diagnostics_button)

    def _emit_start_request(self) -> None:
        self._active_queue_parameters = (self.nodes.value(), self.hash_mb.value())
        self.run_requested.emit(self.limit.value(), self.nodes.value(), self.hash_mb.value(), False,
                                self.retry_failed.isChecked(), self.selective_review.isChecked())

    def _emit_resume_request(self) -> None:
        if self._active_queue_parameters is not None:
            nodes, hash_mb = self._active_queue_parameters
            self.nodes.setValue(nodes)
            self.hash_mb.setValue(hash_mb)
        self.run_requested.emit(self.limit.value(), self.nodes.value(), self.hash_mb.value(), True,
                                False, self.selective_review.isChecked())

    def _confirm_cancel(self) -> None:
        answer = QMessageBox.question(
            self, "Отменить пакетный анализ",
            "Остановить текущий анализ и отметить оставшуюся очередь как отменённую? "
            "Уже сохранённые результаты останутся в базе.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self.cancel_requested.emit()

    def set_readiness(self, engine_path: str, engine_verified: bool, account_connected: bool,
                      account_reason: str | None = None) -> None:
        path = Path(engine_path) if engine_path else None
        available = bool(path and path.is_file())
        if not available:
            self.engine_status.setText("Stockfish не настроен. Выберите или проверьте движок в настройках.")
        elif engine_verified:
            self.engine_status.setText("Stockfish готов · UCI-проверка пройдена")
        else:
            self.engine_status.setText("Stockfish найден, требуется проверка в настройках")
        self.account_status.setText(
            "Аккаунт подключён. Перед каждым заданием будет выполнена live fair-play проверка."
            if account_connected else
            (account_reason or
             "Аккаунт Lichess не подключён; подключите его для обязательной проверки безопасности."))
        enabled = available and engine_verified and account_connected
        self._readiness = enabled
        self._update_action_state()
        self.engine_settings_button.setEnabled(True)

    def _worker_finished(self, paused: bool, message: str) -> None:
        self._paused = bool(paused)
        self.set_running(False)
        self.run_label.setText(
            "Текущий запуск приостановлен; состояние общей очереди указано ниже."
            if paused else "Текущий запуск завершён; итоговые счётчики указаны ниже.")
        self.progress_label.setText(message)

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        if running:
            self._paused = False
        self.limit.setEnabled(not running)
        self.nodes.setEnabled(not running)
        self.hash_mb.setEnabled(not running)
        self.retry_failed.setEnabled(not running)
        self.selective_review.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.cancel_button.setEnabled(running)
        self.pause_button.setVisible(running)
        self.cancel_button.setVisible(running)
        self._update_action_state()

    def _update_action_state(self) -> None:
        idle = not self._running
        self.start_button.setEnabled(idle and self._readiness and not self._paused)
        self.start_all_button.setEnabled(idle and self._readiness and not self._paused)
        self.start_all_button.setVisible(not self._paused)
        self.resume_button.setEnabled(idle and self._readiness and self._paused)
        self.start_button.setVisible(not self._paused)
        self.resume_button.setVisible(self._paused)

    def closeEvent(self, event) -> None:
        if self.pause_button.isEnabled() or self.cancel_button.isEnabled():
            self.progress_label.setText("Сначала поставьте очередь на паузу или отмените её.")
            event.ignore()
            return
        super().closeEvent(event)


class ActiveGamesDialog(QDialog):
    refresh_requested = Signal()
    open_requested = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Активные партии Lichess")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.status = QLabel("Нажмите «Обновить», чтобы запросить список у Lichess.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.games = QListWidget()
        self.games.itemDoubleClicked.connect(lambda _item: self.open_selected())
        layout.addWidget(self.games)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Обновить список")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        buttons.addWidget(self.refresh_button)
        self.open_button = QPushButton("Открыть выбранную партию")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected)
        buttons.addWidget(self.open_button)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.games.currentItemChanged.connect(
            lambda current, _previous: self.open_button.setEnabled(current is not None)
        )

    def set_loading(self) -> None:
        self.status.setText("Запрашиваем активные партии…")
        self.refresh_button.setEnabled(False)

    def set_error(self, message: str) -> None:
        self.status.setText(message)
        self.refresh_button.setEnabled(True)

    def set_games(self, games: list[dict]) -> None:
        self.games.clear()
        for game in games:
            opponent = game.get("opponent") or {}
            opponent_name = opponent.get("username") or opponent.get("id") or "Соперник"
            color = {"white": "белыми", "black": "чёрными"}.get(
                game.get("color"), "цвет не указан"
            )
            speed = str(game.get("speed") or "контроль не указан")
            kind = "заочная" if speed.casefold() == "correspondence" else "реальное время"
            is_my_turn = game.get("isMyTurn")
            turn = ("ваш ход" if is_my_turn else "ход соперника"
                    if isinstance(is_my_turn, bool) else "чей ход не указан")
            seconds = game.get("secondsLeft")
            remaining = f" · осталось {int(seconds)} с" if isinstance(seconds, (int, float)) else ""
            item = QListWidgetItem(
                f"{opponent_name} · вы {color} · {kind} ({speed}) · {turn}{remaining}"
            )
            item.setData(Qt.ItemDataRole.UserRole, game)
            self.games.addItem(item)
        self.status.setText(
            f"Активных партий: {len(games)}." if games else "Lichess не вернул активных партий."
        )
        self.refresh_button.setEnabled(True)
        self.open_button.setEnabled(self.games.currentItem() is not None)

    def open_selected(self) -> None:
        item = self.games.currentItem()
        if item is not None:
            self.open_requested.emit(item.data(Qt.ItemDataRole.UserRole))
            self.accept()


class LichessConnectionDialog(QDialog):
    """Explicit OAuth/PAT chooser; credentials never enter persistent Qt state."""
    method_changed = Signal(str)
    restore_requested = Signal()
    oauth_requested = Signal()
    personal_token_requested = Signal(str)
    delete_personal_token_requested = Signal()
    revoke_oauth_requested = Signal()
    diagnostics_requested = Signal()

    def __init__(self, method: str = "oauth", *, oauth_saved: bool = False,
                 personal_token_saved: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Подключение к Lichess")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Подключение к Lichess"))
        explanation = QLabel(
            "Выберите способ подключения. Секреты хранятся отдельно в Windows Credential Manager. "
            "Локальные данные партий остаются доступными без сети.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.status_label = QLabel("Не подключено")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.diagnostics_button = QPushButton("Открыть обезличенный отчёт диагностики")
        self.diagnostics_button.clicked.connect(self.diagnostics_requested.emit)
        layout.addWidget(self.diagnostics_button)
        modes = QHBoxLayout()
        self.oauth_radio = QRadioButton("Через браузер (OAuth)")
        self.personal_radio = QRadioButton("Персональный токен")
        self.oauth_radio.setChecked(method != "personal_token")
        self.personal_radio.setChecked(method == "personal_token")
        modes.addWidget(self.oauth_radio)
        modes.addWidget(self.personal_radio)
        layout.addLayout(modes)
        self.pages = QStackedWidget()
        oauth_page = QWidget()
        oauth_layout = QVBoxLayout(oauth_page)
        oauth_layout.addWidget(QLabel(
            "OAuth PKCE откроет Lichess в браузере. Запрашиваются board:play и "
            "challenge:write; puzzle:read хранится отдельно и запрашивается только для истории задач."))
        oauth_link = QPushButton("Открыть страницу управления токенами Lichess")
        oauth_link.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://lichess.org/account/oauth/token")))
        oauth_layout.addWidget(oauth_link)
        self.oauth_saved_badge = QLabel("Сохранённое подключение: " +
                                        ("доступно в Windows Credential Manager" if oauth_saved else "не найдено"))
        oauth_layout.addWidget(self.oauth_saved_badge)
        self.restore_button = QPushButton("Проверить сохранённое подключение")
        self.oauth_button = QPushButton("Подключить через браузер")
        self.oauth_revoke_button = QPushButton("Отозвать и удалить OAuth-токен")
        self.oauth_revoke_button.setEnabled(oauth_saved)
        oauth_layout.addWidget(self.restore_button)
        oauth_layout.addWidget(self.oauth_button)
        oauth_layout.addWidget(self.oauth_revoke_button)
        self.pages.addWidget(oauth_page)

        token_page = QWidget()
        token_layout = QVBoxLayout(token_page)
        token_note = QLabel(
            "Создайте новый минимально привилегированный токен на Lichess и вставьте его здесь. "
            "Не используйте ранее раскрытый токен. Нужные полномочия зависят от функций: "
            "board:play и challenge:write; puzzle:read выдаётся отдельно.")
        token_note.setWordWrap(True)
        token_layout.addWidget(token_note)
        token_link = QPushButton("Создать или отозвать токен на Lichess")
        token_link.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://lichess.org/account/oauth/token")))
        token_layout.addWidget(token_link)
        self.personal_token_input = QLineEdit()
        self.personal_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.personal_token_input.setPlaceholderText("Вставьте новый токен Lichess")
        self.personal_token_input.setClearButtonEnabled(True)
        token_layout.addWidget(self.personal_token_input)
        self.personal_saved_badge = QLabel(
            "Сохранён в Windows Credential Manager" if personal_token_saved
            else "Сохранённый персональный токен не найден")
        token_layout.addWidget(self.personal_saved_badge)
        self.personal_check_button = QPushButton(
            "Заменить" if personal_token_saved else "Проверить и сохранить")
        token_layout.addWidget(self.personal_check_button)
        self.personal_delete_button = QPushButton("Удалить локально сохранённый токен")
        self.personal_delete_button.setEnabled(personal_token_saved)
        token_layout.addWidget(self.personal_delete_button)
        warning = QLabel("Удаление удаляет только локальную копию. Чтобы отозвать полномочия, "
                         "используйте страницу Lichess выше. Программа и другие процессы "
                         "профиля Windows с достаточными правами технически могут читать сохранённые учётные данные.")
        warning.setWordWrap(True)
        token_layout.addWidget(warning)
        self.pages.addWidget(token_page)
        self.pages.setCurrentIndex(0 if method != "personal_token" else 1)
        layout.addWidget(self.pages)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.oauth_radio.toggled.connect(self._on_mode_toggled)
        self.personal_radio.toggled.connect(self._on_mode_toggled)
        self.restore_button.clicked.connect(self.restore_requested.emit)
        self.oauth_button.clicked.connect(self.oauth_requested.emit)
        self.personal_check_button.clicked.connect(self._submit_personal_token)
        self.personal_delete_button.clicked.connect(self.delete_personal_token_requested.emit)
        self.oauth_revoke_button.clicked.connect(self.revoke_oauth_requested.emit)

    def _on_mode_toggled(self, checked: bool) -> None:
        if not checked:
            return
        method = "oauth" if self.oauth_radio.isChecked() else "personal_token"
        self.pages.setCurrentIndex(0 if method == "oauth" else 1)
        self.method_changed.emit(method)

    def _submit_personal_token(self) -> None:
        token = self.personal_token_input.text()
        if token.strip():
            self.personal_token_requested.emit(token)
            self.personal_token_input.clear()

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)



class SettingsDialog(QDialog):
    install_requested = Signal()
    sound_preview_requested = Signal(str)
    connection_settings_requested = Signal()
    engine_probe_result = Signal(int, bool, str)

    def __init__(self, settings: QSettings, parent=None, repository: GameRepository | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setModal(True)
        root_layout = QVBoxLayout(self)
        self.settings_tabs = QTabWidget(self)
        root_layout.addWidget(self.settings_tabs, 1)

        def add_settings_tab(title: str) -> QVBoxLayout:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            page_layout.setSpacing(8)
            page_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.settings_tabs.addTab(scroll, title)
            return page_layout

        layout = add_settings_tab("Общие / доска")
        self.color = QComboBox()
        self.color.addItem("Белыми", chess.WHITE)
        self.color.addItem("Чёрными", chess.BLACK)
        color = settings.value("player_color", chess.WHITE, type=int)
        self.color.setCurrentIndex(self.color.findData(color))
        layout.addWidget(QLabel("Моя сторона для теста предходов"))
        layout.addWidget(self.color)
        self.flipped = QCheckBox("Перевернуть доску")
        self.flipped.setChecked(settings.value("board_flipped", False, type=bool))
        layout.addWidget(self.flipped)
        self.legal = QCheckBox("Показывать доступные ходы")
        self.legal.setChecked(settings.value("show_legal_moves", True, type=bool))
        layout.addWidget(self.legal)
        self.last = QCheckBox("Подсвечивать последний ход")
        self.last.setChecked(settings.value("show_last_move", True, type=bool))
        layout.addWidget(self.last)
        self.annotations = QCheckBox("Включить ручные стрелки и выделения")
        self.annotations.setChecked(settings.value("annotations_enabled", True, type=bool))
        layout.addWidget(self.annotations)
        self.allow_premoves = QCheckBox("Разрешить предходы")
        self.allow_premoves.setChecked(settings.value("allow_premoves", True, type=bool))
        layout.addWidget(self.allow_premoves)

        layout = add_settings_tab("Звуки")
        sounds_header = QLabel("Звуки приложения")
        sounds_header.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(sounds_header)
        self.sound_moves = QCheckBox("Звуки ходов")
        self.sound_moves.setChecked(settings.value("sound_moves_enabled", True, type=bool))
        layout.addWidget(self.sound_moves)
        self.sound_notifications = QCheckBox("Звуки уведомлений")
        self.sound_notifications.setChecked(settings.value("sound_notifications_enabled", True, type=bool))
        layout.addWidget(self.sound_notifications)
        self.sound_history = QCheckBox("Звуки при просмотре истории")
        self.sound_history.setChecked(settings.value("sound_history_enabled", False, type=bool))
        layout.addWidget(self.sound_history)
        self.sound_selectors: dict[str, QComboBox] = {}
        stored_sound_choices = {
            cue: settings.value(f"sound_selection_{cue}", default_id, type=str)
            for cue, default_id in DEFAULT_SOUND_SELECTIONS.items()
        }
        sound_choices = selected_sound_ids(stored_sound_choices)
        for cue, definition in SOUND_CATALOG["cues"].items():
            row = QHBoxLayout()
            row.addWidget(QLabel(definition["label"]))
            selector = QComboBox()
            selector.setObjectName(f"sound_selection_{cue}")
            selector.setAccessibleName(f"Звук: {definition['label']}")
            for option in definition["options"]:
                selector.addItem(option["label"], option["id"])
            selector.setCurrentIndex(max(0, selector.findData(sound_choices[cue])))
            selector.setToolTip("Выберите запись из встроенной библиотеки звуков.")
            row.addWidget(selector, 1)
            layout.addLayout(row)
            self.sound_selectors[cue] = selector
        self.sound_volume = QSlider(Qt.Orientation.Horizontal)
        self.sound_volume.setRange(0, 100)
        self.sound_volume.setValue(settings.value("sound_volume", 45, type=int))
        self.sound_volume.setAccessibleName("Громкость звуков")
        self.sound_volume_label = QLabel(f"Громкость · {self.sound_volume.value()}%")
        self.sound_volume.valueChanged.connect(
            lambda value: self.sound_volume_label.setText(f"Громкость · {value}%"))
        layout.addWidget(self.sound_volume_label)
        layout.addWidget(self.sound_volume)
        self.sound_start_preview = QPushButton("Прослушать начало партии")
        self.sound_start_preview.setToolTip("Прослушать отдельный сигнал начала новой партии.")
        self.sound_start_preview.clicked.connect(
            lambda: self.sound_preview_requested.emit("game_start"))
        layout.addWidget(self.sound_start_preview)
        self.sound_preview = QPushButton("Послушать ход")
        self.sound_preview.setToolTip("Прослушать натуральную запись обычного хода.")
        self.sound_preview.clicked.connect(lambda: self.sound_preview_requested.emit("move"))
        layout.addWidget(self.sound_preview)
        self.sound_capture_preview = QPushButton("Послушать взятие")
        self.sound_capture_preview.setToolTip("Прослушать отдельную запись взятия фигуры.")
        self.sound_capture_preview.clicked.connect(
            lambda: self.sound_preview_requested.emit("capture"))
        layout.addWidget(self.sound_capture_preview)
        self.sound_availability_note = QLabel(
            "Выбор звуков хранится локально и применяется после сохранения настроек. "
            "Библиотека встроена в приложение; если звук не слышен, проверьте вывод Windows.")
        self.sound_availability_note.setWordWrap(True)
        layout.addWidget(self.sound_availability_note)
        self.sound_classic_preset = QPushButton("Пресет «Классический» · 45%")
        self.sound_classic_preset.clicked.connect(self._set_classic_sound_preset)
        layout.addWidget(self.sound_classic_preset)

        layout = add_settings_tab("Stockfish и анализ")
        layout.addWidget(QLabel("Stockfish для завершённых партий и свободной доски"))
        self.auto_external_analysis = QCheckBox(
            "Автоматически анализировать открытые PGN и позиции")
        self.auto_external_analysis.setChecked(settings.value(
            "analysis/auto_external_studies", True, type=bool))
        self.auto_external_analysis.setToolTip(
            "После явного открытия внешнего PGN или позиции запустить локальный Stockfish "
            "после проверки Fair Play. Материал и оценки остаются временными.")
        layout.addWidget(self.auto_external_analysis)
        engine_row = QHBoxLayout()
        configured_path = settings.value("stockfish_path", "", type=str)
        self.engine_path = QLineEdit(discover_stockfish(configured_path) or configured_path)
        self._engine_verified_path = settings.value("stockfish_verified_path", "", type=str)
        self.engine_path.textChanged.connect(self._engine_path_changed)
        self.engine_path.setPlaceholderText("Найдите, выберите или установите Stockfish")
        engine_row.addWidget(self.engine_path, 1)
        find_engine = QPushButton("Найти")
        find_engine.clicked.connect(self._find_engine)
        engine_row.addWidget(find_engine)
        browse_engine = QPushButton("Обзор…")
        browse_engine.clicked.connect(self._browse_engine)
        engine_row.addWidget(browse_engine)
        layout.addLayout(engine_row)
        self.install_engine = QPushButton("Установить официальный Stockfish 19…")
        self.install_engine.clicked.connect(self.install_requested.emit)
        layout.addWidget(self.install_engine)
        self.engine_test = QPushButton("Проверить движок (до 5 секунд)")
        self.engine_test.clicked.connect(self._test_engine)
        layout.addWidget(self.engine_test)
        self.engine_test_status = QLabel("Проверка запускается только по этой кнопке.")
        self.engine_test_status.setWordWrap(True)
        layout.addWidget(self.engine_test_status)
        license_note = QLabel(
            'Stockfish распространяется по GPL v3. Официальный выпуск и исходный код: '
            '<a href="https://stockfishchess.org/download/">Stockfish download</a> · '
            '<a href="https://github.com/official-stockfish/Stockfish">GitHub</a>. '
            'Установка начинается только по нажатию кнопки выше.')
        license_note.setOpenExternalLinks(True)
        license_note.setWordWrap(True)
        layout.addWidget(license_note)
        self.analysis_preset = QComboBox()
        self.analysis_preset.addItem("Быстрый · 25 000 узлов", "quick")
        self.analysis_preset.addItem("Обычный · 100 000 узлов", "normal")
        self.analysis_preset.addItem("Подробный · 500 000 узлов", "detailed")
        preset = settings.value("analysis_preset", "normal", type=str)
        self.analysis_preset.setCurrentIndex(max(0, self.analysis_preset.findData(preset)))
        self.analysis_preset.currentIndexChanged.connect(self._apply_analysis_preset)
        layout.addWidget(QLabel("Профиль анализа"))
        layout.addWidget(self.analysis_preset)
        self.analysis_limit = QComboBox()
        self.analysis_limit.addItem("Узлы", "nodes")
        self.analysis_limit.addItem("Глубина", "depth")
        self.analysis_limit.addItem("Секунды на позицию", "time")
        limit_kind = settings.value("analysis_limit_kind", "nodes", type=str)
        self.analysis_limit.setCurrentIndex(max(0, self.analysis_limit.findData(limit_kind)))
        layout.addWidget(QLabel("Ограничение на позицию"))
        layout.addWidget(self.analysis_limit)
        self.analysis_limit_value = QSpinBox()
        self.analysis_limit_value.setRange(1, 1_000_000)
        self.analysis_limit_value.setGroupSeparatorShown(True)
        self.analysis_limit_value.setMinimumWidth(150)
        self.analysis_limit_value.setValue(settings.value("analysis_limit_value", 100_000, type=int))
        layout.addWidget(self.analysis_limit_value)
        self.analysis_limit.currentIndexChanged.connect(self._update_limit_range)
        self._update_limit_range()
        self.batch_analysis_nodes = QSpinBox()
        self.batch_analysis_nodes.setRange(1_000, 10_000_000)
        self.batch_analysis_nodes.setSingleStep(10_000)
        self.batch_analysis_nodes.setGroupSeparatorShown(True)
        self.batch_analysis_nodes.setMinimumWidth(170)
        self.batch_analysis_nodes.setSuffix(" узлов на позицию")
        self.batch_analysis_nodes.setValue(settings.value("batch_analysis_nodes", 20_000, type=int))
        layout.addWidget(QLabel("Бюджет нового массового анализа"))
        layout.addWidget(self.batch_analysis_nodes)
        self.batch_analysis_cost = QLabel()
        self.batch_analysis_cost.setWordWrap(True)
        self.batch_analysis_nodes.valueChanged.connect(
            lambda _value: self.batch_analysis_cost.setText(
                f"Оценка вычислительной работы для примера 20 партий × 120 позиций: "
                f"{self.batch_analysis_nodes.value():,} × 120 × 20 узлов. Это не прогноз времени.".replace(",", " ")))
        self.batch_analysis_nodes.valueChanged.emit(self.batch_analysis_nodes.value())
        layout.addWidget(self.batch_analysis_cost)
        self.engine_threads = QSpinBox()
        self.engine_threads.setRange(1, 8)
        self.engine_threads.setValue(settings.value("stockfish_threads", 1, type=int))
        layout.addWidget(QLabel("Потоки Stockfish (1–8)"))
        layout.addWidget(self.engine_threads)
        self.engine_hash = QSpinBox()
        self.engine_hash.setRange(16, 512)
        self.engine_hash.setSuffix(" МБ")
        self.engine_hash.setValue(settings.value("stockfish_hash_mb", 128, type=int))
        layout.addWidget(QLabel("Память Hash (16–512 МБ)"))
        layout.addWidget(self.engine_hash)

        layout = add_settings_tab("Lichess / подключение")
        self.auto_lichess_archive = QCheckBox(
            "Автоматически синхронизировать архив партий после подключения и каждые 10 минут")
        self.auto_lichess_archive.setChecked(
            settings.value("lichess/auto_archive_sync", True, type=bool))
        self.auto_lichess_archive.setToolTip(
            "Продолжает сохранённую начальную загрузку, затем запрашивает только изменения по курсору. "
            "Учитываются готовность игрового потока, активные партии и Retry-After.")
        layout.addWidget(self.auto_lichess_archive)
        self.auto_lichess_puzzles = QCheckBox(
            "Автоматически обновлять сохранённую историю задач Lichess")
        self.auto_lichess_puzzles.setChecked(
            settings.value("lichess/auto_puzzle_history_sync", True, type=bool))
        self.auto_lichess_puzzles.setToolTip(
            "Используется только отдельный ранее сохранённый puzzle:read токен. "
            "Автоматическая авторизация и загрузка новых задач не выполняются.")
        layout.addWidget(self.auto_lichess_puzzles)
        self.auto_lichess_analytics = QCheckBox(
            "Автоматически дозагружать готовую аналитику завершённых партий")
        self.auto_lichess_analytics.setChecked(
            settings.value("lichess/auto_ready_analytics", True, type=bool))
        self.auto_lichess_analytics.setToolTip(
            "Только готовые данные из документированного JSON export Lichess; серверный анализ не заказывается.")
        layout.addWidget(self.auto_lichess_analytics)
        self.pause_lichess_analytics = QCheckBox("Приостановить фоновую проверку партий")
        self.pause_lichess_analytics.setChecked(
            settings.value("lichess/ready_analytics_paused", False, type=bool))
        layout.addWidget(self.pause_lichess_analytics)
        self.lichess_analytics_daily_limit = QSpinBox()
        self.lichess_analytics_daily_limit.setRange(1, 100)
        self.lichess_analytics_daily_limit.setSuffix(" запросов в день")
        self.lichess_analytics_daily_limit.setValue(
            settings.value("lichess/ready_analytics_daily_limit", 25, type=int))
        self.lichess_analytics_daily_limit.setToolTip(
            "Ограничивает отдельные запросы завершённых партий; обычный предел — 25 в день.")
        layout.addWidget(self.lichess_analytics_daily_limit)
        connection_note = QLabel(
            "Автоматическая проверка запускается только после обычного успешного подключения и рабочего потока событий. "
            "Режимы --offline и --manual-connect без явной авторизации не читают учётные данные и не запрашивают сеть.")
        connection_note.setWordWrap(True)
        layout.addWidget(connection_note)
        self.connection_settings_button = QPushButton("Настроить способ подключения Lichess…")
        self.connection_settings_button.clicked.connect(self.connection_settings_requested.emit)
        layout.addWidget(self.connection_settings_button)

        layout = add_settings_tab("Диагностика")
        diagnostics_box = QGroupBox("Диагностика")
        diagnostics_layout = QVBoxLayout(diagnostics_box)
        self.open_logs_button = QPushButton("Открыть папку логов")
        self.open_logs_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_directory()))))
        diagnostics_layout.addWidget(self.open_logs_button)
        self.copy_report_button = QPushButton("Скопировать обезличенный отчёт")
        self.copy_report_button.clicked.connect(self._copy_diagnostic_report)
        diagnostics_layout.addWidget(self.copy_report_button)
        self.share_report = QCheckBox("Разрешаю включить этот отчёт в сообщение поддержки")
        diagnostics_layout.addWidget(self.share_report)
        layout.addWidget(diagnostics_box)
        self._diagnostics_repository = repository
        # Kept as a compatibility/status field; engine probing now uses the
        # shared hidden UCI launcher rather than QProcess.
        self._engine_process: QProcess | None = None
        self._engine_probe_thread: threading.Thread | None = None
        self._engine_probe_generation = 0
        self._engine_probe_cancel = threading.Event()
        self._engine_probe_lock = threading.Lock()
        self._engine_probe_engine = None
        self.engine_probe_result.connect(self._engine_probe_finished)
        self.finished.connect(lambda _result: self._stop_engine_probe())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root_layout.addWidget(buttons)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            maximum_width, maximum_height = max(640, available.width()), max(480, available.height())
            self.setMaximumSize(maximum_width, maximum_height)
            self.resize(min(900, maximum_width), min(650, maximum_height))
        else:
            self.resize(840, 600)

    def _copy_diagnostic_report(self) -> None:
        if not self.share_report.isChecked():
            self.engine_test_status.setText(
                "Сначала отметьте согласие на включение обезличенного отчёта в сообщение.")
            return
        QApplication.clipboard().setText(anonymized_report(
            schema_version=SCHEMA_VERSION,
            engine_verified=bool(self._engine_verified_path and
                                 Path(self._engine_verified_path).is_file()),
            recent_counts=(self._diagnostics_repository.analysis_job_counts()
                           if self._diagnostics_repository is not None else {})))
        self.engine_test_status.setText("Обезличенный диагностический отчёт скопирован.")


    def _set_classic_sound_preset(self) -> None:
        self.sound_moves.setChecked(True)
        self.sound_volume.setValue(45)

    def sound_selection(self, cue: str) -> str:
        selector = self.sound_selectors.get(cue)
        return str(selector.currentData()) if selector is not None else DEFAULT_SOUND_SELECTIONS[cue]

    def save(self, settings: QSettings) -> None:
        settings.setValue("player_color", self.color.currentData())
        settings.setValue("board_flipped", self.flipped.isChecked())
        settings.setValue("show_legal_moves", self.legal.isChecked())
        settings.setValue("show_last_move", self.last.isChecked())
        settings.setValue("annotations_enabled", self.annotations.isChecked())
        settings.setValue("allow_premoves", self.allow_premoves.isChecked())
        settings.setValue("sound_moves_enabled", self.sound_moves.isChecked())
        settings.setValue("sound_notifications_enabled", self.sound_notifications.isChecked())
        settings.setValue("sound_history_enabled", self.sound_history.isChecked())
        settings.setValue("sound_volume", self.sound_volume.value())
        for cue in DEFAULT_SOUND_SELECTIONS:
            settings.setValue(f"sound_selection_{cue}", self.sound_selection(cue))
        settings.setValue("stockfish_path", self.engine_path.text().strip())
        settings.setValue("stockfish_verified_path", self._engine_verified_path)
        settings.setValue("analysis_preset", self.analysis_preset.currentData())
        settings.setValue("analysis_limit_kind", self.analysis_limit.currentData())
        settings.setValue("analysis_limit_value", self.analysis_limit_value.value())
        settings.setValue("analysis/auto_external_studies", self.auto_external_analysis.isChecked())
        settings.setValue("batch_analysis_nodes", self.batch_analysis_nodes.value())
        settings.setValue("stockfish_threads", self.engine_threads.value())
        settings.setValue("stockfish_hash_mb", self.engine_hash.value())
        settings.setValue("lichess/auto_ready_analytics", self.auto_lichess_analytics.isChecked())
        settings.setValue("lichess/auto_archive_sync", self.auto_lichess_archive.isChecked())
        settings.setValue("lichess/auto_puzzle_history_sync", self.auto_lichess_puzzles.isChecked())
        settings.setValue("lichess/ready_analytics_paused", self.pause_lichess_analytics.isChecked())
        settings.setValue("lichess/ready_analytics_daily_limit", self.lichess_analytics_daily_limit.value())
        settings.sync()

    def _browse_engine(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите установленный Stockfish UCI executable", self.engine_path.text(),
            "Программы (*.exe);;Все файлы (*)")
        if path:
            self.engine_path.setText(path)

    def _find_engine(self) -> None:
        path = discover_stockfish(self.engine_path.text().strip())
        if path:
            self.engine_path.setText(path)
            self.engine_test_status.setText(
                "Файл найден. Нажмите «Проверить движок», чтобы выполнить UCI handshake.")
        else:
            self.engine_test_status.setText(
                "В известных пользовательских путях и PATH Stockfish не найден.")

    def _engine_path_changed(self, value: str) -> None:
        self._engine_probe_generation += 1
        if value.strip() != self._engine_verified_path:
            self._engine_verified_path = ""

    def _update_limit_range(self) -> None:
        kind = self.analysis_limit.currentData()
        ranges = {"nodes": (1_000, 100_000_000, 100_000),
                  "depth": (1, 100, 16), "time": (1, 3_600, 5)}
        minimum, maximum, default = ranges.get(kind, ranges["nodes"])
        old_value = self.analysis_limit_value.value()
        self.analysis_limit_value.setRange(minimum, maximum)
        self.analysis_limit_value.setValue(old_value if minimum <= old_value <= maximum else default)

    def _apply_analysis_preset(self) -> None:
        if self.analysis_limit.currentData() != "nodes":
            return
        values = {"quick": 25_000, "normal": 100_000, "detailed": 500_000}
        self.analysis_limit_value.setValue(
            values.get(self.analysis_preset.currentData(), self.analysis_limit_value.value()))

    def analysis_options(self) -> AnalysisOptions:
        kind = self.analysis_limit.currentData()
        value = self.analysis_limit_value.value()
        if kind == "time":
            value *= 1000
        return AnalysisOptions(limit_kind=kind, limit_value=value,
                              threads=self.engine_threads.value(),
                              hash_mb=self.engine_hash.value())

    def _test_engine(self) -> None:
        path = self.engine_path.text().strip()
        if not path:
            self.engine_test_status.setText("Сначала выберите локальный executable.")
            return
        if self._engine_probe_thread is not None and self._engine_probe_thread.is_alive():
            return
        self._engine_probe_cancel = threading.Event()
        self._engine_probe_generation += 1
        generation = self._engine_probe_generation
        self.engine_test.setEnabled(False)
        self.engine_test_status.setText("UCI-проверка идёт в фоне; консольное окно не создаётся…")
        self._engine_verified_path = ""

        def work() -> None:
            engine = None
            try:
                resolved = str(Path(path).expanduser().resolve(strict=True))
                engine = popen_uci_hidden(
                    resolved, timeout=3.0, cwd=str(Path(resolved).parent))
                with self._engine_probe_lock:
                    self._engine_probe_engine = engine
                if self._engine_probe_cancel.is_set():
                    raise RuntimeError("Проверка остановлена.")
                engine.ping()
                if self._engine_probe_cancel.is_set():
                    raise RuntimeError("Проверка остановлена.")
                if shiboken6.isValid(self):
                    self.engine_probe_result.emit(
                        generation, True, str(engine.id.get("name") or "UCI engine"))
            except Exception as exc:
                if shiboken6.isValid(self):
                    self.engine_probe_result.emit(generation, False, str(exc))
            finally:
                if engine is not None:
                    try:
                        engine.quit()
                    except Exception:
                        pass
                with self._engine_probe_lock:
                    if self._engine_probe_engine is engine:
                        self._engine_probe_engine = None

        self._engine_probe_thread = threading.Thread(
            target=work, name="chess-coach-engine-probe", daemon=True)
        self._engine_probe_thread.start()

    def _engine_probe_finished(self, generation: int, success: bool, detail: str) -> None:
        current = generation == self._engine_probe_generation
        if current and success:
            self._engine_verified_path = str(Path(self.engine_path.text().strip()).resolve())
            self.engine_test_status.setText(f"UCI готов: {detail}")
        elif current:
            self._engine_verified_path = ""
            self.engine_test_status.setText(f"UCI-проверка не пройдена: {detail}")
        self.engine_test.setEnabled(True)

    def _stop_engine_probe(self) -> None:
        thread = self._engine_probe_thread
        if thread is None or not thread.is_alive():
            return
        self._engine_probe_generation += 1
        self._engine_probe_cancel.set()
        with self._engine_probe_lock:
            engine = self._engine_probe_engine
        if engine is not None:
            try:
                engine.quit()
            except Exception:
                pass
        thread.join(timeout=0.25)
