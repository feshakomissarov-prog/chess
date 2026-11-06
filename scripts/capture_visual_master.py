from pathlib import Path
import os
import time
from hashlib import sha256
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from chess_coach.application.dashboard import dashboard_options, dashboard_snapshot
from chess_coach.bootstrap import create_runtime
from chess_coach.window import MainWindow
app=QApplication([])
assert app.platformName()=="windows"
out = Path(os.environ.get("CC_VISUAL_OUTPUT", "visual-evidence")).resolve()
out.mkdir(parents=True, exist_ok=True)
runtime = create_runtime(offline_mode=True)
repo = runtime.repository
runtime.settings.setValue("workspace/selected_account", "lichess:synthetic")
line="e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7 f1e1 b7b5 a4b3 d7d6 c2c3 e8g8"
scores=(15,45,20,52,23,70,39,95,120,85,105,97,141,188,162,201,265)
for i in range(8):
    repo.save_snapshot(str(i),"startpos",line,"white",
        ("Synthetic Owl","Synthetic Fox","Synthetic Rook")[i%3],False,"180+2",
        "resign", "white" if i%3 else "black",started_at=f"2026-11-0{1+i%6}T18:30:00+00:00")
repo.wait_for_writes()
for game_id in ("5", "4", "3"):
    rev=repo.current_line(game_id)
    repo.save_analysis(game_id,line_revision=rev["line_revision"],
        line_hash=rev["line_hash"],source="stockfish",engine_name="SyntheticFixture",
        engine_version="test",parameters={"nodes":100},assessments=[
            {"ply":ply,"white_cp":cp if game_id != "3" else -cp,"depth":16}
            for ply,cp in enumerate(scores)],classifications=[])
repo.wait_for_writes()
account=repo.ensure_archive_account("Synthetic")
from datetime import datetime, timedelta, timezone
today=datetime.now(timezone.utc)
repo.save_puzzle_player_rating(account, [[d.year,d.month-1,d.day,1640+i*9+(i%4)*13]
    for i in range(20) for d in [today-timedelta(days=60-3*i)]])
with repo._connection() as db:
    db.executemany("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) VALUES (?,?,?,?,?,?)",
        [(account,str(i),str(i),today.isoformat(),"white",("Synthetic Owl","Synthetic Fox","Synthetic Rook")[i%3]) for i in range(8)])
    db.execute("UPDATE games SET source='lichess_archive',lichess_accuracy_white=87.4 WHERE game_id='5'")
assert repo.materialize_game_accuracy_batch()["processed"] == 3
candidates=repo.list_games_needing_fast_analysis(engine_version="fixture-queue",parameters={"nodes":100})
repo.enqueue_analysis_jobs(candidates,analysis_pass="fast",engine_version="fixture-queue",parameters={"nodes":100})
for state in ("complete", "complete", "failed", "paused"):
    job=repo.claim_next_analysis_job("fixture",engine_version="fixture-queue",analysis_pass="fast")
    assert job
    repo.finish_analysis_job(job["job_id"],"fixture",status=state)
class VisualFixtureWindow(MainWindow):
    def _refresh_home_dashboard(self):
        # Exercise the production renderer with a fixed real SQLite snapshot.
        # The visual harness verifies layout and navigation independently of
        # the dashboard worker's asynchronous account-selection tests.
        pass


w = VisualFixtureWindow(runtime=runtime)
w.resize(1672, 941)
w.show()
try:
    assert QTest.qWaitForWindowExposed(w, 5000)


    def wait_for(condition):
        deadline = time.monotonic() + 15
        while not condition():
            assert time.monotonic() < deadline, "Timed out waiting for synthetic data in the visible window"
            QTest.qWait(30)


    w._home_view.set_selector_options(dashboard_options(repo))
    w._dashboard_options_loaded = True
    selected = w.dashboard_account.findData(account)
    assert selected >= 0, "Synthetic account is absent from the selector"
    w.dashboard_account.blockSignals(True)
    w.dashboard_account.setCurrentIndex(selected)
    w.dashboard_account.blockSignals(False)
    snapshot = dashboard_snapshot(repo, account_id=account)
    assert snapshot["puzzle_player_rating"].get("latest")
    geometry = []
    routes = (
        ("01-home", lambda: QTest.mouseClick(w._nav_buttons["home"], Qt.MouseButton.LeftButton)),
        ("02-games", lambda: QTest.mouseClick(w._nav_buttons["games"], Qt.MouseButton.LeftButton)),
        ("03-background", lambda: QTest.mouseClick(w.library_page.background_button,
                                                   Qt.MouseButton.LeftButton)),
    )
    for width, height in ((1672, 941), (1360, 860), (1280, 720)):
        w.resize(width, height)
        QTest.qWait(300)
        for name, route in routes:
            assert w.isVisible(), "visible smoke window was closed"
            route()
            QTest.qWait(300)
            if name == "01-home":
                w._render_dashboard_snapshot(snapshot)
                QTest.qWait(30)
                assert w._home_view.puzzle_rating.text().isdigit()
                assert w._dashboard_metric_values["accuracy"].text().endswith("%")
                if width == 1672:
                    cards = [w._home_view.dashboard_metrics[key] for key in ("games", "accuracy")]
                    widths = [card.width() for card in (*cards, w.home_progress_card)]
                    assert max(widths) - min(widths) <= 4, widths
            if name == "02-games":
                lib = w.library_page
                wait_for(lambda: lib.detail_open.isVisible() and lib.detail_open.isEnabled()
                         and lib.export_game.isVisible())
            if name == "03-background":
                wait_for(lambda: w._background_operations_page.isVisible()
                         and w._background_operations_page.workflow_progress["stockfish"].value() > 0)
            capture = w.grab()
            filename = f"{width}x{height}-{name}.png"
            assert capture.save(str(out / filename))
            line = (f"{filename}: window {w.width()}x{w.height()} logical px, "
                    f"capture {capture.width()}x{capture.height()} px, DPR {capture.devicePixelRatio()}")
            geometry.append(line)
            print(line)
finally:
    w.close()
    app.processEvents()
refs = Path(__file__).resolve().parent.parent / "tests" / "visual" / "reference"
expected = (
    ("01_HOME", "01-home", "45066d18a5ba324dff62a2001059cfe0131c293d9cb46eeb07b3f19e38885c52"),
    ("02_MY_GAMES", "02-games", "42df53480ffea4dd720638b753c7f7ca798af3f2bf4efabb907be19671ef5cbe"),
    ("03_BACKGROUND", "03-background", "87e37894fb09832cdfaa0190b6816cae8679098194459d9780f1d17c7b7ab67a"),
)
for art, screen, checksum in expected:
    path = refs / f"{art}_APPROVED.png"
    assert sha256(path.read_bytes()).hexdigest() == checksum
    images = [QImage(str(path)), QImage(str(out / f"1672x941-{screen}.png"))]
    assert not any(image.isNull() for image in images)
    width = sum(image.width() for image in images) + 12
    canvas = QImage(width, max(image.height() for image in images), QImage.Format.Format_RGB32)
    canvas.fill(QColor("#43465a"))
    painter = QPainter(canvas)
    painter.drawImage(0, 0, images[0])
    painter.drawImage(images[0].width() + 12, 0, images[1])
    painter.end()
    assert canvas.save(str(out / f"pair-{screen}.png"))
(out / "capture.txt").write_text(
    f"SHA: {os.environ.get('CC_VISUAL_SHA', 'local working tree')}\n"
    f"Platform: {app.platformName()} · QT_SCALE_FACTOR={os.environ.get('QT_SCALE_FACTOR', '1')}\n"
    "Left: approved reference. Right: visible Windows Qt application, synthetic SQLite.\n"
    + "\n".join(geometry) + "\n",
    encoding="utf-8",
)
