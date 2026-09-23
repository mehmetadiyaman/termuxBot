"""
state_machine.py — Durum Makinesi Motoru
==========================================
Uygulamanın ekran geçişlerini State-Transition modeli ile yönetir.
Her state, beklenen validasyon kuralları, yapılacak action'lar ve
olası geçişleri tanımlar.

Sorumluluklar:
  • State, Action, Transition veri yapıları
  • StateMachine — state geçiş motoru
  • Katmanlı doğrulama (L0/L1/L2) ile dump optimizasyonu
  • Transition retry ve hata kurtarma
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

from command_runner import CommandRunner
from ui_bridge import UIBridge
from screen_validator import ScreenValidator, ValidationLevel, ValidationResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
MAX_TRANSITION_RETRIES: int = 3
TRANSITION_RETRY_WAIT: float = 1.0
POST_ACTION_SETTLE_MS: float = 500      # Action sonrası UI settle bekleme (ms)


# ---------------------------------------------------------------------------
# Action Tipleri
# ---------------------------------------------------------------------------
class ActionType(Enum):
    """Bir state'te yapılabilecek action tipleri."""

    TAP_TEXT = auto()           # Text'e göre element bul ve tıkla
    TAP_DESC = auto()           # Content-desc'e göre element bul ve tıkla
    TAP_ID = auto()             # Resource-id'ye göre element bul ve tıkla
    TAP_COORD = auto()          # Sabit koordinata tıkla (L0)
    INPUT_TEXT = auto()         # Metin gir
    KEYEVENT = auto()           # Keyevent gönder (BACK, HOME, vb.)
    SWIPE = auto()              # Kaydırma hareketi
    WAIT = auto()               # Belirtilen süre bekle (ms)
    DISMISS_KEYBOARD = auto()   # Klavyeyi kapat
    CUSTOM = auto()             # Özel callback fonksiyonu


@dataclass
class Action:
    """
    Bir state'te yapılacak tek bir işlem.

    Örnekler:
        Action(ActionType.TAP_TEXT, target="Sign In")
        Action(ActionType.TAP_COORD, target=(540, 1200))
        Action(ActionType.INPUT_TEXT, target="user@example.com")
        Action(ActionType.KEYEVENT, target=4)  # BACK
        Action(ActionType.WAIT, target=2000)    # 2 saniye bekle
        Action(ActionType.CUSTOM, callback=my_function)
    """

    action_type: ActionType
    target: Any = None                      # Text, koordinat, keycode, süre vb.
    callback: Optional[Callable] = None     # CUSTOM action için
    description: str = ""                   # İnsan okunur açıklama (loglama)
    dismiss_keyboard_after: bool = False    # Action sonrası klavyeyi kapat

    def __repr__(self) -> str:
        label = self.description or f"{self.action_type.name}({self.target})"
        return f"Action({label})"


# ---------------------------------------------------------------------------
# State Tanımı
# ---------------------------------------------------------------------------
@dataclass
class Transition:
    """
    Bir state'ten diğerine geçiş kuralı.

    Attributes:
        target_state:   Hedef state adı.
        condition:      Geçiş koşulu — None ise default (tek çıkış).
                        String ise Activity adı eşleşmesi.
                        Callable ise özel koşul fonksiyonu.
        priority:       Birden fazla geçiş eşleşirse, düşük priority kazanır.
    """

    target_state: str
    condition: Optional[str | Callable[..., bool]] = None
    priority: int = 0

    def __repr__(self) -> str:
        cond = self.condition if self.condition else "default"
        return f"→ {self.target_state} (when: {cond})"


@dataclass
class State:
    """
    Durum makinesindeki tek bir state (ekran/adım).

    Attributes:
        name:               Benzersiz state adı (ör: "login_screen").
        validation_level:   Bu state'e giriş doğrulama seviyesi.
        expected_activity:  L1 doğrulama için beklenen Activity adı.
        expected_texts:     L2 doğrulama için ekranda beklenen text'ler.
        actions:            Bu state'te yapılacak action listesi (sıralı).
        transitions:        Olası geçiş kuralları.
        pre_action_wait:    Action'lardan önce bekleme süresi (ms).
        post_action_wait:   Action'lardan sonra bekleme süresi (ms).
        on_enter:           State'e girildiğinde çağrılacak callback.
        on_exit:            State'ten çıkıldığında çağrılacak callback.
    """

    name: str
    validation_level: ValidationLevel = ValidationLevel.L1_ACTIVITY
    expected_activity: str = ""
    expected_texts: list[str] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    pre_action_wait: float = 0              # ms
    post_action_wait: float = POST_ACTION_SETTLE_MS   # ms
    on_enter: Optional[Callable] = None
    on_exit: Optional[Callable] = None
    is_terminal: bool = False               # Bu state akışın sonu mu?

    def __repr__(self) -> str:
        return f"State('{self.name}', {len(self.actions)} actions, {len(self.transitions)} transitions)"

    @property
    def default_transition(self) -> Optional[Transition]:
        """Koşulsuz (default) geçişi döndürür."""
        for t in sorted(self.transitions, key=lambda x: x.priority):
            if t.condition is None:
                return t
        return None


# ---------------------------------------------------------------------------
# State Machine Olayları (Event Callbacks)
# ---------------------------------------------------------------------------
@dataclass
class MachineEvent:
    """State Machine tarafından tetiklenen bir olay."""

    event_type: str             # "state_enter", "state_exit", "transition", "error"
    state_name: str
    details: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Hatalar
# ---------------------------------------------------------------------------
class TransitionError(Exception):
    """Beklenen state'e geçiş sağlanamadı."""

    def __init__(self, from_state: str, expected: str, actual: str, message: str = ""):
        self.from_state = from_state
        self.expected = expected
        self.actual = actual
        super().__init__(
            message or f"Transition failed: '{from_state}' → '{expected}' (actual: '{actual}')"
        )


class StateNotFoundError(Exception):
    """Tanımlanmamış bir state'e geçiş istendi."""
    pass


class ValidationError(Exception):
    """State doğrulaması başarısız."""
    pass


# ---------------------------------------------------------------------------
# StateMachine
# ---------------------------------------------------------------------------
class StateMachine:
    """
    Durum makinesi motoru — state'ler arası geçişi yönetir.

    Kullanım:
        runner = CommandRunner()
        ui = UIBridge(runner)
        validator = ScreenValidator(runner)
        machine = StateMachine(runner, ui, validator)

        # State'leri tanımla
        machine.add_state(State(
            name="login_screen",
            expected_activity=".ui.LoginActivity",
            actions=[Action(ActionType.TAP_TEXT, target="Sign In")],
            transitions=[Transition(target_state="otp_screen")],
        ))
        machine.add_state(State(
            name="otp_screen",
            expected_activity=".ui.OTPActivity",
            actions=[...],
            transitions=[Transition(target_state="home_screen")],
            is_terminal=True,
        ))

        # Çalıştır
        machine.set_initial_state("login_screen")
        machine.run()
    """

    def __init__(
        self,
        runner: CommandRunner,
        ui: UIBridge,
        validator: ScreenValidator,
    ) -> None:
        self.runner = runner
        self.ui = ui
        self.validator = validator

        self._states: dict[str, State] = {}
        self._current_state: Optional[State] = None
        self._history: list[str] = []               # Geçilen state geçmişi
        self._event_log: list[MachineEvent] = []     # Olay logu
        self._running: bool = False

    # ----- State Yönetimi --------------------------------------------------

    def add_state(self, state: State) -> None:
        """State makinesine yeni bir state ekler."""
        if state.name in self._states:
            logger.warning("State '%s' is being overwritten", state.name)
        self._states[state.name] = state
        logger.debug("State added: %s", state)

    def add_states(self, states: list[State]) -> None:
        """Birden fazla state'i toplu ekler."""
        for state in states:
            self.add_state(state)

    def get_state(self, name: str) -> State:
        """İsimle state döndürür. Bulamazsa StateNotFoundError."""
        if name not in self._states:
            raise StateNotFoundError(f"State '{name}' not found")
        return self._states[name]

    @property
    def current_state(self) -> Optional[State]:
        return self._current_state

    @property
    def state_history(self) -> list[str]:
        return list(self._history)

    @property
    def is_running(self) -> bool:
        return self._running

    # ----- Execution -------------------------------------------------------

    def set_initial_state(self, state_name: str) -> None:
        """Başlangıç state'ini ayarlar (run() öncesi çağrılmalı)."""
        self._current_state = self.get_state(state_name)
        self._history = [state_name]
        logger.info("Initial state set: '%s'", state_name)

    def run(self) -> None:
        """
        State makinesini çalıştırır.

        Başlangıç state'inden itibaren her state'in action'larını yürütür
        ve transition kurallarına göre sonraki state'e geçer.
        Terminal state'e ulaşınca veya geçiş bulunamazsa durur.
        """
        if self._current_state is None:
            raise RuntimeError("Initial state not set. Call set_initial_state() first.")

        self._running = True
        logger.info("═══ State Machine started at '%s' ═══", self._current_state.name)

        try:
            while self._running and self._current_state is not None:
                state = self._current_state

                # 1. State'e giriş doğrulaması
                if not self._validate_state(state):
                    raise ValidationError(
                        f"State '{state.name}' validation failed"
                    )

                # 2. on_enter callback
                if state.on_enter:
                    logger.debug("Calling on_enter for '%s'", state.name)
                    state.on_enter(self)

                self._emit_event("state_enter", state.name)

                # 3. Pre-action bekleme
                if state.pre_action_wait > 0:
                    time.sleep(state.pre_action_wait / 1000)

                # 4. Action'ları sırasıyla yürüt
                self._execute_actions(state)

                # 5. Post-action bekleme
                if state.post_action_wait > 0:
                    time.sleep(state.post_action_wait / 1000)

                # 6. Terminal state kontrolü
                if state.is_terminal:
                    logger.info(
                        "═══ Reached terminal state: '%s' ═══", state.name
                    )
                    self._emit_event("terminal", state.name)
                    break

                # 7. on_exit callback
                if state.on_exit:
                    logger.debug("Calling on_exit for '%s'", state.name)
                    state.on_exit(self)

                self._emit_event("state_exit", state.name)

                # 8. Transition — sonraki state'e geç
                self._transition(state)

        except (TransitionError, ValidationError, StateNotFoundError) as exc:
            logger.error("State Machine error: %s", exc)
            self._emit_event("error", self._current_state.name if self._current_state else "?", {"error": str(exc)})
            raise
        finally:
            self._running = False
            logger.info(
                "═══ State Machine stopped. History: %s ═══",
                " → ".join(self._history),
            )

    def stop(self) -> None:
        """State makinesini dışarıdan durdurur."""
        self._running = False
        logger.info("State Machine stop requested")

    # ----- State Doğrulama ------------------------------------------------

    def _validate_state(self, state: State) -> bool:
        """
        State'e giriş doğrulamasını katmanına göre yapar.

        L0: Doğrulama yok → her zaman True.
        L1: Activity adı kontrolü (dumpsys, ~50ms).
        L2: Tam dump + text kontrolü (~500ms).
        """
        level = state.validation_level

        if level == ValidationLevel.L0_BLIND:
            logger.debug("L0 validation — skip for '%s'", state.name)
            return True

        if level == ValidationLevel.L1_ACTIVITY:
            if not state.expected_activity:
                logger.debug(
                    "L1 requested but no expected_activity for '%s' — skip",
                    state.name,
                )
                return True

            result = self.validator.expect_activity(state.expected_activity)
            if result:
                logger.debug(
                    "L1 OK [%.0fms]: '%s' ← %s",
                    result.elapsed_ms, state.name, result.actual,
                )
                return True

            # L1 başarısız → L2'ye yüksel
            logger.info(
                "L1 MISS for '%s': expected='%s' actual='%s' → escalating to L2",
                state.name, state.expected_activity, result.actual,
            )
            # Aşağıya düş (L2 kontrolüne devam)

        # L2: Tam dump + text doğrulama
        if state.expected_texts:
            xml = self.ui.dump_ui(force=True)
            if xml is None:
                logger.error("L2 validation: dump failed for '%s'", state.name)
                return False

            for expected_text in state.expected_texts:
                elem = self.ui.find_in_cache(expected_text)
                if elem is None:
                    logger.info(
                        "L2 MISS: text '%s' not found on screen for state '%s'",
                        expected_text, state.name,
                    )
                    return False

            logger.debug("L2 OK: all expected texts found for '%s'", state.name)
            return True

        # Hiçbir kural tanımlanmamış — geç
        return True

    # ----- Action Yürütme ------------------------------------------------

    def _execute_actions(self, state: State) -> None:
        """State'in action listesini sırasıyla yürütür."""
        for i, action in enumerate(state.actions):
            logger.info(
                "[%s] Action %d/%d: %s",
                state.name, i + 1, len(state.actions), action,
            )

            self._execute_single_action(action)

            # Klavye kapatma
            if action.dismiss_keyboard_after:
                time.sleep(0.2)     # Klavye animasyonu için kısa bekleme
                self.runner.dismiss_keyboard()
                time.sleep(0.3)     # UI settle

    def _execute_single_action(self, action: Action) -> None:
        """Tek bir action'ı tipine göre yürütür."""
        at = action.action_type

        if at == ActionType.TAP_TEXT:
            success = self.ui.tap_element_by_text(str(action.target))
            if not success:
                logger.warning("TAP_TEXT failed: '%s' not found", action.target)

        elif at == ActionType.TAP_DESC:
            success = self.ui.tap_element_by_desc(str(action.target))
            if not success:
                logger.warning("TAP_DESC failed: '%s' not found", action.target)

        elif at == ActionType.TAP_ID:
            success = self.ui.tap_element_by_id(str(action.target))
            if not success:
                logger.warning("TAP_ID failed: '%s' not found", action.target)

        elif at == ActionType.TAP_COORD:
            x, y = action.target
            self.runner.tap(x, y)

        elif at == ActionType.INPUT_TEXT:
            self.runner.input_text(str(action.target))

        elif at == ActionType.KEYEVENT:
            self.runner.keyevent(int(action.target))

        elif at == ActionType.SWIPE:
            x1, y1, x2, y2, *rest = action.target
            duration = rest[0] if rest else 300
            self.runner.swipe(x1, y1, x2, y2, duration)

        elif at == ActionType.WAIT:
            wait_ms = float(action.target)
            time.sleep(wait_ms / 1000)

        elif at == ActionType.DISMISS_KEYBOARD:
            self.runner.dismiss_keyboard()

        elif at == ActionType.CUSTOM:
            if action.callback:
                action.callback(self)
            else:
                logger.warning("CUSTOM action has no callback")

        else:
            logger.error("Unknown action type: %s", at)

    # ----- State Geçişi ---------------------------------------------------

    def _transition(self, state: State) -> None:
        """
        Mevcut state'ten sonraki state'e geçiş yapar.

        Geçiş kuralları priority sırasıyla değerlendirilir.
        Eşleşen ilk kural uygulanır. Hiçbir kural eşleşmezse
        default transition kullanılır.
        """
        next_state_name: Optional[str] = None

        # Koşullu geçişleri değerlendir
        sorted_transitions = sorted(state.transitions, key=lambda t: t.priority)

        for transition in sorted_transitions:
            if transition.condition is None:
                # Default transition — en son değerlendir
                continue

            if isinstance(transition.condition, str):
                # Activity adı eşleşmesi
                result = self.validator.expect_activity(transition.condition)
                if result.passed:
                    next_state_name = transition.target_state
                    logger.info(
                        "Transition matched: '%s' → '%s' (activity: %s)",
                        state.name, next_state_name, transition.condition,
                    )
                    break

            elif callable(transition.condition):
                # Özel koşul fonksiyonu
                if transition.condition(self):
                    next_state_name = transition.target_state
                    logger.info(
                        "Transition matched: '%s' → '%s' (custom condition)",
                        state.name, next_state_name,
                    )
                    break

        # Koşullu geçiş bulunamadıysa → default
        if next_state_name is None:
            default = state.default_transition
            if default is None:
                logger.warning(
                    "No transition found from '%s' — stopping", state.name
                )
                self._running = False
                return
            next_state_name = default.target_state
            logger.info(
                "Default transition: '%s' → '%s'",
                state.name, next_state_name,
            )

        # Geçişi uygula (retry mekanizması ile)
        self._apply_transition(state.name, next_state_name)

    def _apply_transition(
        self,
        from_state: str,
        to_state: str,
    ) -> None:
        """
        State geçişini uygular ve doğrular.

        Başarısız olursa retry yapar. Max retry aşılırsa TransitionError.
        """
        target = self.get_state(to_state)

        for attempt in range(1, MAX_TRANSITION_RETRIES + 1):
            self._current_state = target
            self._history.append(to_state)

            self._emit_event("transition", to_state, {
                "from": from_state,
                "attempt": attempt,
            })

            # Doğrulama — hedef state'in validation'ını çalıştır
            if self._validate_state(target):
                logger.info(
                    "Transition OK: '%s' → '%s' (attempt %d)",
                    from_state, to_state, attempt,
                )
                # Cache invalidate — yeni ekrana geçtik
                self.ui.invalidate_cache()
                return

            # Doğrulama başarısız — retry
            logger.warning(
                "Transition validation failed: '%s' → '%s' (attempt %d/%d)",
                from_state, to_state, attempt, MAX_TRANSITION_RETRIES,
            )

            if attempt < MAX_TRANSITION_RETRIES:
                time.sleep(TRANSITION_RETRY_WAIT * attempt)
                # Geçiş tekrar denemesi — belki ekran henüz yüklenmedi
                self.ui.invalidate_cache()

        # Tüm denemeler başarısız
        actual_activity = self.validator.get_current_activity() or "<unknown>"
        raise TransitionError(
            from_state=from_state,
            expected=to_state,
            actual=actual_activity,
        )

    # ----- Event Mekanizması -----------------------------------------------

    def _emit_event(
        self,
        event_type: str,
        state_name: str,
        details: Optional[dict] = None,
    ) -> None:
        """Olay loguna kayıt ekler."""
        event = MachineEvent(
            event_type=event_type,
            state_name=state_name,
            details=details or {},
        )
        self._event_log.append(event)

    @property
    def events(self) -> list[MachineEvent]:
        """Tüm olay logunu döndürür."""
        return list(self._event_log)
