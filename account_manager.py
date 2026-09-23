"""
account_manager.py — Viewpoints Hesap Yedekleme/Geri Yükleme Sistemi
=====================================================================
Root yetkisi kullanarak Viewpoints uygulamasının tüm verilerini
(oturum, çerezler, tercihler) yedekler ve geri yükler.

Kullanım:
  - backup_account(label): Aktif hesabı yedekler
  - restore_account(label): Seçilen hesabı geri yükler
  - list_accounts(): Tüm yedeklenmiş hesapları listeler
  - delete_account(label): Bir yedeği siler
"""

import os
import subprocess
import json
import logging
import time
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s │ %(levelname)-7s │ %(name)-20s │ %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('account_manager')

# ── Sabitler ────────────────────────────────────────────────
PACKAGE = "com.facebook.viewpoints"
APP_DATA_DIR = f"/data/data/{PACKAGE}"
BACKUP_ROOT = os.path.expanduser("~/viewpoints_accounts")
MANIFEST_FILE = os.path.join(BACKUP_ROOT, "accounts.json")


def _run_root(cmd: str, timeout: int = 30) -> tuple[int, str]:
    """Root yetkisiyle komut çalıştırır."""
    full_cmd = f'su -c "{cmd}"'
    try:
        result = subprocess.run(
            full_cmd, shell=True, capture_output=True,
            text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.error(f"Komut zaman aşımı: {cmd}")
        return -1, ""
    except Exception as e:
        logger.error(f"Komut hatası: {e}")
        return -1, str(e)


def _ensure_dirs():
    """Yedekleme klasörlerinin var olduğundan emin olur."""
    os.makedirs(BACKUP_ROOT, exist_ok=True)


def _load_manifest() -> dict:
    """Hesap manifest dosyasını yükler."""
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"accounts": []}
    return {"accounts": []}


def _save_manifest(data: dict):
    """Hesap manifest dosyasını kaydeder."""
    with open(MANIFEST_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def backup_account(label: str, email: str = "", phone: str = "") -> bool:
    """
    Aktif Viewpoints hesabının tüm verilerini yedekler.
    
    Args:
        label: Hesap etiketi (örn: "hesap_1" veya mail adresi)
        email: Hesaba ait e-posta (kayıt amaçlı)
        phone: Hesaba ait telefon numarası (kayıt amaçlı)
    
    Returns:
        Başarılıysa True
    """
    _ensure_dirs()
    
    # Güvenli dosya adı oluştur
    safe_label = label.replace("@", "_at_").replace(".", "_")
    backup_path = os.path.join(BACKUP_ROOT, f"{safe_label}.tar.gz")
    
    logger.info(f"📦 Hesap yedekleniyor: {label}")
    
    # 1. Uygulamayı durdur (veri tutarlılığı için)
    logger.info("Uygulama durduruluyor...")
    _run_root(f"am force-stop {PACKAGE}")
    time.sleep(1)
    
    # 2. Uygulama verisini tar.gz olarak yedekle
    logger.info(f"Veri sıkıştırılıyor: {backup_path}")
    rc, out = _run_root(
        f"tar czf {backup_path} -C /data/data {PACKAGE}",
        timeout=60
    )
    
    if rc != 0:
        logger.error(f"Yedekleme başarısız! Çıktı: {out}")
        return False
    
    # 3. Yedek dosyasının oluştuğunu doğrula
    rc, size = _run_root(f"stat -c %s {backup_path}")
    if rc != 0 or not size or size == "0":
        logger.error("Yedek dosyası oluşturulamadı veya boş!")
        return False
    
    file_size_kb = int(size) / 1024
    logger.info(f"✅ Yedek oluşturuldu: {file_size_kb:.1f} KB")
    
    # 4. Manifest'e kaydet
    manifest = _load_manifest()
    
    # Aynı etiketle eski yedek varsa güncelle
    manifest["accounts"] = [
        a for a in manifest["accounts"] if a["label"] != label
    ]
    
    manifest["accounts"].append({
        "label": label,
        "email": email,
        "phone": phone,
        "file": f"{safe_label}.tar.gz",
        "size_kb": round(file_size_kb, 1),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    
    _save_manifest(manifest)
    logger.info(f"✅ Hesap kaydedildi: {label} ({email})")
    return True


def restore_account(label: str) -> bool:
    """
    Yedeklenmiş bir hesabı geri yükler.
    
    Args:
        label: Geri yüklenecek hesabın etiketi
    
    Returns:
        Başarılıysa True
    """
    manifest = _load_manifest()
    
    # Hesabı bul
    account = None
    for a in manifest["accounts"]:
        if a["label"] == label:
            account = a
            break
    
    if not account:
        logger.error(f"Hesap bulunamadı: {label}")
        return False
    
    backup_path = os.path.join(BACKUP_ROOT, account["file"])
    
    # Yedek dosyasının var olduğunu kontrol et
    if not os.path.exists(backup_path):
        logger.error(f"Yedek dosyası bulunamadı: {backup_path}")
        return False
    
    logger.info(f"🔄 Hesap geri yükleniyor: {label} ({account.get('email', '')})")
    
    # 1. Uygulamayı durdur
    logger.info("Uygulama durduruluyor...")
    _run_root(f"am force-stop {PACKAGE}")
    time.sleep(1)
    
    # 2. Mevcut uygulama verisini temizle
    logger.info("Mevcut veri temizleniyor...")
    _run_root(f"pm clear {PACKAGE}")
    time.sleep(1)
    
    # 3. Yedeği geri yükle
    logger.info("Yedek geri yükleniyor...")
    rc, out = _run_root(
        f"tar xzf {backup_path} -C /data/data",
        timeout=60
    )
    
    if rc != 0:
        logger.error(f"Geri yükleme başarısız! Çıktı: {out}")
        return False
    
    # 4. Dosya sahipliğini düzelt (çok önemli!)
    logger.info("Dosya izinleri düzeltiliyor...")
    # Uygulamanın UID'sini bul
    rc, uid_line = _run_root(f"stat -c %u {APP_DATA_DIR}")
    if rc == 0 and uid_line:
        uid = uid_line.strip()
        _run_root(f"chown -R {uid}:{uid} {APP_DATA_DIR}")
        _run_root(f"restorecon -R {APP_DATA_DIR}")
    
    logger.info(f"✅ Hesap geri yüklendi: {label}")
    return True


def list_accounts() -> list[dict]:
    """Tüm yedeklenmiş hesapları listeler."""
    manifest = _load_manifest()
    return manifest.get("accounts", [])


def delete_account(label: str) -> bool:
    """Bir hesap yedeğini siler."""
    manifest = _load_manifest()
    
    account = None
    for a in manifest["accounts"]:
        if a["label"] == label:
            account = a
            break
    
    if not account:
        logger.error(f"Hesap bulunamadı: {label}")
        return False
    
    # Dosyayı sil
    backup_path = os.path.join(BACKUP_ROOT, account["file"])
    if os.path.exists(backup_path):
        os.remove(backup_path)
    
    # Manifest'ten kaldır
    manifest["accounts"] = [
        a for a in manifest["accounts"] if a["label"] != label
    ]
    _save_manifest(manifest)
    
    logger.info(f"🗑️  Hesap silindi: {label}")
    return True


def get_account_count() -> int:
    """Toplam yedeklenmiş hesap sayısını döndürür."""
    return len(list_accounts())


if __name__ == "__main__":
    # Test: Hesapları listele
    accounts = list_accounts()
    if accounts:
        print(f"\n📋 Kayıtlı hesaplar ({len(accounts)} adet):\n")
        for i, acc in enumerate(accounts, 1):
            print(f"  {i}. {acc['label']}")
            print(f"     📧 {acc.get('email', 'N/A')}")
            print(f"     📱 {acc.get('phone', 'N/A')}")
            print(f"     📅 {acc.get('created', 'N/A')}")
            print(f"     💾 {acc.get('size_kb', 0)} KB")
            print()
    else:
        print("\n📋 Henüz yedeklenmiş hesap yok.\n")
