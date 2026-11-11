# chat_client.py
# Console Chat Client
# Server'a direkt (port 5050) yada relay üzerinden (port 5060) bağlanır.
# Mesajları alma işi ayrı bir threadde yapılıyor, böylece kullanıcı yazı yazarken de mesajlar gelmeye devam ediyo.
#
# Örnek:
#   python chat_client.py                                -> ip, port ve nickname sorulur
#   python chat_client.py --nick Ali                     -> ip/port default (127.0.0.1:5050)
#   python chat_client.py --port 5060 --nick Ali         -> relay üzerinden bağlanır

import argparse
import socket
import threading

import config
import protocol
import utils

running = False   # bağlantı devam ediyor mu, receive thread de buna bakıyor
my_nick = ""      # server'ın atadığı nickname (istediğimizden farklı olabilir)
users = []        # server'dan gelen güncel kullanıcı listesi


def show(line):
    """Gelen mesajı basar ve '> ' promptunu tekrar yazar."""
    print("\r" + line + "\n> ", end="", flush=True)


def short_time(ts):
    """Server'ın gönderdiği '2026-11-11 14:05:33' zamanının sadece saat kısmı."""
    if ts == None:
        return "??:??:??"
    return ts[11:]


def users_text():
    """Kullanıcı listesini numaralı yazar. /msg 2 merhaba gibi numara ile de mesaj atılabiliyor."""
    text = "Online users (" + str(len(users)) + "):"
    for i in range(len(users)):
        text += "\n  " + str(i + 1) + ". " + users[i]
        if users[i] == my_nick:
            text += " (you)"
    return text


def handle_server_message(msg):
    """Server'dan gelen mesajı türüne göre ekrana basar."""
    global users
    t = msg.get("type")
    time_str = "[" + short_time(msg.get("time")) + "] "

    if t == "public":
        show(time_str + msg.get("from", "?") + ": " + msg.get("text", ""))
    elif t == "private":
        show(time_str + "[PRIVATE from " + msg.get("from", "?") + "] " + msg.get("text", ""))
    elif t == "private_sent":
        show(time_str + "[PRIVATE to " + msg.get("to", "?") + "] " + msg.get("text", ""))
    elif t == "join":
        users = msg.get("users", users)
        show(time_str + "*** " + msg.get("nick", "?") + " joined the chat. Online: " + ", ".join(users))
    elif t == "leave":
        users = msg.get("users", users)
        show(time_str + "*** " + msg.get("nick", "?") + " left the chat. Online: " + ", ".join(users))
    elif t == "userlist":
        users = msg.get("users", users)
        show(users_text())
    elif t == "muted":
        show("[RATE LIMIT] " + msg.get("text", ""))
    elif t == "error":
        show("[ERROR] " + msg.get("text", ""))
    else:
        show("[?] Unknown message from server: " + str(msg))


def receive_messages(reader):
    """Server'dan gelen mesajları dinler (ayrı threadde çalışıyor)."""
    global running
    while running:
        try:
            msg = reader.read_message()
        except protocol.ProtocolError as e:
            show("[!] Invalid data from server: " + str(e))
            continue
        except OSError:
            msg = None   # socket kapanınca buraya düşüyor

        if msg == None:
            # running False ise bağlantıyı biz kapatmışızdır, mesaj basmaya gerek yok
            if running:
                print("\n[!] Connection to the server was lost. The server or relay is not available.")
                print("[!] Press Enter to exit.")
            break
        handle_server_message(msg)
    running = False


def connect_to_server(host, port, nick):
    """Server'a (yada relay'e) bağlanıp register olur. Başarılıysa (socket, reader) döner, değilse None."""
    global my_nick, users

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect((host, port))
    except ConnectionRefusedError:
        print("[ERROR] Connection refused. Is the server (or relay) running on " + host + ":" + str(port) + "?")
        s.close()
        return None
    except socket.timeout:
        print("[ERROR] Connection timeout: " + host + ":" + str(port))
        s.close()
        return None
    except socket.gaierror:
        print("[ERROR] Host not found: " + host)
        s.close()
        return None
    except OSError as e:
        print("[ERROR] Could not connect: " + str(e))
        s.close()
        return None

    reader = protocol.MessageReader(s)
    try:
        protocol.send_message(s, {"type": "register", "nick": nick})
        s.settimeout(10)
        reply = reader.read_message()
    except (OSError, protocol.ProtocolError) as e:
        print("[ERROR] Registration failed: " + str(e))
        s.close()
        return None

    if reply == None:
        print("[ERROR] Server closed the connection during registration.")
        s.close()
        return None
    if reply["type"] == "error":
        print("[ERROR] Server rejected the registration: " + reply.get("text", ""))
        s.close()
        return None
    if reply["type"] != "welcome":
        print("[ERROR] Unexpected reply from server: " + str(reply))
        s.close()
        return None

    s.settimeout(None)   # bağlandıktan sonra mesaj beklerken timeout olmasın
    my_nick = reply["nick"]
    users = reply.get("users", [])
    return s, reader


def print_help():
    print("Commands:")
    print("  <message>               send a public message to everyone")
    print("  /msg <nick> <message>   send a private message")
    print("  /msg <no> <message>     send a private message to user number <no> (see /users)")
    print("  /users                  show the online users")
    print("  /help                   show this help")
    print("  /quit or /exit          leave the chat")


def send_private(sock, line):
    """'/msg Ali merhaba' yada '/msg 2 merhaba' komutunu işler."""
    parts = line.split(" ", 2)
    if len(parts) < 3 or parts[2].strip() == "":
        print("Usage: /msg <nick or number> <message>")
        return

    target = parts[1]
    if target.isdigit():
        n = int(target)
        if n < 1 or n > len(users):
            print("Invalid user number. Type /users to see the list.")
            return
        target = users[n - 1]

    protocol.send_message(sock, {"type": "private", "to": target, "text": parts[2]})


def chat_loop(sock, reader):
    """Bağlandıktan sonra mesajlaşma. Gönderme burda, alma ayrı threadde."""
    global running
    running = True

    t = threading.Thread(target=receive_messages, args=(reader,))
    t.daemon = True
    t.start()

    print("Connected as '" + my_nick + "'. Type /help for commands, /quit to exit.")
    print(users_text())

    try:
        while running:
            line = input("> ")
            if not running:
                break
            line = line.strip()
            if line == "":
                continue

            if line == "/quit" or line == "/exit":
                break
            elif line == "/help":
                print_help()
            elif line == "/users":
                protocol.send_message(sock, {"type": "users"})
            elif line == "/msg" or line.startswith("/msg "):
                send_private(sock, line)
            elif line.startswith("/"):
                print("Unknown command. Type /help to see the commands.")
            else:
                protocol.send_message(sock, {"type": "public", "text": line})
    except (KeyboardInterrupt, EOFError):
        print()
    except OSError as e:
        print("[ERROR] Could not send the message, connection lost: " + str(e))

    # server hala bağlıysa çıktığımızı haber verelim
    if running:
        running = False
        try:
            protocol.send_message(sock, {"type": "quit"})
        except OSError:
            pass
    running = False

    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()
    print("Disconnected. Bye!")


def main():
    parser = argparse.ArgumentParser(description="Console chat client (CEN437 Project 02)")
    parser.add_argument("--host", help="server or relay IP (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="server port " + str(config.CHAT_PORT)
                        + " (direct) or relay port " + str(config.RELAY_LISTEN_PORT))
    parser.add_argument("--nick", help="nickname")
    args = parser.parse_args()

    utils.print_header("CHAT CLIENT")

    # hiç argüman verilmediyse ip ve portu soruyoruz, en az biri verildiyse eksikler default oluyor
    ask = args.host == None and args.port == None and args.nick == None

    host = args.host
    if host == None:
        if ask:
            host = utils.ask_value("Server IP", "127.0.0.1")
        else:
            host = "127.0.0.1"
    port = args.port
    if port == None:
        if ask:
            port = utils.ask_int("Port (" + str(config.CHAT_PORT) + " = direct, "
                                 + str(config.RELAY_LISTEN_PORT) + " = relay)", config.CHAT_PORT)
        else:
            port = config.CHAT_PORT
    nick = args.nick
    while nick == None or nick.strip() == "":
        nick = input("Nickname: ")
    nick = nick.strip()

    result = connect_to_server(host, port, nick)
    if result == None:
        return
    sock, reader = result

    if my_nick.startswith("*") and not nick.startswith("*"):
        print("[i] You are connected through the relay.")
    if my_nick.lstrip("*") != nick:
        print("[!] Nickname '" + nick + "' is already taken, your nickname is '" + my_nick + "'")

    chat_loop(sock, reader)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nProgram closed.")
