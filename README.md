# Comms

A console chat room for everyone on the same computer or local network. No server needed — every running copy sees every message.

## Run

Requires Python 3.8+ (no extra packages on macOS/Linux).

```
python3 chat.py
```

Enter a username when asked, then start typing. On Windows, first run `pip install windows-curses`.

## Keys

| Key | Action |
| --- | --- |
| Enter | Send message |
| Up / Down | Scroll one line |
| PageUp / PageDown | Scroll one page |
| Home / End | Jump to oldest / newest |
| Mouse wheel | Scroll |
| `/quit` or Ctrl+C | Leave |

## Notes

- Uses UDP multicast (`239.255.42.99:50999`, TTL 1), so messages stay on the local network.
- Your firewall must allow UDP port 50999 for chatting between computers. Some guest/corporate Wi-Fi networks block multicast.
- Messages are not encrypted and are not stored — you only see messages sent while you're running the app.
