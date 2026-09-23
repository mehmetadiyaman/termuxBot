"""
ui_bridge.py — UI Etkileşim Köprüsü
======================================
uiautomator dump ile XML iskelet çekimi, element arama,
bounds→koordinat hesaplama ve ElementCache yönetimi.

Sorumluluklar:
  • uiautomator dump tetikleme ve XML okuma
  • Regex ile element bulma (text, content-desc, resource-id)
  • Bounds → merkez koordinat hesaplama
  • ElementCache — aynı ekranda tekrar dump çekmeden pozisyon kullanma
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from command_runner import CommandRunner, CommandResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DUMP_PATH: str = "/sdcard/window_dump.xml"          # uiautomator çıktı yolu
DUMP_TIMEOUT: float = 10.0                          # dump komutu timeout
DUMP_RETRY_WAIT: float = 1.0                        # dump başarısız olursa bekleme

# Bounds regex — "[left,top][right,bottom]" formatını parse eder
BOUNDS_PATTERN = re.compile(
    r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]'
)


# ---------------------------------------------------------------------------
# Veri Yapıları
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UIElement:
    """Ekranda bulunan bir UI element'i."""

    text: str
    content_desc: str
    resource_id: str
    class_name: str
    bounds_raw: str                  # Orijinal bounds string'i
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> tuple[int, int]:
        """Element'in merkez koordinatlarını döndürür."""
        cx = (self.left + self.right) // 2
        cy = (self.top + self.bottom) // 2
        return cx, cy

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def __repr__(self) -> str:
        label = self.text or self.content_desc or self.resource_id or "?"
        cx, cy = self.center
        return f"UIElement('{label}' @({cx},{cy}))"


@dataclass
class ElementCache:
    """
    Aynı ekrandaki element pozisyonlarını cache'ler.

    Bir dump çekildikten sonra bulunan tüm element'ler cache'e alınır.
    Ekran değişmediği sürece (aynı state'te kalınıyorsa) cache geçerlidir.
    Scroll veya sayfa geçişi olduğunda invalidate() çağrılır.
    """

    _elements: dict[str, UIElement] = field(default_factory=dict)
    _raw_xml: str = ""
    _timestamp: float = 0.0
    _valid: bool = False

    @property
    def is_valid(self) -> bool:
        return self._valid

    @property
    def age_ms(self) -> float:
        """Cache'in yaşını milisaniye olarak döndürür."""
        if self._timestamp == 0:
            return float("inf")
        return (time.monotonic() - self._timestamp) * 1000

    @property
    def xml(self) -> str:
        """Cache'teki ham XML'i döndürür."""
        return self._raw_xml

    def store(self, xml: str, elements: list[UIElement]) -> None:
        """Yeni dump sonuçlarını cache'e alır."""
        self._raw_xml = xml
        self._elements.clear()
        for elem in elements:
            # Birden fazla key ile indexle → hem text, hem content-desc,
            # hem resource-id ile aranabilsin
            for key in (elem.text, elem.content_desc, elem.resource_id):
                if key:
                    self._elements[key] = elem
        self._timestamp = time.monotonic()
        self._valid = True
        logger.debug("Cache updated: %d elements stored", len(elements))

    def get(self, identifier: str) -> Optional[UIElement]:
        """Cache'ten identifier ile element arar."""
        if not self._valid:
            return None
        return self._elements.get(identifier)

    def invalidate(self) -> None:
        """Cache'i geçersiz kılar (scroll, sayfa geçişi vb.)."""
        self._elements.clear()
        self._raw_xml = ""
        self._valid = False
        logger.debug("Cache invalidated")


# ---------------------------------------------------------------------------
# UIBridge
# ---------------------------------------------------------------------------
class UIBridge:
    """
    uiautomator dump üzerinden UI element'leri keşfeder ve etkileşim sağlar.

    Kullanım:
        runner = CommandRunner()
        ui = UIBridge(runner)

        # Tam dump çek ve element bul
        elem = ui.find_element_by_text("Sign In")
        if elem:
            runner.tap(*elem.center)

        # Cache'ten element bul (dump çekmez)
        elem = ui.find_in_cache("Sign In")

        # Regex ile arama
        elems = ui.find_elements_by_pattern(r"Step \\d+ of \\d+")
    """

    def __init__(
        self,
        runner: CommandRunner,
        dump_path: str = DUMP_PATH,
    ) -> None:
        self.runner = runner
        self.dump_path = dump_path
        self.cache = ElementCache()

    # ----- Dump Yönetimi ---------------------------------------------------

    def dump_ui(self, *, force: bool = False) -> Optional[str]:
        """
        uiautomator dump çeker ve XML içeriğini döndürür.

        Args:
            force: True ise cache geçerli olsa bile yeni dump çeker.

        Returns:
            XML string veya None (başarısız).
        """
        # Cache hâlâ geçerliyse ve force değilse, mevcut XML'i dön
        if self.cache.is_valid and not force:
            logger.debug("Using cached XML (age: %.0fms)", self.cache.age_ms)
            return self.cache.xml

        # Dump komutunu çalıştır (ADB shell üzerinden)
        result = self.runner.run(
            f"adb shell uiautomator dump {self.dump_path}",
            timeout=DUMP_TIMEOUT,
            retries=2,
        )

        if not result.success:
            # Fallback: direkt uiautomator dene
            result = self.runner.run_root(
                f"uiautomator dump {self.dump_path}",
                timeout=DUMP_TIMEOUT,
                retries=1,
            )

        if not result.success:
            logger.error(
                "uiautomator dump failed: rc=%d stderr='%s'",
                result.return_code, result.stderr[:200],
            )
            return None

        # XML dosyasını oku
        read_result = self.runner.run(
            f"adb shell cat {self.dump_path}",
            timeout=5.0,
            retries=1,
        )

        if not read_result.success or not read_result.output:
            # Fallback: direkt cat
            read_result = self.runner.run_root(
                f"cat {self.dump_path}",
                timeout=5.0,
                retries=1,
            )

        if not read_result.success or not read_result.output:
            logger.error("Failed to read dump file at %s", self.dump_path)
            return None

        xml_content = read_result.output
        logger.debug(
            "Dump OK [%.0fms] — XML length: %d chars",
            result.elapsed_ms, len(xml_content),
        )

        # Tüm element'leri parse et ve cache'e al
        elements = self._parse_all_elements(xml_content)
        self.cache.store(xml_content, elements)

        return xml_content

    # ----- Element Arama — Text -------------------------------------------

    def find_element_by_text(
        self, text: str, *, dump: bool = True
    ) -> Optional[UIElement]:
        """
        Ekranda belirtilen text'e sahip element'i bulur.

        Args:
            text:  Aranacak tam metin.
            dump:  True ise önce dump çeker (cache geçersizse).

        Returns:
            UIElement veya None.
        """
        # Önce cache'e bak
        cached = self.cache.get(text)
        if cached is not None:
            logger.debug("Cache hit: '%s' → %s", text, cached)
            return cached

        # Cache'te yoksa dump çek
        if dump:
            xml = self.dump_ui()
            if xml is None:
                return None
            # Dump sonrası cache'e tekrar bak
            return self.cache.get(text)

        return None

    # ----- Element Arama — Content-Desc -----------------------------------

    def find_element_by_desc(
        self, desc: str, *, dump: bool = True
    ) -> Optional[UIElement]:
        """content-desc değeriyle element bulur."""
        cached = self.cache.get(desc)
        if cached is not None:
            return cached

        if dump:
            xml = self.dump_ui()
            if xml is None:
                return None
            return self.cache.get(desc)

        return None

    # ----- Element Arama — Resource ID ------------------------------------

    def find_element_by_id(
        self, resource_id: str, *, dump: bool = True
    ) -> Optional[UIElement]:
        """resource-id değeriyle element bulur."""
        cached = self.cache.get(resource_id)
        if cached is not None:
            return cached

        if dump:
            xml = self.dump_ui()
            if xml is None:
                return None
            return self.cache.get(resource_id)

        return None

    # ----- Element Arama — Regex (her zaman XML üzerinden) ----------------

    def find_elements_by_pattern(
        self,
        pattern: str,
        *,
        field: str = "text",
        dump: bool = True,
    ) -> list[UIElement]:
        """
        Regex pattern'i ile eşleşen tüm element'leri döndürür.

        Args:
            pattern:  Regex deseni.
            field:    Hangi attribute'ta aranacağı: "text", "content-desc",
                      "resource-id".
            dump:     True ise gerektiğinde yeni dump çeker.

        Returns:
            Eşleşen UIElement listesi.
        """
        xml = self.cache.xml if self.cache.is_valid else None

        if xml is None and dump:
            xml = self.dump_ui()

        if xml is None:
            return []

        compiled = re.compile(pattern)
        results: list[UIElement] = []

        for elem in self._parse_all_elements(xml):
            target_value = getattr(elem, field.replace("-", "_"), "")
            if target_value and compiled.search(target_value):
                results.append(elem)

        return results

    # ----- Element Bul ve Tıkla (Convenience) -----------------------------

    def tap_element_by_text(
        self, text: str, *, dump: bool = True
    ) -> bool:
        """
        Text'e göre element bulur ve merkez koordinatına tıklar.

        Returns:
            True: element bulundu ve tıklandı.
            False: element bulunamadı.
        """
        elem = self.find_element_by_text(text, dump=dump)
        if elem is None:
            logger.warning("tap_element_by_text: '%s' not found", text)
            return False

        cx, cy = elem.center
        self.runner.tap(cx, cy)
        logger.info("Tapped '%s' at (%d, %d)", text, cx, cy)

        # Tap sonrası cache'i invalidate et — ekran değişmiş olabilir
        self.cache.invalidate()
        return True

    def tap_element_by_desc(
        self, desc: str, *, dump: bool = True
    ) -> bool:
        """content-desc'e göre element bulur ve tıklar."""
        elem = self.find_element_by_desc(desc, dump=dump)
        if elem is None:
            logger.warning("tap_element_by_desc: '%s' not found", desc)
            return False

        cx, cy = elem.center
        self.runner.tap(cx, cy)
        logger.info("Tapped desc='%s' at (%d, %d)", desc, cx, cy)
        self.cache.invalidate()
        return True

    def tap_element_by_id(
        self, resource_id: str, *, dump: bool = True
    ) -> bool:
        """resource-id'ye göre element bulur ve tıklar."""
        elem = self.find_element_by_id(resource_id, dump=dump)
        if elem is None:
            logger.warning("tap_element_by_id: '%s' not found", resource_id)
            return False

        cx, cy = elem.center
        self.runner.tap(cx, cy)
        logger.info("Tapped id='%s' at (%d, %d)", resource_id, cx, cy)
        self.cache.invalidate()
        return True

    # ----- Cache Kısa Yollar -----------------------------------------------

    def find_in_cache(self, identifier: str) -> Optional[UIElement]:
        """Sadece cache'ten arar, dump çekmez."""
        return self.cache.get(identifier)

    def invalidate_cache(self) -> None:
        """Cache'i manuel olarak geçersiz kılar."""
        self.cache.invalidate()

    # ----- Ekran Durumu Sorguları ------------------------------------------

    def is_text_on_screen(
        self, text: str, *, dump: bool = True
    ) -> bool:
        """Belirtilen text ekranda var mı?"""
        return self.find_element_by_text(text, dump=dump) is not None

    def wait_for_text(
        self,
        text: str,
        *,
        timeout: float = 10.0,
        poll_interval: float = 1.0,
    ) -> Optional[UIElement]:
        """
        Belirtilen text ekranda görünene kadar bekler.

        Her poll_interval saniyede bir dump çeker.
        Timeout aşılırsa None döner.
        """
        deadline = time.monotonic() + timeout
        attempt = 0

        while time.monotonic() < deadline:
            attempt += 1
            self.cache.invalidate()  # Her yoklamada taze dump
            elem = self.find_element_by_text(text, dump=True)
            if elem is not None:
                logger.info(
                    "wait_for_text: '%s' found after %d polls", text, attempt
                )
                return elem

            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_interval, remaining))

        logger.warning(
            "wait_for_text: '%s' NOT found after %.1fs timeout", text, timeout
        )
        return None

    def wait_for_any_text(
        self,
        texts: list[str],
        *,
        timeout: float = 10.0,
        poll_interval: float = 1.0,
    ) -> Optional[UIElement]:
        """
        Verilen text listesinden herhangi biri ekranda
        görünene kadar bekler. İlk bulunanı döndürür.
        """
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            self.cache.invalidate()
            self.dump_ui(force=True)

            for text in texts:
                elem = self.cache.get(text)
                if elem is not None:
                    logger.info("wait_for_any_text: found '%s'", text)
                    return elem

            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_interval, remaining))

        logger.warning(
            "wait_for_any_text: none of %s found after %.1fs", texts, timeout
        )
        return None

    # ----- İç Mekanizma: XML Parsing ---------------------------------------

    def _parse_all_elements(self, xml: str) -> list[UIElement]:
        """
        Ham XML'den tüm node'ları parse eder ve UIElement listesi döndürür.

        uiautomator dump XML formatı (her node bir <node> tag'i):
          <node index="0" text="Hello" resource-id="com.app:id/btn"
                class="android.widget.Button" content-desc=""
                bounds="[0,0][100,100]" ... />

        Regex tabanlı parse kullanılır — xml.etree yerine, çünkü
        uiautomator dump bazen malformed XML üretebilir.
        """
        elements: list[UIElement] = []

        # Her <node ... /> tag'ini yakala
        node_pattern = re.compile(r'<node\s+[^>]+/?>', re.DOTALL)

        for match in node_pattern.finditer(xml):
            node_str = match.group(0)

            text = self._extract_attr(node_str, "text")
            content_desc = self._extract_attr(node_str, "content-desc")
            resource_id = self._extract_attr(node_str, "resource-id")
            class_name = self._extract_attr(node_str, "class")
            bounds_raw = self._extract_attr(node_str, "bounds")

            # Bounds parse
            bounds_match = BOUNDS_PATTERN.search(bounds_raw)
            if not bounds_match:
                continue  # Bounds olmayan node'u atla

            left, top, right, bottom = (
                int(bounds_match.group(1)),
                int(bounds_match.group(2)),
                int(bounds_match.group(3)),
                int(bounds_match.group(4)),
            )

            # Sıfır boyutlu element'leri atla (görünmez)
            if left >= right or top >= bottom:
                continue

            elements.append(
                UIElement(
                    text=text,
                    content_desc=content_desc,
                    resource_id=resource_id,
                    class_name=class_name,
                    bounds_raw=bounds_raw,
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                )
            )

        logger.debug("Parsed %d elements from XML", len(elements))
        return elements

    @staticmethod
    def _extract_attr(node_str: str, attr_name: str) -> str:
        """
        Bir <node> string'inden belirli bir attribute değerini çeker.

        Ör: text="Hello" → "Hello"
        """
        pattern = re.compile(
            rf'{attr_name}="([^"]*)"'
        )
        match = pattern.search(node_str)
        return match.group(1) if match else ""
