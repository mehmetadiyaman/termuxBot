"""
bot.py — Viewpoints Full Onboarding v4
=======================================
11-state akış: SMSPool (telefon) + GuerrillaMail (e-posta) + Otomatik Yedekleme
Koordinatlar: Ekran görüntülerinden kalibrasyon yapıldı.
Profil formu: XML dump ile dropdown/picker yönetimi.
Hesap yedekleme: Root ile uygulama verisi tar.gz olarak saklanır.
"""

from __future__ import annotations
import logging
import sys
import time

from state_machine import State, Action, ActionType, Transition
from screen_validator import ValidationLevel
from app_lifecycle import ClearMode
from flow_runner import FlowRunner, FlowDefinition
import smspool_api
import email_api
import account_manager
import device_spoofer
from identity_generator import generate_identity

LOG_LEVEL = logging.INFO
PACKAGE_NAME = "com.facebook.viewpoints"

# ── Sabit Koordinatlar (ekran görüntülerinden kalibre) ──────
# Onboarding
COORD_ONBOARDING_CONTINUE = (359, 1472)

# Telefon girişi (3ekran.png)
COORD_PHONE_BOX      = (359, 450)    # "Mobile number" text field
COORD_PHONE_CONTINUE = (359, 603)    # Continue butonu

# SMS dialog
COORD_SMS_OK = (359, 859)

# Kod girişi (1.png) — ilk alt çizgi kutusu
COORD_CODE_FIRST_BOX = (110, 505)

# Profil form (XML dump'tan kesin koordinatlar)
# First name label: [32,470][222,524] input: [32,530][328,568]
COORD_FIRST_NAME  = (180, 500)
# Last name label: [393,470][581,524] input: [393,530][688,568]
COORD_LAST_NAME   = (540, 500)
# State dropdown: label [393,590][486,644]
COORD_STATE_DROP  = (540, 620)
# Date of birth: container [32,670][327,808] label [32,710][253,764]
COORD_DOB_DROP    = (180, 740)
# Gender: label [393,710][521,764]
COORD_GENDER_DROP = (540, 740)
# Language: container [32,790][688,928] label [32,830][667,884]
COORD_LANG_DROP   = (350, 860)
# Next button: [32,1432][688,1512]
COORD_NEXT_BTN    = (360, 1472)

# DOB picker (74.png) — 3 sütunlu scroll dialog
COORD_DOB_MONTH  = (185, 675)    # Ay sütunu (ortadaki seçili satır)
COORD_DOB_DAY    = (340, 675)    # Gün sütunu
COORD_DOB_YEAR   = (490, 675)    # Yıl sütunu
COORD_DOB_OK     = (500, 965)    # OK butonu
COORD_DOB_CANCEL = (360, 965)    # Cancel butonu

# Dil seçimi (76.png) — Search + checkbox listesi
COORD_LANG_SEARCH = (350, 340)   # Search kutusu
COORD_LANG_FIRST  = (350, 540)   # İlk sonuç
COORD_LANG_DONE   = (360, 1472)  # Done butonu (Next ile aynı yer)

# Sonraki ekranlar
COORD_ALLOW_CONTINUE    = (359, 1472)
COORD_EMAIL_BOX         = (350, 640)
COORD_EMAIL_CHECKBOX    = (57, 1267)
COORD_CONFIRM_BTN       = (359, 1472)
COORD_CONTINUE_PROGRAMS = (359, 1472)

# ── Global state ────────────────────────────────────────────
ORDER_DATA: dict = {}
IDENTITY: dict = {}
USER_EMAIL: str = ""
EMAIL_TOKEN: str = ""


# ── Custom Action'lar ───────────────────────────────────────

def trigger_text_validation(runner):
    """
    Android 'input text' bazen uygulamanın onChange event'lerini tetiklemez.
    Bunu aşmak için bir boşluk (SPACE=62) ekleyip siliyoruz (DEL=67).
    """
    runner.keyevent(62)
    time.sleep(0.1)
    runner.keyevent(67)
    time.sleep(0.1)

def enter_phone_number(machine) -> None:
    """SMSPool'dan numara al ve telefon ekranına gir."""
    global ORDER_DATA
    log = logging.getLogger(__name__)
    runner = machine.runner

    log.info("SMSPool'dan US numara alınıyor...")
    ORDER_DATA = smspool_api.order_number()
    phone = ORDER_DATA.get("phonenumber", "")
    log.info("Numara: +1%s (order=%s)", phone, ORDER_DATA.get("order_id"))

    # Numara kutusuna tıkla
    runner.tap(*COORD_PHONE_BOX)
    time.sleep(0.3)
    runner.input_text(phone)
    trigger_text_validation(runner)
    time.sleep(0.2)

    # Klavyeyi kapat + Continue
    runner.dismiss_keyboard()
    time.sleep(0.4)
    runner.tap(*COORD_PHONE_CONTINUE)
    time.sleep(1.0)


def wait_and_enter_code(machine) -> None:
    """SMSPool'dan kodu bekle ve tek tek tuşla."""
    log = logging.getLogger(__name__)
    order_id = ORDER_DATA.get("order_id")
    if not order_id:
        log.error("order_id yok!")
        return

    log.info("SMS kodu bekleniyor (order=%s)...", order_id)
    code = smspool_api.wait_for_code(order_id, timeout=30, poll_interval=3)
    if not code:
        log.error("SMS kodu alınamadı! İade oluşturuluyor ve bot yeniden başlatılıyor...")
        smspool_api.cancel_order(order_id)
        raise Exception("RESTART_FLOW_SMS_TIMEOUT")

    log.info("Kod giriliyor: %s", code)
    runner = machine.runner

    # İlk kutuya tıkla, klavyenin açılmasını bekle
    runner.tap(*COORD_CODE_FIRST_BOX)
    time.sleep(1.0)

    # Her haneli tek tek gir
    for digit in code:
        runner.input_text(digit)
        time.sleep(0.3)

    # Doğrulama sonrası sayfanın geçişini bekle
    time.sleep(3.0)


def wait_for_user_profile(machine) -> None:
    """Kullanıcının profil formunu elle doldurmasını bekle."""
    print("\n" + "=" * 55)
    print("👤 PROFİL FORMU (MANUEL MÜDAHALE BEKLENİYOR)")
    print("   Lütfen telefonda formu kendiniz eksiksiz doldurun.")
    print("   Next'e basıp formu bitirin.")
    print("   Bot sayfayı izliyor... Form kaybolduğunda OTOMATİK devam edecek.")
    print("=" * 55)
    
    while True:
        machine.ui.invalidate_cache()
        # "First name" yazısı ekrandan gidince sonraki sayfaya geçildiğini anlıyoruz
        elem = machine.ui.find_element_by_text("First name", dump=True)
        if not elem:
            print("✅ Form ekranı geçildi! Bot tekrar devrede.")
            time.sleep(2.0)
            break
        time.sleep(3.0)

def enter_email(machine) -> None:
    """GuerrillaMail API'den otomatik email adresi alır ve forma girer."""
    global USER_EMAIL, EMAIL_TOKEN
    log = logging.getLogger(__name__)

    log.info("GuerrillaMail e-posta hesabı oluşturuluyor...")
    email, password, token = email_api.create_account()
    
    if not email:
        log.error("E-posta hesabı oluşturulamadı!")
        return

    USER_EMAIL = email
    EMAIL_TOKEN = token
    log.info("Email oluşturuldu: %s", USER_EMAIL)
    print(f"\n📧 Bot Email'i aldı: {USER_EMAIL}")

    runner = machine.runner
    runner.tap(*COORD_EMAIL_BOX)
    time.sleep(0.5)
    runner.input_text(USER_EMAIL)
    trigger_text_validation(runner)
    time.sleep(0.3)
    runner.dismiss_keyboard()
    time.sleep(0.3)
    runner.tap(*COORD_EMAIL_CHECKBOX)
    time.sleep(0.2)


def wait_email_verify(machine) -> None:
    """GuerrillaMail gelen kutusunu dinleyip linke otomatik tıklar."""
    global EMAIL_TOKEN
    log = logging.getLogger(__name__)

    print("\n" + "=" * 55)
    print("📬 E-posta kutusu dinleniyor... (GuerrillaMail API)")
    print("   Bot, Facebook onay mailini otomatik bulacak ve onaylayacak.")
    print("=" * 55)
    
    if not EMAIL_TOKEN:
        log.error("Token yok! Mail okunamıyor.")
        return

    link = email_api.wait_for_verification_link(EMAIL_TOKEN, timeout=120, poll_interval=5)
    
    if link:
        log.info("Link bulundu! Tıklanıyor: %s", link)
        print("✅ Doğrulama linki bulundu! Açılıyor...")
        
        # Linki Android sisteminde (Chrome veya doğrudan uygulama ile) aç
        # am start -a android.intent.action.VIEW -d "<LINK>"
        runner = machine.runner
        runner.run(f"am start -a android.intent.action.VIEW -d '{link}'", capture=False)
        time.sleep(5.0)  # Uygulamanın veya tarayıcının açılmasını bekle
        
        # Viewpoints linke tıklayınca muhtemelen kendini açacak veya tarayıcı açılıp "Continue to programs" butonu getirecek
        # Biraz bekledikten sonra ekranı izlemeye devam edebiliriz.
        while True:
            machine.ui.invalidate_cache()
            elem = machine.ui.find_element_by_text("Continue to programs", dump=True)
            if elem:
                print("✅ Email başarıyla onaylandı ve uygulama açıldı!")
                break
            time.sleep(3.0)
    else:
        log.error("Zaman aşımı! Onay maili gelmedi.")


# ── Flow ────────────────────────────────────────────────────

def build_full_flow() -> FlowDefinition:
    states = [
        # 1. Splash
        State(name="app_loading", validation_level=ValidationLevel.L0_BLIND,
              actions=[Action(ActionType.WAIT, target=2500, description="Splash")],
              transitions=[Transition(target_state="onboarding_1")]),

        # 2. Onboarding 1
        State(name="onboarding_1", validation_level=ValidationLevel.L0_BLIND,
              actions=[
                  Action(ActionType.TAP_COORD, target=COORD_ONBOARDING_CONTINUE, description="Continue(1)"),
                  Action(ActionType.WAIT, target=500, description="Geçiş"),
              ], transitions=[Transition(target_state="onboarding_2")]),

        # 3. Onboarding 2
        State(name="onboarding_2", validation_level=ValidationLevel.L0_BLIND,
              actions=[
                  Action(ActionType.TAP_COORD, target=COORD_ONBOARDING_CONTINUE, description="Continue(2)"),
                  Action(ActionType.WAIT, target=700, description="Geçiş"),
              ], transitions=[Transition(target_state="phone_input")]),

        # 4. Telefon
        State(name="phone_input", validation_level=ValidationLevel.L0_BLIND,
              actions=[
                  Action(ActionType.CUSTOM, callback=enter_phone_number, description="Numara gir"),
              ], transitions=[Transition(target_state="sms_dialog")]),

        # 5. SMS Dialog
        State(name="sms_dialog", validation_level=ValidationLevel.L0_BLIND,
              actions=[
                  Action(ActionType.TAP_COORD, target=COORD_SMS_OK, description="SMS OK"),
                  Action(ActionType.WAIT, target=1000, description="Geçiş"),
              ], transitions=[Transition(target_state="code_entry")]),

        # 6. Kod girişi
        State(name="code_entry", validation_level=ValidationLevel.L0_BLIND,
              actions=[
                  Action(ActionType.CUSTOM, callback=wait_and_enter_code, description="SMS kodu bekle+gir"),
                  Action(ActionType.WAIT, target=5000, description="Doğrulama bekleme"),
              ], transitions=[Transition(target_state="profile_form")]),

        # 7. Profil formu (Manuel)
        State(name="profile_form", validation_level=ValidationLevel.L0_BLIND,
              actions=[
                  Action(ActionType.CUSTOM, callback=wait_for_user_profile, description="Manuel form bekle"),
              ], transitions=[Transition(target_state="notifications")]),

        # 8. Notifications
        State(name="notifications", validation_level=ValidationLevel.L0_BLIND,
              actions=[
                  Action(ActionType.TAP_COORD, target=COORD_ALLOW_CONTINUE, description="Allow and continue"),
                  Action(ActionType.WAIT, target=1000, description="Geçiş"),
              ], transitions=[Transition(target_state="email_input")]),

        # 9. Email
        State(name="email_input", validation_level=ValidationLevel.L0_BLIND,
              actions=[
                  Action(ActionType.CUSTOM, callback=enter_email, description="Email gir"),
                  Action(ActionType.TAP_COORD, target=COORD_CONFIRM_BTN, description="Confirm"),
                  Action(ActionType.WAIT, target=1000, description="Geçiş"),
              ], transitions=[Transition(target_state="email_verify")]),

        # 10. Email doğrulama
        State(name="email_verify", validation_level=ValidationLevel.L0_BLIND,
              actions=[
                  Action(ActionType.CUSTOM, callback=wait_email_verify, description="Email onay bekle"),
              ], transitions=[Transition(target_state="welcome_final")]),

        # 11. Final
        State(name="welcome_final", validation_level=ValidationLevel.L0_BLIND,
              actions=[
                  Action(ActionType.TAP_COORD, target=COORD_CONTINUE_PROGRAMS, description="Continue to programs"),
              ], transitions=[], is_terminal=True),
    ]

    return FlowDefinition(
        name="viewpoints_full",
        states=states,
        initial_state="app_loading",
        reset_before=True,
        clear_mode=ClearMode.FULL,
        timeout=300.0,
    )


def main() -> None:
    while True:
        print("=" * 55)
        print("  🚀 Viewpoints FULL Onboarding v4")
        print("=" * 55)
        try:
            bal = smspool_api.get_balance()
            print(f"  💰 SMSPool: ${bal:.2f}")
        except Exception:
            pass
        print()

        # ── Cihaz Kimliğini Değiştir (Anti-Detect) ─────────────
        device_spoofer.randomize_all()

        runner = FlowRunner(package_name=PACKAGE_NAME, main_activity=None, log_level=LOG_LEVEL)
        flow = build_full_flow()
        start = time.monotonic()
        result = runner.execute_flow(flow)
        total = (time.monotonic() - start) * 1000

        print("\n" + "=" * 55)
        print(result.summary)
        print(f"\n⏱️  Toplam: {total:.0f}ms")
        print("=" * 55)

        if result.error and "RESTART_FLOW_SMS_TIMEOUT" in result.error:
            print("\n🔄 SMS Zaman aşımı! Numara iade edildi, bot 3 saniye içinde YENİDEN BAŞLATILIYOR...\n")
            time.sleep(3)
            continue

        if result.success:
            print("\n✅ Hesap başarıyla oluşturuldu!")
            if IDENTITY:
                print(f"   İsim: {IDENTITY['first_name']} {IDENTITY['last_name']}")
            if USER_EMAIL:
                print(f"   Email: {USER_EMAIL}")

            # ── Otomatik Yedekleme ──────────────────────────────
            label = USER_EMAIL if USER_EMAIL else f"hesap_{account_manager.get_account_count() + 1}"
            phone = ORDER_DATA.get('phonenumber', '')
            print(f"\n📦 Hesap yedekleniyor: {label}")
            if account_manager.backup_account(label, email=USER_EMAIL, phone=phone):
                count = account_manager.get_account_count()
                print(f"✅ Yedekleme tamamlandı! (Toplam {count} hesap)")
                print(f"   Geri yüklemek için: python accounts.py")
            else:
                print("⚠️  Yedekleme başarısız oldu, ancak hesap aktif.")
            
            # Başarılı olduğunda döngüden çık
            break
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
