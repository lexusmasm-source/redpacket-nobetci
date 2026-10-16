#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Red Packet Nöbetçi Botu v2 (güçlendirilmiş)
------------------------------------------
Binance Red Packet kodu yayınlayan siteleri düzenli aralıklarla tarar,
daha önce görülmemiş YENİ kodları tespit edip Telegram üzerinden bildirir.

v2 yenilikleri:
  * Tarihi dinamik üretilen kaynak URL'leri (quiknotes + coingabbar bugünkü
    sayfası her gün otomatik bulunur — kaynak listesi elle güncellenmez).
  * Başarısız indirmelerde üstel beklemeli tekrar deneme (3 deneme).
  * Kullanıcıya daha önce verilmiş kodlar önceden kayıtlı gelir (PRESEED),
    böylece eski kodlar "yeni" diye bildirilmez.
  * Kaynaklar arası kısa bekleme (kibar tarama).
  * Çok sayıda yeni kodda Telegram mesajını parçalama.
  * İsteğe bağlı günlük nabız mesajı (DAILY_HEARTBEAT=1).

Gerekli ortam değişkenleri (GitHub Secrets'tan gelir, kimseyle paylaşılmaz):
  TELEGRAM_BOT_TOKEN  - BotFather'dan alınan bot token'ı
  TELEGRAM_CHAT_ID    - Bildirim gidecek sohbetin ID'si
İsteğe bağlı:
  DAILY_HEARTBEAT=1   - Her gün 08:00 UTC civarı "nöbetçi aktif" özeti gönderir

Bağımlılık yok (sadece Python standart kütüphanesi).
"""

import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(BASE_DIR, "seen_codes.txt")
HEARTBEAT_FILE = os.path.join(BASE_DIR, "last_heartbeat.txt")

# ---------------------------------------------------------------------------
# DAHA ÖNCE KULLANICIYA VERİLMİŞ KODLAR (tekrar "yeni" diye bildirilmesin)
# ---------------------------------------------------------------------------
PRESEED = """
XM4L9H0A HIUXT6ID D4VWAKQW 2KWLRNZZ 2PKCZN3J 573M89Q2 8JCPBD4A 5U8Z3N26
U6RY73Y4 NA70R0CF IJJ4BSGC 4VEZQO62 FTBQX3IN 8JG9S3NX IQCGVB7P 34IX8IVJ
PXMG1FDK OZXFMVX9 S1R3RF0F R9YZHVFO OEWIMUC5 HM1UEINW EJM2E1LA UF7YDEPM
KF0ZVQUZ 9C66OXGP EETP5VI9 MJKT4FTJ PGP3WHE6 N2O66L2E W2Q71U55 4700J8Z3
XDRD0RPV MVCGD4X8 0XLFFWUR QRT45ZFS QUQ4HRFG BPW4WINM56 BPCC69GSWB
BP4HB6RFF9 HPX9X17U BF7I36T8 ZD40JS90 E9UDQNHV 407PIZ30 5Z4EW2U0 UR2G4X20
BUF639YS UZ2G191Z 37AOH2OA BPC920QBRQ BPTBIZZUB4 CRYPTERIAL
""".split()

# ---------------------------------------------------------------------------
# KAYNAKLAR
# ---------------------------------------------------------------------------

def build_sources():
    """Bugünün tarihine göre dinamik URL'ler + sabit yedekler."""
    today = datetime.datetime.now(datetime.timezone.utc)
    day = today.day
    month = today.strftime("%B").lower()  # october
    year = today.year
    dynamic = [
        # quiknotes: .../binance-red-packet-code-today-2026/16-october-2026/
        f"https://www.quiknotes.in/binance-red-packet-code-today-2026/{day}-{month}-{year}/",
        # coingabbar: .../binance-red-packet-code-16-october-2026-claim-free-bnb
        f"https://www.coingabbar.com/en/binance-red-packet-code-{day}-{month}-{year}-claim-free-bnb",
    ]
    static = [
        "https://blog.mexc.com/crypto-knowledge/binance-red-packet-codes-today/",
        "https://miningcombo.com/binance-red-packet-codes/",
        # Aşağıdakiler tarihe bağlı eski sayfalar; yedek olarak durur:
        "https://www.bitrue.com/blog/binance-red-packet-code-october-16-17-18-2026",
        "https://www.quiknotes.in/binance-red-packet-code-today-2026-16-october-2026/",
    ]
    # Sırayı koruyarak tekilleştir
    seen, out = set(), []
    for u in dynamic + static:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


DENYLIST = [
    "BINANCE", "REDPACKET", "REDPACKETS", "CRYPTO", "OCTOBER", "COMINGSOON",
    "SPECIAL", "CLAIM", "REWARD", "REWARDS", "BONUS", "TELEGRAM", "WHATSAPP",
    "YOUTUBE", "LIMITED", "UPDATE", "UPDATED", "TODAY", "DAILY", "FREE",
    "USDT", "BITCOIN", "ETHEREUM", "GITHUB", "ACTIONS", "CLAIMFREE", "FREEBNB",
    "SEPTEMBER", "NOVEMBER", "DECEMBER",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

TIMEOUT = 25
MAX_RETRIES = 3
POLITE_DELAY = 1.5  # kaynaklar arası bekleme (sn)
CHUNK_SIZE = 12     # tek Telegram mesajındaki maks kod sayısı


# ---------------------------------------------------------------------------
# YARDIMCILAR
# ---------------------------------------------------------------------------

def fetch(url):
    """Sayfayı indir (3 deneme, üstel bekleme). Başarısızsa None."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            text = re.sub(r"<script.*?</script>", " ", raw, flags=re.S | re.I)
            text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            return (
                text.replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&#39;", "'")
                .replace("&quot;", '"')
            )
        except Exception as exc:
            last_err = exc
            wait = 2 ** attempt
            print(f"  [deneme {attempt}/{MAX_RETRIES} başarısız: {exc}] {wait} sn bekleniyor...",
                  flush=True)
            time.sleep(wait)
    print(f"[UYARI] indirilemedi: {url} ({last_err})", flush=True)
    return None


def extract_codes(text):
    """Metinden kod formatındaki token'ları çıkar, sırayı koruyarak tekilleştir."""
    found = []
    for token in re.findall(r"\b[A-Z0-9]{8,10}\b", text):
        if not re.search(r"\d", token):
            continue
        if not re.search(r"[A-Z]", token):
            continue
        upper = token.upper()
        if any(bad in upper for bad in DENYLIST):
            continue
        if upper not in found:
            found.append(upper)
    return found


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return {line.strip().upper() for line in f if line.strip()}


def save_seen(codes):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for code in sorted(codes):
            f.write(code + "\n")


def send_telegram(message):
    """Telegram'a mesaj gönder. Kimlik bilgileri yoksa ekrana yaz (test modu)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[TEST MODU] Telegram kimliği yok, mesaj gönderilmedi:", flush=True)
        print("-" * 40)
        print(re.sub(r"<[^>]+>", "", message))
        print("-" * 40)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message, "parse_mode": "HTML",
         "disable_web_page_preview": True}
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                print(f"[HATA] Telegram API hatası: {data}", flush=True)
                return False
            return True
    except Exception as exc:
        print(f"[HATA] Telegram mesajı gönderilemedi: {exc}", flush=True)
        return False


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def maybe_heartbeat(new_count_today, total_tracked):
    """DAILY_HEARTBEAT=1 ise günde bir kez nabız mesajı gönder."""
    if os.environ.get("DAILY_HEARTBEAT", "").strip() != "1":
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    if now.hour != 8:  # sadece 08:00 UTC turunda
        return
    today_str = now.strftime("%Y-%m-%d")
    last = ""
    if os.path.exists(HEARTBEAT_FILE):
        with open(HEARTBEAT_FILE, "r", encoding="utf-8") as f:
            last = f.read().strip()
    if last == today_str:
        return  # bugün zaten gönderilmiş
    ok = send_telegram(
        "✅ <b>Nöbetçi aktif</b>\n\n"
        f"Son 24 saatte {new_count_today} yeni kod yakalandı.\n"
        f"Toplam {total_tracked} kod izleniyor. Her şey yolunda 👌"
    )
    if ok:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            f.write(today_str)


# ---------------------------------------------------------------------------
# ANA AKIŞ
# ---------------------------------------------------------------------------

def main():
    seen = load_seen()
    first_run = not os.path.exists(SEEN_FILE)
    if first_run:
        seen |= {c.upper() for c in PRESEED}  # eski kodları sessizce tanı

    sources = build_sources()
    print(f"{len(sources)} kaynak taranacak.", flush=True)

    all_found = []
    for i, url in enumerate(sources):
        print(f"Taranıyor ({i+1}/{len(sources)}): {url}", flush=True)
        text = fetch(url)
        if text is None:
            continue
        codes = extract_codes(text)
        print(f"  -> {len(codes)} aday kod bulundu", flush=True)
        for c in codes:
            if c not in all_found:
                all_found.append(c)
        if i < len(sources) - 1:
            time.sleep(POLITE_DELAY)

    new_codes = [c for c in all_found if c not in seen]
    new_today = 0

    if first_run:
        save_seen(set(all_found) | seen)
        send_telegram(
            "🟢 <b>Nöbet başladı! (v2)</b>\n\n"
            f"Şu an {len(all_found)} aktif kod + daha önce verilenler kayıt altında. "
            "Bundan sonra <b>YENİ</b> bir kod yayınlandığı anda sana haber vereceğim. "
            "Kod gelince hızlı ol: Binance → More → Gift &amp; Campaign → "
            "Red Packet → Receive → Claim."
        )
        print(f"İlk çalıştırma tamamlandı, {len(all_found)} aktif kod kaydedildi.", flush=True)
    elif new_codes:
        new_today = len(new_codes)
        all_ok = True
        for n, part in enumerate(chunked(new_codes, CHUNK_SIZE), start=1):
            lines = "\n".join(f"<code>{c}</code>" for c in part)
            header = "🚨 <b>YENİ Red Packet kodu!</b> 🚨"
            if len(new_codes) > CHUNK_SIZE:
                header += f" ({n})"
            msg = (
                f"{header}\n\n{lines}\n\n"
                "Hemen dene: Binance → More → Gift &amp; Campaign → "
                "Red Packet → Receive → Claim.\n"
                "Kotalı, dakikalar içinde bitebilir — O ile 0'a dikkat!"
            )
            if not send_telegram(msg):
                all_ok = False
                break
            time.sleep(1)
        if all_ok:
            save_seen(seen | set(new_codes))
            print(f"{len(new_codes)} yeni kod bildirildi ve kaydedildi: {new_codes}", flush=True)
        else:
            print("[HATA] Bildirim gönderilemedi, liste güncellenmedi.", flush=True)
            return 1
    else:
        print("Yeni kod yok.", flush=True)

    maybe_heartbeat(new_today, len(seen | set(new_codes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
