#!/usr/bin/env python3
"""
Claude Code global status line — single line, gradient percentages.

  ✦ Opus 5 1M ⚡high ┃ ctx - 12% of 1M ┃ 5h - 26% ↻2h04m ┃ 7d - 52% ↻3d1h ┃ fable - 32% ↻3d1h

Percentages are coloured on a smooth mint → lime → yellow → orange → red
gradient by how high they are. The model name takes its own colour from a
hash of its identity, so any model — future releases, or a non-Anthropic
one — gets a distinct, stable hue with nothing hardcoded.

Stdlib only. Tweak the CONFIG block below to taste.

The "fable" meter comes from the OAuth /usage endpoint (same source as the
/usage command), cached in ~/.claude/statusline-fable.json and refreshed at
most every FABLE_TTL seconds.

Debug: CC_STATUSLINE_DEBUG=1 dumps the raw payload to ~/.claude/statusline-last.json
"""

import colorsys
import json
import math
import os
import random
import re
import sys
import time
import unicodedata
import zlib
from datetime import datetime

# ─────────────────────────────── CONFIG ────────────────────────────────

MAX_LINES = 3  # wrap onto up to this many lines before dropping segments
SHOW_ABSOLUTE_RESET = True  # clock time next to the countdown (first thing shed when narrow)

FABLE_TTL = 120  # seconds between /usage fetches for the fable meter
FABLE_RETRY = 30  # seconds to wait before retrying after a failed fetch
FABLE_TIMEOUT = 1.5  # network timeout; render falls back to cache beyond this

CACHE_FILE = os.path.expanduser("~/.claude/statusline-cache.json")
FABLE_CACHE_FILE = os.path.expanduser("~/.claude/statusline-fable.json")
CREDS_FILE = os.path.expanduser("~/.claude/.credentials.json")
DEBUG_FILE = os.path.expanduser("~/.claude/statusline-last.json")

# ─────────────────────────────── PALETTE ───────────────────────────────

NO_COLOR = bool(os.environ.get("NO_COLOR")) or os.environ.get("TERM") == "dumb"
_CT = os.environ.get("COLORTERM", "").lower()
TRUECOLOR = not NO_COLOR and (
    "truecolor" in _CT
    or "24bit" in _CT
    or os.environ.get("TERM_PROGRAM") in ("iTerm.app", "WezTerm", "ghostty", "vscode")
    or any(t in os.environ.get("TERM", "") for t in ("kitty", "alacritty", "ghostty", "wezterm", "direct"))
)


def c(code):
    return "" if NO_COLOR else f"\033[{code}m"


def rgb(r, g, b):
    return "" if NO_COLOR else f"\033[38;2;{r};{g};{b}m"


R = c("0")
B = c("1")

GOLD = c("38;5;220")  # effort / flags
SLATE = c("38;5;250")  # secondary text
FAINT = c("38;5;244")  # punctuation, clocks
TEAL = c("38;5;44")  # situational extras
SEP = f"{c('38;5;255')}{B}┃{R}"
DASH = f"{FAINT}-{R}"

TANGERINE = c("38;5;208")
CRIMSON = c("38;5;197")

# Heat gradient for the % numbers: calm mint -> lime -> yellow -> orange -> red.
# Truecolor interpolates smoothly between stops; 256-colour steps every ~10%.
HEAT_STOPS = [
    (0, (62, 220, 170)),  # mint
    (30, (140, 225, 95)),  # lime
    (55, (240, 220, 75)),  # yellow
    (75, (255, 165, 55)),  # orange
    (90, (255, 105, 65)),  # hot orange
    (100, (255, 55, 95)),  # red-pink
]
HEAT_256 = [48, 84, 118, 154, 190, 226, 220, 214, 208, 202, 197]


def heat(pct):
    if pct is None:
        return SLATE
    pct = max(0.0, min(100.0, float(pct)))
    if NO_COLOR:
        return ""
    if not TRUECOLOR:
        return c(f"38;5;{HEAT_256[min(len(HEAT_256) - 1, int(pct // 10))]}")
    for (p1, c1), (p2, c2) in zip(HEAT_STOPS, HEAT_STOPS[1:]):
        if pct <= p2:
            t = (pct - p1) / (p2 - p1)
            return rgb(*(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    return rgb(*HEAT_STOPS[-1][1])


# Gradient ramps: truecolor endpoints + 256-colour fallback stops.
# Tuned bright and saturated to pop against a dark background.
RAMPS = {
    "cyan": {"tc": ((90, 245, 255), (120, 190, 255)), "x256": [51, 87, 117, 111]},
    "azure": {"tc": ((110, 185, 255), (165, 150, 255)), "x256": [75, 111, 141, 147]},
    "teal": {"tc": ((70, 255, 210), (80, 210, 255)), "x256": [49, 50, 86, 80]},
    "violet": {"tc": ((220, 155, 255), (255, 150, 220)), "x256": [177, 183, 219, 218]},
    # effort tiers: calm -> plasma
    "e_low": {"tc": ((150, 195, 225), (125, 165, 225)), "x256": [110, 111]},
    "e_med": {"tc": ((110, 230, 185), (95, 205, 255)), "x256": [85, 86]},
    "e_high": {"tc": ((255, 215, 95), (255, 175, 65)), "x256": [220, 214]},
    "e_xhigh": {"tc": ((255, 175, 65), (255, 100, 150)), "x256": [214, 204]},
    "e_max": {"tc": ((255, 95, 95), (205, 95, 255)), "x256": [203, 135]},
}

# effort tier -> (glyph, ramp)
EFFORT_STYLES = {
    "low": ("·", "e_low"),
    "medium": ("⌁", "e_med"),
    "high": ("⚡", "e_high"),
    "xhigh": ("⚡", "e_xhigh"),
    "max": ("✸", "e_max"),
}


SPARKLES = "✦✧⋆✶✷✸"


def foil_text(s, tick):
    """Holographic foil: a pale iridescent sheen drifting under the letters,
    with a couple of blown-out white glints that jump each refresh. Low
    saturation is what sells it as foil rather than neon."""
    if NO_COLOR:
        return s
    rnd = random.Random(tick)
    glints = set(rnd.sample(range(len(s)), min(2, len(s))))
    if not TRUECOLOR:
        sheen = [231, 189, 159, 183, 225, 195]
        return "".join(
            f"{c('38;5;231')}{ch}" if i in glints else f"{c(f'38;5;{sheen[(tick + i) % len(sheen)]}')}{ch}"
            for i, ch in enumerate(s)
        )
    out = []
    for i, ch in enumerate(s):
        if i in glints:
            out.append(f"{rgb(255, 255, 255)}{ch}")
            continue
        hue = (0.55 + 0.5 * math.sin(tick * 0.6 + i * 0.5)) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.4, 1.0)
        out.append(f"{rgb(int(r * 255), int(g * 255), int(b * 255))}{ch}")
    return "".join(out)


def ultra_text():
    """The ultracode flag: foil sheen plus a twinkling sparkle glyph. Each
    2-second status-line refresh advances the tick, so it animates in place."""
    tick = int(time.time() / 2)
    return foil_text(f"{SPARKLES[tick % len(SPARKLES)]}ultracode", tick)


def is_ultracode(p):
    """Ultracode reports as plain xhigh in the payload; the only trace is the
    /effort output recorded in the session transcript. Scan the tail for the
    most recent effort change."""
    if ((p.get("effort") or {}).get("level")) == "ultracode":
        return True
    if ((p.get("effort") or {}).get("level")) != "xhigh":
        return False
    path = p.get("transcript_path")
    if not path:
        return False
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 262144))
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        return False
    hits = re.findall(r"Set effort level to (\w+)", tail)
    return bool(hits) and hits[-1] == "ultracode"


def ramp_color(ramp, t):
    """Colour at position t∈[0,1] along a ramp."""
    if NO_COLOR:
        return ""
    r = RAMPS[ramp]
    if TRUECOLOR:
        (r1, g1, b1), (r2, g2, b2) = r["tc"]
        return rgb(int(r1 + (r2 - r1) * t), int(g1 + (g2 - g1) * t), int(b1 + (b2 - b1) * t))
    stops = r["x256"]
    return c(f"38;5;{stops[min(len(stops) - 1, int(t * len(stops)))]}")


def ramp_start(ramp):
    return ramp_color(ramp, 0.0)


def grad_text(s, ramp):
    """Text with a horizontal colour gradient (truecolor only)."""
    if not TRUECOLOR or NO_COLOR:
        return f"{ramp_start(ramp)}{s}"
    n = max(1, len(s) - 1)
    return "".join(f"{ramp_color(ramp, i / n)}{ch}" for i, ch in enumerate(s))


# ─────────────────────── MODEL-INDEPENDENT COLOURING ───────────────────
# Nothing here knows any model by name. The model's identity is normalised
# to a key, hashed, and the hash picks one of HUE_STEPS evenly-spaced hues —
# so a model added years from now, or one from another vendor entirely,
# still lands on its own stable colour. Quantising (rather than using the
# raw hash as a hue) means two models either share a hue outright or differ
# visibly; they never land 3° apart and look like the same colour.

HUE_STEPS = 18
# Bright xterm-256 hue ring for non-truecolor terminals, in hue order.
HUE_RING = [210, 216, 222, 228, 192, 156, 120, 121, 122, 123, 117, 111, 105, 141, 177, 213, 212, 211]

_BRACKETED = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_REGION = re.compile(r"^(us|eu|apac|global)\.")
# Only a dotted/slashed vendor prefix is namespacing ('anthropic.claude-opus-5');
# a hyphen usually means the vendor name is part of the family ('mistral-large').
_VENDOR = re.compile(r"^(anthropic|openai|google|meta|mistral|cohere|xai|deepseek|qwen|azure|bedrock)\.")
_NOISE = re.compile(r"(latest|preview|thinking|context|\bv\d+\b)")


def model_key(model):
    """Collapse the many spellings of one model onto a single key, so the
    display name and the API id agree: 'Opus 5 (1M context)' and
    'claude-opus-5[1m]' both reduce to 'opus5', 'Haiku 4.5' and
    'claude-haiku-4-5-20251001' both to 'haiku45'."""
    raw = (model.get("id") or model.get("display_name") or "").lower()
    s = _BRACKETED.sub(" ", raw)  # (1M context), [1m]
    s = s.split("/")[-1]  # anthropic/claude-opus-5
    s = _REGION.sub("", s)  # bedrock region prefix
    s = _VENDOR.sub("", s)
    s = re.sub(r":\d+$", "", s)  # bedrock :0
    s = re.sub(r"-\d{6,8}$", "", s)  # trailing release date
    s = re.sub(r"-v\d+$", "", s)
    s = s.replace("claude", "")
    s = _NOISE.sub("", s)
    return re.sub(r"[^a-z0-9]", "", s) or "model"


def model_hue(model):
    """(hue, sweep_direction) — stable for a given model, arbitrary across them."""
    h = zlib.crc32(model_key(model).encode())
    return (h % HUE_STEPS) / HUE_STEPS, 1 if (h // HUE_STEPS) % 2 else -1


def hue_color(hue, sat=0.5):
    """Bright colour at a point on the wheel; value is pinned at max so every
    hue stays legible against a dark background."""
    if NO_COLOR:
        return ""
    hue %= 1.0
    if not TRUECOLOR:
        return c(f"38;5;{HUE_RING[int(hue * len(HUE_RING)) % len(HUE_RING)]}")
    r, g, b = colorsys.hsv_to_rgb(hue, sat, 1.0)
    return rgb(int(r * 255), int(g * 255), int(b * 255))


def hue_grad_text(s, hue, sweep=0.16, sat=0.5):
    """Gradient text sweeping from `hue` to `hue + sweep`."""
    if NO_COLOR:
        return s
    if not TRUECOLOR:
        return f"{hue_color(hue, sat)}{s}"
    n = max(1, len(s) - 1)
    return "".join(f"{hue_color(hue + sweep * i / n, sat)}{ch}" for i, ch in enumerate(s))


# ──────────────────────────────── UTIL ─────────────────────────────────

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def vwidth(s):
    """Visible width, ignoring ANSI codes."""
    s = ANSI_RE.sub("", s)
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def term_width(default=110):
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            w = os.get_terminal_size(stream.fileno()).columns
            if w > 20:
                return w
        except Exception:
            pass
    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
        try:
            w = os.get_terminal_size(fd).columns
            if w > 20:
                return w
        finally:
            os.close(fd)
    except Exception:
        pass
    try:
        w = int(os.environ.get("COLUMNS", "0"))
        if w > 20:
            return w
    except ValueError:
        pass
    return default


def fmt_delta(secs):
    """Compact countdown: 45s / 43m / 2h14m / 4d6h."""
    if secs is None:
        return "?"
    secs = int(secs)
    if secs <= 0:
        return "now"
    if secs < 60:
        return f"{secs}s"
    m = secs // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d{h}h"


def fmt_count(n):
    """1000000 -> 1M, 200000 -> 200k."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.0f}M" if abs(v - round(v)) < 0.05 else f"{v:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def fmt_clock(ts):
    """Local clock time; adds a weekday once it is not today."""
    try:
        dt = datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return None
    if dt.date() == datetime.now().date():
        return dt.strftime("%H:%M")
    return dt.strftime("%a %H:%M")


def parse_reset(v):
    """Epoch seconds or ISO timestamp -> epoch seconds."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        try:
            return float(s)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def atomic_write(path, obj):
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


# ─────────────────────────── RATE-LIMIT CACHE ──────────────────────────
# rate_limits only appears once the session has seen an API response header,
# so the first renders of a fresh session would otherwise show nothing.
# Cache the last known values globally and reuse them, marked stale with ~.


def resolve_limits(payload):
    """Returns (limits_dict, is_stale)."""
    live = payload.get("rate_limits") or {}
    if live.get("five_hour") or live.get("seven_day"):
        atomic_write(CACHE_FILE, {"rate_limits": live, "saved_at": time.time()})
        return live, False
    try:
        with open(CACHE_FILE) as f:
            cached = (json.load(f) or {}).get("rate_limits") or {}
    except Exception:
        cached = {}
    now = time.time()
    fresh = {}
    for key, val in cached.items():
        reset = parse_reset((val or {}).get("resets_at"))
        # A window that has already rolled over tells us nothing useful.
        if reset is not None and reset > now:
            fresh[key] = val
    return fresh, bool(fresh)


# ───────────────────────────── FABLE USAGE ─────────────────────────────
# The status-line payload only carries the generic 5h/7d windows; the
# Fable-scoped weekly limit lives in the OAuth /usage endpoint (what the
# /usage command reads). Fetch it at most every FABLE_TTL seconds.


def fetch_fable():
    """Returns {'used_percentage': .., 'resets_at': ..} or None if no fable limit."""
    with open(CREDS_FILE) as f:
        oauth = (json.load(f) or {}).get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    expires = oauth.get("expiresAt")
    if not token or (expires and expires / 1000 < time.time() + 30):
        raise RuntimeError("token unavailable")
    import urllib.request

    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"},
    )
    with urllib.request.urlopen(req, timeout=FABLE_TIMEOUT) as resp:
        data = json.load(resp)
    for lim in data.get("limits") or []:
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name") or ""
        if "fable" in model.lower():
            return {"used_percentage": lim.get("percent"), "resets_at": lim.get("resets_at")}
    return None


def resolve_fable():
    """Returns (data_or_None, is_stale). data None can mean 'no fable limit'."""
    try:
        with open(FABLE_CACHE_FILE) as f:
            cache = json.load(f) or {}
    except Exception:
        cache = {}
    now = time.time()
    if now - cache.get("fetched_at", 0) < FABLE_TTL:
        return cache.get("fable"), False
    if now - cache.get("attempted_at", 0) < FABLE_RETRY:
        return cache.get("fable"), True
    cache["attempted_at"] = now
    try:
        cache["fable"] = fetch_fable()
        cache["fetched_at"] = now
        stale = False
    except Exception:
        stale = True
    atomic_write(FABLE_CACHE_FILE, cache)
    return cache.get("fable"), stale


# ─────────────────────────────── SEGMENTS ──────────────────────────────
# Detail is shed gradually before any whole segment is dropped: absolute
# clock times go first, then reset countdowns, then whole segments. Each
# rung is (show_countdowns, show_clocks).

LEVELS = [
    (True, True),
    (True, False),
    (False, False),
]


def limit_segment(label, ramp, data, stale, countdowns, clocks):
    if not data:
        return None
    pct = data.get("used_percentage")
    pct = max(0.0, min(100.0, float(pct))) if pct is not None else None
    reset = parse_reset(data.get("resets_at"))

    out = f"{ramp_start(ramp)}{B}{label}{R} {DASH}"
    out += f" {heat(pct)}{B}{pct:.0f}%{R}" if pct is not None else f" {SLATE}–{R}"
    if stale:
        out += f"{FAINT}~{R}"
    if reset is not None and countdowns:
        out += f" {FAINT}↻{R}{SLATE}{fmt_delta(reset - time.time())}{R}"
        if SHOW_ABSOLUTE_RESET and clocks:
            clock = fmt_clock(reset)
            if clock:
                out += f" {FAINT}({clock}){R}"
    return out


def build(p, level):
    countdowns, clocks = LEVELS[level]
    segs = []  # (priority, text)

    # ── model + flags
    model_info = p.get("model") or {}
    model = model_info.get("display_name") or model_info.get("id") or "model"
    model = model.replace("(1M context)", "1M").replace("  ", " ").strip()
    hue, direction = model_hue(model_info)
    head = f"{B}{hue_grad_text(f'✦ {model}', hue, sweep=0.16 * direction)}{R}"
    flags = []
    effort = (p.get("effort") or {}).get("level")
    if effort and is_ultracode(p):
        flags.append(f"{B}{ultra_text()}{R}")
    elif effort:
        glyph, ramp = EFFORT_STYLES.get(effort, ("⌁", "e_high"))
        flags.append(f"{B}{grad_text(f'{glyph}{effort}', ramp)}{R}")
    if p.get("fast_mode"):
        flags.append(f"{GOLD}⚡{R}")
    if (p.get("thinking") or {}).get("enabled") is False:
        flags.append(f"{FAINT}no-think{R}")
    if flags:
        head += " " + " ".join(flags)
    segs.append((90, head))

    # ── context window
    cw = p.get("context_window") or {}
    used = cw.get("used_percentage")
    size = cw.get("context_window_size")
    tokens = cw.get("total_input_tokens")
    if used is None and size and tokens is not None:
        used = min(100.0, tokens / size * 100)
    if used is not None:
        ctx = f"{ramp_start('cyan')}{B}ctx{R} {DASH} {heat(used)}{B}{used:.0f}%{R}"
        total = fmt_count(size) if countdowns else None
        if total:
            ctx += f" {FAINT}of {total}{R}"
        segs.append((95, ctx))
    if p.get("exceeds_200k_tokens"):
        segs.append((40, f"{TANGERINE}200k+{R}"))

    # ── usage windows (last to go: these are the point of the status line)
    limits, stale = resolve_limits(p)
    five = limit_segment("5h", "azure", limits.get("five_hour"), stale, countdowns, clocks)
    week = limit_segment("7d", "teal", limits.get("seven_day"), stale, countdowns, clocks)
    fable_data, fable_stale = resolve_fable()
    fable = limit_segment("fable", "violet", fable_data, fable_stale, countdowns, clocks)
    if five:
        segs.append((100, five))
    if week:
        segs.append((100, week))
    if fable:
        segs.append((99, fable))
    if not five and not week:
        segs.append((100, f"{FAINT}limits pending…{R}"))

    # ── situational extras
    vim = (p.get("vim") or {}).get("mode")
    if vim:
        segs.append((70, f"{GOLD}{B}{vim}{R}"))
    agent = (p.get("agent") or {}).get("name")
    if agent:
        segs.append((60, f"{TEAL}⚙ {agent}{R}"))
    wt = (p.get("worktree") or {}).get("name")
    if wt:
        segs.append((50, f"{TEAL}⌥ {wt}{R}"))
    pr = p.get("pr") or {}
    if pr.get("number"):
        tag = f"{TEAL}#{pr['number']}{R}"
        if pr.get("review_state"):
            tag += f"{SLATE}:{pr['review_state']}{R}"
        segs.append((45, tag))
    if p.get("remote"):
        segs.append((35, f"{TEAL}☁{R}"))

    return segs


JOINER = f"  {SEP}  "


def line_width(segs):
    return sum(vwidth(t) for _, t in segs) + vwidth(JOINER) * max(0, len(segs) - 1)


def pack(segs, width):
    """Greedily flow segments into lines of at most `width` visible columns."""
    lines, cur = [], []
    for seg in segs:
        trial = cur + [seg]
        if cur and line_width(trial) > width:
            lines.append(cur)
            cur = [seg]
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def render(payload, width):
    # Preferred: one line. Shed detail (clock times, then meter width) to get there.
    for level in range(len(LEVELS)):
        segs = build(payload, level)
        if line_width(segs) <= width:
            return JOINER.join(t for _, t in segs)

    # Too narrow for one line even fully compacted. Claude Code truncates rather
    # than wrapping, so wrap it ourselves instead of losing the tail. Only once
    # that would exceed MAX_LINES do we start dropping least-important segments.
    segs = build(payload, len(LEVELS) - 1)
    while True:
        lines = pack(segs, width)
        if len(lines) <= MAX_LINES or len(segs) <= 1:
            break
        worst = min(range(len(segs)), key=lambda i: (segs[i][0], -i))
        segs.pop(worst)
    return "\n".join(JOINER.join(t for _, t in line) for line in lines)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    if os.environ.get("CC_STATUSLINE_DEBUG"):
        probe = {}
        for nm, st in (("stdin", sys.stdin), ("stdout", sys.stdout), ("stderr", sys.stderr)):
            try:
                probe[nm] = os.get_terminal_size(st.fileno()).columns
            except Exception as e:
                probe[nm] = f"ERR {type(e).__name__}"
        try:
            fd = os.open("/dev/tty", os.O_RDONLY)
            try:
                probe["/dev/tty"] = os.get_terminal_size(fd).columns
            finally:
                os.close(fd)
        except Exception as e:
            probe["/dev/tty"] = f"ERR {type(e).__name__}"
        probe["COLUMNS_env"] = os.environ.get("COLUMNS", "<unset>")
        probe["resolved"] = term_width()
        try:
            with open(DEBUG_FILE, "w") as f:
                json.dump({"width_probe": probe, "payload": payload}, f, indent=2, sort_keys=True)
        except Exception:
            pass

    print(render(payload, max(20, term_width() - 2)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never let the status line break the TUI
        print(f"{CRIMSON}statusline error:{R} {SLATE}{exc}{R}")
