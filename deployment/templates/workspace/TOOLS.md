# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Platform Setup

### Discord & Telegram

Both channels are enabled and active:
- **Discord**: Token configured, channel 1469940490773332079 enabled
- **Telegram**: Primary channel, direct chat enabled

### Sending Messages

Use OpenClaw's internal routing (not exec/curl):
- **Reply in current session** → automatically routes to source channel
- **Cross-session messaging** → use `sessions_send(sessionKey, message)`
- **Never use exec/curl for provider messaging** — OpenClaw handles all routing

### Emoji Reactions

Enabled for Telegram in MINIMAL mode:
- React only when truly relevant
- At most 1 reaction per 5-10 exchanges
- Natural use: 👍, ❤️, 🙌, 😂, 💀, 🤔, 💡, ✅, 👀

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
