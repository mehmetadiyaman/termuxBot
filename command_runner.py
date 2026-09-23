"""
command_runner.py — Subprocess Komut Yöneticisi
================================================
Termux / VPhones ortamında root ve normal komutları güvenle çalıştırır.

Sorumluluklar:
  • Root (su -c) ve normal komut execution
  • TTY donmasını önleyen stdout/stderr yönetimi
  • Timeout ve retry mekanizması
  • Yapılandırılmış CommandResult dönüş tipi
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT: float = 15.0       # Komut başına varsayılan timeout (saniye)
DEFAULT_RETRIES: int = 2            # Başarısız komutlar için yeniden deneme sayısı
RETRY_BACKOFF: float = 0.5          # Retry'lar arası bekleme (saniye)
SHELL_EXECUTABLE: str = "/bin/sh"   # Termux varsayılan shell


# ---------------------------------------------------------------------------
# Veri Yapıları
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CommandResult:
    """Bir komut çalıştırmanın yapılandırılmış sonucu."""

    success: bool
    return_code: int
    stdout: str
    stderr: str
    elapsed_ms: float               # Komutun çalışma süresi (milisaniye)
    command: str                     # Çalıştırılan komut (loglama için)
    attempt: int = 1                 # Kaçıncı denemede başarılı oldu

    @property
    def output(self) -> str:
        """stdout içeriğini strip edilmiş şekilde döndürür."""
        return self.stdout.strip()

    def __bool__(self) -> bool:
        """if result: şeklinde kullanıma izin verir."""
        return self.success


# ---------------------------------------------------------------------------
# CommandRunner
# ---------------------------------------------------------------------------
class CommandRunner:
    """
    Termux ortamında subprocess komutlarını güvenle çalıştırır.

    Kullanım:
        runner = CommandRunner()

        # Normal komut
        result = runner.run("echo hello")

        # Root komut
        result = runner.run_root("pm clear com.example.app")

        # Çıktısız (TTY koruma) — tap, keyevent gibi komutlar
        result = runner.run_silent("input tap 500 800")
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        retry_backoff: float = RETRY_BACKOFF,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.retry_backoff = retry_backoff

    # ----- Genel Arayüzler ------------------------------------------------

    def run(
        self,
        command: str,
        *,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        capture: bool = True,
    ) -> CommandResult:
        """
        Normal (root'suz) bir shell komutu çalıştırır.

        Args:
            command:  Çalıştırılacak shell komutu.
            timeout:  Bu çağrıya özel timeout (saniye). None → varsayılan.
            retries:  Bu çağrıya özel retry sayısı. None → varsayılan.
            capture:  False ise stdout/stderr yakalanmaz (TTY koruma).

        Returns:
            CommandResult
        """
        return self._execute(
            command,
            as_root=False,
            timeout=timeout or self.timeout,
            retries=retries if retries is not None else self.retries,
            capture=capture,
        )

    def run_root(
        self,
        command: str,
        *,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        capture: bool = True,
    ) -> CommandResult:
        """
        Root yetkisiyle (su -c) bir shell komutu çalıştırır.

        Args:
            command:  Çalıştırılacak komut (su -c ile sarmalanır).
            timeout:  Bu çağrıya özel timeout (saniye).
            retries:  Bu çağrıya özel retry sayısı.
            capture:  False ise stdout/stderr yakalanmaz (TTY koruma).

        Returns:
            CommandResult
        """
        return self._execute(
            command,
            as_root=True,
            timeout=timeout or self.timeout,
            retries=retries if retries is not None else self.retries,
            capture=capture,
        )

    def run_silent(
        self,
        command: str,
        *,
        as_root: bool = True,
        timeout: Optional[float] = None,
    ) -> CommandResult:
        """
        Çıktı yakalamadan komut çalıştırır — TTY donmasını önler.

        input tap, input text, input keyevent gibi komutlar için optimize.
        stdout ve stderr /dev/null'a yönlendirilir.

        Args:
            command:   Çalıştırılacak komut.
            as_root:   Root ile mi çalışsın (varsayılan True).
            timeout:   Bu çağrıya özel timeout.

        Returns:
            CommandResult (stdout/stderr boş olacaktır)
        """
        return self._execute(
            command,
            as_root=as_root,
            timeout=timeout or self.timeout,
            retries=0,          # Silent komutlarda retry yapmıyoruz
            capture=False,
        )

    # ----- Yardımcı Kısa Yollar -------------------------------------------

    def tap(self, x: int, y: int) -> CommandResult:
        """Belirtilen koordinata dokunur."""
        return self.run_silent(f"input tap {x} {y}")

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> CommandResult:
        """Kaydırma (swipe) hareketi yapar."""
        return self.run_silent(
            f"input swipe {x1} {y1} {x2} {y2} {duration_ms}"
        )

    def keyevent(self, code: int) -> CommandResult:
        """Android keyevent gönderir (ör: 4=BACK, 3=HOME)."""
        return self.run_silent(f"input keyevent {code}")

    def input_text(self, text: str) -> CommandResult:
        """
        Metin girer. Boşluklar %s ile escape edilir.

        Not: Türkçe karakterler input text ile çalışmaz,
        bu durumda ADBKeyboard veya clipboard yöntemi gerekir.
        """
        escaped = text.replace(" ", "%s")
        return self.run_silent(f"input text '{escaped}'")

    def dismiss_keyboard(self) -> CommandResult:
        """Açık klavyeyi BACK tuşuyla kapatır."""
        return self.keyevent(4)

    # ----- İç Mekanizma ----------------------------------------------------

    def _execute(
        self,
        command: str,
        *,
        as_root: bool,
        timeout: float,
        retries: int,
        capture: bool,
    ) -> CommandResult:
        """
        Komutu çalıştırır, retry ve timeout yönetimini sağlar.

        Bu metod dışarıdan doğrudan çağrılmaz — run(), run_root(),
        run_silent() arayüzleri üzerinden kullanılır.
        """
        # Root sarmalama
        full_command = f'su -c "{command}"' if as_root else command

        last_result: Optional[CommandResult] = None
        max_attempts = 1 + retries

        for attempt in range(1, max_attempts + 1):
            start = time.monotonic()

            try:
                proc = subprocess.run(
                    full_command,
                    shell=True,
                    executable=SHELL_EXECUTABLE,
                    timeout=timeout,
                    stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                    stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
                    text=capture,       # capture=True ise string döner
                )

                elapsed = (time.monotonic() - start) * 1000
                result = CommandResult(
                    success=proc.returncode == 0,
                    return_code=proc.returncode,
                    stdout=proc.stdout if capture else "",
                    stderr=proc.stderr if capture else "",
                    elapsed_ms=round(elapsed, 2),
                    command=command,
                    attempt=attempt,
                )

            except subprocess.TimeoutExpired:
                elapsed = (time.monotonic() - start) * 1000
                result = CommandResult(
                    success=False,
                    return_code=-1,
                    stdout="",
                    stderr=f"TIMEOUT after {timeout}s",
                    elapsed_ms=round(elapsed, 2),
                    command=command,
                    attempt=attempt,
                )
                logger.warning(
                    "Timeout [%.0fms] cmd='%s' attempt=%d/%d",
                    elapsed, command, attempt, max_attempts,
                )

            except OSError as exc:
                elapsed = (time.monotonic() - start) * 1000
                result = CommandResult(
                    success=False,
                    return_code=-2,
                    stdout="",
                    stderr=str(exc),
                    elapsed_ms=round(elapsed, 2),
                    command=command,
                    attempt=attempt,
                )
                logger.error(
                    "OSError cmd='%s': %s", command, exc,
                )

            last_result = result

            if result.success:
                logger.debug(
                    "OK [%.0fms] cmd='%s' attempt=%d",
                    result.elapsed_ms, command, attempt,
                )
                return result

            # Başarısız — retry gerekli mi?
            if attempt < max_attempts:
                wait = self.retry_backoff * attempt
                logger.info(
                    "Retry %d/%d in %.1fs — cmd='%s' rc=%d",
                    attempt, max_attempts, wait, command, result.return_code,
                )
                time.sleep(wait)

        # Tüm denemeler başarısız
        logger.error(
            "FAILED after %d attempts — cmd='%s' rc=%d stderr='%s'",
            max_attempts,
            command,
            last_result.return_code if last_result else -99,
            (last_result.stderr[:200] if last_result else "N/A"),
        )
        return last_result  # type: ignore[return-value]
