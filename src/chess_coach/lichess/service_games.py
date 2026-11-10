"""Interactive Lichess game, challenge and move commands."""

from __future__ import annotations

import random
import threading
import time
import uuid

from .api import LichessError, RateLimitError
from .game_status import ACTIONABLE_GAME_STATUSES, TERMINAL_GAME_STATUSES

_TERMINAL_GAME_STATUSES = TERMINAL_GAME_STATUSES


GAME_ACTION_CONFLICTS = {
    # Draw offers are independent from a deliberate resignation. A server-confirmed
    # incoming offer may be answered while an outgoing offer is stale.
    "offer_draw": frozenset({"offer_draw", "abort", "claim_draw", "takeback"}),
    "accept_draw": frozenset({"offer_draw", "accept_draw", "decline_draw", "resign",
                               "abort", "claim_draw", "claim_victory", "takeback"}),
    "decline_draw": frozenset({"offer_draw", "accept_draw", "decline_draw", "resign",
                                "abort", "claim_draw", "claim_victory", "takeback"}),
    "resign": frozenset({"offer_draw", "accept_draw", "decline_draw", "resign",
                          "abort", "claim_draw", "claim_victory", "takeback"}),
    "abort": frozenset({"offer_draw", "accept_draw", "decline_draw", "resign",
                        "abort", "claim_draw", "claim_victory", "takeback"}),
    "claim_draw": frozenset({"offer_draw", "accept_draw", "decline_draw", "resign",
                             "abort", "claim_draw", "claim_victory", "takeback"}),
    "claim_victory": frozenset({"offer_draw", "accept_draw", "decline_draw", "resign",
                                 "abort", "claim_draw", "claim_victory", "takeback"}),
    "takeback": frozenset({"offer_draw", "accept_draw", "decline_draw", "resign",
                           "abort", "claim_draw", "claim_victory", "takeback"}),
}


class LichessGameActionsMixin:
    def _invalidate_actions_after_rate_limit(self, generation: int) -> None:
        self.invalidate_game_action_admission(generation)
        try:
            remaining = max(1, int(getattr(self.client, "rate_limit_remaining", 0)))
        except (TypeError, ValueError):
            remaining = 60
        self._emit_session_status(
            generation,
            f"Ограничение Lichess (429): повтор через {remaining} с; "
            "ожидаем свежий подтверждённый снимок партии.")

    def mark_event_stream_ready(self, generation: int) -> bool:
        """Record current transport readiness and require fresh game snapshots."""
        generation = int(generation)
        if not self.is_auth_generation_current(generation):
            return False
        with self._pending_action_lock:
            if generation != self._auth_generation or self._connection_stop.is_set():
                return False
            self._event_stream_ready_generation = generation
            # A reconnect does not restore per-game action admission.
            self._game_action_admissions.clear()
            self._accepted_game_full_sequence.clear()
        return True

    def invalidate_game_action_admission(self, generation: int,
                                         game_id: str | None = None, *,
                                         event_stream: bool = False) -> None:
        """Close service-side action admission after transport/safety loss."""
        generation = int(generation)
        with self._pending_action_lock:
            if event_stream and self._event_stream_ready_generation == generation:
                self._event_stream_ready_generation = None
            if game_id is None:
                for key in tuple(self._game_action_admissions):
                    if key[0] == generation:
                        self._game_action_admissions.pop(key, None)
                for key in tuple(self._accepted_game_full_sequence):
                    if key[0] == generation:
                        self._accepted_game_full_sequence.pop(key, None)
            else:
                self._game_action_admissions.pop((generation, str(game_id)), None)
                self._accepted_game_full_sequence.pop((generation, str(game_id)), None)

    def authorize_game_actions(self, game_id: str, generation: int,
                               snapshot_sequence: int, *, color: str) -> bool:
        """Admit commands only after MainWindow accepted a fresh gameFull."""
        generation = int(generation)
        if (color not in {"white", "black"} or int(snapshot_sequence) <= 0
                or not self.is_auth_generation_current(generation)):
            return False
        key = (generation, str(game_id))
        with self._pending_action_lock:
            authoritative = self._game_authoritative_state.get(key) or {}
            if (generation != self.auth_generation
                    or self._event_stream_ready_generation != generation
                    or self._connection_stop.is_set()
                    or authoritative.get("status") not in ACTIONABLE_GAME_STATUSES
                    or self._accepted_game_full_sequence.get(key) != int(snapshot_sequence)):
                return False
            self._game_action_admissions[key] = {
                "snapshot_sequence": int(snapshot_sequence), "color": color,
            }
            return True

    def _game_action_admission_ready_locked(self, generation: int,
                                            game_id: str) -> bool:
        admission = self._game_action_admissions.get((generation, str(game_id)))
        authoritative = self._game_authoritative_state.get((generation, str(game_id))) or {}
        try:
            cooldown = max(0, int(getattr(self.client, "rate_limit_remaining", 0)))
        except (TypeError, ValueError):
            cooldown = 1
        return bool(
            generation == self._auth_generation
            and self._event_stream_ready_generation == generation
            and admission is not None
            and admission.get("snapshot_sequence", 0) > 0
            and authoritative.get("status") in ACTIONABLE_GAME_STATUSES
            and cooldown == 0
            and not self._connection_stop.is_set()
        )

    @staticmethod
    def _own_draw_offer_is_set(authoritative: dict, admission: dict | None) -> bool:
        if not admission:
            return False
        own_key = "wdraw" if admission.get("color") == "white" else "bdraw"
        return authoritative.get(own_key) is True

    def claim_draw(self, game_id: str):
        self._action(lambda token: self.client.claim_draw(token, game_id),
                     game_id=game_id, command="claim_draw")

    def cancel_open_invitation(self, challenge_id: str,
                               expected_generation: int | None = None):
        context = self.capture_operation_context(command="cancel_invitation")
        if expected_generation is not None and expected_generation != context.generation:
            return context.request_id
        def work():
            try:
                token = self.operation_token(context)
                if not token:
                    return
                self.client.cancel_challenge(token, challenge_id)
                if self.is_operation_context_current(context):
                    self.session_invitation_cancelled.emit(
                        context.generation, context.request_id, challenge_id)
                    self.invitation_cancelled.emit(challenge_id)
            except Exception as exc:
                self._emit_operation_failure(context, exc)
        self._thread(work, "chess-coach-cancel-invitation")
        return context.request_id

    def takeback(self, game_id: str, action: str, request_id: str):
        accept = action != "decline"
        context = self.capture_operation_context(
            game_id=game_id, command="takeback", request_id=request_id)
        key = (context.generation, str(game_id), "takeback")
        with self._pending_action_lock:
            if context.connection_stop.is_set():
                self.session_takeback_action_completed.emit(
                    context.generation, str(game_id), request_id, -2)
                return None
            authoritative = self._game_authoritative_state.get(
                (context.generation, str(game_id)), {})
            if (authoritative.get("status") in TERMINAL_GAME_STATUSES
                    or not self._game_action_admission_ready_locked(
                        context.generation, str(game_id))):
                self.session_takeback_action_completed.emit(
                    context.generation, str(game_id), request_id, -2)
                return None
            if key in self._pending_actions:
                return self._pending_actions[key]
            if any(
                    pending_key[0] == context.generation
                    and pending_key[1] == str(game_id)
                    and "takeback" in GAME_ACTION_CONFLICTS.get(pending_key[2], ())
                    for pending_key in self._pending_actions):
                self.session_takeback_action_completed.emit(
                    context.generation, str(game_id), request_id, -2)
                return None
            self._pending_actions[key] = request_id
            game_key = (context.generation, str(game_id))
            baseline = dict(self._game_authoritative_state.get(game_key) or {})
            self._pending_action_states[key] = {
                "context": context, "baseline": baseline, "status": "in_flight",
                "confirmed": False, "takeback_action": action,
            }
        self.session_action_started.emit(
            context.generation, request_id, "takeback", str(game_id))

        def work():
            status = 0
            token = self.operation_token(context)
            with self._pending_action_lock:
                admitted = self._game_action_admission_ready_locked(
                    context.generation, str(game_id))
            try:
                if not token or not admitted:
                    status = -2
                else:
                    self.client.handle_takeback(token, game_id, accept)
            except Exception as exc:
                status = exc.status if isinstance(exc, LichessError) and exc.status else -1
                if status == 429:
                    self._invalidate_actions_after_rate_limit(context.generation)
            definitive = status in {-2, 400, 401, 403, 404, 422}
            with self._pending_action_lock:
                state = self._pending_action_states.get(key)
                if state and state.get("context") is context:
                    if definitive:
                        self._pending_action_states.pop(key, None)
                        self._pending_actions.pop(key, None)
                    else:
                        state["status"] = "awaiting_authoritative_state"
                        state["ack_sequence"] = self._game_authoritative_sequence.get(
                            (context.generation, str(game_id)), 0)
                        state["ack_state"] = dict(self._game_authoritative_state.get(
                            (context.generation, str(game_id)), {}))
                        if state["confirmed"]:
                            self._pending_action_states.pop(key, None)
                            self._pending_actions.pop(key, None)
            if self.is_operation_context_current(context):
                self.session_takeback_action_completed.emit(
                    context.generation, game_id, request_id, status)
                self.takeback_action_completed.emit(game_id, request_id, status)
                self.session_action_completed.emit(
                    context.generation, request_id, "takeback", str(game_id), status)
                if state and not definitive and state.get("confirmed"):
                    self.session_action_reconciled.emit(
                        context.generation, request_id, "takeback", str(game_id))

        self._thread(work, "chess-coach-takeback")

    def resign(self, game_id: str):
        self._action(lambda token: self.client.resign(token, game_id),
                     game_id=game_id, command="resign")

    def _emit_operation_failure(self, context, exc) -> None:
        if not self.is_operation_context_current(context):
            return
        status = exc.status if isinstance(exc, LichessError) and exc.status else 0
        message = str(exc)
        self.session_operation_failed.emit(
            context.generation, context.request_id, context.command,
            context.game_id or "", status, message)
        self.error.emit(message)

    def _action(self, action, *, game_id: str | None = None,
                command: str = "game_action"):
        context = self.capture_operation_context(game_id=game_id, command=command)
        key = (context.generation, str(game_id or ""), str(command))
        with self._pending_action_lock:
            if context.connection_stop.is_set():
                return None
            if key in self._pending_actions:
                return self._pending_actions[key]
            authoritative = self._game_authoritative_state.get(
                (context.generation, str(game_id or "")), {})
            admission = self._game_action_admissions.get(
                (context.generation, str(game_id or "")))
            if (authoritative.get("status") in TERMINAL_GAME_STATUSES
                    or not self._game_action_admission_ready_locked(
                        context.generation, str(game_id or ""))):
                return None
            if command == "offer_draw" and self._own_draw_offer_is_set(
                    authoritative, admission):
                return None
            blocked_by_pending = any(
                pending_key[0] == context.generation
                and pending_key[1] == str(game_id or "")
                and command in GAME_ACTION_CONFLICTS.get(pending_key[2], ())
                for pending_key in self._pending_actions)
            if blocked_by_pending:
                return None
            self._pending_actions[key] = context.request_id
            game_key = (context.generation, str(game_id or ""))
            baseline = dict(self._game_authoritative_state.get(game_key) or {})
            self._pending_action_states[key] = {
                "context": context, "baseline": baseline,
                "status": "in_flight", "confirmed": False,
            }
        self.session_action_started.emit(
            context.generation, context.request_id, context.command, context.game_id or "")
        def work():
            token = None
            result_status = 0
            try:
                with self._pending_action_lock:
                    admitted = self._game_action_admission_ready_locked(
                        context.generation, str(game_id or ""))
                token = self.operation_token(context) if admitted else None
                if not token or not admitted:
                    result_status = -2
                else:
                    action(token)
            except Exception as exc:
                result_status = exc.status if isinstance(exc, LichessError) and exc.status else -1
                if result_status == 429:
                    self._invalidate_actions_after_rate_limit(context.generation)
                self._emit_operation_failure(context, exc)
            definitive_rejection = result_status in {-2, 400, 401, 403, 404, 422}
            with self._pending_action_lock:
                state = self._pending_action_states.get(key)
                if state and state.get("context") is context:
                    if definitive_rejection:
                        self._pending_action_states.pop(key, None)
                        self._pending_actions.pop(key, None)
                    else:
                        state["status"] = "awaiting_authoritative_state"
                        state["ack_sequence"] = self._game_authoritative_sequence.get(
                            (context.generation, str(game_id or "")), 0)
                        state["ack_state"] = dict(self._game_authoritative_state.get(
                            (context.generation, str(game_id or "")), {}))
                        if state["confirmed"]:
                            self._pending_action_states.pop(key, None)
                            self._pending_actions.pop(key, None)
            if self.is_auth_generation_current(context.generation):
                self.session_action_completed.emit(
                    context.generation, context.request_id, command,
                    context.game_id or "", result_status)
                if state and not definitive_rejection and state["confirmed"]:
                    self.session_action_reconciled.emit(
                        context.generation, context.request_id, command,
                        context.game_id or "")
        self._thread(work, "chess-coach-lichess-action")
        return context.request_id

    def has_pending_action(self, game_id: str, command: str) -> bool:
        with self._pending_action_lock:
            return any(key[1:] == (str(game_id), str(command))
                       for key in self._pending_actions)

    def reconcile_game_actions(self, game_id: str, event: dict,
                               generation: int | None = None, *, color: str = "white",
                               resync: bool = False,
                               accepted_line_change: bool = False,
                               snapshot_sequence: int | None = None) -> None:
        """Resolve one-shot POSTs only after a newer authoritative game snapshot."""
        generation = self.auth_generation if generation is None else int(generation)
        state = event.get("state", event) if isinstance(event, dict) else {}
        if not isinstance(state, dict) or event.get("type") not in {"gameFull", "gameState"}:
            return
        game_key = (generation, str(game_id))
        fingerprint = {
            "status": state.get("status"), "moves": str(state.get("moves") or ""),
            "wdraw": state.get("wdraw"), "bdraw": state.get("bdraw"),
            "wtakeback": state.get("wtakeback"), "btakeback": state.get("btakeback"),
            "turns": state.get("turns"), "color": color,
        }
        context = self.capture_operation_context(game_id=game_id)
        if context.generation != generation:
            return
        with self._pending_action_lock:
            if context.connection_stop.is_set():
                return
            previous = self._game_authoritative_state.get(game_key) or {}
            if (previous.get("status") in _TERMINAL_GAME_STATUSES
                    and fingerprint.get("status") not in _TERMINAL_GAME_STATUSES):
                return
            old_moves = tuple(str(previous.get("moves") or "").split())
            new_moves = tuple(str(fingerprint.get("moves") or "").split())
            for flag in ("wdraw", "bdraw"):
                anchor_key = f"_{flag}_anchor_moves"
                previous_anchor = previous.get(anchor_key)
                if previous.get(flag) is True:
                    anchor = (tuple(previous_anchor) if previous_anchor is not None
                              else old_moves)
                    line_progressed_from_offer = (
                        len(new_moves) > len(anchor) and new_moves[:len(anchor)] == anchor)
                    if fingerprint.get(flag) is None:
                        # Missing is unknown. Keep the latch and its original
                        # line anchor so a later explicit false can reconcile.
                        fingerprint[flag] = True
                    elif fingerprint.get(flag) is False and not (
                            line_progressed_from_offer or accepted_line_change or resync):
                        fingerprint[flag] = True
                    fingerprint[anchor_key] = anchor if fingerprint.get(flag) is True else None
                elif fingerprint.get(flag) is True:
                    fingerprint[anchor_key] = new_moves
                elif fingerprint.get(flag) is None and previous.get(flag) is not None:
                    # Preserve a known value only for an unchanged line. On a
                    # later line, the missing field remains explicitly unknown.
                    if new_moves == old_moves:
                        fingerprint[flag] = previous.get(flag)
                        fingerprint[anchor_key] = previous.get(anchor_key)
                    else:
                        fingerprint[anchor_key] = None
                else:
                    fingerprint[anchor_key] = None
            previous_fingerprint = {key: value for key, value in previous.items()
                                    if key != "sequence"}
            fresh = previous_fingerprint != fingerprint
            sequence = self._game_authoritative_sequence.get(game_key, 0)
            if fresh:
                sequence += 1
                self._game_authoritative_sequence[game_key] = sequence
            fingerprint["sequence"] = sequence
            if fresh or not previous:
                self._game_authoritative_state[game_key] = fingerprint
            if (event.get("type") == "gameFull"
                    and type(snapshot_sequence) is int and snapshot_sequence > 0):
                # MainWindow supplies this only after apply_event accepted the
                # full snapshot. Reconnect clears it, so a stale lease cannot be
                # reactivated by a later direct service call.
                self._accepted_game_full_sequence[game_key] = snapshot_sequence
            resolved = []
            for key, action_state in tuple(self._pending_action_states.items()):
                context = action_state.get("context")
                if (not context or context.generation != generation
                        or context.game_id != str(game_id)):
                    continue
                effect_command = context.command
                if context.command == "takeback":
                    effect_command = f"takeback_{action_state.get('takeback_action') or 'offer'}"
                effect_seen = self._action_effect_reconciled(
                    effect_command, action_state.get("baseline") or {}, fingerprint)
                if (not effect_seen and fresh and context.command in {"offer_draw", "takeback"}
                        and action_state["status"] == "awaiting_authoritative_state"
                        and sequence > action_state.get("ack_sequence", sequence)
                        and fingerprint.get("status") in {"created", "started", "playing"}):
                    ack = action_state.get("ack_state") or {}
                    before_moves = str(ack.get("moves") or "").split()
                    after_moves = str(fingerprint.get("moves") or "").split()
                    if context.command == "offer_draw":
                        own_flag = "wdraw" if color == "white" else "bdraw"
                        flag_cleared = (ack.get(own_flag) is True
                                        and fingerprint.get(own_flag) is False)
                        line_advanced = len(after_moves) > len(before_moves)
                        effect_seen = (fingerprint.get(own_flag) is False
                                       and (flag_cleared or line_advanced))
                if not effect_seen:
                    continue
                if action_state["status"] == "in_flight":
                    action_state["confirmed"] = True
                    continue
                self._pending_action_states.pop(key, None)
                self._pending_actions.pop(key, None)
                resolved.append((context.request_id, context.command))
        if self.is_auth_generation_current(generation):
            for request_id, command in resolved:
                self.session_action_reconciled.emit(
                    generation, request_id, command, str(game_id))

    @staticmethod
    def _action_effect_reconciled(command: str, before: dict, after: dict) -> bool:
        status = str(after.get("status") or "")
        terminal = status in TERMINAL_GAME_STATUSES
        if command in {"abort", "resign", "claim_draw", "claim_victory", "accept_draw"}:
            return terminal
        if command == "offer_draw":
            key = "wdraw" if after["color"] == "white" else "bdraw"
            return after.get(key) is True and before.get(key) is not True
        if command == "decline_draw":
            key = "bdraw" if after["color"] == "white" else "wdraw"
            return terminal or (
                before.get(key) is True and after.get(key) is not True)
        if command == "takeback_offer":
            key = "wtakeback" if after["color"] == "white" else "btakeback"
            return (after.get(key) is True and before.get(key) is not True) or (
                len(str(after.get("moves") or "").split())
                < len(str(before.get("moves") or "").split()))
        if command == "takeback_accept":
            return terminal or len(str(after.get("moves") or "").split()) < len(
                str(before.get("moves") or "").split())
        if command == "takeback_decline":
            key = "btakeback" if after["color"] == "white" else "wtakeback"
            return terminal or (before.get(key) is True and after.get(key) is False)
        return False

    def open_friend_game(self, color: str, clock_limit: int | None, increment: int = 0, rated: bool = False):
        context = self.capture_operation_context(command="create_friend_game")
        cancel_event = threading.Event()
        key = (context.generation, context.request_id)
        with self._pending_action_lock:
            for pending in self._pending_friend_challenges.values():
                if pending["context"].generation == context.generation:
                    return pending["context"].request_id
            if context.connection_stop.is_set():
                return None
            self._pending_friend_challenges[key] = {
                "context": context, "cancel_event": cancel_event,
                "challenge_id": None, "phase": "creating",
            }

        def action(token):
            try:
                result = self._create_open(token, color, clock_limit, increment, rated,
                                           request_key=key, context=context)
            except Exception as exc:
                with self._pending_action_lock:
                    pending = self._pending_friend_challenges.get(key)
                    if pending and pending["phase"] == "creating":
                        if getattr(exc, "status", None) in {400, 401, 403, 404, 422}:
                            self._pending_friend_challenges.pop(key, None)
                        else:
                            pending["phase"] = "unknown"
                raise
            with self._pending_action_lock:
                self._pending_friend_challenges.pop(key, None)
            return result

        return self._challenge(action, context=context)

    def friend_request_phase(self, request_id: str, generation: int) -> str | None:
        with self._pending_action_lock:
            pending = self._pending_friend_challenges.get((generation, request_id))
            return pending["phase"] if pending else None

    def _cancel_friend_by_id(self, key, context, token, challenge_id) -> None:
        try:
            if not token or not self.is_operation_context_current(context):
                raise LichessError("Сессия изменилась; отмена не подтверждена.")
            self.client.cancel_challenge(token, challenge_id)
        except Exception as exc:
            status = getattr(exc, "status", None)
            with self._pending_action_lock:
                pending = self._pending_friend_challenges.get(key)
                if pending:
                    pending["phase"] = ("cancel_retry" if status in {401, 403, 429}
                                        else "cancel_unknown")
            raise

    def cancel_pending_friend_challenge(self, request_id: str | None,
                                        expected_generation: int | None = None) -> bool:
        if not request_id:
            return False
        generation = self.auth_generation if expected_generation is None else int(expected_generation)
        key = (generation, str(request_id))
        with self._pending_action_lock:
            pending = self._pending_friend_challenges.get(key)
        if not pending or not self.is_operation_context_current(pending["context"]):
            return False
        context = pending["context"]
        with self._pending_action_lock:
            if self._pending_friend_challenges.get(key) is not pending:
                return False
            phase = pending["phase"]
            if phase in {"creating", "accepting"}:
                pending["cancel_event"].set()
                return True
            if phase != "cancel_retry":
                return False
            pending["phase"] = "cancelling"
            challenge_id = pending["challenge_id"]

        def work():
            try:
                token = self.operation_token(context)
                self._cancel_friend_by_id(key, context, token, challenge_id)
            except Exception as exc:
                if self.is_operation_context_current(context):
                    self.session_challenge_status_changed.emit(
                        generation, str(request_id), f"Не удалось отменить приглашение: {exc}",
                        self.friend_request_phase(str(request_id), generation) == "cancel_retry")
                return
            with self._pending_action_lock:
                self._pending_friend_challenges.pop(key, None)
            if self.is_operation_context_current(context):
                self.session_challenge_status_changed.emit(
                    generation, str(request_id), "Приглашение отменено Lichess.", False)

        self._thread(work, "chess-coach-cancel-friend-invitation")
        return True

    def mark_game_finished(self, game_id: str) -> None:
        """Allow a bounded stream resync to fetch the final authoritative line."""
        with self._event_lock:
            self._final_sync_game_ids.add(game_id)

    def cancel_username_challenge(self):
        stop = self._username_challenge_stop
        challenge_id = self._username_challenge_id
        context = self._username_challenge_context
        if not stop or not context or not self.is_operation_context_current(context):
            return False
        if not challenge_id:
            # The challenge POST is still waiting for its first event. Keep the
            # stream alive until it supplies the server ID, then cancel that ID;
            # stopping the stream here could orphan a successfully-created invite.
            self._username_challenge_cancel_requested = True
            self.session_challenge_status_changed.emit(
                context.generation, context.request_id,
                "Запросили отмену; ждём ID вызова Lichess для подтверждения.", True)
            self.challenge_status_changed.emit(
                "Запросили отмену; ждём ID вызова Lichess для подтверждения.", True)
            return True
        if context and self.is_operation_context_current(context):
            def work():
                cancelled = False
                try:
                    token = self.operation_token(context)
                    if not token:
                        raise LichessError("Соединение Lichess изменилось; повторите отмену после подключения.")
                    self.client.cancel_challenge(token, challenge_id)
                    cancelled = True
                except Exception as exc:
                    if self.is_operation_context_current(context):
                        self.session_challenge_status_changed.emit(
                            context.generation, context.request_id,
                            f"Не удалось отменить вызов: {exc}", True)
                        self.challenge_status_changed.emit(
                            f"Не удалось отменить вызов: {exc}", True)
                if cancelled and self.is_operation_context_current(context):
                    # Do not pass the challenge stream's stop event to the
                    # cancellation request: that would suppress the request
                    # itself in coordinators that skip already-cancelled work.
                    stop.set()
                    self.session_challenge_status_changed.emit(
                        context.generation, context.request_id,
                        "Вызов отменён.", False)
                    self.challenge_status_changed.emit("Вызов отменён.", False)
            self._thread(work, "chess-coach-cancel-invitation")
            return True
        else:
            stop.set()
            return False

    def start_game(self, game_id: str, token: str | None = None,
                   expected_generation: int | None = None) -> None:
        context = self.capture_operation_context(game_id=game_id, command="game_stream")
        if expected_generation is not None and expected_generation != context.generation:
            return
        current_token = self.operation_token(context)
        # Internal auth/restore callers provide an expected generation and must
        # match its captured credential. The explicit token-only signature is
        # retained for compatibility with callers that already captured the
        # immutable token before invoking this method.
        if expected_generation is not None and token is not None and token != current_token:
            return
        token = token if token is not None else current_token
        if not token or not self.is_operation_context_current(context):
            return
        stop = threading.Event()
        with self._auth_lock:
            auth_generation = context.generation
            connection_stop = context.connection_stop
        with self._auth_lock:
            if (auth_generation != self._auth_generation
                    or connection_stop is not self._connection_stop):
                return
            with self._event_lock:
                if game_id in self._game_stops:
                    return
                self._game_stops[game_id] = stop

        def work():
            delay = 1
            final_attempts = 0
            try:
                while not self._stop.is_set() and not connection_stop.is_set() and not stop.is_set():
                    try:
                        for event in self.client.stream_game(token, game_id, stop):
                            if (self._stop.is_set() or connection_stop.is_set() or stop.is_set()
                                    or not self.is_auth_generation_current(auth_generation)):
                                break
                            delay = 1
                            if not self._emit_session_game_event(auth_generation, game_id, event):
                                break
                            state = event.get("state", event)
                            is_terminal = event.get("type") in {"gameFull", "gameState"} and state.get("status") not in {None, "started", "created", "started_soon", "playing"}
                            if is_terminal:
                                with self._event_lock:
                                    self._final_sync_game_ids.discard(game_id)
                            if event.get("type") == "gameFull" and is_terminal:
                                stop.set()
                            if event.get("type") == "gameState" and is_terminal:
                                stop.set()
                        if not self._stop.is_set() and not connection_stop.is_set() and not stop.is_set():
                            raise LichessError("Поток партии завершился.")
                    except RateLimitError as exc:
                        if not self.is_auth_generation_current(auth_generation):
                            break
                        self.invalidate_game_action_admission(auth_generation, game_id)
                        with self._event_lock:
                            final_sync = game_id in self._final_sync_game_ids
                        if final_sync and final_attempts >= 1:
                            self._emit_session_status(auth_generation, f"Финальная синхронизация партии {game_id} ожидает повторного подключения.")
                            break
                        if final_sync:
                            final_attempts += 1
                        self._emit_session_status(
                            auth_generation,
                            f"Ограничение Lichess (429): синхронизация партии через {max(60, exc.retry_after)} с"
                        )
                        connection_stop.wait(max(60, exc.retry_after))
                    except LichessError as exc:
                        if not self.is_auth_generation_current(auth_generation):
                            break
                        if exc.status == 401:
                            connection_stop.set()
                            self._emit_session_error(auth_generation, str(exc))
                            break
                        self.invalidate_game_action_admission(auth_generation, game_id)
                        with self._event_lock:
                            final_sync = game_id in self._final_sync_game_ids
                        if final_sync and final_attempts >= 1:
                            self._emit_session_status(auth_generation, f"Финальная синхронизация партии {game_id} ожидает повторного подключения.")
                            break
                        if final_sync:
                            final_attempts += 1
                        self._emit_session_status(auth_generation, "Потеряно соединение с партией; синхронизация…")
                        connection_stop.wait(delay)
                        delay = min(delay * 2, 30)
            finally:
                with self._event_lock:
                    if self._game_stops.get(game_id) is stop:
                        self._game_stops.pop(game_id, None)
        self._thread(work, f"chess-coach-game-{game_id}")

    def close(self, timeout: float = 1.0) -> bool:
        """Stop workers cooperatively. Return False while any worker still owns resources."""
        with self._thread_lock:
            self._closing = True
        # Do not retain a selected-game retry that could be reconsidered by a
        # worker finalizer during shutdown.
        with self._analysis_backfill_lock:
            self._pending_selected_analysis.clear()
            if self._analysis_backfill_stop is not None:
                self._analysis_backfill_stop.set()
        self._stop.set()
        self._connection_stop.set()
        if self._archive_stop is not None:
            self._archive_stop.set()
        if self._puzzle_stop is not None:
            self._puzzle_stop.set()
        if self._callback:
            self._callback.cancel()
            self._callback = None
        if self._puzzle_callback:
            self._puzzle_callback.cancel()
            self._puzzle_callback = None
        if self._username_challenge_stop:
            self._username_challenge_stop.set()
        for stop in self._game_stops.values():
            stop.set()
        deadline = time.monotonic() + max(0.0, timeout)
        with self._thread_lock:
            threads = list(self._threads)
        for thread in threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        alive = [thread for thread in threads if thread.is_alive()
                 and thread is not threading.current_thread()]
        with self._thread_lock:
            self._threads = alive
        return not alive

    def pending_username_challenge_id(self, request_id: str | None) -> str | None:
        context = self._username_challenge_context
        if context and context.request_id == request_id and self.is_operation_context_current(context):
            return self._username_challenge_id
        return None

    def challenge_username(self, username: str, color: str, clock_limit: int | None, increment: int = 0, rated: bool = False):
        stop = threading.Event()
        self._username_challenge_stop = stop
        self._username_challenge_id = None
        self._username_challenge_cancel_requested = False
        context = self.capture_operation_context(command="username_challenge")
        self._username_challenge_context = context
        def action(token):
            form = {"rated": str(rated).lower(), "color": color, "keepAliveStream": "true"}
            if clock_limit is not None:
                form.update({"clock.limit": clock_limit, "clock.increment": increment})
            if self.is_operation_context_current(context):
                self.session_challenge_status_changed.emit(
                    context.generation, context.request_id,
                    f"Ожидаем ответ от {username}…", True)
                self.challenge_status_changed.emit(f"Ожидаем ответ от {username}…", True)
            try:
                for event in self.client.username_challenge_events(token, username, form, stop):
                    if not self.is_operation_context_current(context):
                        break
                    self.event_received.emit(event)
                    challenge_id = event.get("challenge", {}).get("id")
                    if challenge_id:
                        self._username_challenge_id = challenge_id
                        if self._username_challenge_cancel_requested:
                            try:
                                self.client.cancel_challenge(token, challenge_id)
                            except Exception as exc:
                                # A failed cancellation is not authoritative.
                                # Keep the stream and pending UI alive so the
                                # owner can retry once the API cooldown permits.
                                self._username_challenge_cancel_requested = False
                                message = f"Не удалось отменить вызов: {exc}"
                                self.session_challenge_status_changed.emit(
                                    context.generation, context.request_id, message, True)
                                self.challenge_status_changed.emit(message, True)
                            else:
                                stop.set()
                                self.session_challenge_status_changed.emit(
                                    context.generation, context.request_id,
                                    "Вызов отменён после получения его ID.", False)
                                self.challenge_status_changed.emit(
                                    "Вызов отменён после получения его ID.", False)
                                break
                    kind = event.get("type")
                    if kind == "gameStart":
                        self.session_challenge_status_changed.emit(
                            context.generation, context.request_id,
                            "Вызов принят; партия запускается…", False)
                        self.challenge_status_changed.emit("Вызов принят; партия запускается…", False)
                        game_id = (event.get("game") or {}).get("gameId")
                        return {"id": game_id or self._username_challenge_id}
                    if kind == "challengeDeclined":
                        self.session_challenge_status_changed.emit(
                            context.generation, context.request_id, "Игрок отклонил вызов.", False)
                        self.challenge_status_changed.emit("Игрок отклонил вызов.", False)
                        break
                    if kind == "challengeCanceled":
                        self.session_challenge_status_changed.emit(
                            context.generation, context.request_id, "Вызов отменён.", False)
                        self.challenge_status_changed.emit("Вызов отменён.", False)
                        break
                    done = event.get("done")
                    if done:
                        message = {
                            "accepted": "Игрок принял вызов; партия запускается…",
                            "declined": "Игрок отклонил вызов.",
                            "canceled": "Вызов отменён.",
                        }.get(done, f"Ожидание завершено: {done}.")
                        self.session_challenge_status_changed.emit(
                            context.generation, context.request_id, message, False)
                        self.challenge_status_changed.emit(message, False)
                        if done == "accepted":
                            return {"id": self._username_challenge_id}
                        break
            finally:
                if stop.is_set():
                    if self.is_operation_context_current(context):
                        self.session_challenge_status_changed.emit(
                            context.generation, context.request_id,
                            "Ожидание вызова отменено.", False)
                        self.challenge_status_changed.emit("Ожидание вызова отменено.", False)
                if self._username_challenge_stop is stop:
                    self._username_challenge_stop = None
                    self._username_challenge_id = None
                    self._username_challenge_context = None
                    self._username_challenge_cancel_requested = False
        return self._challenge(action, context=context)

    def check_active_games_for_analysis(self, request_id: str) -> None:
        """Take one explicit server snapshot before a user-started local analysis."""
        context = self.capture_operation_context(command="analysis_safety",
                                                 request_id=request_id)
        def work():
            try:
                token = self.operation_token(context)
                if not token:
                    if self.is_operation_context_current(context):
                        self.analysis_safety_error.emit(
                            request_id, "Токен Lichess недоступен; безопасность анализа не подтверждена.")
                    return
                games = self.client.get_playing_games(token)
                if self.is_operation_context_current(context):
                    self.analysis_safety_games_received.emit(request_id, games)
            except Exception as exc:
                if self.is_operation_context_current(context):
                    self.analysis_safety_error.emit(request_id, str(exc))

        self._thread(work, "chess-coach-analysis-safety")

    def claim_victory(self, game_id: str):
        self._action(lambda token: self.client.claim_victory(token, game_id),
                     game_id=game_id, command="claim_victory")

    def create_ai_game(self, level: int, color: str, clock_limit: int | None, increment: int = 0):
        context = self.capture_operation_context(command="create_ai_game")
        return self._challenge(
            lambda token: self.client.create_ai_challenge(token, level, color, clock_limit, increment),
            context=context)

    def fetch_active_games(self) -> str:
        """Fetch a single account/playing snapshot on explicit user request."""
        context = self.capture_operation_context(command="active_games_snapshot")
        def work():
            try:
                token = self.operation_token(context)
                if not token:
                    if self.is_operation_context_current(context):
                        self.session_active_games_error.emit(
                            context.generation, context.request_id,
                            "Сначала подключите аккаунт Lichess.")
                    return
                games = self.client.get_playing_games(token)
                if self.is_operation_context_current(context):
                    self.session_active_games_received.emit(
                        context.generation, context.request_id, games)
                    self.active_games_received.emit(games)
            except Exception as exc:
                if self.is_operation_context_current(context):
                    message = str(exc)
                    self.session_active_games_error.emit(
                        context.generation, context.request_id, message)
                    self.active_games_error.emit(message)

        self._thread(work, "chess-coach-active-games")
        return context.request_id

    def send_move(self, game_id: str, uci: str, request_id: str):
        context = self.capture_operation_context(game_id=game_id,
                                                command="send_move",
                                                request_id=request_id)
        def work():
            token = None
            try:
                token = self.operation_token(context)
                if not token:
                    if self.is_operation_context_current(context):
                        self.session_move_failed.emit(
                            context.generation, game_id, uci, request_id, 0)
                    return
                self.client.send_move(token, game_id, uci)
                if self.is_operation_context_current(context):
                    self.session_move_accepted.emit(
                        context.generation, game_id, uci, request_id)
            except Exception as exc:
                status = exc.status if isinstance(exc, LichessError) and exc.status else 0
                revoked = False
                if status == 401:
                    revoked = bool(token and self.reject_current_credential_if_matches(context, token))
                if (self.is_operation_context_current(context)
                        or (revoked and self.is_auth_generation_current(context.generation))):
                    self.session_move_failed.emit(
                        context.generation, game_id, uci, request_id, status)
                    self.move_failed.emit(game_id, uci, request_id, status)
        self._thread(work, "chess-coach-send-move")

    def abort(self, game_id: str):
        self._action(lambda token: self.client.abort(token, game_id),
                     game_id=game_id, command="abort")

    def draw(self, game_id: str, accept: bool, *, accepting_offer: bool = False):
        return self._action(
            lambda token: self.client.handle_draw(token, game_id, accept),
            game_id=game_id,
            command=("accept_draw" if accepting_offer else
                     "offer_draw" if accept else "decline_draw"))

    def _create_open(self, token, color, clock_limit, increment, rated, *, request_key, context):
        if color == "random":
            color = random.choice(("white", "black"))
        result = self.client.create_open_challenge(token, color, rated, clock_limit, increment)
        challenge_id = result.get("id")
        if not challenge_id:
            raise LichessError("Lichess не подтвердил ID приглашения.")
        time_control = result.get("timeControl") or {}
        expected_type = "unlimited" if clock_limit is None else "clock"
        valid_control = isinstance(time_control, dict) and time_control.get("type") == expected_type
        if clock_limit is not None:
            valid_control = (valid_control and time_control.get("limit") == clock_limit
                             and time_control.get("increment") == increment)
        friend_url = ((result.get("blackUrl") or result.get("urlBlack")) if color == "white"
                      else (result.get("whiteUrl") or result.get("urlWhite")))
        valid = valid_control and (not rated or result.get("rated") is True) and bool(friend_url)

        with self._pending_action_lock:
            pending = self._pending_friend_challenges.get(request_key)
            if not pending:
                return None
            pending["challenge_id"] = challenge_id
            cancelled = pending["cancel_event"].is_set()
            pending["phase"] = "cancelling" if cancelled or not valid else "accepting"
        if cancelled or not valid:
            self._cancel_friend_by_id(request_key, context, token, challenge_id)
            if not valid and not cancelled:
                return {"id": challenge_id, "cancelled": True,
                        "reason": "Параметры или ссылка не подтверждены Lichess. Приглашение отменено."}
            return {"id": challenge_id, "cancelled": True}

        if not self.is_operation_context_current(context):
            return None
        try:
            result["accept"] = self.client.accept_open_challenge(token, challenge_id, color)
        except Exception as exc:
            # Only a definitive rejection allows rollback. An ambiguous accept
            # may already have joined the invitation on the server.
            rejected = getattr(exc, "status", None) in {400, 401, 403, 404, 422}
            with self._pending_action_lock:
                if self._pending_friend_challenges.get(request_key) is pending:
                    pending["phase"] = "cancelling" if rejected else "accept_unknown"
            if not rejected:
                raise
            self._cancel_friend_by_id(request_key, context, token, challenge_id)
            return {"id": challenge_id, "cancelled": True,
                    "reason": "Lichess отклонил присоединение. Приглашение отменено."}
        result["friendUrl"] = friend_url
        if pending["cancel_event"].is_set():
            result["cancel_too_late"] = True
        return result

    def _challenge(self, action, *, context=None):
        context = context or self.capture_operation_context(command="create_challenge")
        def work():
            try:
                token = self.operation_token(context)
                if not token:
                    if self.is_operation_context_current(context):
                        self.session_operation_failed.emit(
                            context.generation, context.request_id, context.command,
                            context.game_id or "", -2,
                            "Для текущего способа подключения нет доступных учётных данных.")
                    if context.command == "create_friend_game":
                        with self._pending_action_lock:
                            self._pending_friend_challenges.pop(
                                (context.generation, context.request_id), None)
                    return
                result = action(token)
                if (context.command == "create_ai_game"
                        and (not isinstance(result, dict) or not (result.get("id") or result.get("gameId")))):
                    raise LichessError("Lichess не подтвердил ID партии.")
                if isinstance(result, dict) and self.is_operation_context_current(context):
                    self.session_challenge_ready.emit(
                        context.generation, context.request_id, result)
                    self.challenge_ready.emit(result)
            except Exception as exc:
                self._emit_operation_failure(context, exc)
        self._thread(work, "chess-coach-challenge")
        return context.request_id
