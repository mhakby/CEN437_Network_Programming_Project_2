# config.py
# Proje genelinde kullanılan ayarlar. Port, ip, rate limit gibi değerleri burdan değiştiriyoruz.
# chat_server.py, chat_client.py ve chat_relay.py bu değerleri komut satırından da alabiliyor (--help ile bakılabilir).

import os

# loglar her zaman proje klasörüne yazılsın diye (programı başka yerden çalıştırınca karışıyodu)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------- Server ----------
SERVER_HOST = "127.0.0.1"   # başka bilgisayarlardan bağlanılacaksa 0.0.0.0 yapılmalı
CHAT_PORT = 5050            # direkt bağlanan python clientlar için
RELAY_PORT = 5051           # sadece chat_relay.py bu porta bağlanıyor (nickler * ile başlar)

# monitor sayfası: HTTP ve WebSocket aynı portu kullanıyor, chat portlarından ayrı
MONITOR_HOST = "127.0.0.1"
MONITOR_PORT = 8080

# ---------- Relay (optional) ----------
RELAY_LISTEN_HOST = "127.0.0.1"
RELAY_LISTEN_PORT = 5060    # client relay üzerinden bağlanacaksa bu porta bağlanır

# ---------- Protocol ----------
BUFFER_SIZE = 1024
ENCODING = "utf-8"
MAX_MESSAGE_SIZE = 4096     # bir mesaj satırının (json) max boyutu, byte
MAX_TEXT_LENGTH = 500       # mesajın içeriği max kaç karakter olabilir
MAX_NICK_LENGTH = 16
REGISTER_TIMEOUT = 30       # bağlanan client 30 sn içinde register göndermezse bağlantı kapatılır

# ---------- Rate limit ----------
# Her kullanıcı için ayrı: RATE_LIMIT_INTERVAL saniye içinde en fazla RATE_LIMIT_COUNT mesaj.
# Fazlası gönderilirse kullanıcı MUTE_DURATION saniye boyunca mute ediliyor.
RATE_LIMIT_COUNT = 5
RATE_LIMIT_INTERVAL = 5
MUTE_DURATION = 15

# ---------- Files ----------
LOG_DIR = os.path.join(BASE_DIR, "logs")
SERVER_LOG = os.path.join(LOG_DIR, "server_log.txt")
ERROR_LOG = os.path.join(LOG_DIR, "error_log.txt")
RELAY_LOG = os.path.join(LOG_DIR, "relay_log.txt")
MONITOR_PAGE = os.path.join(BASE_DIR, "monitor.html")
