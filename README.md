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

- Sends each message by UDP multicast (`239.255.42.99:50999`, TTL 1) and UDP broadcast, so messages stay on the local network.
- Messages are not encrypted and are not stored — you only see messages sent while you're running the app.

## Troubleshooting (not seeing messages from another computer)

The title bar shows `LAN <your IP>` when on the network, or `THIS COMPUTER ONLY` if it couldn't reach it.

1. **macOS Local Network permission** — System Settings → Privacy & Security → **Local Network** → turn on your terminal app (Terminal, iTerm, …). Restart the terminal and Comms.
2. **macOS firewall** — System Settings → Network → Firewall. If on, allow incoming connections for Python (or turn off "Block all incoming connections").
3. **Same network** — both IPs in the title bar should start the same way (e.g. `192.168.0.x`). Guest Wi-Fi and networks with "client/AP isolation" block devices from talking to each other.
