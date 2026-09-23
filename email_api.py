import urllib.request
import urllib.parse
import json
import time
import re
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s │ %(levelname)-7s │ %(name)-20s │ %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('email_api')

API_BASE = "https://api.guerrillamail.com/ajax.php"

def _get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json'
    }

def create_account():
    """
    GuerrillaMail kullanarak rastgele bir e-posta hesabı oluşturur.
    Döndürdüğü: (email_adresi, None, token)
    """
    url = f"{API_BASE}?f=get_email_address"
    req = urllib.request.Request(url, headers=_get_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
            email = data.get('email_addr')
            token = data.get('sid_token')
            logger.info(f"GuerrillaMail Email oluşturuldu: {email}")
            return email, None, token
    except Exception as e:
        logger.error(f"Hesap oluşturulamadı: {e}")
        return None, None, None

def get_messages(token, seq=0):
    """Gelen mesajların listesini çeker."""
    url = f"{API_BASE}?f=check_email&seq={seq}&sid_token={token}"
    req = urllib.request.Request(url, headers=_get_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
            return data.get('list', [])
    except Exception as e:
        logger.error(f"Mesajlar alınamadı: {e}")
        return []

def get_message_content(token, mail_id):
    """Bir mesajın tam içeriğini çeker."""
    url = f"{API_BASE}?f=fetch_email&email_id={mail_id}&sid_token={token}"
    req = urllib.request.Request(url, headers=_get_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        logger.error(f"Mesaj okunamadı: {e}")
        return None

def extract_viewpoints_link(content):
    """Gelen mailin içeriğinden Facebook Viewpoints onay linkini bulur."""
    if not content:
        return None
    
    # URL'leri bul
    urls = re.findall(r'href="([^"]+)"', content)
    for url in urls:
        if 'facebook.com' in url or 'viewpoints' in url or 'confirm' in url:
            return url
            
    # Düz URL regex'i ile de deneyelim
    urls = re.findall(r'https?://[^\s<"\']+', content)
    for url in urls:
         if 'facebook' in url or 'confirm' in url:
             return url
             
    return None

def wait_for_verification_link(token, timeout=120, poll_interval=5):
    """
    Belirtilen token için yeni bir mesaj bekler,
    geldiğinde içindeki Viewpoints onay linkini çıkarıp döndürür.
    """
    logger.info("E-posta kutusu dinleniyor... (GuerrillaMail API)")
    start_time = time.time()
    
    seen_ids = set()
    last_seq = 0
    
    while time.time() - start_time < timeout:
        messages = get_messages(token, seq=last_seq)
        
        for msg in messages:
            msg_id = msg.get('mail_id')
            if msg_id and msg_id not in seen_ids:
                seen_ids.add(msg_id)
                # GuerrillaMail karşılama mailini atla
                if 'no-reply@guerrillamail.com' in msg.get('mail_from', ''):
                    continue
                    
                logger.info(f"Yeni mesaj alındı! Gönderen: {msg.get('mail_from')}")
                
                # Facebook/Viewpoints filtresi
                if 'facebook' in msg.get('mail_from', '').lower() or 'viewpoints' in msg.get('mail_from', '').lower():
                    full_msg = get_message_content(token, msg_id)
                    if full_msg:
                        content = full_msg.get('mail_body', '')
                        link = extract_viewpoints_link(content)
                        if link:
                            logger.info(f"Onay linki bulundu: {link}")
                            return link
                        else:
                            logger.warning("Mail içeriğinde onay linki bulunamadı!")
                
        time.sleep(poll_interval)
        
    logger.error("Zaman aşımı! E-posta gelmedi.")
    return None

if __name__ == "__main__":
    email, _, token = create_account()
    if email and token:
        wait_for_verification_link(token, timeout=30)
