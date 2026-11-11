# utils.py
# Loglama, zaman damgası ve input alma gibi server, client ve relay'in ortak kullandığı fonksiyonlar

import os
import threading
from datetime import datetime

import config

# birden fazla thread aynı anda log dosyasına yazınca satırlar birbirine karışabilyor, o yüzden lock var
log_lock = threading.Lock()


def get_timestamp():
    """Log ve mesajlar için zaman damgası. Mesajlardaki zaman her zaman server'da uretiliyor."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_log(message, log_file=None):
    """Mesajı zaman damgasyla log dosyasına ekler. Dosya verilmezse server_log'a yazar."""
    if log_file == None:
        log_file = config.SERVER_LOG

    with log_lock:
        if not os.path.exists(config.LOG_DIR):
            os.makedirs(config.LOG_DIR)
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(get_timestamp() + " " + message + "\n")
        except Exception as e:
            print("Could not write to log file:", e)


def log_error(message, log_file=None):
    """Hatayı ekrna basar, error_log'a ve normal log dosyasına yazar."""
    print("[ERROR]", message)
    write_log("[ERROR] " + message, config.ERROR_LOG)
    write_log("[ERROR] " + message, log_file)   # genel loga da yazıyorumki akış belli olsun


def ask_value(prompt, default):
    """Kullanıcıdan string alır (ip adresi gibi). Boş bırakılırsa default döner."""
    val = input(prompt + " [" + str(default) + "]: ").strip()
    if val == "":
        return default
    return val


def ask_int(prompt, default):
    """Kullanıcıdan sayı alır. Boş bırakılırsa yada sayı girilmezse default döner."""
    val = input(prompt + " [" + str(default) + "]: ").strip()
    if val == "":
        return default
    try:
        return int(val)
    except ValueError:
        print("Invalid number, using default value:", default)
        return default


def print_header(title):
    """Başlıklar için."""
    print()
    print("=" * 50)
    print(" " + title)
    print("=" * 50)
