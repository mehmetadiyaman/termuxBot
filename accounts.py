#!/usr/bin/env python3
"""
accounts.py — Viewpoints Hesap Yönetim Menüsü
===============================================
Yedeklenmiş hesapları listeleme, geri yükleme ve silme işlemleri
için kullanıcı dostu bir terminal menüsü sunar.

Kullanım:
  python accounts.py              → İnteraktif menü
  python accounts.py list         → Hesapları listele
  python accounts.py restore 3   → 3. hesabı geri yükle
  python accounts.py backup test  → Aktif hesabı "test" olarak yedekle
"""

import sys
import subprocess
import account_manager

PACKAGE = "com.facebook.viewpoints"


def print_header():
    print()
    print("=" * 55)
    print("  📱 Viewpoints Hesap Yöneticisi")
    print("=" * 55)


def print_accounts():
    """Tüm hesapları numaralı liste olarak gösterir."""
    accounts = account_manager.list_accounts()
    if not accounts:
        print("\n  ⚠️  Henüz yedeklenmiş hesap yok.\n")
        return accounts

    print(f"\n  📋 Kayıtlı Hesaplar ({len(accounts)} adet):\n")
    print(f"  {'No':<4} {'Etiket':<20} {'Email':<35} {'Tarih':<20} {'Boyut':<10}")
    print(f"  {'─'*4} {'─'*20} {'─'*35} {'─'*20} {'─'*10}")

    for i, acc in enumerate(accounts, 1):
        label = acc.get('label', '?')[:20]
        email = acc.get('email', 'N/A')[:35]
        date = acc.get('created', 'N/A')[:20]
        size = f"{acc.get('size_kb', 0)} KB"
        print(f"  {i:<4} {label:<20} {email:<35} {date:<20} {size:<10}")

    print()
    return accounts


def launch_app():
    """Viewpoints uygulamasını başlatır."""
    print("  🚀 Viewpoints başlatılıyor...")
    try:
        subprocess.run(
            f'su -c "am start -n {PACKAGE}/com.facebook.viewpoints.app.ViewpointsActivity"',
            shell=True, capture_output=True, timeout=10
        )
    except Exception:
        # Alternatif başlatma
        subprocess.run(
            f'su -c "monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1"',
            shell=True, capture_output=True, timeout=10
        )
    print("  ✅ Uygulama başlatıldı!\n")


def interactive_menu():
    """Etkileşimli menü gösterir."""
    while True:
        print_header()
        print()
        print("  1️⃣   Hesapları Listele")
        print("  2️⃣   Hesap Geri Yükle (Restore)")
        print("  3️⃣   Aktif Hesabı Yedekle (Backup)")
        print("  4️⃣   Hesap Sil")
        print("  5️⃣   Çıkış")
        print()

        try:
            choice = input("  Seçiminiz (1-5): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  👋 Görüşürüz!\n")
            break

        if choice == "1":
            print_accounts()

        elif choice == "2":
            accounts = print_accounts()
            if not accounts:
                continue
            try:
                num = input("  Geri yüklenecek hesap numarası: ").strip()
                idx = int(num) - 1
                if 0 <= idx < len(accounts):
                    acc = accounts[idx]
                    print(f"\n  🔄 '{acc['label']}' geri yükleniyor...\n")
                    success = account_manager.restore_account(acc['label'])
                    if success:
                        print(f"\n  ✅ Hesap geri yüklendi: {acc['label']}")
                        launch_app()
                    else:
                        print(f"\n  ❌ Geri yükleme başarısız!\n")
                else:
                    print("  ❌ Geçersiz numara!\n")
            except (ValueError, KeyboardInterrupt):
                print("  ❌ Geçersiz giriş!\n")

        elif choice == "3":
            label = input("  Hesap etiketi (isim): ").strip()
            email = input("  Email (opsiyonel): ").strip()
            if label:
                success = account_manager.backup_account(label, email=email)
                if success:
                    print(f"\n  ✅ Hesap yedeklendi: {label}\n")
                else:
                    print(f"\n  ❌ Yedekleme başarısız!\n")
            else:
                print("  ❌ Etiket boş olamaz!\n")

        elif choice == "4":
            accounts = print_accounts()
            if not accounts:
                continue
            try:
                num = input("  Silinecek hesap numarası: ").strip()
                idx = int(num) - 1
                if 0 <= idx < len(accounts):
                    acc = accounts[idx]
                    confirm = input(f"  ⚠️  '{acc['label']}' silinecek. Emin misiniz? (e/h): ").strip().lower()
                    if confirm == 'e':
                        account_manager.delete_account(acc['label'])
                        print(f"  ✅ Hesap silindi: {acc['label']}\n")
                    else:
                        print("  İptal edildi.\n")
                else:
                    print("  ❌ Geçersiz numara!\n")
            except (ValueError, KeyboardInterrupt):
                print("  ❌ Geçersiz giriş!\n")

        elif choice == "5":
            print("\n  👋 Görüşürüz!\n")
            break

        else:
            print("  ❌ Geçersiz seçim!\n")


def main():
    args = sys.argv[1:]

    if not args:
        interactive_menu()
        return

    cmd = args[0].lower()

    if cmd == "list":
        print_accounts()

    elif cmd == "restore":
        if len(args) < 2:
            print("  Kullanım: python accounts.py restore <numara veya etiket>")
            return
        target = args[1]
        accounts = account_manager.list_accounts()

        # Numara mı etiket mi?
        try:
            idx = int(target) - 1
            if 0 <= idx < len(accounts):
                acc = accounts[idx]
                success = account_manager.restore_account(acc['label'])
                if success:
                    launch_app()
            else:
                print(f"  ❌ Geçersiz numara: {target}")
        except ValueError:
            # Etiket olarak dene
            success = account_manager.restore_account(target)
            if success:
                launch_app()

    elif cmd == "backup":
        label = args[1] if len(args) > 1 else f"hesap_{account_manager.get_account_count() + 1}"
        email = args[2] if len(args) > 2 else ""
        account_manager.backup_account(label, email=email)

    else:
        print(f"  ❌ Bilinmeyen komut: {cmd}")
        print("  Kullanım: python accounts.py [list|restore|backup]")


if __name__ == "__main__":
    main()
