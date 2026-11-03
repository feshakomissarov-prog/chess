from chess_coach.application.fair_play import (
    playing_snapshot_allows_analysis,
    refresh_playing_snapshot,
)


AI_GAME = {"gameId": "ai-game", "source": "ai", "opponent": {
    "id": None, "username": "Stockfish level 1", "ai": 1}}
BOT_GAME = {"gameId": "bot-game", "source": "friend", "opponent": {
    "id": "bot-account", "username": "SomeBot", "title": "BOT"}}


def test_analysis_policy_accepts_only_complete_official_ai_snapshot():
    assert playing_snapshot_allows_analysis([AI_GAME]) is True
    assert playing_snapshot_allows_analysis([]) is True
    assert playing_snapshot_allows_analysis([BOT_GAME]) is False
    assert playing_snapshot_allows_analysis([{"gameId": "unknown", "source": "ai"}]) is False
    assert playing_snapshot_allows_analysis([{}]) is None
    assert playing_snapshot_allows_analysis({"games": []}) is None
    assert playing_snapshot_allows_analysis([AI_GAME] * 50) is None
    assert playing_snapshot_allows_analysis([], active_human_session=True) is None


def test_fresh_analysis_snapshot_fails_closed_on_stale_account_and_api_errors():
    class Client:
        def __init__(self, games): self.games, self.calls = games, 0
        def get_playing_games(self, token):
            assert token == "synthetic-token"
            self.calls += 1
            return self.games

    class Service:
        token = "synthetic-token"
        username = "Owner"
        def __init__(self, games): self.client = Client(games)

    service = Service([AI_GAME])
    assert refresh_playing_snapshot(service, service.token, "owner", lambda: True)
    service.client.games = [BOT_GAME]
    assert not refresh_playing_snapshot(service, service.token, "owner", lambda: True)
    assert not refresh_playing_snapshot(service, service.token, "other", lambda: True)
    assert not refresh_playing_snapshot(service, service.token, "owner", lambda: False)
    service.client.get_playing_games = lambda _token: (_ for _ in ()).throw(RuntimeError("429"))
    assert not refresh_playing_snapshot(service, service.token, "owner", lambda: True)


def test_fresh_analysis_snapshot_rechecks_session_after_http_response():
    class Service:
        token = "synthetic-token"
        username = "Owner"
        class Client:
            def get_playing_games(self, _token): return [AI_GAME]
        client = Client()

    checks = iter((True, False))
    assert not refresh_playing_snapshot(Service(), "synthetic-token", "owner",
                                        lambda: next(checks))
