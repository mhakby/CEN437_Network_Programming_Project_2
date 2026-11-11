# chat_server.py

# Portlar (config.py'den yada komut satırından değiştirilebilir):
#   CHAT_PORT    (5050) -> direkt bağlanan python clientlar
#   RELAY_PORT   (5051) -> sadece chat_relay.py bağlanıyor, bu porttan gelen nickler * ile başlıyor
#   MONITOR_PORT (8080) -> monitor.html + websocket (monitor.py), chat portlarından ayrı
#
# Her client için ayrı bir thread açılıyor (one thread per client).
# Ortak verilere (clients, mesaj sayacı, log dosyası) lock ile erişiliyor.

import argparse
import socket
import threading
import time

import config
import monitor
import protocol
import utils


class ChatClient:
    """Bağlı bir chat kullanıcısı. Socket'e yazma send_lock ile korunuyor."""

    def __init__(self, sock, addr, from_relay):
        self.sock = sock
        self.addr = addr
        self.from_relay = from_relay
        self.nick = None
        self.send_lock = threading.Lock()

        # rate limit için (sadece bu clientın kendi threadi değiştiriyor, lock gerekmiyor)
        self.window_start = time.time()
        self.window_count = 0
        self.muted_until = 0

    def send(self, msg):
        """Mesajı gönderir. Bağlantı kopmuşsa False döner."""
        try:
            protocol.send_message(self.sock, msg, self.send_lock)
            return True
        except OSError:
            return False

    def shutdown(self):
        """Socketi kapatmadan önce shutdown, böylece recv'de bekleyen thread de uyanıyor."""
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def name(self):
        """Loglarda göstermek için. Henüz register olmadıysa ip:port."""
        address = self.addr[0] + ":" + str(self.addr[1])
        if self.nick == None:
            return address
        return self.nick + " (" + address + ")"


clients = {}   # nickname -> ChatClient
# önce normal Lock kullanmıştım ama aynı thread lock'u ikinci kez almaya çalışınca deadlock oldu,
# RLock aynı threadin tekrar almasına izin veriyor
clients_lock = threading.RLock()

message_count = 0   # server'ın işleyip ilettiği public/private mesaj sayısı (monitor'de gösteriliyor)
count_lock = threading.Lock()

server_running = True


# -----------------------
# Yardımcı fonksiyonlar
# -------------------------

def get_user_list():
    with clients_lock:
        return sorted(clients.keys())


def get_user_count():
    with clients_lock:
        return len(clients)


def get_message_count():
    with count_lock:
        return message_count


def increase_message_count():
    """Her public/private mesaj için 1 artıyor (kaç kişiye gittiği önemli değil)."""
    global message_count
    with count_lock:
        message_count += 1
        return message_count


def log_event(kind, text):
    """
    Olayı konsola basar, logs/server_log.txt'ye yazar ve monitor sayfasına gönderir.
    kind: CONNECT, JOIN, LEAVE, DISCONNECT, PUBLIC, PRIVATE, PRIVATE_FAIL, RATE_LIMIT, ERROR
    """
    line = "[" + kind + "] " + text
    print(utils.get_timestamp() + " " + line)
    utils.write_log(line)
    if kind == "ERROR":
        utils.write_log(line, config.ERROR_LOG)
    monitor.publish_event(kind, text, get_user_count(), get_message_count())


def broadcast(msg, exclude=None):
    """
    Mesajın bir kopyasını bütün kullanıcılara kendi TCP bağlantıları üzerinden gönderir
    (application layer, IP broadcast degil). exclude verilirse ona göndermiyor.
    """
    with clients_lock:
        targets = list(clients.values())   # lock'u uzun tutmamak için listeyi kopyalıyoruz

    for c in targets:
        if c is exclude:
            continue
        if not c.send(msg):
            # gönderilemedi, bu clientın kendi threadi bağlantının koptuğunu görüp temizlik yapacak
            c.shutdown()


def check_nickname(nick, from_relay):
    """Nickname geçerli mi kontrol eder. Geçerliyse None, değilse hata mesajı döner."""
    if not isinstance(nick, str) or nick == "":
        return "Nickname cannot be empty"

    if from_relay:
        # relay'den gelen nick tam olarak 1 tane * ile başlamalı
        if not nick.startswith("*") or nick.startswith("**"):
            return "Relay nicknames must start with exactly one *"
        name_part = nick[1:]
    else:
        if nick.startswith("*"):
            return "Nicknames starting with * are reserved for relay users"
        name_part = nick

    if name_part == "":
        return "Nickname cannot be empty"
    if len(name_part) > config.MAX_NICK_LENGTH:
        return "Nickname is too long (max " + str(config.MAX_NICK_LENGTH) + " characters)"
    for ch in name_part:
        if not (ch.isalnum() or ch == "_" or ch == "-"):
            return "Nickname can only contain letters, numbers, _ and -"
    return None


def register_nickname(client, requested):
    """
    Nick'i kaydeder, alınmışsa sonuna sayı ekler: Ali -> Ali2 -> Ali3 ...
    *Ali alınmışsa *Ali2 oluyor, baştaki * korunuyor.
    Kontrol + kayıt + welcome mesajı aynı lock içinde yapılıyor. Yoksa aynı anda gelen iki client
    aynı nicki alabilir yada welcome'dan önce client'a başka bir mesaj gidebilir.
    """
    with clients_lock:
        nick = requested
        suffix = 2
        while nick in clients:
            nick = requested + str(suffix)
            suffix += 1
        client.nick = nick
        clients[nick] = client
        users = sorted(clients.keys())
        client.send({"type": "welcome", "nick": nick, "users": users, "time": utils.get_timestamp()})
    return nick, users


def remove_client(client):
    """Client'ı listeden siler, socketi kapatır ve diğerlerine haber verir."""
    removed = False
    users = []
    with clients_lock:
        if client.nick != None and clients.get(client.nick) is client:
            del clients[client.nick]
            removed = True
            users = sorted(clients.keys())

    client.shutdown()
    client.sock.close()

    if removed:
        broadcast({"type": "leave", "nick": client.nick, "users": users, "time": utils.get_timestamp()})
        log_event("LEAVE", client.name() + " left the chat. Online users: " + str(len(users)))
    else:
        log_event("DISCONNECT", client.name() + " disconnected before registration")


def check_text(text):
    """Mesaj içeriği geçerli mi. Geçerliyse None, değilse hata mesajı."""
    if not isinstance(text, str):
        return "Message text is missing"
    if text.strip() == "":
        return "Message cannot be empty"
    if len(text) > config.MAX_TEXT_LENGTH:
        return "Message is too long (max " + str(config.MAX_TEXT_LENGTH) + " characters)"
    return None


def send_error(client, text):
    client.send({"type": "error", "text": text, "time": utils.get_timestamp()})


# -------------
# Rate limit
# ----------------

def check_rate_limit(client):
    """
    Basit fixed window rate limit (her kullanıcı için ayrı):
    RATE_LIMIT_INTERVAL saniyede en fazla RATE_LIMIT_COUNT mesaj.
    Aşılırsa kullanıcı MUTE_DURATION saniye mute ediliyor, mute süresince mesajları reddediliyor.
    Mesaj gönderilebilirse True döner.
    """
    now = time.time()

    if now < client.muted_until:
        left = int(client.muted_until - now) + 1
        send_error(client, "You are muted for " + str(left) + " more seconds (rate limit). Message was not sent.")
        log_event("RATE_LIMIT", "Message from " + client.nick + " rejected, user is muted (" + str(left) + " s left)")
        return False

    # pencere süresi dolduysa sayacı sıfırla
    if now - client.window_start >= config.RATE_LIMIT_INTERVAL:
        client.window_start = now
        client.window_count = 0

    client.window_count += 1
    if client.window_count > config.RATE_LIMIT_COUNT:
        client.muted_until = now + config.MUTE_DURATION
        client.window_count = 0
        client.send({
            "type": "muted",
            "seconds": config.MUTE_DURATION,
            "text": "Too many messages! Limit is " + str(config.RATE_LIMIT_COUNT) + " messages per "
                    + str(config.RATE_LIMIT_INTERVAL) + " seconds. You are muted for "
                    + str(config.MUTE_DURATION) + " seconds.",
            "time": utils.get_timestamp(),
        })
        log_event("RATE_LIMIT", client.nick + " exceeded the limit (" + str(config.RATE_LIMIT_COUNT) + " msg / "
                  + str(config.RATE_LIMIT_INTERVAL) + " s), muted for " + str(config.MUTE_DURATION) + " s")
        return False

    return True


# ----------------
# Mesaj işleme
# -------------

def handle_public(client, msg):
    text = msg.get("text")
    err = check_text(text)
    if err != None:
        send_error(client, err)
        log_event("ERROR", "Invalid public message from " + client.nick + ": " + err)
        return

    # gönderen her zaman bağlantıdan alınıyor. Client "from" gönderse bile kullanmıyoruz
    # yoksa başkasının adıyla mesaj atabilir (impersonation)
    out = {"type": "public", "from": client.nick, "text": text, "time": utils.get_timestamp()}
    broadcast(out)   # gönderen dahil herkese
    increase_message_count()
    log_event("PUBLIC", "from=" + client.nick + " to=ALL text='" + text + "'")


def handle_private(client, msg):
    to = msg.get("to")
    text = msg.get("text")

    if not isinstance(to, str) or to.strip() == "":
        send_error(client, "Private message needs a recipient")
        log_event("ERROR", "Private message without recipient from " + client.nick)
        return
    err = check_text(text)
    if err != None:
        send_error(client, err)
        log_event("ERROR", "Invalid private message from " + client.nick + ": " + err)
        return

    with clients_lock:
        target = clients.get(to)

    if target == None:
        # mesaj saklanmıyor, sadece gönderene hata dönüyoruz
        send_error(client, "User '" + to + "' is not online. Private message was not delivered.")
        log_event("PRIVATE_FAIL", "from=" + client.nick + " to=" + to + " (user not online)")
        return
    if target is client:
        send_error(client, "You cannot send a private message to yourself")
        return

    now = utils.get_timestamp()
    out = {"type": "private", "from": client.nick, "to": target.nick, "text": text, "time": now}
    if not target.send(out):
        send_error(client, "Could not deliver the message to '" + to + "'")
        log_event("PRIVATE_FAIL", "from=" + client.nick + " to=" + to + " (send failed)")
        return

    # gönderene de bir kopya (delivery confirmation), başka hiç kimseye gitmiyor
    client.send({"type": "private_sent", "to": target.nick, "text": text, "time": now})
    increase_message_count()
    log_event("PRIVATE", "from=" + client.nick + " to=" + target.nick + " text='" + text + "'")


def process_message(client, msg):
    """Register olmuş bir clienttan gelen mesajı işler. Client çıkmak istiyorsa False döner."""
    msg_type = msg["type"]
    # print("debug:", client.nick, msg)

    if msg_type == "public":
        if check_rate_limit(client):
            handle_public(client, msg)
    elif msg_type == "private":
        if check_rate_limit(client):
            handle_private(client, msg)
    elif msg_type == "users":
        client.send({"type": "userlist", "users": get_user_list()})
    elif msg_type == "quit":
        return False
    elif msg_type == "register":
        send_error(client, "You are already registered as " + client.nick)
    else:
        send_error(client, "Unknown message type: " + msg_type)
        log_event("ERROR", "Unknown message type '" + msg_type + "' from " + client.nick)
    return True


def do_register(client, reader):
    """
    Registration: client'ın ilk mesajı {"type": "register", "nick": "..."} olmak zorunda.
    Başarılıysa welcome mesajı ile atanan nick gönderiliyor. Başarılıysa True döner.
    """
    client.sock.settimeout(config.REGISTER_TIMEOUT)
    try:
        msg = reader.read_message()
    except socket.timeout:
        log_event("ERROR", client.name() + " did not register in " + str(config.REGISTER_TIMEOUT) + " seconds")
        return False
    except protocol.ProtocolError as e:
        send_error(client, "Invalid registration: " + str(e))
        log_event("ERROR", "Invalid registration from " + client.name() + ": " + str(e))
        return False
    client.sock.settimeout(None)

    if msg == None:
        return False
    if msg["type"] != "register":
        send_error(client, "First message must be a register message")
        log_event("ERROR", client.name() + " sent '" + msg["type"] + "' before registering")
        return False

    nick = msg.get("nick")
    if isinstance(nick, str):
        nick = nick.strip()
    err = check_nickname(nick, client.from_relay)
    if err != None:
        send_error(client, err)
        log_event("ERROR", "Registration rejected for " + client.name() + " (nick=" + str(nick) + "): " + err)
        return False

    assigned, users = register_nickname(client, nick)
    broadcast({"type": "join", "nick": assigned, "users": users, "time": utils.get_timestamp()}, exclude=client)

    info = client.name() + " joined the chat"
    if assigned != nick:
        info += " (requested '" + nick + "' was taken)"
    if client.from_relay:
        info += " [via relay]"
    log_event("JOIN", info + ". Online users: " + str(len(users)))
    return True


def handle_client(sock, addr, from_relay):
    """Her client için ayrı threadde çalışıyor."""
    client = ChatClient(sock, addr, from_relay)
    reader = protocol.MessageReader(sock)

    where = "direct"
    if from_relay:
        where = "relay"
    log_event("CONNECT", "New connection from " + addr[0] + ":" + str(addr[1]) + " (" + where + ")")

    try:
        if not do_register(client, reader):
            return

        while server_running:
            try:
                msg = reader.read_message()
            except protocol.MessageTooLong as e:
                # buffer'ın neresinin mesaj olduğu belli değil artık, bağlantıyı kapatıyoruz
                send_error(client, str(e) + ". You are disconnected.")
                log_event("ERROR", "Message too long from " + client.name() + ", disconnecting")
                break
            except protocol.ProtocolError as e:
                # sadece bu mesaj bozuk, bağlantı devam edebilir
                send_error(client, "Invalid message: " + str(e))
                log_event("ERROR", "Invalid message from " + client.name() + ": " + str(e))
                continue

            if msg == None:
                break   # client bağlantıyı kapattı
            if not process_message(client, msg):
                break   # /quit

    except OSError as e:
        # client penceresi direkt kapatılınca windowsta ConnectionResetError (10054) geliyo
        if server_running:
            log_event("ERROR", "Connection error with " + client.name() + ": " + str(e))
    except Exception as e:
        # beklenmeyen bir hata olursa sadece bu client etkilensin, server çalışmaya devam etsin
        log_event("ERROR", "Unexpected error with " + client.name() + ": " + repr(e))
    finally:
        remove_client(client)


# -------------
# Listening
# -----------

def create_listener(host, port):
    """Listening socketi oluşturur. Port kullanılıyorsa None döner."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    except OSError as e:
        utils.log_error("Could not listen on " + host + ":" + str(port) + " -> " + str(e))
        s.close()
        return None
    s.listen(10)
    # windowsta Ctrl+C accept()'i kesmiyodu, timeout koyunca düzeldi..
    s.settimeout(1.0)
    return s


def accept_loop(server_sock, from_relay):
    """Gelen bağlantıları kabul eder, her biri için thread açar."""
    while server_running:
        try:
            conn, addr = server_sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        conn.settimeout(None)
        t = threading.Thread(target=handle_client, args=(conn, addr, from_relay))
        t.daemon = True
        t.start()
    server_sock.close()


def stop_server():
    """Ctrl+C ile kapatırken bütün clientların bağlantısını kapatır."""
    global server_running
    server_running = False
    with clients_lock:
        targets = list(clients.values())
    for c in targets:
        c.shutdown()
    monitor.stop_monitor()
    utils.write_log("=== Server stopped ===")


def main():
    parser = argparse.ArgumentParser(description="Multi-user chat server (CEN437 Project 02)")
    parser.add_argument("--host", default=config.SERVER_HOST, help="listening IP for chat/relay ports (default: %(default)s)")
    parser.add_argument("--port", type=int, default=config.CHAT_PORT, help="TCP chat port (default: %(default)s)")
    parser.add_argument("--relay-port", type=int, default=config.RELAY_PORT, help="port for chat_relay.py (default: %(default)s)")
    parser.add_argument("--no-relay", action="store_true", help="do not open the relay port")
    parser.add_argument("--monitor-host", default=config.MONITOR_HOST, help="listening IP for the monitor page (default: %(default)s)")
    parser.add_argument("--monitor-port", type=int, default=config.MONITOR_PORT, help="HTTP + WebSocket monitor port (default: %(default)s)")
    args = parser.parse_args()

    # monitor portu chat portlarından biriyle aynı olamaz
    if args.monitor_port == args.port or (not args.no_relay and args.monitor_port == args.relay_port):
        print("Monitor port must be different from the chat ports!")
        return
    if not args.no_relay and args.port == args.relay_port:
        print("Chat port and relay port must be different!")
        return

    chat_sock = create_listener(args.host, args.port)
    if chat_sock == None:
        return

    relay_sock = None
    if not args.no_relay:
        relay_sock = create_listener(args.host, args.relay_port)
        if relay_sock == None:
            chat_sock.close()
            return

    try:
        monitor.start_monitor(args.monitor_host, args.monitor_port)
    except OSError as e:
        utils.log_error("Could not start monitor on port " + str(args.monitor_port) + " -> " + str(e))
        chat_sock.close()
        if relay_sock != None:
            relay_sock.close()
        return

    monitor_url = "http://" + args.monitor_host + ":" + str(args.monitor_port) + "/"
    if args.monitor_host == "0.0.0.0":
        monitor_url = "http://127.0.0.1:" + str(args.monitor_port) + "/"

    utils.print_header("MULTI-USER CHAT SERVER")
    print("Chat port (direct) : " + args.host + ":" + str(args.port))
    if relay_sock != None:
        print("Relay port         : " + args.host + ":" + str(args.relay_port))
    else:
        print("Relay port         : disabled")
    print("Monitor page       : " + monitor_url)
    print("Log file           : " + config.SERVER_LOG)
    print("Rate limit         : " + str(config.RATE_LIMIT_COUNT) + " messages / " + str(config.RATE_LIMIT_INTERVAL)
          + " s per user, mute " + str(config.MUTE_DURATION) + " s")
    print("Press Ctrl+C to stop the server.")
    print("=" * 50)
    utils.write_log("=== Server started: chat=" + args.host + ":" + str(args.port)
                    + " relay=" + ("disabled" if relay_sock == None else str(args.relay_port))
                    + " monitor=" + args.monitor_host + ":" + str(args.monitor_port) + " ===")

    t = threading.Thread(target=accept_loop, args=(chat_sock, False))
    t.daemon = True
    t.start()
    if relay_sock != None:
        t2 = threading.Thread(target=accept_loop, args=(relay_sock, True))
        t2.daemon = True
        t2.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[SERVER] Stopping...")
    stop_server()
    print("[SERVER] Stopped.")


if __name__ == "__main__":
    main()
