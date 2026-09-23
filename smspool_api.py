"""
smspool_api.py — SMSPool API İstemcisi v2
==========================================
urllib tabanlı — Termux'ta ekstra paket gerektirmez.
Retry + tüm status kodları desteği.
"""

from __future__ import annotations
import json
import time
import logging
import urllib.request
import urllib.parse

log = logging.getLogger(__name__)

API_BASE = "https://api.smspool.net"
API_KEY = "3aOdF4F7vurgXP5pUmRzQeOyMHDY2Eh0"

# Viewpoints service ID
SERVICE_VIEWPOINTS = 329
COUNTRY_US = 1


class SMSPoolError(Exception):
    pass


def _post(endpoint: str, data: dict, retries: int = 3) -> dict:
    """API'ye POST isteği gönder (retry destekli)."""
    data["key"] = API_KEY
    encoded = urllib.parse.urlencode(data).encode()
    url = f"{API_BASE}/{endpoint}"
    req = urllib.request.Request(url, data=encoded)

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode()
                return json.loads(body)
        except Exception as e:
            last_err = e
            if attempt < retries:
                log.warning("API retry %d/%d: %s — %s", attempt, retries, endpoint, e)
                time.sleep(2)
            else:
                log.error("API failed after %d retries: %s — %s", retries, endpoint, e)

    raise SMSPoolError(str(last_err))


def get_balance() -> float:
    """Bakiye sorgula."""
    r = _post("request/balance", {})
    return float(r.get("balance", 0))


def order_number(
    service: int = SERVICE_VIEWPOINTS,
    country: int = COUNTRY_US,
) -> dict:
    """
    Numara satın al.

    Returns:
        {"success": 1, "phonenumber": "2334567890",
         "order_id": "XXXX", "cc": "1", ...}
    """
    r = _post("purchase/sms", {
        "service": str(service),
        "country": str(country),
    })
    if r.get("success") != 1:
        raise SMSPoolError(f"Order failed: {r}")
    log.info(
        "Number ordered: +%s%s (order=%s)",
        r.get("cc", "?"), r.get("phonenumber", "?"),
        r.get("order_id", "?"),
    )
    return r


def check_sms(order_id: str) -> dict:
    """
    SMS durumunu kontrol et.

    Status codes (bilinen):
        1 = Bekleniyor (pending)
        2 = Tamamlandı (bazı sürümlerde)
        3 = SMS alındı ("sms" field'ında kod var)
        4 = İptal/Refund
        5 = Süresi doldu
        6 = Refund edildi
        8 = Tamamlandı (yeni API)
    """
    return _post("sms/check", {"orderid": order_id})


def _extract_code(sms_text: str) -> str | None:
    """SMS metninden 4-8 haneli kodu çıkar."""
    if not sms_text:
        return None
    # Sadece rakamları çıkar
    digits = "".join(c for c in sms_text if c.isdigit())
    if len(digits) >= 4:
        return digits[:6] if len(digits) >= 6 else digits
    return sms_text.strip() if sms_text.strip() else None


def wait_for_code(
    order_id: str,
    timeout: float = 120,
    poll_interval: float = 3,
) -> str | None:
    """
    SMS kodunu bekle. Gelince kodu döndür, timeout/hata olursa None.
    """
    start = time.monotonic()
    TERMINAL_STATUSES = {4, 5, 6}  # iptal/süre doldu/refund

    while (time.monotonic() - start) < timeout:
        try:
            r = check_sms(order_id)
        except SMSPoolError as e:
            log.warning("Check API hatası (devam ediliyor): %s", e)
            time.sleep(poll_interval)
            continue

        status = r.get("status")
        sms = r.get("sms", "")
        full_code = r.get("full_code", "")

        # SMS kodu var mı? (status ne olursa olsun)
        if sms:
            code = _extract_code(sms)
            if code:
                log.info("SMS code received (status=%s): %s", status, code)
                return code

        if full_code:
            code = _extract_code(full_code)
            if code:
                log.info("Full code received (status=%s): %s", status, code)
                return code

        # Status 3 veya 8 = tamamlandı ama sms boş olabilir
        if status in (2, 3, 8) and not sms and not full_code:
            log.info("Status=%s but no SMS yet, waiting...", status)

        # Terminal durumlar — beklemeyi kes
        if status in TERMINAL_STATUSES:
            log.error("Order terminal state: status=%s (order=%s)", status, order_id)
            return None

        elapsed = time.monotonic() - start
        log.info(
            "Waiting for SMS... (status=%s, elapsed=%.0fs)",
            status, elapsed,
        )
        time.sleep(poll_interval)

    log.error("SMS timeout after %.0fs", timeout)
    return None


def cancel_order(order_id: str) -> bool:
    """Siparişi iptal et."""
    try:
        r = _post("sms/cancel", {"orderid": order_id})
        return r.get("success") == 1
    except Exception:
        return False
