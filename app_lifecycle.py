"""
app_lifecycle.py — Uygulama Yaşam Döngüsü Yöneticisi
=======================================================
Root yetkisiyle hedef uygulamanın veri/cache sıfırlama,
zorla durdurma ve yeniden başlatma döngüsünü yönetir.

Sorumluluklar:
  • pm clear ile tam veri sıfırlama
  • Seçici cache/DB temizliği
  • force-stop ve launch yönetimi
  • Reset + launch döngüsü (test senaryoları için)
  • Uygulama durum sorguları (çalışıyor mu, activity, vb.)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from command_runner import CommandRunner, CommandResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DEFAULT_LAUNCH_WAIT: float = 2.0       # Launch sonrası bekleme (saniye)
DEFAULT_CLEAR_WAIT: float = 0.5        # Clear sonrası bekleme (saniye)
RESET_MAX_RETRIES: int = 3             # reset_and_launch retry sayısı
PROCESS_CHECK_TIMEOUT: float = 3.0     # pidof / dumpsys timeout
ACTIVITY_QUERY_TIMEOUT: float = 5.0    # dumpsys activity sorgu timeout


# ---------------------------------------------------------------------------
# Veri Yapıları
# ---------------------------------------------------------------------------
class ClearMode(Enum):
    """Uygulama verisi temizleme modları."""

    FULL = auto()           # pm clear — her şeyi siler
    CACHE_ONLY = auto()     # Sadece cache dizini
    DATABASE = auto()       # Belirli bir veritabanı dosyası
    SHARED_PREFS = auto()   # SharedPreferences temizliği


@dataclass(frozen=True)
class AppStatus:
    """Uygulamanın anlık durumu."""

    is_running: bool
    pid: Optional[int]              # Process ID (çalışıyorsa)
    current_activity: str           # Aktif Activity (çalışıyorsa)
    package: str

    def __bool__(self) -> bool:
        """if status: → uygulama çalışıyor mu?"""
        return self.is_running


@dataclass(frozen=True)
class LifecycleResult:
    """Bir yaşam döngüsü işleminin sonucu."""

    success: bool
    operation: str                  # "clear", "stop", "launch", "reset"
    message: str
    elapsed_ms: float

    def __bool__(self) -> bool:
        return self.success


# ---------------------------------------------------------------------------
# AppLifecycle
# ---------------------------------------------------------------------------
class AppLifecycle:
    """
    Hedef uygulamanın yaşam döngüsünü yönetir.

    Kullanım:
        runner = CommandRunner()
        app = AppLifecycle(runner, "com.facebook.viewpoints")

        # Durum sorgusu
        status = app.get_status()
        print(f"Running: {status.is_running}, PID: {status.pid}")

        # Tam sıfırlama + yeniden başlatma
        result = app.reset_and_launch()
        if result:
            print("App is fresh and running!")

        # Sadece cache temizle
        app.clear_cache()

        # Zorla durdur
        app.force_stop()
    """

    def __init__(
        self,
        runner: CommandRunner,
        package_name: str,
        main_activity: Optional[str] = None,
    ) -> None:
        """
        Args:
            runner:          CommandRunner instance.
            package_name:    Hedef uygulama paket adı (ör: "com.facebook.viewpoints").
            main_activity:   Ana Activity adı (ör: ".ui.MainActivity").
                             None ise monkey ile başlatılır.
        """
        self.runner = runner
        self.package = package_name
        self.main_activity = main_activity

    # ----- Durum Sorguları -------------------------------------------------

    def get_status(self) -> AppStatus:
        """
        Uygulamanın anlık durumunu sorgular.

        Returns:
            AppStatus — is_running, pid, current_activity bilgilerini içerir.
        """
        pid = self._get_pid()
        activity = ""

        if pid is not None:
            activity = self._get_current_activity() or ""

        return AppStatus(
            is_running=pid is not None,
            pid=pid,
            current_activity=activity,
            package=self.package,
        )

    def is_running(self) -> bool:
        """Uygulama çalışıyor mu? (kısa yol)"""
        return self._get_pid() is not None

    def is_foreground(self) -> bool:
        """Uygulama ön planda mı?"""
        activity = self._get_current_activity()
        if activity is None:
            return False
        return self.package in activity

    # ----- Durdurma --------------------------------------------------------

    def force_stop(self) -> LifecycleResult:
        """
        Uygulamayı zorla durdurur (am force-stop).

        Bu komut uygulamanın tüm process'lerini ve servislerini
        anında sonlandırır. Veriye dokunmaz.
        """
        start = time.monotonic()

        result = self.runner.run_root(
            f"am force-stop {self.package}",
            timeout=5.0,
            retries=1,
        )

        elapsed = (time.monotonic() - start) * 1000

        if result.success:
            logger.info(
                "force_stop OK [%.0fms]: %s", elapsed, self.package
            )
            return LifecycleResult(
                success=True,
                operation="stop",
                message=f"{self.package} stopped",
                elapsed_ms=round(elapsed, 2),
            )

        logger.error(
            "force_stop FAILED [%.0fms]: %s — %s",
            elapsed, self.package, result.stderr[:200],
        )
        return LifecycleResult(
            success=False,
            operation="stop",
            message=f"Failed to stop: {result.stderr[:100]}",
            elapsed_ms=round(elapsed, 2),
        )

    # ----- Veri Temizleme --------------------------------------------------

    def clear_data(self) -> LifecycleResult:
        """
        Tüm uygulama verisini sıfırlar (pm clear).

        Bu komut:
          • Tüm veri, cache, DB, SharedPreferences dosyalarını siler
          • Uygulamayı otomatik olarak durdurur (force-stop dahildir)
          • Uygulamayı "ilk kurulum" state'ine getirir

        Not: pm clear kendi içinde force-stop yapar, ayrıca
             force_stop() çağırmaya gerek yoktur.
        """
        start = time.monotonic()

        result = self.runner.run_root(
            f"pm clear {self.package}",
            timeout=10.0,
            retries=1,
        )

        elapsed = (time.monotonic() - start) * 1000

        # pm clear başarılı olduğunda "Success" döner
        if result.success and "Success" in result.output:
            logger.info(
                "clear_data OK [%.0fms]: %s — %s",
                elapsed, self.package, result.output,
            )
            return LifecycleResult(
                success=True,
                operation="clear",
                message=f"Data cleared: {result.output}",
                elapsed_ms=round(elapsed, 2),
            )

        # Başarısız — bazen race condition oluşabilir
        logger.warning(
            "clear_data attempt failed [%.0fms]: %s — stdout='%s' stderr='%s'",
            elapsed, self.package, result.output, result.stderr[:200],
        )

        # Retry stratejisi: force_stop → clear
        logger.info("Retrying: force_stop → pm clear")
        self.force_stop()
        time.sleep(DEFAULT_CLEAR_WAIT)

        retry_result = self.runner.run_root(
            f"pm clear {self.package}",
            timeout=10.0,
            retries=0,
        )

        elapsed = (time.monotonic() - start) * 1000

        if retry_result.success and "Success" in retry_result.output:
            logger.info(
                "clear_data OK (retry) [%.0fms]: %s", elapsed, self.package
            )
            return LifecycleResult(
                success=True,
                operation="clear",
                message=f"Data cleared (retry): {retry_result.output}",
                elapsed_ms=round(elapsed, 2),
            )

        logger.error(
            "clear_data FAILED [%.0fms]: %s", elapsed, self.package
        )
        return LifecycleResult(
            success=False,
            operation="clear",
            message=f"Failed to clear data: {retry_result.stderr[:100]}",
            elapsed_ms=round(elapsed, 2),
        )

    def clear_cache(self) -> LifecycleResult:
        """
        Sadece cache dizinini temizler.

        Uygulama verileri (oturum, DB) korunur.
        Hafif temizlik — tam sıfırlama gerekmediğinde kullanılır.
        """
        start = time.monotonic()
        data_dir = f"/data/data/{self.package}"

        result = self.runner.run_root(
            f"rm -rf {data_dir}/cache/* {data_dir}/code_cache/*",
            timeout=5.0,
            retries=1,
        )

        elapsed = (time.monotonic() - start) * 1000

        if result.success:
            logger.info(
                "clear_cache OK [%.0fms]: %s", elapsed, self.package
            )
            return LifecycleResult(
                success=True,
                operation="clear_cache",
                message="Cache cleared",
                elapsed_ms=round(elapsed, 2),
            )

        logger.warning(
            "clear_cache FAILED [%.0fms]: %s — %s",
            elapsed, self.package, result.stderr[:200],
        )
        return LifecycleResult(
            success=False,
            operation="clear_cache",
            message=f"Cache clear failed: {result.stderr[:100]}",
            elapsed_ms=round(elapsed, 2),
        )

    def clear_database(self, db_name: str) -> LifecycleResult:
        """
        Belirli bir veritabanı dosyasını siler.

        Cerrahi hassasiyet — sadece belirtilen DB'yi hedefler.

        Args:
            db_name: Veritabanı dosya adı (ör: "app.db", "cache.db").
        """
        start = time.monotonic()
        db_path = f"/data/data/{self.package}/databases/{db_name}"

        # İlgili dosyaları da sil (WAL, journal)
        result = self.runner.run_root(
            f"rm -f {db_path} {db_path}-wal {db_path}-shm {db_path}-journal",
            timeout=5.0,
            retries=0,
        )

        elapsed = (time.monotonic() - start) * 1000

        if result.success:
            logger.info(
                "clear_database OK [%.0fms]: %s/%s",
                elapsed, self.package, db_name,
            )
            return LifecycleResult(
                success=True,
                operation="clear_database",
                message=f"Database '{db_name}' deleted",
                elapsed_ms=round(elapsed, 2),
            )

        return LifecycleResult(
            success=False,
            operation="clear_database",
            message=f"Database delete failed: {result.stderr[:100]}",
            elapsed_ms=round(elapsed, 2),
        )

    def clear_shared_prefs(self) -> LifecycleResult:
        """SharedPreferences dosyalarını siler."""
        start = time.monotonic()
        prefs_dir = f"/data/data/{self.package}/shared_prefs"

        result = self.runner.run_root(
            f"rm -rf {prefs_dir}/*",
            timeout=5.0,
            retries=0,
        )

        elapsed = (time.monotonic() - start) * 1000

        if result.success:
            logger.info(
                "clear_shared_prefs OK [%.0fms]: %s", elapsed, self.package
            )
            return LifecycleResult(
                success=True,
                operation="clear_shared_prefs",
                message="SharedPreferences cleared",
                elapsed_ms=round(elapsed, 2),
            )

        return LifecycleResult(
            success=False,
            operation="clear_shared_prefs",
            message=f"SharedPrefs clear failed: {result.stderr[:100]}",
            elapsed_ms=round(elapsed, 2),
        )

    # ----- Başlatma --------------------------------------------------------

    def launch(
        self,
        activity: Optional[str] = None,
        *,
        wait: bool = True,
        extra_args: str = "",
    ) -> LifecycleResult:
        """
        Uygulamayı başlatır.

        Args:
            activity:    Başlatılacak Activity. None ise:
                         - main_activity tanımlıysa onu kullan
                         - değilse monkey ile başlat
            wait:        True ise launch sonrası DEFAULT_LAUNCH_WAIT bekler.
            extra_args:  am start'a eklenecek ek argümanlar.

        Returns:
            LifecycleResult
        """
        start = time.monotonic()
        target_activity = activity or self.main_activity

        if target_activity:
            # am start ile belirli Activity'yi başlat
            full_activity = target_activity
            if target_activity.startswith("."):
                full_activity = f"{self.package}/{target_activity}"
            elif "/" not in target_activity:
                full_activity = f"{self.package}/{target_activity}"

            cmd = f"am start -n {full_activity} {extra_args}".strip()
        else:
            # monkey ile uygulamanın launcher Activity'sini başlat
            cmd = f"monkey -p {self.package} -c android.intent.category.LAUNCHER 1"

        result = self.runner.run_root(cmd, timeout=10.0, retries=1)
        elapsed = (time.monotonic() - start) * 1000

        if not result.success:
            logger.error(
                "launch FAILED [%.0fms]: %s — %s",
                elapsed, self.package, result.stderr[:200],
            )
            return LifecycleResult(
                success=False,
                operation="launch",
                message=f"Launch failed: {result.stderr[:100]}",
                elapsed_ms=round(elapsed, 2),
            )

        # Launch sonrası bekleme — UI'ın yüklenmesi için
        if wait:
            time.sleep(DEFAULT_LAUNCH_WAIT)

        elapsed = (time.monotonic() - start) * 1000
        logger.info("launch OK [%.0fms]: %s", elapsed, self.package)

        return LifecycleResult(
            success=True,
            operation="launch",
            message=f"Launched: {cmd}",
            elapsed_ms=round(elapsed, 2),
        )

    # ----- Tam Reset Döngüsü ----------------------------------------------

    def reset_and_launch(
        self,
        activity: Optional[str] = None,
        *,
        clear_mode: ClearMode = ClearMode.FULL,
        db_name: str = "",
    ) -> LifecycleResult:
        """
        Tam sıfırlama + yeniden başlatma döngüsü.

        Akış:
          1. Veri temizleme (clear_mode'a göre)
          2. Kısa bekleme (OS'un temizliği tamamlaması için)
          3. Uygulama başlatma
          4. Başarı kontrolü

        Args:
            activity:    Başlatılacak Activity.
            clear_mode:  Temizleme modu (FULL, CACHE_ONLY, DATABASE, SHARED_PREFS).
            db_name:     DATABASE modu için veritabanı dosya adı.

        Returns:
            LifecycleResult
        """
        start = time.monotonic()

        for attempt in range(1, RESET_MAX_RETRIES + 1):
            logger.info(
                "reset_and_launch attempt %d/%d for %s (mode=%s)",
                attempt, RESET_MAX_RETRIES, self.package, clear_mode.name,
            )

            # 1. Temizleme
            clear_result = self._do_clear(clear_mode, db_name)
            if not clear_result:
                logger.warning(
                    "Clear failed on attempt %d: %s",
                    attempt, clear_result.message,
                )
                if attempt < RESET_MAX_RETRIES:
                    time.sleep(1.0)
                    continue
                break

            # 2. Kısa bekleme — OS'un dosya sistemini sync etmesi için
            time.sleep(DEFAULT_CLEAR_WAIT)

            # 3. Başlatma
            launch_result = self.launch(activity, wait=True)
            if not launch_result:
                logger.warning(
                    "Launch failed on attempt %d: %s",
                    attempt, launch_result.message,
                )
                if attempt < RESET_MAX_RETRIES:
                    time.sleep(1.0)
                    continue
                break

            # 4. Çalışıyor mu kontrolü
            if self.is_running():
                elapsed = (time.monotonic() - start) * 1000
                logger.info(
                    "reset_and_launch OK [%.0fms]: %s (attempt %d)",
                    elapsed, self.package, attempt,
                )
                return LifecycleResult(
                    success=True,
                    operation="reset",
                    message=f"Reset and launched (attempt {attempt})",
                    elapsed_ms=round(elapsed, 2),
                )

            logger.warning(
                "App not running after launch on attempt %d", attempt
            )
            if attempt < RESET_MAX_RETRIES:
                time.sleep(1.0)

        # Tüm denemeler başarısız
        elapsed = (time.monotonic() - start) * 1000
        logger.error(
            "reset_and_launch FAILED after %d attempts [%.0fms]: %s",
            RESET_MAX_RETRIES, elapsed, self.package,
        )
        return LifecycleResult(
            success=False,
            operation="reset",
            message=f"Reset failed after {RESET_MAX_RETRIES} attempts",
            elapsed_ms=round(elapsed, 2),
        )

    # ----- Bilgi Sorguları -------------------------------------------------

    def list_data_contents(self) -> Optional[str]:
        """
        Uygulamanın /data/data dizin içeriğini listeler.

        Debug amaçlı — hangi dosyaların/DB'lerin bulunduğunu görmek için.
        """
        result = self.runner.run_root(
            f"ls -la /data/data/{self.package}/",
            timeout=5.0,
            retries=0,
        )
        return result.output if result.success else None

    def list_databases(self) -> Optional[str]:
        """Uygulamanın veritabanı dosyalarını listeler."""
        result = self.runner.run_root(
            f"ls -la /data/data/{self.package}/databases/ 2>/dev/null",
            timeout=5.0,
            retries=0,
        )
        return result.output if result.success else None

    def get_app_size(self) -> Optional[str]:
        """Uygulamanın veri dizini boyutunu döndürür."""
        result = self.runner.run_root(
            f"du -sh /data/data/{self.package}/ 2>/dev/null",
            timeout=5.0,
            retries=0,
        )
        return result.output if result.success else None

    # ----- İç Mekanizma ----------------------------------------------------

    def _do_clear(self, mode: ClearMode, db_name: str = "") -> LifecycleResult:
        """clear_mode'a göre uygun temizleme metodunu çağırır."""
        if mode == ClearMode.FULL:
            return self.clear_data()
        elif mode == ClearMode.CACHE_ONLY:
            return self.clear_cache()
        elif mode == ClearMode.DATABASE:
            if not db_name:
                return LifecycleResult(
                    success=False,
                    operation="clear_database",
                    message="db_name required for DATABASE mode",
                    elapsed_ms=0,
                )
            return self.clear_database(db_name)
        elif mode == ClearMode.SHARED_PREFS:
            return self.clear_shared_prefs()
        else:
            return LifecycleResult(
                success=False,
                operation="clear",
                message=f"Unknown clear mode: {mode}",
                elapsed_ms=0,
            )

    def _get_pid(self) -> Optional[int]:
        """Uygulamanın process ID'sini döndürür. Çalışmıyorsa None."""
        result = self.runner.run_root(
            f"pidof {self.package}",
            timeout=PROCESS_CHECK_TIMEOUT,
            retries=0,
        )

        if result.success and result.output:
            try:
                # pidof birden fazla PID dönebilir, ilkini al
                return int(result.output.split()[0])
            except (ValueError, IndexError):
                pass

        return None

    def _get_current_activity(self) -> Optional[str]:
        """Uygulamanın aktif Activity'sini sorgular."""
        result = self.runner.run_root(
            f"dumpsys activity top | grep 'ACTIVITY {self.package}' | tail -1",
            timeout=ACTIVITY_QUERY_TIMEOUT,
            retries=0,
        )

        if result.success and result.output:
            # "ACTIVITY com.pkg/.Activity hash pid=123" formatı
            parts = result.output.strip().split()
            for part in parts:
                if self.package in part:
                    return part

        return None
