# claude-statusline

A single-line status line for [Claude Code](https://claude.com/claude-code) that shows the
model, reasoning effort, context usage, and every rate-limit window you care about — including
the per-model weekly quota that Claude Code doesn't expose to status lines on its own.

```
✦ Opus 5 1M ⚡high  ┃  ctx - 12% of 1M  ┃  5h - 26% ↻2h04m (23:50)  ┃  7d - 52% ↻2d20h (Sun 16:00)  ┃  fable - 32% ↻2d20h (Sun 16:00)
```

Stdlib Python only. No dependencies, no config file.

## What it shows

| Segment | Meaning |
| --- | --- |
| `✦ Opus 5 1M` | Current model, coloured by a hash of its identity (see below) |
| `⚡high` | Reasoning effort tier, with a glyph and gradient per tier |
| `ctx - 12% of 1M` | Context window used, and the window's total size |
| `5h - 26% ↻2h04m` | Five-hour rate limit, percent used and time until reset |
| `7d - 52% ↻2d20h` | Seven-day rate limit |
| `fable - 32% ↻2d20h` | Per-model weekly quota, fetched from the OAuth usage endpoint |

Percentages are coloured on a continuous mint → lime → yellow → orange → red gradient, so
the number's colour alone tells you how close to a limit you are.

Situational segments appear only when relevant: vim mode, subagent name, worktree, PR number
and review state, remote indicator, and a `200k+` marker.

## Design notes

**Model colours are not hardcoded.** The model's id is normalised to a key, hashed with CRC32,
and the hash selects one of 18 evenly-spaced hues plus a sweep direction. Any model — a future
release, or one from another vendor entirely — gets its own stable colour with no code change.
Normalisation collapses the many spellings of one model onto a single key, so `Opus 5`,
`Opus 5 (1M context)`, `claude-opus-5[1m]`, and `us.anthropic.claude-opus-5-v1:0` all share a
colour, and release-date suffixes like `claude-haiku-4-5-20251001` don't split a model's identity.

Hues are quantised rather than taken raw from the hash, so two models either share a colour
outright or differ visibly — they never land 3° apart and read as the same colour rendered wrong.

**The per-model weekly quota needs an extra fetch.** Claude Code's status line payload carries
only the generic five-hour and seven-day windows. The model-scoped weekly limit lives in the
OAuth usage endpoint that the `/usage` command reads, so the script fetches it directly, at most
once every `FABLE_TTL` seconds, with a short timeout. Results are cached; on a failed fetch the
last known value is shown with a faint `~`.

**Effort tiers each have an identity.** `·low` (powder blue), `⌁medium` (mint → azure),
`⚡high` (gold → amber), `⚡xhigh` (amber → pink), `✸max` (red → violet), and an animated
holographic `✦ultracode` — pale iridescent sheen with white glints that jump on each refresh.

**It degrades rather than truncating.** Claude Code truncates a status line that's too long, so
the script measures visible width itself (ignoring ANSI codes) and sheds detail in stages:
absolute clock times go first, then reset countdowns, then it wraps onto multiple lines, and only
then does it drop whole segments by priority. It renders in truecolor, falls back to a 256-colour
palette, and respects `NO_COLOR`.

## Install

Requires Python 3.8+ and Claude Code.

```sh
git clone https://github.com/<you>/claude-statusline.git
cp claude-statusline/statusline.py ~/.claude/statusline.py
```

Then point Claude Code at it, in `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 \"$HOME/.claude/statusline.py\"",
    "padding": 0,
    "refreshInterval": 2
  }
}
```

`refreshInterval: 2` keeps the countdowns current and drives the ultracode animation.

## Configuration

Edit the `CONFIG` block at the top of the script:

| Setting | Default | Effect |
| --- | --- | --- |
| `MAX_LINES` | `3` | Wrap onto at most this many lines before dropping segments |
| `SHOW_ABSOLUTE_RESET` | `True` | Show clock time next to each countdown |
| `FABLE_TTL` | `120` | Seconds between usage-endpoint fetches |
| `FABLE_RETRY` | `30` | Seconds before retrying after a failed fetch |
| `FABLE_TIMEOUT` | `1.5` | Network timeout; falls back to cache beyond this |

Colours live in the `PALETTE` block: `RAMPS` for the fixed label gradients, `HEAT_STOPS` for the
percentage gradient, and `HUE_RING` for the 256-colour fallback.

## Files it touches

| Path | Purpose |
| --- | --- |
| `~/.claude/statusline-cache.json` | Last known rate limits, so a fresh session isn't blank |
| `~/.claude/statusline-fable.json` | Cached per-model quota + fetch timestamps |
| `~/.claude/.credentials.json` | Read only, to authenticate the usage fetch |

## Debugging

```sh
CC_STATUSLINE_DEBUG=1
```

dumps the raw payload and terminal-width probe to `~/.claude/statusline-last.json`.

To preview a render without waiting for Claude Code:

```sh
echo '{"model":{"display_name":"Opus 5","id":"claude-opus-5"},"effort":{"level":"high"},
"context_window":{"used_percentage":12,"context_window_size":1000000}}' | python3 statusline.py
```

The script never raises into the TUI: any unexpected error is caught and printed as a short
`statusline error:` message instead of breaking the status line.
