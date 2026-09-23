"""
flow_runner.py — Flow Orkestratör
===================================
State Machine, UIBridge, ScreenValidator ve AppLifecycle modüllerini
bir araya getirerek uçtan uca otomasyon akışlarını yönetir.

Sorumluluklar:
  • Tüm alt modülleri başlatma ve bağlama
  • Flow tanımlarını (state zincirleri) yürütme
  • Reset → Run → Verify döngüsü
  • Loglama konfigürasyonu
  • Hata raporlama ve özet
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from command_runner import CommandRunner
from ui_bridge import UIBridge
from screen_validator import ScreenValidator
from state_machine import (
    StateMachine,
    State,
    Action,
    ActionType,
    Transition,
    TransitionError,
    StateNotFoundError,
    ValidationError,
)
from app_lifecycle import AppLifecycle, ClearMode, LifecycleResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DEFAULT_FLOW_TIMEOUT: float = 300.0    # Bir flow için max süre (5 dk)


# ---------------------------------------------------------------------------
# Veri Yapıları
# ---------------------------------------------------------------------------
@dataclass
class FlowResult:
    """Bir flow çalıştırmasının sonucu."""

    success: bool
    flow_name: str
    states_visited: list[str]
    total_elapsed_ms: float
    error: Optional[str] = None

    def __bool__(self) -> bool:
        return self.success

    @property
    def summary(self) -> str:
        """İnsan okunur özet."""
        status = "✓ OK" if self.success else "✗ FAILED"
        path = " → ".join(self.states_visited) if self.states_visited else "(none)"
        lines = [
            f"Flow: {self.flow_name} [{status}]",
            f"Time: {self.total_elapsed_ms:.0f}ms",
            f"Path: {path}",
        ]
        if self.error:
            lines.append(f"Error: {self.error}")
        return "\n".join(lines)


@dataclass
class FlowDefinition:
    """
    Bir otomasyon akışının tanımı.

    Attributes:
        name:            Flow adı (ör: "login_flow").
        states:          Bu flow'a ait state listesi.
        initial_state:   Başlangıç state adı.
        reset_before:    True ise flow başlamadan önce uygulamayı sıfırla.
        clear_mode:      Sıfırlama modu (reset_before=True ise).
        timeout:         Flow için max çalışma süresi (saniye).
        description:     Flow açıklaması.
    """

    name: str
    states: list[State]
    initial_state: str
    reset_before: bool = True
    clear_mode: ClearMode = ClearMode.FULL
    timeout: float = DEFAULT_FLOW_TIMEOUT
    description: str = ""


# ---------------------------------------------------------------------------
# FlowRunner
# ---------------------------------------------------------------------------
class FlowRunner:
    """
    Orkestratör — tüm alt modülleri koordine ederek flow'ları yürütür.

    Kullanım:
        runner = FlowRunner(package_name="com.facebook.viewpoints")

        # Flow tanımla
        flow = FlowDefinition(
            name="login_flow",
            states=[
                State(name="welcome", ...),
                State(name="login", ...),
                State(name="home", ..., is_terminal=True),
            ],
            initial_state="welcome",
        )

        # Çalıştır
        result = runner.execute_flow(flow)
        print(result.summary)
    """

    def __init__(
        self,
        package_name: str,
        main_activity: Optional[str] = None,
        *,
        cmd_timeout: float = 15.0,
        cmd_retries: int = 2,
        log_level: int = logging.INFO,
    ) -> None:
        """
        Args:
            package_name:    Hedef uygulama paket adı.
            main_activity:   Ana Activity adı (opsiyonel).
            cmd_timeout:     CommandRunner varsayılan timeout.
            cmd_retries:     CommandRunner varsayılan retry sayısı.
            log_level:       Loglama seviyesi.
        """
        # Loglama konfigürasyonu
        self._setup_logging(log_level)

        # Alt modülleri başlat
        self.cmd = CommandRunner(
            timeout=cmd_timeout,
            retries=cmd_retries,
        )
        self.ui = UIBridge(self.cmd)
        self.validator = ScreenValidator(self.cmd)
        self.app = AppLifecycle(
            self.cmd,
            package_name,
            main_activity,
        )
        self.machine = StateMachine(self.cmd, self.ui, self.validator)

        self.package_name = package_name
        self._flow_history: list[FlowResult] = []

        logger.info(
            "FlowRunner initialized for '%s'", package_name
        )

    # ----- Flow Çalıştırma -------------------------------------------------

    def execute_flow(self, flow: FlowDefinition) -> FlowResult:
        """
        Bir flow tanımını uçtan uca çalıştırır.

        Akış:
          1. (Opsiyonel) Uygulama sıfırlama + yeniden başlatma
          2. State Machine'e state'leri yükle
          3. Başlangıç state'ini ayarla
          4. State Machine'i çalıştır
          5. Sonuç raporla

        Args:
            flow: Çalıştırılacak FlowDefinition.

        Returns:
            FlowResult
        """
        start = time.monotonic()

        logger.info(
            "═══════════════════════════════════════════════════"
        )
        logger.info(
            "Flow '%s' starting — %d states, initial='%s', reset=%s",
            flow.name,
            len(flow.states),
            flow.initial_state,
            flow.reset_before,
        )
        logger.info(
            "═══════════════════════════════════════════════════"
        )

        try:
            # 1. Uygulama sıfırlama
            if flow.reset_before:
                reset_result = self.app.reset_and_launch(
                    clear_mode=flow.clear_mode
                )
                if not reset_result:
                    elapsed = (time.monotonic() - start) * 1000
                    result = FlowResult(
                        success=False,
                        flow_name=flow.name,
                        states_visited=[],
                        total_elapsed_ms=round(elapsed, 2),
                        error=f"App reset failed: {reset_result.message}",
                    )
                    self._flow_history.append(result)
                    logger.error("Flow '%s' aborted: app reset failed", flow.name)
                    return result

                logger.info("App reset OK — proceeding with flow")

            # 2. State Machine'i hazırla (önceki state'leri temizle)
            self.machine = StateMachine(self.cmd, self.ui, self.validator)
            self.machine.add_states(flow.states)
            self.machine.set_initial_state(flow.initial_state)

            # 3. State Machine'i çalıştır
            self.machine.run()

            # 4. Başarılı sonuç
            elapsed = (time.monotonic() - start) * 1000
            result = FlowResult(
                success=True,
                flow_name=flow.name,
                states_visited=self.machine.state_history,
                total_elapsed_ms=round(elapsed, 2),
            )

        except TransitionError as exc:
            elapsed = (time.monotonic() - start) * 1000
            result = FlowResult(
                success=False,
                flow_name=flow.name,
                states_visited=self.machine.state_history,
                total_elapsed_ms=round(elapsed, 2),
                error=f"TransitionError: {exc}",
            )

        except ValidationError as exc:
            elapsed = (time.monotonic() - start) * 1000
            result = FlowResult(
                success=False,
                flow_name=flow.name,
                states_visited=self.machine.state_history,
                total_elapsed_ms=round(elapsed, 2),
                error=f"ValidationError: {exc}",
            )

        except StateNotFoundError as exc:
            elapsed = (time.monotonic() - start) * 1000
            result = FlowResult(
                success=False,
                flow_name=flow.name,
                states_visited=self.machine.state_history,
                total_elapsed_ms=round(elapsed, 2),
                error=f"StateNotFoundError: {exc}",
            )

        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            result = FlowResult(
                success=False,
                flow_name=flow.name,
                states_visited=self.machine.state_history,
                total_elapsed_ms=round(elapsed, 2),
                error=f"Unexpected: {type(exc).__name__}: {exc}",
            )
            logger.exception("Unexpected error in flow '%s'", flow.name)

        self._flow_history.append(result)

        # Sonuç logu
        logger.info(
            "═══════════════════════════════════════════════════"
        )
        for line in result.summary.split("\n"):
            logger.info(line)
        logger.info(
            "═══════════════════════════════════════════════════"
        )

        return result

    # ----- Çoklu Flow Çalıştırma -------------------------------------------

    def execute_flows(
        self,
        flows: list[FlowDefinition],
        *,
        stop_on_failure: bool = True,
    ) -> list[FlowResult]:
        """
        Birden fazla flow'u sırasıyla çalıştırır.

        Args:
            flows:            Çalıştırılacak flow listesi.
            stop_on_failure:  True ise bir flow başarısız olunca dur.

        Returns:
            FlowResult listesi.
        """
        results: list[FlowResult] = []

        for i, flow in enumerate(flows):
            logger.info(
                "━━━ Flow %d/%d: '%s' ━━━",
                i + 1, len(flows), flow.name,
            )

            result = self.execute_flow(flow)
            results.append(result)

            if not result.success and stop_on_failure:
                logger.warning(
                    "Stopping: flow '%s' failed (stop_on_failure=True)",
                    flow.name,
                )
                break

        # Toplu özet
        passed = sum(1 for r in results if r.success)
        total = len(results)
        logger.info(
            "━━━ All flows done: %d/%d passed ━━━", passed, total
        )

        return results

    # ----- Yardımcılar -----------------------------------------------------

    def quick_reset(self, clear_mode: ClearMode = ClearMode.FULL) -> bool:
        """Uygulamayı hızlıca sıfırlayıp yeniden başlatır."""
        result = self.app.reset_and_launch(clear_mode=clear_mode)
        return result.success

    def get_app_status(self):
        """Uygulamanın anlık durumunu döndürür."""
        return self.app.get_status()

    @property
    def history(self) -> list[FlowResult]:
        """Tüm flow çalıştırma geçmişini döndürür."""
        return list(self._flow_history)

    def print_history(self) -> None:
        """Flow geçmişini yazdırır."""
        if not self._flow_history:
            print("No flow history.")
            return

        for i, result in enumerate(self._flow_history):
            print(f"\n--- Flow Run #{i + 1} ---")
            print(result.summary)

    # ----- Loglama ---------------------------------------------------------

    @staticmethod
    def _setup_logging(level: int) -> None:
        """Proje geneli loglama konfigürasyonu."""
        # Root logger'ı ayarla (sadece henüz handler yoksa)
        root_logger = logging.getLogger()

        if not root_logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s │ %(levelname)-5s │ %(name)-20s │ %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
            root_logger.addHandler(handler)

        root_logger.setLevel(level)

        # Alt modüllerin log seviyelerini ayarla
        logging.getLogger("command_runner").setLevel(level)
        logging.getLogger("ui_bridge").setLevel(level)
        logging.getLogger("screen_validator").setLevel(level)
        logging.getLogger("state_machine").setLevel(level)
        logging.getLogger("app_lifecycle").setLevel(level)
