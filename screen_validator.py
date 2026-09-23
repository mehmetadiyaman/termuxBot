"""
screen_validator.py — Hafif Ekran Doğrulama Katmanı
=====================================================
Tam XML dump çekmeden, dumpsys tabanlı hızlı sorgularla
ekran durumunu doğrular. State Machine'in L1 (Predictive)
katmanı bu modülü kullanır.

Sorumluluklar:
  • Aktif Activity tespiti (dumpsys activity top)
  • Aktif pencere focus kontrolü (dumpsys window)
  • State'e giriş doğrulaması (validator çalıştırma)
  • Ekran değişim algılama
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from command_runner import CommandRunner

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
ACTIVITY_QUERY_TIMEOUT: float = 5.0
WINDOW_QUERY_TIMEOUT: float = 3.0


# ---------------------------------------------------------------------------
# Veri Yapıları
# ---------------------------------------------------------------------------
class ValidationLevel(Enum):
    """Doğrulama seviyeleri — L0'dan L2'ye artan hassasiyet."""

    L0_BLIND = auto()       # Doğrulama yok, doğrudan action
    L1_ACTIVITY = auto()    # Sadece Activity adı kontrolü (~50ms)
    L2_FULL_DUMP = auto()   # Tam XML dump ile doğrulama (~500ms)


@dataclass(frozen=True)
class ScreenInfo:
    """Aktif ekranın hafif bilgisi (dump olmadan elde edilir)."""

    package_name: str           # Ör: "com.facebook.viewpoints"
    activity_name: str          # Ör: ".ui.LoginActivity"
    window_focus: str           # Ör: "com.facebook.viewpoints/.ui.LoginActivity"
    timestamp: float            # Sorgu zamanı (monotonic)

    @property
    def full_activity(self) -> str:
        """Paket adı + activity birleşimi."""
        if self.activity_name.startswith("."):
            return f"{self.package_name}{self.activity_name}"
        return self.activity_name


@dataclass
class ValidationResult:
    """Bir doğrulama girişiminin sonucu."""

    passed: bool
    level: ValidationLevel
    expected: str               # Beklenen değer
    actual: str                 # Gerçek değer
    elapsed_ms: float
    message: str = ""

    def __bool__(self) -> bool:
        return self.passed


# ---------------------------------------------------------------------------
# ScreenValidator
# ---------------------------------------------------------------------------
class ScreenValidator:
    """
    Hafif ekran doğrulama — tam dump çekmeden ekranın
    beklenen durumda olup olmadığını kontrol eder.

    Kullanım:
        runner = CommandRunner()
        validator = ScreenValidator(runner)

        # Aktif Activity'yi sorgula
        info = validator.get_screen_info()
        print(info.activity_name)  # ".ui.LoginActivity"

        # Beklenen Activity'de miyiz?
        result = validator.expect_activity(".ui.LoginActivity")
        if result:
            print("Doğru ekrandayız!")

        # Activity değişene kadar bekle
        validator.wait_for_activity_change(current=".ui.LoginActivity")
    """

    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner
        self._last_screen: Optional[ScreenInfo] = None

    # ----- Ekran Bilgisi Sorguları ----------------------------------------

    def get_screen_info(self) -> Optional[ScreenInfo]:
        """
        Aktif ekranın hafif bilgisini döndürür.

        `dumpsys activity top` ile aktif Activity'yi,
        `dumpsys window windows` ile aktif pencere focus'unu alır.

        Bu iki sorgu birlikte ~50-80ms sürer (dump'ın ~500ms'ine karşı).
        """
        activity_info = self._query_top_activity()
        window_info = self._query_window_focus()

        if activity_info is None:
            return None

        pkg, act = activity_info

        info = ScreenInfo(
            package_name=pkg,
            activity_name=act,
            window_focus=window_info or "",
            timestamp=time.monotonic(),
        )

        self._last_screen = info
        logger.debug(
            "ScreenInfo: pkg=%s act=%s focus=%s",
            pkg, act, window_info,
        )
        return info

    def get_current_activity(self) -> Optional[str]:
        """Sadece aktif Activity adını döndürür (kısa yol)."""
        info = self.get_screen_info()
        return info.activity_name if info else None

    def get_current_package(self) -> Optional[str]:
        """Sadece aktif paket adını döndürür (kısa yol)."""
        info = self.get_screen_info()
        return info.package_name if info else None

    # ----- Doğrulama Metodları --------------------------------------------

    def expect_activity(
        self,
        expected_activity: str,
        *,
        partial_match: bool = True,
    ) -> ValidationResult:
        """
        Aktif Activity'nin beklenen değerle eşleştiğini doğrular.

        Args:
            expected_activity:  Beklenen Activity adı
                                (ör: ".ui.LoginActivity" veya tam qualified).
            partial_match:      True ise substring eşleşmesi yeterli.

        Returns:
            ValidationResult
        """
        start = time.monotonic()
        info = self.get_screen_info()
        elapsed = (time.monotonic() - start) * 1000

        if info is None:
            return ValidationResult(
                passed=False,
                level=ValidationLevel.L1_ACTIVITY,
                expected=expected_activity,
                actual="<query_failed>",
                elapsed_ms=round(elapsed, 2),
                message="dumpsys sorgusu başarısız",
            )

        actual = info.full_activity

        if partial_match:
            matched = expected_activity in actual
        else:
            matched = expected_activity == actual

        result = ValidationResult(
            passed=matched,
            level=ValidationLevel.L1_ACTIVITY,
            expected=expected_activity,
            actual=actual,
            elapsed_ms=round(elapsed, 2),
            message="" if matched else f"Activity mismatch: expected '{expected_activity}', got '{actual}'",
        )

        if not matched:
            logger.info(
                "Activity mismatch [%.0fms]: expected='%s' actual='%s'",
                elapsed, expected_activity, actual,
            )

        return result

    def expect_package(self, expected_package: str) -> ValidationResult:
        """Aktif paketin beklenen değerle eşleştiğini doğrular."""
        start = time.monotonic()
        info = self.get_screen_info()
        elapsed = (time.monotonic() - start) * 1000

        if info is None:
            return ValidationResult(
                passed=False,
                level=ValidationLevel.L1_ACTIVITY,
                expected=expected_package,
                actual="<query_failed>",
                elapsed_ms=round(elapsed, 2),
                message="dumpsys sorgusu başarısız",
            )

        matched = info.package_name == expected_package

        return ValidationResult(
            passed=matched,
            level=ValidationLevel.L1_ACTIVITY,
            expected=expected_package,
            actual=info.package_name,
            elapsed_ms=round(elapsed, 2),
            message="" if matched else f"Package mismatch: expected '{expected_package}', got '{info.package_name}'",
        )

    # ----- Bekleme (Wait) Metodları ----------------------------------------

    def wait_for_activity(
        self,
        target_activity: str,
        *,
        timeout: float = 10.0,
        poll_interval: float = 0.5,
        partial_match: bool = True,
    ) -> ValidationResult:
        """
        Belirtilen Activity aktif olana kadar bekler.

        Hafif dumpsys sorgusu ile poll eder (XML dump çekmez).
        """
        deadline = time.monotonic() + timeout
        last_actual = ""

        while time.monotonic() < deadline:
            result = self.expect_activity(
                target_activity, partial_match=partial_match
            )
            if result.passed:
                logger.info(
                    "wait_for_activity: '%s' active [%.0fms]",
                    target_activity, result.elapsed_ms,
                )
                return result

            last_actual = result.actual
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_interval, remaining))

        elapsed = timeout * 1000
        return ValidationResult(
            passed=False,
            level=ValidationLevel.L1_ACTIVITY,
            expected=target_activity,
            actual=last_actual,
            elapsed_ms=round(elapsed, 2),
            message=f"Timeout: '{target_activity}' not active after {timeout}s",
        )

    def wait_for_activity_change(
        self,
        current_activity: str,
        *,
        timeout: float = 10.0,
        poll_interval: float = 0.3,
    ) -> ValidationResult:
        """
        Mevcut Activity'den farklı bir Activity'ye geçiş olana kadar bekler.

        Bir buton tıklandıktan sonra yeni ekranın yüklenmesini
        beklemek için kullanılır.
        """
        deadline = time.monotonic() + timeout
        new_actual = current_activity

        while time.monotonic() < deadline:
            info = self.get_screen_info()
            if info is not None:
                new_actual = info.full_activity
                if current_activity not in new_actual:
                    elapsed = (time.monotonic() - (deadline - timeout)) * 1000
                    logger.info(
                        "Activity changed: '%s' → '%s' [%.0fms]",
                        current_activity, new_actual, elapsed,
                    )
                    return ValidationResult(
                        passed=True,
                        level=ValidationLevel.L1_ACTIVITY,
                        expected=f"!={current_activity}",
                        actual=new_actual,
                        elapsed_ms=round(elapsed, 2),
                    )

            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_interval, remaining))

        elapsed = timeout * 1000
        return ValidationResult(
            passed=False,
            level=ValidationLevel.L1_ACTIVITY,
            expected=f"!={current_activity}",
            actual=new_actual,
            elapsed_ms=round(elapsed, 2),
            message=f"Activity did not change from '{current_activity}' after {timeout}s",
        )

    def has_screen_changed(self) -> bool:
        """
        Son sorgulanan ekrandan farklı bir ekrana geçilmiş mi?

        Önceki get_screen_info() sonucuyla karşılaştırır.
        """
        if self._last_screen is None:
            return True  # İlk sorgu — "değişmiş" say

        current = self.get_screen_info()
        if current is None:
            return True  # Sorgu başarısız — güvenli tarafta kal

        changed = current.full_activity != self._last_screen.full_activity
        if changed:
            logger.info(
                "Screen changed: '%s' → '%s'",
                self._last_screen.full_activity,
                current.full_activity,
            )
        return changed

    # ----- İç Mekanizma: dumpsys Sorguları ---------------------------------

    def _query_top_activity(self) -> Optional[tuple[str, str]]:
        """
        `dumpsys activity top` ile aktif Activity'yi sorgular.

        Returns:
            (package_name, activity_name) tuple veya None.

        Çıktı formatı (Android versiyonuna göre değişebilir):
          TASK ... #0 ... ACTIVITY com.pkg/.ActivityName ...
          veya
          ACTIVITY com.pkg/.ActivityName ... (hash) pid=...
        """
        result = self.runner.run_root(
            "dumpsys activity top | grep ACTIVITY | tail -1",
            timeout=ACTIVITY_QUERY_TIMEOUT,
            retries=1,
        )

        if not result.success or not result.output:
            logger.warning("dumpsys activity top query failed")
            return None

        line = result.output.strip()

        # Pattern: ACTIVITY com.package/.ActivityName
        # veya:    ACTIVITY com.package/com.package.ActivityName
        activity_pattern = re.compile(
            r'ACTIVITY\s+([\w.]+)/([\w.]+)'
        )
        match = activity_pattern.search(line)

        if match:
            return match.group(1), match.group(2)

        logger.warning("Could not parse activity from: '%s'", line[:200])
        return None

    def _query_window_focus(self) -> Optional[str]:
        """
        `dumpsys window windows` ile aktif pencere focus'unu sorgular.

        Returns:
            Focus string (ör: "com.pkg/.Activity") veya None.

        Çıktı formatı:
          mCurrentFocus=Window{hash u0 com.pkg/.Activity}
          veya
          mFocusedWindow=Window{hash u0 com.pkg/.Activity}
        """
        result = self.runner.run_root(
            "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedWindow' | head -1",
            timeout=WINDOW_QUERY_TIMEOUT,
            retries=0,
        )

        if not result.success or not result.output:
            logger.debug("Window focus query returned empty")
            return None

        line = result.output.strip()

        # Pattern: Window{hash u0 com.package/.Activity}
        focus_pattern = re.compile(
            r'Window\{[^}]*\s+u\d+\s+([\w./]+)\}'
        )
        match = focus_pattern.search(line)

        if match:
            return match.group(1)

        logger.debug("Could not parse window focus from: '%s'", line[:200])
        return None
