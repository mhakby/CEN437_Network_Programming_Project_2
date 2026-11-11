# protocol.py
# Uygulama mesaj formatı ve framing (Çerçeve - TCP'de mesaj sınırlarını belirleme)
#
# Her mesaj tek satırlık bir JSON objesi ve sonunda "\n" var. Örnek:
#   {"type": "public", "text": "merhaba"}\n
#
# TCP bir stream olduğu için bir recv() ile yarım mesaj da gelebilyor, birden fazla mesaj da.
# Bu yüzden gelen byte'ları buffer'da biriktiriyoruz ve "\n" görünce bir mesajı kesip alıyoruz.
# Mesajın içinde enter (\n) olsa bile json.dumps onu "\\n" diye yazdığı için framing bozulmuyor.
# Encoding: UTF-8, bir satır en fazla config.MAX_MESSAGE_SIZE byte olabilir.

import json
import config


class ProtocolError(Exception):
    """Bozuk, json olmayan yada type alanı olmayan mesaj gelince."""
    pass


class MessageTooLong(ProtocolError):
    """Mesaj MAX_MESSAGE_SIZE'dan büyükse. Bu durumda bağlantı kapatılıyor."""
    pass


def encode_message(msg):
    """dict -> json satırı (bytes). Türkçe karakterler bozulmasın diye ensure_ascii=False."""
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    return line.encode(config.ENCODING)


def send_message(sock, msg, lock=None):
    """
    Mesajı sockete gönderir.
    lock verilirse aynı sockete iki thread aynı anda yazamaz, yoksa iki mesajın byte'ları
    birbirine karışıp framing bozulabilir (mesela iki kişi aynı anda public mesaj atınca).
    """
    data = encode_message(msg)
    if lock != None:
        with lock:
            sock.sendall(data)
    else:
        sock.sendall(data)


def decode_line(line):
    """Tek bir satırı (bytes) dict'e çevirir. Hatalıysa ProtocolError fırlatır."""
    try:
        text = line.decode(config.ENCODING)
    except UnicodeDecodeError:
        raise ProtocolError("Message is not valid UTF-8")

    try:
        msg = json.loads(text)
    except ValueError:
        raise ProtocolError("Message is not valid JSON")

    if not isinstance(msg, dict):
        raise ProtocolError("Message must be a JSON object")
    if "type" not in msg or not isinstance(msg["type"], str):
        raise ProtocolError("Message has no 'type' field")
    return msg


class MessageReader:
    """
    Bir socketten satır satır mesaj okur.
    Bir recv ile gelen fazla byte'lar (sonraki mesajın başı) buffer'da kalıyor,
    sonraki read_message() çağrısında önce onlar kullanılıyor.
    """

    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""

    def read_line(self):
        """Bir sonraki tam satırı döndürür ("\n" olmadan). Bağlantı kapandıysa None döner."""
        while True:
            pos = self.buffer.find(b"\n")
            if pos != -1:
                line = self.buffer[:pos]
                self.buffer = self.buffer[pos + 1:]
                if len(line) > config.MAX_MESSAGE_SIZE:
                    raise MessageTooLong("Message is larger than " + str(config.MAX_MESSAGE_SIZE) + " bytes")
                if line.strip() == b"":
                    continue   # boş satırları atlıyoruz
                return line

            # "\n" gelmeden buffer çok büyüdüyse karşı taraf saçma bişey gönderiyo demektir
            if len(self.buffer) > config.MAX_MESSAGE_SIZE:
                raise MessageTooLong("Message is larger than " + str(config.MAX_MESSAGE_SIZE) + " bytes")

            data = self.sock.recv(config.BUFFER_SIZE)
            if not data:
                return None
            self.buffer += data

    def read_message(self):
        """Bir sonraki mesajı dict olarak döndürür. Bağlantı kapandıysa None."""
        line = self.read_line()
        if line == None:
            return None
        return decode_line(line)

    def get_remaining(self):
        """Buffer'da kalan henüz işlenmemiş byte'ları verir ve buffer'ı temizler (relay kullanıyor)."""
        data = self.buffer
        self.buffer = b""
        return data
