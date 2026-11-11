# monitor.py
# Server Monitoring (HTTP + WebSocket)
# monitor.html sayfası ve WebSocket aynı portta çalışıyor (config.MONITOR_PORT), chat portlarından tamamen ayrı.
#   http://127.0.0.1:8080/     -> monitor.html
#   ws://127.0.0.1:8080/ws     -> canlı event akışı
# Buraya bağlananlar chat kullanıcısı değil, user listesinde görünmüyorlar.
#
# Ekstra kütüphane kullanmamak için WebSocket handshake ve frame gönderme kısmını kendim yazdım (RFC 6455).
# Sadece server -> browser yönünde text frame gönderiyoruz. Browser'dan gelen frameleri çözmüyoruz,
# sadece close frame gelince yada bağlantı kopunca viewer'ı listeden çıkarıyoruz.

import base64
import hashlib
import json
import socket
import struct
import threading

import config
import utils

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"   # RFC'de sabit olarak verilen string

viewers = []                    # bağlı websocket'ler (monitor sayfaları)
viewers_lock = threading.Lock()
running = False

# son sayılar, yeni açılan sayfa 0 görmesin diye bağlanır bağlanmaz bunu gönderiyoruz
stats = {"users": 0, "messages": 0}


def make_frame(text):
    """Text'i websocket frame'ine çevirir. Server'dan giden frameler maskelenmiyor."""
    payload = text.encode("utf-8")
    length = len(payload)
    # 0x81 = FIN biti + text opcode
    if length < 126:
        header = struct.pack("!BB", 0x81, length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x81, 126, length)
    else:
        header = struct.pack("!BBQ", 0x81, 127, length)
    return header + payload


def send_to_viewers(data):
    """dict'i json yapıp bütün monitor sayfalarına gönderir. Hata veren sayfayı listeden siler."""
    frame = make_frame(json.dumps(data, ensure_ascii=False))
    with viewers_lock:
        for v in viewers[:]:
            try:
                v.sendall(frame)
            except OSError:
                # sayfa kapanmış, chat'i etkilemesin diye sessizce çıkarıyoruz
                viewers.remove(v)
                try:
                    v.close()
                except OSError:
                    pass


def publish_event(kind, text, users, messages):
    """Server'daki bir olayı (mesaj, bağlantı, hata...) monitor sayfalarına gönderir."""
    stats["users"] = users
    stats["messages"] = messages
    data = {
        "time": utils.get_timestamp(),
        "kind": kind,
        "text": text,
        "users": users,
        "messages": messages,
    }
    send_to_viewers(data)


def read_http_request(conn):
    """HTTP request'in header kısmını okur ("\r\n\r\n" gelene kadar)."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(1024)
        if not chunk:
            return None
        data += chunk
        if len(data) > 8192:   # çok uzun header, saçma bir istek
            return None
    return data.decode("utf-8", errors="replace")


def parse_request(text):
    """Request'ten path'i ve header'ları çıkarır. Header isimleri küçük harfe çevriliyor."""
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    path = "/"
    if len(parts) > 1:
        path = parts[1]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return path, headers


def send_http(conn, status, content_type, body):
    """Basit bir HTTP cevabı gönderir."""
    header = "HTTP/1.1 " + status + "\r\n"
    header += "Content-Type: " + content_type + "\r\n"
    header += "Content-Length: " + str(len(body)) + "\r\n"
    header += "Connection: close\r\n\r\n"
    conn.sendall(header.encode("utf-8") + body)


def websocket_handshake(conn, headers):
    """Sec-WebSocket-Accept hesaplayıp 101 Switching Protocols cevabını gönderir."""
    key = headers.get("sec-websocket-key")
    if key == None:
        send_http(conn, "400 Bad Request", "text/plain", b"Missing Sec-WebSocket-Key")
        return False
    sha1 = hashlib.sha1((key + WS_MAGIC).encode("utf-8")).digest()
    accept = base64.b64encode(sha1).decode("utf-8")
    response = "HTTP/1.1 101 Switching Protocols\r\n"
    response += "Upgrade: websocket\r\n"
    response += "Connection: Upgrade\r\n"
    response += "Sec-WebSocket-Accept: " + accept + "\r\n\r\n"
    conn.sendall(response.encode("utf-8"))
    return True


def viewer_loop(conn):
    """Browser'ın gönderdiğiyle işimiz yok, sadece bağlantı kapandı mı diye okuyoruz."""
    while running:
        try:
            data = conn.recv(1024)
        except socket.timeout:
            continue
        except OSError:
            break
        if not data:
            break
        opcode = data[0] & 0x0F
        if opcode == 0x8:   # close frame, sayfa kapatıldı
            break
        # ping (0x9) gelirse pong göndermek lazım aslında ama browserlar kendiliğinden göndermiyo


def remove_viewer(conn):
    with viewers_lock:
        if conn in viewers:
            viewers.remove(conn)
    try:
        conn.close()
    except OSError:
        pass


def handle_connection(conn, addr):
    """Monitor portuna gelen bağlantı: ya websocket ya da sayfanın kendisi isteniyor."""
    conn.settimeout(5)
    try:
        request = read_http_request(conn)
        if request == None:
            conn.close()
            return
        path, headers = parse_request(request)

        if headers.get("upgrade", "").lower() == "websocket":
            if not websocket_handshake(conn, headers):
                conn.close()
                return
            # timeout'u kısa tutuyoruz ki takılan bir sayfa yüzünden send'ler uzun süre beklemesin
            conn.settimeout(2)
            hello = {"time": utils.get_timestamp(), "kind": "INFO", "text": "Monitor connected",
                     "users": stats["users"], "messages": stats["messages"]}
            with viewers_lock:
                conn.sendall(make_frame(json.dumps(hello)))
                viewers.append(conn)
            print("[MONITOR] Viewer connected from " + addr[0] + ":" + str(addr[1]))
            utils.write_log("[MONITOR] Viewer connected from " + addr[0] + ":" + str(addr[1]))
            viewer_loop(conn)
            remove_viewer(conn)
            print("[MONITOR] Viewer disconnected " + addr[0] + ":" + str(addr[1]))
            utils.write_log("[MONITOR] Viewer disconnected " + addr[0] + ":" + str(addr[1]))

        elif path == "/" or path == "/monitor.html":
            f = open(config.MONITOR_PAGE, "rb")
            body = f.read()
            f.close()
            send_http(conn, "200 OK", "text/html; charset=utf-8", body)
            conn.close()

        else:
            # favicon.ico vs
            send_http(conn, "404 Not Found", "text/plain", b"Not found")
            conn.close()
    except OSError as e:
        # print("monitor debug:", e)
        remove_viewer(conn)


def accept_loop(server_sock):
    while running:
        try:
            conn, addr = server_sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        t = threading.Thread(target=handle_connection, args=(conn, addr))
        t.daemon = True
        t.start()
    server_sock.close()


def start_monitor(host, port):
    """Monitor server'ını ayrı bir threadde başlatır. Port kullanılıyorsa OSError fırlatır."""
    global running
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(5)
    s.settimeout(1.0)   # Ctrl+C ile kapatırken accept'te takılmasın diye
    running = True
    t = threading.Thread(target=accept_loop, args=(s,))
    t.daemon = True
    t.start()
    return s


def stop_monitor():
    global running
    running = False
    with viewers_lock:
        for v in viewers:
            try:
                v.close()
            except OSError:
                pass
        viewers.clear()
