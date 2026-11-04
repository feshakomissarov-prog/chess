"""Independent page widgets used by the root window shell."""

from .home import HomePage
from .library import LibraryPage
from .play import PlayPage
from .puzzles import PuzzleHistoryPage
from .result import ResultPage
from .position_setup import PositionSetupWidget

__all__ = ["HomePage", "LibraryPage", "PlayPage", "PuzzleHistoryPage", "ResultPage",
           "PositionSetupWidget"]
