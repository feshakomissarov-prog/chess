from __future__ import annotations

from datetime import date, timedelta

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolTip

from chess_coach.ui.pages.home import PuzzleRatingSparkline


def _points(*, low: int, high: int, days: int):
    today = date.today()
    count = min(days + 1, 8)
    return [
        {"day": (today - timedelta(days=days - round(days * i / (count - 1))))
         .isoformat(),
         "rating": round(low + (high - low) * i / (count - 1)),
         "attempts": (12, 8)}
        for i in range(count)
    ]


@pytest.mark.parametrize(
    "size,low,high,days",
    [((640, 160), 700, 2800, 90), ((260, 130), 1901, 1923, 8),
     ((190, 112), 820, 2480, 60)],
)
def test_rating_sparkline_labels_are_adaptive_and_inside_widget(
        qtbot, size, low, high, days):
    chart = PuzzleRatingSparkline()
    qtbot.addWidget(chart)
    chart.set_points(_points(low=low, high=high, days=days))
    chart.resize(*size)
    chart.show()
    chart.repaint()
    qtbot.wait(10)
    chart.repaint()

    assert 2 <= len(chart._x_label_bounds) <= 4
    assert 2 <= len(chart._y_label_bounds) <= 5
    assert not any("−" in label or label.startswith("-")
                   for label in chart._x_axis_labels)
    for bounds in chart._x_label_bounds + [rect for _value, rect in chart._y_label_bounds]:
        assert chart.rect().contains(bounds)
    for labels in (chart._x_label_bounds,
                   [rect for _value, rect in chart._y_label_bounds]):
        assert all(not first.intersects(second)
                   for i, first in enumerate(labels)
                   for second in labels[i + 1:])
    plot = chart._plot_rect()
    assert all(rect.right() < plot.left()
               for _value, rect in chart._y_label_bounds)


def test_rating_sparkline_past_endpoint_is_date_not_today_and_tooltip_is_exact(qtbot):
    chart = PuzzleRatingSparkline()
    qtbot.addWidget(chart)
    today = date.today()
    points = [
        {"day": (today - timedelta(days=31)).isoformat(), "rating": 1812},
        {"day": (today - timedelta(days=13)).isoformat(), "rating": 1830},
        {"day": (today - timedelta(days=1)).isoformat(), "rating": 1844},
    ]
    chart.set_points(points)
    chart.resize(360, 140)
    chart.show()
    chart.repaint()
    qtbot.wait(10)

    assert date.fromisoformat(points[-1]["day"]).strftime("%d.%m") in chart._x_axis_labels
    assert "сегодня" not in chart._x_axis_labels
    endpoint = chart._vertices()[-1]
    QToolTip.hideText()
    QTest.mouseMove(chart, QPoint(round(endpoint.x()), round(endpoint.y())))
    qtbot.waitUntil(lambda: points[-1]["day"] in QToolTip.text(), timeout=500)
    assert "рейтинг Lichess 1844" in QToolTip.text()
    QToolTip.hideText()
