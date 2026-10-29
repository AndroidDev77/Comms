#!/usr/bin/env python3
"""
Comms - a console chat room for everyone on the same computer or local network.

Run it in several terminals (or on several machines on the same LAN):

    python3 chat.py

Messages are sent over UDP multicast, so there is no server to start: every
running copy of the app sees every message.

Keys:
    Enter               send message
    Up / Down           scroll one line
    PageUp / PageDown   scroll one page
    Home / End          jump to oldest / newest message
    Mouse wheel         scroll (if your terminal supports it)
    /quit or Ctrl+C     leave the chat
"""

import curses
import json
import queue
import socket
import struct
import sys
import textwrap
import threading
import time
import uuid
from datetime import datetime

GROUP = "239.255.42.99"   # administratively scoped multicast address (LAN only)
PORT = 50999
TTL = 1                   # don't let packets leave the local network
MAX_TEXT = 1000           # keep each message comfortably inside one UDP packet
MAX_HISTORY = 5000        # messages kept in memory for scrollback


# --------------------------------------------------------------------------- #
# Networking
# --------------------------------------------------------------------------- #

LOCAL_NETWORK_HINT = (
    "On macOS, open System Settings > Privacy & Security > Local Network, "
    "turn on your terminal app (Terminal, iTerm, ...), then restart Comms."
)


class Network:
    def __init__(self):
        self.client_id = uuid.uuid4().hex
        self.local_only = False
        self.warning = None     # set when we can't reach the network
        self.sock = self._make_socket()

    def _make_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        # Allow several copies of the app on the same machine to share the port.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", PORT))

        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, TTL)
        # Loop our own packets back so other copies on this computer see them.
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)

        try:
            mreq = struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as e:
            # No usable network interface: fall back to this computer only.
            self._use_loopback(sock, e)
        return sock

    def _use_loopback(self, sock, error):
        if self.local_only:
            return
        self.local_only = True
        self.warning = (f"Can't reach the network ({error}). Chatting with this "
                        f"computer only. {LOCAL_NETWORK_HINT}")
        lo = socket.inet_aton("127.0.0.1")
        mreq = struct.pack("4s4s", socket.inet_aton(GROUP), lo)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            pass  # already a member
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, lo)

    def send(self, kind, user, text=""):
        packet = json.dumps({
            "app": "comms",
            "kind": kind,
            "id": self.client_id,
            "mid": uuid.uuid4().hex,  # lets receivers drop duplicate copies
            "user": user,
            "text": text[:MAX_TEXT],
            "ts": time.time(),
        }).encode("utf-8")

        if self.local_only:
            self.sock.sendto(packet, (GROUP, PORT))
            return

        # Send both multicast and broadcast: many home routers drop one or the other.
        sent, error = False, None
        for dest in (GROUP, "255.255.255.255"):
            try:
                self.sock.sendto(packet, (dest, PORT))
                sent = True
            except OSError as e:
                error = e
        if not sent:
            # e.g. "No route to host": no network, or macOS Local Network
            # permission denied. Keep working locally but tell the user.
            self._use_loopback(self.sock, error)
            self.sock.sendto(packet, (GROUP, PORT))

    def listen(self, inbox):
        """Runs in a background thread, pushing decoded messages onto inbox."""
        while True:
            try:
                data, _addr = self.sock.recvfrom(65535)
            except OSError:
                return  # socket closed on exit
            try:
                msg = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(msg, dict) and msg.get("app") == "comms":
                inbox.put(msg)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# User interface
# --------------------------------------------------------------------------- #

class ChatUI:
    def __init__(self, stdscr, net, username):
        self.scr = stdscr
        self.net = net
        self.username = username
        self.inbox = queue.Queue()
        self.messages = []      # list of (color_pair, text)
        self.seen = {}          # message ids already shown (insertion-ordered)
        self.warned = False
        self.scroll = 0         # lines scrolled up from the bottom
        self.input = ""
        self.cursor = 0

    # ----- setup -------------------------------------------------------------

    def setup(self):
        curses.curs_set(1)
        self.scr.keypad(True)
        self.scr.timeout(100)   # poll for network messages 10x per second
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)      # other users
            curses.init_pair(2, curses.COLOR_GREEN, -1)     # me
            curses.init_pair(3, curses.COLOR_YELLOW, -1)    # system
            curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_CYAN)  # bars

    # ----- message handling --------------------------------------------------

    def add(self, color, text):
        self.messages.append((color, text))
        if len(self.messages) > MAX_HISTORY:
            del self.messages[0]
        # Keep the view still if the user is reading older messages.
        if self.scroll:
            self.scroll += len(self.wrap(text))

    def handle(self, msg):
        # Each message arrives twice (multicast + broadcast); show it once.
        mid = msg.get("mid")
        if mid:
            if mid in self.seen:
                return
            self.seen[mid] = True
            if len(self.seen) > MAX_HISTORY:
                del self.seen[next(iter(self.seen))]
        try:
            stamp = datetime.fromtimestamp(float(msg.get("ts"))).strftime("%H:%M:%S")
        except (TypeError, ValueError, OverflowError, OSError):
            stamp = datetime.now().strftime("%H:%M:%S")
        user = str(msg.get("user", "?"))[:32]
        text = str(msg.get("text", ""))[:MAX_TEXT]
        kind = msg.get("kind")
        mine = msg.get("id") == self.net.client_id

        if kind == "msg":
            self.add(2 if mine else 1, f"[{stamp}] {user}: {text}")
        elif kind == "join":
            self.add(3, f"[{stamp}] *** {user} joined the chat")
        elif kind == "leave":
            self.add(3, f"[{stamp}] *** {user} left the chat")

    def drain_inbox(self):
        if self.net.warning and not self.warned:
            self.warned = True
            self.add(3, f"*** WARNING: {self.net.warning}")
        changed = False
        while True:
            try:
                self.handle(self.inbox.get_nowait())
                changed = True
            except queue.Empty:
                return changed

    # ----- drawing -----------------------------------------------------------

    def wrap(self, text):
        width = max(10, curses.COLS - 1)
        return textwrap.wrap(text, width, subsequent_indent="    ",
                             replace_whitespace=False, drop_whitespace=True) or [""]

    def all_lines(self):
        lines = []
        for color, text in self.messages:
            lines.extend((color, line) for line in self.wrap(text))
        return lines

    def view_height(self):
        return max(1, curses.LINES - 3)  # title bar, status bar, input line

    def draw(self):
        self.scr.erase()
        h, w = curses.LINES, curses.COLS
        bar = curses.color_pair(4) if curses.has_colors() else curses.A_REVERSE

        where = "THIS COMPUTER ONLY" if self.net.local_only else f"LAN {local_ip()}"
        title = f" Comms - you are {self.username} - {where} "
        self.scr.addnstr(0, 0, title.ljust(w), w - 1, bar)

        lines = self.all_lines()
        height = self.view_height()
        max_scroll = max(0, len(lines) - height)
        self.scroll = min(self.scroll, max_scroll)
        end = len(lines) - self.scroll
        visible = lines[max(0, end - height):end]
        for row, (color, line) in enumerate(visible, start=1):
            attr = curses.color_pair(color) if curses.has_colors() else 0
            self.scr.addnstr(row, 0, line, w - 1, attr)

        if self.scroll:
            status = f" -- scrolled up {self.scroll} lines (End to jump to newest) -- "
        else:
            status = " Enter: send | PgUp/PgDn/Up/Down: scroll | /quit: exit "
        self.scr.addnstr(h - 2, 0, status.ljust(w), w - 1, bar)

        # Input line, horizontally scrolled so the cursor is always visible.
        prompt = "> "
        room = max(1, w - len(prompt) - 1)
        start = max(0, self.cursor - room + 1)
        self.scr.addnstr(h - 1, 0, prompt + self.input[start:start + room], w - 1)
        self.scr.move(h - 1, len(prompt) + self.cursor - start)
        self.scr.refresh()

    # ----- input -------------------------------------------------------------

    def on_key(self, key):
        """Returns False when the user wants to quit."""
        page = self.view_height() - 1

        if key in ("\n", "\r", curses.KEY_ENTER):
            text = self.input.strip()
            self.input, self.cursor = "", 0
            if text in ("/quit", "/exit"):
                return False
            if text:
                self.net.send("msg", self.username, text)
                self.scroll = 0
        elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            if self.cursor:
                self.input = self.input[:self.cursor - 1] + self.input[self.cursor:]
                self.cursor -= 1
        elif key == curses.KEY_DC:
            self.input = self.input[:self.cursor] + self.input[self.cursor + 1:]
        elif key == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_RIGHT:
            self.cursor = min(len(self.input), self.cursor + 1)
        elif key == curses.KEY_UP:
            self.scroll += 1
        elif key == curses.KEY_DOWN:
            self.scroll = max(0, self.scroll - 1)
        elif key == curses.KEY_PPAGE:
            self.scroll += page
        elif key == curses.KEY_NPAGE:
            self.scroll = max(0, self.scroll - page)
        elif key == curses.KEY_HOME:
            self.scroll = len(self.all_lines())
        elif key == curses.KEY_END:
            self.scroll = 0
        elif key == curses.KEY_MOUSE:
            try:
                _, _, _, _, state = curses.getmouse()
            except curses.error:
                return True
            if state & curses.BUTTON4_PRESSED:
                self.scroll += 3
            elif state & getattr(curses, "BUTTON5_PRESSED", 0):
                self.scroll = max(0, self.scroll - 3)
        elif key == curses.KEY_RESIZE:
            curses.update_lines_cols()
        elif isinstance(key, str) and key.isprintable():
            if len(self.input) < MAX_TEXT:
                self.input = self.input[:self.cursor] + key + self.input[self.cursor:]
                self.cursor += 1
        return True

    # ----- main loop ---------------------------------------------------------

    def run(self):
        self.setup()
        threading.Thread(target=self.net.listen, args=(self.inbox,), daemon=True).start()
        self.add(3, f"Welcome, {self.username}! Messages are shared with everyone "
                    f"running Comms on this computer or network.")
        self.net.send("join", self.username)

        try:
            while True:
                self.drain_inbox()
                self.draw()
                try:
                    key = self.scr.get_wch()
                except curses.error:
                    continue  # timeout, no key pressed
                if not self.on_key(key):
                    break
        except KeyboardInterrupt:
            pass
        finally:
            try:
                self.net.send("leave", self.username)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

_local_ip = None


def local_ip():
    """This computer's LAN address, shown in the title bar to help troubleshooting."""
    global _local_ip
    if _local_ip is None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # no packet is sent; just picks a route
            _local_ip = s.getsockname()[0]
        except OSError:
            _local_ip = "no network"
        finally:
            s.close()
    return _local_ip


def ask_username():
    while True:
        try:
            name = input("Enter a username: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if not name:
            print("Username can't be empty.")
        elif len(name) > 32:
            print("Please keep it to 32 characters or fewer.")
        else:
            return name


def main():
    username = ask_username()
    try:
        net = Network()
    except OSError as e:
        sys.exit(f"Could not open the chat socket on port {PORT}: {e}")
    try:
        curses.wrapper(lambda stdscr: ChatUI(stdscr, net, username).run())
    finally:
        net.close()
    print("Goodbye!")


if __name__ == "__main__":
    main()
