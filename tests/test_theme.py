from chess_coach.ui.theme import TOKENS, contrast_ratio, stylesheet


def test_m46_theme_matches_approved_dark_tokens():
    assert TOKENS.app == "#14171C"
    assert TOKENS.sidebar == "#191C22"
    assert TOKENS.surface == "#1F232A"
    assert TOKENS.accent == "#C6A477"
    assert TOKENS.board_light == "#C9B79D"
    assert TOKENS.board_dark == "#78675C"
    css = stylesheet()
    assert "QFrame#appSidebar" in css
    assert "QPushButton#navItem" in css


def test_primary_and_secondary_text_meet_normal_text_contrast_on_surfaces():
    for foreground in (TOKENS.text, TOKENS.secondary):
        for background in (TOKENS.app, TOKENS.sidebar, TOKENS.surface):
            assert contrast_ratio(foreground, background) >= 4.5

