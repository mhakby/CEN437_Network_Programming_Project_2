# Multi-User Chat System – CEN437 Project 02

**Name Surname:** Mehmet Hadi Akbey
**Student ID:** 2021555501

A multi-user chat system with a Python TCP chat server, a console Python client,
a read-only monitoring page that gets live events over WebSocket, and an optional relay
that adds the `*` prefix to nicknames. The chat part is built on top of the Simple Chat
module from Project 01 (one thread per client, `sendall` / `recv`, Ctrl+C handling).

## Requirements

- Python 3.8 or newer (tested with **Python 3.11.8** on Windows 11)
- No extra libraries are needed, only the standard library is used
  (`socket`, `threading`, `json`, `argparse`, `hashlib`, `base64`, `struct`, `time`, `datetime`, `os`).
  The WebSocket part is written by hand with `socket`, so nothing has to be installed.
  `requirements.txt` is included but it has no packages in it.
- Any browser with WebSocket support for the monitor page (Chrome, Edge, Firefox vs).

## Files

| File | Description |
|------|-------------|
| `chat_server.py` | Chat server (direct port + relay port + starts the monitor) |
| `chat_client.py` | Console chat client, run more than one of these |
| `chat_relay.py` | Optional relay, adds `*` to the nickname |
| `monitor.py` | Monitoring HTTP + WebSocket server (used by `chat_server.py`) |
| `monitor.html` | Read-only monitoring page |
| `protocol.py` | Message format, framing (`\n` delimited JSON), reading/sending messages |
| `utils.py` | Logging and common functions |
| `config.py` | Default IPs, ports, limits and log file paths |
| `requirements.txt` | Dependency file (standard library only) |

## Default Addresses and Ports

All of them can be changed in `config.py` or with command line arguments (`--help`).

| What | Default | Argument |
|------|---------|----------|
| Server listening IP (chat + relay ports) | `127.0.0.1` | `chat_server.py --host` |
| TCP chat port (direct clients) | `5050` | `chat_server.py --port` |
| Server relay port (only for `chat_relay.py`) | `5051` | `chat_server.py --relay-port` / `--no-relay` |
| Monitor HTTP + WebSocket IP | `127.0.0.1` | `chat_server.py --monitor-host` |
| Monitor HTTP + WebSocket port | `8080` | `chat_server.py --monitor-port` |
| Relay listening IP / port | `127.0.0.1:5060` | `chat_relay.py --listen-host --listen-port` |
| Relay → server | `127.0.0.1:5051` | `chat_relay.py --server-host --server-port` |

To use the chat from other computers start the server with `--host 0.0.0.0`
(and `--monitor-host 0.0.0.0` for the monitor page).

## How to Run

Startup order: **server → (relay) → clients → monitor page**. Open a new terminal for each one.

```
python chat_server.py
python chat_relay.py                        (optional)
python chat_client.py                       (asks IP, port and nickname)
python chat_client.py --nick Ali            (127.0.0.1:5050 is used)
python chat_client.py --nick Veli
python chat_client.py --port 5060 --nick Ayse      (through the relay -> *Ayse)
```

Other examples:

```
python chat_server.py --host 0.0.0.0 --port 6000 --monitor-port 9000
python chat_client.py --host 192.168.1.101 --port 6000 --nick Ali
```

Monitor page: open **http://127.0.0.1:8080/** in the browser (the server prints this URL when it starts).
Press Ctrl+C to stop the server or the relay.

## Client Usage

| Command | Description |
|---------|-------------|
| `<message>` | Public message to everyone |
| `/msg <nick> <message>` | Private message, e.g. `/msg Veli hello` |
| `/msg <number> <message>` | Private message to the user number in the `/users` list, e.g. `/msg 2 hello` |
| `/users` | Shows the numbered online user list |
| `/help` | Shows the commands |
| `/quit` or `/exit` | Leaves the chat (Ctrl+C also works) |

Public messages are shown as `[14:05:33] Ali: hello`, private messages as
`[14:05:40] [PRIVATE from Ali] ...` and `[14:05:40] [PRIVATE to Veli] ...`.
Join/leave messages also show the current user list. If the server or relay is closed,
the client prints `Connection to the server was lost`.

## Design Notes

### Message format and framing

- Every message is **one JSON object on one line**, ending with `\n`. Encoding is **UTF-8**.
- TCP is a stream, so one `recv()` can return half of a message or two messages together.
  `MessageReader` in `protocol.py` keeps the bytes in a buffer and cuts a message only when it sees `\n`.
  The remaining bytes stay in the buffer for the next message.
- A `\n` inside a chat message does not break the framing because `json.dumps` writes it as `\\n`.
- Max size of one message line is **4096 bytes** (`MAX_MESSAGE_SIZE`). The message text can be at most
  **500 characters**, nicknames at most **16 characters** (letters, numbers, `_`, `-`).
- Every socket write is done with a per-connection lock, so two threads can not mix their bytes on the same connection.

Client → server:

```
{"type": "register", "nick": "Ali"}
{"type": "public", "text": "hello"}
{"type": "private", "to": "Veli", "text": "hi"}
{"type": "users"}
{"type": "quit"}
```

Server → client:

```
{"type": "welcome", "nick": "Ali2", "users": [...], "time": "..."}     answer to register
{"type": "public", "from": "Ali", "text": "...", "time": "2026-11-11 14:05:33"}
{"type": "private", "from": "Ali", "to": "Veli", "text": "...", "time": "..."}
{"type": "private_sent", "to": "Veli", "text": "...", "time": "..."}  confirmation to the sender
{"type": "join" / "leave", "nick": "...", "users": [...], "time": "..."}
{"type": "userlist", "users": [...]}
{"type": "muted", "seconds": 15, "text": "..."}
{"type": "error", "text": "..."}
```

### Registration

1. Client connects and sends `register` as the **first** message (within 30 seconds).
2. Server checks the nickname. On the direct port a nickname starting with `*` is rejected.
   On the relay port the nickname has to start with exactly one `*`.
3. If the nickname is taken a number is added and checked again: `Ali → Ali2 → Ali3`, `*Ali → *Ali2`.
   Checking and saving the nickname is done inside the same lock, so two clients can not get the same name.
4. Server sends `welcome` with the assigned nickname and the user list, and sends `join` to the others.
   If registration fails the server sends `error` and closes the connection.

### Validation

- Invalid JSON, missing `type`, unknown type, missing/empty/too long text, missing recipient → `error` message,
  the connection stays open.
- A line bigger than 4096 bytes → `error` and the connection is closed (the buffer can not be trusted anymore).
- The sender is always taken from the registered connection. A `from` field sent by a client is ignored,
  so nobody can send messages with another user's name.
- A private message to a user that is not online returns an error to the sender. It is not stored.

### Concurrency

- One thread per client (`handle_client`), one thread for each listening socket, one thread per monitor viewer.
- Shared data is protected with locks: `clients_lock` (user list / nickname registry), `count_lock` (message counter),
  `log_lock` (log file), `viewers_lock` (monitor pages) and a `send_lock` for every client socket.
- When a client disconnects (`/quit`, closed window, error) it is removed from the list, its socket is closed,
  the others get a `leave` message and the thread finishes. The server keeps running.

### Monitoring

- `monitor.py` serves `monitor.html` and the WebSocket on the **same monitor port (8080)**, which is different
  from the chat ports. `/` returns the page, `/ws` is the WebSocket (handshake is done with `Sec-WebSocket-Accept`).
- Only events after the page is opened are shown (connect, join, leave, public, private, failed private, rate limit, errors).
- The page shows the online user count and the processed message count. The count goes up by 1 for each
  public/private message that is forwarded, no matter how many users get it. Rejected messages and system events are not counted.
- Monitor pages are not chat users, they never appear in the user list. Closing the page does not affect the chat.

### Rate-limit policy

- Per chat user (every connection has its own counter, also relayed users).
- **5 messages in a 5 second window** (public + private together). The counter resets every 5 seconds.
- The 6th message is rejected and the user is **muted for 15 seconds**. The user gets a `muted` message
  and during the mute every message is rejected with an `error` that shows the remaining seconds.
- Every rate-limit event is written to the log and shown on the monitor page.
- Values are in `config.py` (`RATE_LIMIT_COUNT`, `RATE_LIMIT_INTERVAL`, `MUTE_DURATION`).

### Relay (optional)

- Path: `chat_client.py` → `chat_relay.py (5060)` → `chat_server.py relay port (5051)`.
  The monitor page does not go through the relay.
- For every accepted client the relay opens a separate connection to the server, so every relayed user has
  its own nickname and its own rate limit.
- The relay reads the first message with the same `MessageReader` (works even if the register message comes
  in pieces), rejects nicknames starting with `*`, adds one `*` and sends the register message to the server.
  Bytes that came after the register message are forwarded too.
- After that two threads copy bytes in both directions without changing them. The `welcome` message with
  the assigned nickname goes back to the client as it is.
- When one side closes, both sockets are closed and the threads finish.
- The server knows relay traffic because it comes to the separate relay port. There is no authentication
  (not required).

## Logs

The `logs/` folder is created automatically.

| File | Content |
|------|---------|
| `logs/server_log.txt` | Every event: connections, joins/leaves, public and private messages (time, type, sender, recipient), failed private messages, rate-limit events, errors, monitor viewers |
| `logs/error_log.txt` | Errors only |
| `logs/relay_log.txt` | Relay connections and closes |

Example lines:

```
2026-11-11 14:05:33 [PUBLIC] from=Ali to=ALL text='hello'
2026-11-11 14:05:40 [PRIVATE] from=Ali to=Veli text='hi'
2026-11-11 14:05:52 [PRIVATE_FAIL] from=Ali to=Ghost (user not online)
2026-11-11 14:06:10 [RATE_LIMIT] Veli exceeded the limit (5 msg / 5 s), muted for 15 s
```

## Basic Functional Tests

Screenshots are in the `test_outputs/` folder. All components were run on the same computer.

**T01 – Public chat:** Server + two clients (`Mhakby`, `Ali`). Both sides send public messages. Every message
has the time and the nickname, the join message shows the user list, `/users` shows the numbered list.
→ `T01_public_chat.png`

**T02 – Private chat:** Third client `Ayşe` is connected. `Mhakby` sends `/msg Ali ...`, only Ali gets it
(Ayşe's window has nothing). Then `/msg Ghost hi` → `User 'Ghost' is not online` error.
→ `T02_private_chat.png`, `T02_private_error.png`

**T03 – Nicknames:** Two clients ask for `Ali`, the second one becomes `Ali2`. A direct client asking for
`*Ali` gets `Nicknames starting with * are reserved for relay users`.
→ `T03_same_nickname.png`, `T03_star_rejected.png`

**T04 – Disconnection:** One client uses `/quit`, another one is closed with the window X button.
The server writes `LEAVE`, the other clients get the new user list and the server keeps working.
→ `T04_exit.png`, `T04_window_closed.png`

**T05 – Rate limiting:** 1) One Client try to send a message which is too long (over 500 characters ).
2)Messages are sent fast from one client. The first 5 are sent, then `[RATE LIMIT] Too many messages! 
... muted for 15 seconds`, then the other messages are rejected until the mute ends.
→ `T05_rate_limit.png`, `T05_rate_limit_2.png`

**T06 – Logs and live monitoring:** Monitor page is open at `http://127.0.0.1:8080/`. New messages appear without
refreshing. One public message to 2 users increases the message count by only 1. The page is closed and the chat still works.
`logs/server_log.txt` is shown.
→ `T06_monitor_count_before.png`, `T06_monitor_count_after.png`, `T06_page_closed.png`, `T06_log_file.png`, `T06_log_file_2.png`

**T07 – Relay (optional):** `chat_relay.py` is started, a client connects with `--port 5060 --nick Mehmet` and
gets `*Mehmet`. It exchanges public and private messages with a direct client. After `/quit` the relay log shows
the connection is closed and the server sends `leave`.
→ `T07_relay.png`, `T07_relay_exit.png`

## Known Issues

- When a message arrives while you are typing, the message is printed and `> ` is written again,
  but the text you were typing is not shown again on the screen (it is still there, Enter sends it).
- Nicknames are case sensitive, so `ali` and `Ali` can both be online at the same time.
- If a nickname is only numbers (e.g. `123`), `/msg 123 ...` uses it as a list number, not as a nickname.
- The monitor page does not reconnect by itself. If the server is restarted the page has to be refreshed.
- Turkish characters can look wrong in some old Windows terminals (cmd with a different code page).
- When the suffix is added the nickname can be longer than 16 characters (e.g. a 16 character name + `2`).
