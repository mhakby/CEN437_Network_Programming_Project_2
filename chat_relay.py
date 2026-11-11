# chat_relay.py
# Python client -> relay (5060) -> chat server relay portu (5051)

# Relay her client için server'a ayrı bir bağlantı açıyor. Sadece ilk mesajı (register) okuyup
# nick'in başına * ekliyor, ondan sonra iki yönde de byte'ları hiç değiştirmeden aktarıyor.
# Server'ın atadığı nick (welcome mesajı) da olduğu gibi client'a gidiyor.
# Monitor sayfası relay'den geçmiyor, direkt server'a bağlanıyor.

import argparse
import socket
import threading

import config
import protocol
import utils

active_count = 0             # şu an relay üzerinden bağlı client sayısı
count_lock = threading.Lock()


def log(message):
    """Ekrana basar ve logs/relay_log.txt'ye yazar."""
    print(utils.get_timestamp() + " " + message)
    utils.write_log(message, config.RELAY_LOG)


def change_count(n):
    global active_count
    with count_lock:
        active_count += n
        return active_count


def send_error(sock, text):
    """Client'a server'ın kullandığı formatta hata mesajı gönderir."""
    try:
        protocol.send_message(sock, {"type": "error", "text": text, "time": utils.get_timestamp()})
    except OSError:
        pass


def close_socket(s):
    try:
        s.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        s.close()
    except OSError:
        pass


def pipe(src, dst):
    """src'den gelen byte'ları dst'ye aynen aktarır. Bir taraf kapanınca diğerini de kapatıyoruz."""
    try:
        while True:
            data = src.recv(config.BUFFER_SIZE)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    # karşı yöndeki pipe threadi de recv'den çıksın diye ikisini de shutdown ediyoruz
    for s in (src, dst):
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def handle_relay_client(client_sock, addr, server_host, server_port):
    """Her relay client'ı için ayrı threadde çalışıyor."""
    name = addr[0] + ":" + str(addr[1])
    log("[CONNECT] Client connected to relay: " + name)

    # 1) register mesajını oku. MessageReader parça parça gelse bile "\n" gelene kadar biriktiriyor
    reader = protocol.MessageReader(client_sock)
    client_sock.settimeout(config.REGISTER_TIMEOUT)
    try:
        msg = reader.read_message()
    except (protocol.ProtocolError, OSError) as e:
        send_error(client_sock, "Invalid registration: " + str(e))
        log("[ERROR] Invalid registration from " + name + ": " + str(e))
        close_socket(client_sock)
        return

    if msg == None:
        log("[DISCONNECT] " + name + " closed before registration")
        close_socket(client_sock)
        return
    if msg["type"] != "register" or not isinstance(msg.get("nick"), str):
        send_error(client_sock, "First message must be a register message")
        log("[ERROR] " + name + " did not send a valid register message")
        close_socket(client_sock)
        return

    nick = msg["nick"].strip()
    if nick.startswith("*"):
        send_error(client_sock, "Nickname cannot start with * (the relay adds it automatically)")
        log("[ERROR] Rejected nickname '" + nick + "' from " + name + " (starts with *)")
        close_socket(client_sock)
        return

    # 2) tam olarak bir tane * ekle
    msg["nick"] = "*" + nick

    # 3) server'a bu client için ayrı bir bağlantı aç
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.settimeout(5)
    try:
        server_sock.connect((server_host, server_port))
        server_sock.sendall(protocol.encode_message(msg))
        # client register'dan hemen sonra başka mesaj da gönderdiyse onlar buffer'da kalmış olabilir
        leftover = reader.get_remaining()
        if leftover:
            server_sock.sendall(leftover)
    except OSError as e:
        send_error(client_sock, "Relay could not connect to the chat server")
        log("[ERROR] Could not connect to server " + server_host + ":" + str(server_port) + " -> " + str(e))
        close_socket(server_sock)
        close_socket(client_sock)
        return

    client_sock.settimeout(None)
    server_sock.settimeout(None)
    count = change_count(1)
    log("[RELAY] " + name + " registered as '" + msg["nick"] + "' (requested). Active relayed clients: " + str(count))

    # 4) iki yönde aktarma. Biri ayrı threadde, diğeri bu threadde
    t = threading.Thread(target=pipe, args=(server_sock, client_sock))
    t.daemon = True
    t.start()
    pipe(client_sock, server_sock)
    t.join()

    close_socket(client_sock)
    close_socket(server_sock)
    count = change_count(-1)
    log("[DISCONNECT] Relay connection closed for " + name + " ('" + msg["nick"] + "'). Active relayed clients: " + str(count))


def main():
    parser = argparse.ArgumentParser(description="Chat relay - adds * to nicknames (CEN437 Project 02)")
    parser.add_argument("--listen-host", default=config.RELAY_LISTEN_HOST, help="relay listening IP (default: %(default)s)")
    parser.add_argument("--listen-port", type=int, default=config.RELAY_LISTEN_PORT, help="relay listening port (default: %(default)s)")
    parser.add_argument("--server-host", default="127.0.0.1", help="chat server IP (default: %(default)s)")
    parser.add_argument("--server-port", type=int, default=config.RELAY_PORT, help="server's relay port (default: %(default)s)")
    args = parser.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((args.listen_host, args.listen_port))
    except OSError as e:
        utils.log_error("Could not start relay on port " + str(args.listen_port) + ": " + str(e), config.RELAY_LOG)
        s.close()
        return
    s.listen(10)
    s.settimeout(1.0)

    utils.print_header("CHAT RELAY")
    print("Listening on  : " + args.listen_host + ":" + str(args.listen_port))
    print("Chat server   : " + args.server_host + ":" + str(args.server_port) + " (relay port)")
    print("Log file      : " + config.RELAY_LOG)
    print("Press Ctrl+C to stop the relay.")
    print("=" * 50)
    log("=== Relay started on " + args.listen_host + ":" + str(args.listen_port) + " -> "
        + args.server_host + ":" + str(args.server_port) + " ===")

    try:
        while True:
            try:
                conn, addr = s.accept()
            except socket.timeout:
                continue
            conn.settimeout(None)
            t = threading.Thread(target=handle_relay_client, args=(conn, addr, args.server_host, args.server_port))
            t.daemon = True
            t.start()
    except KeyboardInterrupt:
        print("\n[RELAY] Stopped by user")
    s.close()
    log("=== Relay stopped ===")


if __name__ == "__main__":
    main()
