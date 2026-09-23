"""
device_spoofer.py — Cihaz Parmak İzi Değiştirici (Anti-Detect)
===============================================================
Root yetkisi kullanarak Android cihazın tüm tanımlayıcı bilgilerini
(Android ID, MAC, Bluetooth, Advertising ID) rastgele değiştirir.
Facebook her hesap açılışında yepyeni bir telefon görür.

Kullanım:
  device_spoofer.randomize_all()  → Tüm bilgileri değiştirir
  device_spoofer.get_current()    → Mevcut bilgileri okur
"""

import logging
import os
import random
import subprocess
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s │ %(levelname)-7s │ %(name)-20s │ %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('device_spoofer')


def _run_root(cmd: str, timeout: int = 10) -> tuple[int, str]:
    """Root yetkisiyle komut çalıştırır."""
    full_cmd = f'su -c "{cmd}"'
    try:
        result = subprocess.run(
            full_cmd, shell=True, capture_output=True,
            text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip()
    except Exception as e:
        return -1, str(e)


def _random_hex(length: int) -> str:
    """Rastgele hex string üretir."""
    return ''.join(random.choice('0123456789abcdef') for _ in range(length))


def _random_mac() -> str:
    """Rastgele MAC adresi üretir (ilk byte çift olmalı — unicast)."""
    # İlk byte'ın son biti 0 olmalı (unicast)
    first_byte = random.randint(0, 255) & 0xFE
    octets = [first_byte] + [random.randint(0, 255) for _ in range(5)]
    return ':'.join(f'{b:02x}' for b in octets)


def _random_uuid() -> str:
    """Rastgele UUID (Google Advertising ID formatı) üretir."""
    parts = [_random_hex(8), _random_hex(4), _random_hex(4), _random_hex(4), _random_hex(12)]
    return '-'.join(parts)


# ── Okuma Fonksiyonları ─────────────────────────────────────

def get_current() -> dict:
    """Cihazın mevcut tüm tanımlayıcılarını okur."""
    info = {}

    rc, val = _run_root("settings get secure android_id")
    info['android_id'] = val if rc == 0 else "?"

    rc, val = _run_root("settings get secure bluetooth_address")
    info['bluetooth_mac'] = val if rc == 0 else "?"

    rc, val = _run_root("cat /sys/class/net/wlan0/address")
    info['wifi_mac'] = val if rc == 0 else "?"

    rc, val = _run_root("settings get secure advertising_id")
    info['advertising_id'] = val if rc == 0 and val != 'null' else "yok"

    rc, val = _run_root("getprop ro.serialno")
    info['serial'] = val if rc == 0 and val else "yok"

    return info


# ── Değiştirme Fonksiyonları ────────────────────────────────

def spoof_android_id() -> str:
    """Android ID'yi rastgele değiştirir."""
    new_id = _random_hex(16)
    rc, _ = _run_root(f"settings put secure android_id {new_id}")
    if rc == 0:
        logger.info(f"✅ Android ID → {new_id}")
        return new_id
    else:
        logger.warning("⚠️ Android ID değiştirilemedi")
        return ""


def spoof_bluetooth_mac() -> str:
    """Bluetooth MAC adresini rastgele değiştirir."""
    new_mac = _random_mac().upper()
    rc, _ = _run_root(f"settings put secure bluetooth_address {new_mac}")
    if rc == 0:
        logger.info(f"✅ Bluetooth MAC → {new_mac}")
        return new_mac
    else:
        logger.warning("⚠️ Bluetooth MAC değiştirilemedi")
        return ""


def spoof_wifi_mac() -> str:
    """WiFi MAC adresini rastgele değiştirir (root gerektirir)."""
    new_mac = _random_mac()
    # WiFi arayüzünü kapatıp MAC değiştir, tekrar aç
    _run_root("ip link set wlan0 down")
    time.sleep(0.3)
    rc, _ = _run_root(f"ip link set wlan0 address {new_mac}")
    _run_root("ip link set wlan0 up")
    time.sleep(0.5)

    if rc == 0:
        logger.info(f"✅ WiFi MAC → {new_mac}")
        return new_mac
    else:
        logger.warning("⚠️ WiFi MAC değiştirilemedi (bazı cihazlarda kısıtlı)")
        return ""


def spoof_advertising_id() -> str:
    """Google Advertising ID'yi sıfırlar/değiştirir."""
    new_gaid = _random_uuid()
    # GMS verilerini temizle (GAID'i sıfırlar)
    _run_root("rm -f /data/data/com.google.android.gms/shared_prefs/adid_settings.xml")
    rc, _ = _run_root(f"settings put secure advertising_id {new_gaid}")
    if rc == 0:
        logger.info(f"✅ Advertising ID → {new_gaid}")
        return new_gaid
    else:
        logger.warning("⚠️ Advertising ID değiştirilemedi")
        return ""


def spoof_device_serial() -> str:
    """Cihaz seri numarasını değiştirmeyi dener."""
    new_serial = _random_hex(16).upper()
    rc, _ = _run_root(f"setprop ro.serialno {new_serial}")
    rc2, _ = _run_root(f"setprop ro.boot.serialno {new_serial}")
    if rc == 0 or rc2 == 0:
        logger.info(f"✅ Serial → {new_serial}")
        return new_serial
    else:
        logger.warning("⚠️ Serial değiştirilemedi (salt okunur olabilir)")
        return ""


# ── Ana Fonksiyon ───────────────────────────────────────────

def randomize_all() -> dict:
    """
    Tüm cihaz tanımlayıcılarını rastgele değiştirir.
    Her hesap açılışından önce çağrılmalıdır.

    Returns:
        Değiştirilen bilgilerin sözlüğü
    """
    print("\n" + "=" * 55)
    print("🔄 CİHAZ KİMLİĞİ DEĞİŞTİRİLİYOR (Anti-Detect)")
    print("=" * 55)

    results = {}
    results['android_id'] = spoof_android_id()
    results['bluetooth_mac'] = spoof_bluetooth_mac()
    results['wifi_mac'] = spoof_wifi_mac()
    results['advertising_id'] = spoof_advertising_id()
    results['serial'] = spoof_device_serial()

    # Değişikliklerin uygulanması için kısa bekleme
    time.sleep(1)

    changed = sum(1 for v in results.values() if v)
    print(f"\n✅ {changed}/5 tanımlayıcı değiştirildi.")
    print("   Facebook bu cihazı yepyeni bir telefon olarak görecek!")
    print("=" * 55 + "\n")

    return results


# ── Test / CLI ──────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        info = get_current()
        print("\n📱 Mevcut Cihaz Bilgileri:\n")
        print(f"  Android ID:      {info['android_id']}")
        print(f"  Bluetooth MAC:   {info['bluetooth_mac']}")
        print(f"  WiFi MAC:        {info['wifi_mac']}")
        print(f"  Advertising ID:  {info['advertising_id']}")
        print(f"  Serial:          {info['serial']}")
        print()
    else:
        randomize_all()
        print("Yeni bilgiler:")
        info = get_current()
        for k, v in info.items():
            print(f"  {k}: {v}")
