# AVCardTool — Notifications System Design

**Status: Proposal**

## 1. Goals

AVCardTool runs unattended on a headless Raspberry Pi. Today the only way to
know what it did is `journalctl`, which nobody reads until something has
already gone wrong. This design adds push notifications for the three things
an owner actually cares about:

1. **Flight logs processed** — after an SD card insertion is processed, send a
   summary: which flights were found, the final Hobbs/Tach times, and
   which uploads succeeded or failed.
2. **Navdata loaded to a card** — whenever a new database cycle is installed
   to a card (on insertion *or* by the background re-check), say which
   databases went from which cycle to which cycle, and when the new cycle
   becomes effective.
3. **Cards that stay inserted get new cycles** — harden the existing
   periodic wake-up that checks flyGarmin for new cycles while a card is
   left in the reader, and make its failures *visible* (today an expired
   Garmin token silently stops all updates until the user notices in the
   airplane).

A non-goal: interactive alerting/acknowledgement. This is fire-and-forget
push, one direction.

## 2. Current state

### What already produces the data we want to send

| Flow | Where | Data available today |
|------|-------|----------------------|
| Flight processing | `cli.py` `auto_process()` — `stats` dict and per-flight `analysis_summary` | total/skipped/flights/non-flights counts, per-flight Hobbs `increment_hours` + `ending_hours`, Tach ditto, OOOI times, per-service upload success/failure, non-flight authoritative-hours override |
| Navdata update | `cli.py` `navdata_auto_update()` — `plan`, `installed_cycles`, `issues` list | per-database old cycle (from `.navdata_cycles.json` on the card) → new cycle (`Issue.name`), `Issue.effective_at`, whether it's the current cycle or a pre-downloaded upcoming one, card serial, avionics name, collected ERROR/WARNING list |
| Navdata install | `cli.py` `navdata_install()` | installed file count, `feat_unlk.dat` slots written, per-file errors |

All of this currently goes to stdout → journald and is then lost. **No new
computation is needed — the notifications system is a delivery layer for
data the two auto flows already have in hand.**

### What already wakes up to check Garmin

Requirement 3 is *mechanically* half-built: udev starts three units per card
(`99-avcardtool-sdcard.rules`):

- `avcardtool-processor@<dev>` — flight logs, one-shot on insert
- `avcardtool-navdata@<dev>` — navdata check, one-shot on insert
- `avcardtool-navdata-watch@<dev>` — a bash `while true; sleep 86400; avcardtool navdata auto-update /dev/<dev>; done` loop that re-checks daily for as long as the card stays inserted

`navdata auto-update` is idempotent (compares `.navdata_cycles.json` and
`feat_unlk.dat` CRCs, exits fast when current), so the daily re-check is
cheap. The problems are operational, not mechanical:

1. **Silent auth death.** `GarminAuth.ensure_authenticated()` failing makes
   auto-update `sys.exit(1)` into the journal. The refresh token eventually
   expires or gets revoked, and from that day on the card never updates
   again — with zero user-visible signal. This is the single most likely
   way requirement 3 fails in practice.
2. **The sleep-loop clock resets on every service restart** (e.g. the weekly
   self-update restarts the watch units), and the interval is anchored to
   insertion time rather than a predictable hour.
3. **No catch-up semantics.** A `sleep` loop has no equivalent of systemd's
   `Persistent=true`; a Pi that was powered off when the tick was due waits
   for udev coldplug to re-fire at boot (which works, but is incidental
   rather than designed).
4. **Nothing tells the user it worked.** When the 3 a.m. check installs a new
   cycle to the card sitting in the reader, the whole point is that the user
   *knows* the card is ready before they drive to the airport.

This design fixes 1 and 4 with notifications, and 2 and 3 by replacing the
sleep loop with a systemd timer (§6).

## 3. Architecture overview

```
┌──────────────────────┐   ┌──────────────────────────┐
│ auto_process()       │   │ navdata_auto_update()    │
│  (flight logs)       │   │  (insert + daily timer)  │
└──────────┬───────────┘   └───────────┬──────────────┘
           │  NotificationEvent        │  NotificationEvent
           ▼                           ▼
     ┌─────────────────────────────────────────┐
     │ NotificationManager (core)              │
     │  • event-type filter (config)           │
     │  • fan-out to enabled backends          │
     │  • timeout + retry, never raises        │
     │  • (phase 3) on-disk outbox for offline │
     └───────┬──────────┬──────────┬───────────┘
             ▼          ▼          ▼
          ntfy      webhook     email    (pushover, telegram, …)
```

Module layout mirrors the existing uploader pattern
(`flight_data/uploaders/` + `UPLOADERS` registry), which has proven easy to
extend:

```
src/avcardtool/notifications/
├── __init__.py        # BACKENDS registry: {"ntfy": NtfyBackend, ...}
├── base.py            # NotificationEvent, NotificationResult, NotificationBackend ABC
├── manager.py         # NotificationManager
└── backends/
    ├── ntfy.py        # recommended default
    ├── webhook.py     # generic JSON POST (Slack/Discord/Home Assistant/anything)
    ├── email_smtp.py  # stdlib smtplib, no new deps
    ├── pushover.py
    └── telegram.py
```

No new runtime dependencies: every backend is implementable with `requests`
(already a dependency) or stdlib `smtplib`/`ssl`.

## 4. Core types

```python
# notifications/base.py

class Severity(str, Enum):
    INFO = "info"        # flights processed, navdata installed
    WARNING = "warning"  # partial failure (some uploads failed, unlock 403)
    ERROR = "error"      # auth expired, install failed, nothing delivered

@dataclass
class NotificationEvent:
    event_type: str          # "flights_processed" | "navdata_updated" | ...
    severity: Severity
    title: str               # short, push-notification friendly
    body: str                # multi-line human text (see §7 examples)
    data: Dict[str, Any]     # full structured payload for webhook consumers
    timestamp: str           # ISO-8601, local time

@dataclass
class NotificationResult:
    backend: str
    success: bool
    message: str = ""

class NotificationBackend(ABC):
    def __init__(self, config: Dict[str, Any]): ...
    @abstractmethod
    def send(self, event: NotificationEvent) -> NotificationResult: ...
    def validate_config(self) -> List[str]: ...   # for `config validate` / wizard
```

### Event catalog

| `event_type` | Severity | Fired from | When |
|---|---|---|---|
| `flights_processed` | INFO (WARNING if any upload failed) | `auto_process()` | ≥1 new file was processed this run. **Not** fired when every file was already processed — a card re-insert with nothing new stays silent. |
| `navdata_updated` | INFO | `navdata_auto_update()` per card | ≥1 file was actually installed to the card. The idempotent fast-exit paths ("all current", CRC match) stay silent, so the daily timer produces no noise on the ~27 days per cycle when nothing changed. |
| `navdata_update_failed` | ERROR | `navdata_auto_update()` end of run | The `issues` list contains any ERROR entries (download failed, install failed, mount failed). WARNINGs alone (e.g. batch-session fallback) don't fire it; they ride along in the body when an ERROR does. |
| `garmin_auth_expired` | ERROR | `navdata_auto_update()` auth guard | `ensure_authenticated()` returned False. Body includes the fix: `avcardtool navdata login`. Rate-limited to once per 24h via a state file (`<data_dir>/notifications/state.json`) so the daily timer doesn't nag daily forever. |
| `processing_error` | ERROR | `auto_process()` | Unhandled per-run failure (e.g. mount error, zero uploaders configured but uploads expected). Per-file parse errors stay in the `flights_processed` body instead. |

Severity is mapped to backend-native priority where the backend supports it
(ntfy `Priority:`, Pushover `priority`, email subject prefix). ERROR events
should reach the phone even in OS do-not-disturb tiers where the service
supports it (ntfy `high`, Pushover `1`).

## 5. Configuration

New top-level `notifications` section in `config.json`, with a
`NotificationsConfig` dataclass in `core/config.py` following the existing
`FlightDataConfig`/`NavdataConfig` pattern (per-backend dicts like
`uploaders` so backends can be added without schema churn):

```json
"notifications": {
  "enabled": true,
  "events": {
    "flights_processed": true,
    "navdata_updated": true,
    "navdata_update_failed": true,
    "garmin_auth_expired": true,
    "processing_error": true
  },
  "backends": {
    "ntfy": {
      "enabled": true,
      "server": "https://ntfy.sh",
      "topic": "avcardtool-n12345-x7k2",
      "token": ""
    },
    "webhook": {
      "enabled": false,
      "url": "https://hooks.example.com/...",
      "headers": {}
    },
    "email": {
      "enabled": false,
      "smtp_host": "smtp.gmail.com", "smtp_port": 587, "use_tls": true,
      "username": "", "password": "", "from_addr": "", "to_addrs": []
    },
    "pushover": { "enabled": false, "user_key": "", "app_token": "" },
    "telegram": { "enabled": false, "bot_token": "", "chat_id": "" }
  }
}
```

**ntfy is the recommended default** for the setup wizard: no account, no API
key, free, self-hostable, instant phone push via the ntfy app. The wizard
generates a random unguessable topic name (`avcardtool-<tail>-<6 random
chars>`) and prints the subscribe instructions. Privacy note for the docs:
payloads include tail number and engine times; users who care should
self-host ntfy or use email/webhook — and the wizard should say so.

Setup wizard gets a fourth section ("Notifications") in `setup_wizard()`,
opt-in like the others, asking: enable? → which backend (ntfy default) →
backend credentials → send test notification now (calls
`avcardtool notify-test`, see below).

New CLI helper: `avcardtool notify-test` sends a synthetic event through the
full manager so users can verify delivery end-to-end after setup.

## 6. Delivery semantics

`NotificationManager.notify(event)` must **never break the pipeline that
called it** — a down ntfy server cannot be the reason flight logs didn't
upload or a card didn't get its cycle:

- Every backend call wrapped, 10 s timeout, 3 attempts with 2 s/4 s backoff.
- All exceptions logged (`logger.warning`) and swallowed; results returned
  for the caller's journal line.
- Backends are called sequentially (n ≤ 5, each ≤ ~30 s worst case; the
  systemd units already allow 600–1800 s).
- **Phase 3, offline outbox:** the Pi may be in a hangar with flaky LTE.
  Failed events are spooled as JSON to `<data_dir>/notifications/outbox/`
  and a drain is attempted at the start of every subsequent
  `auto_process`/`auto_update` run (and by the daily timer). Events older
  than 7 days are dropped. This is deliberately not in phase 1 — best-effort
  with retries covers the common case.

## 7. Event payloads and integration points

### 7.1 `flights_processed` — from `auto_process()`

Integration: the upload loop already computes everything per flight; collect
a `flight_lines` list alongside `stats`, then emit one event right after the
existing "Processing Summary" block. Fire only when
`stats['total'] - stats['already_processed'] > 0`.

Title / body rendered from the structured data:

```
✈ N12345 — 2 flights processed
Flight 1 (log_20260708_091502): 1.4h — KOAK 09:15 → KTRK 10:39
  Hobbs +1.40 → 1234.5   Tach +1.21 → 1101.2
Flight 2 (log_20260708_143001): 0.9h — KTRK 14:30 → KOAK 15:24
  Hobbs +0.90 → 1235.4   Tach +0.80 → 1102.0
Uploads: CloudAhoy ✓✓  FlySto ✓✓  Carryd ✓ (1235.4h)
Skipped: 14 already processed, 1 non-flight (ground run)
```

The `data` dict carries the machine-readable version (per-flight OOOI ISO
timestamps, ending hours, per-service booleans + URLs from
`upload_results`) so a webhook consumer (e.g. Home Assistant) can do more
than display text. If the non-flight override adjusted the ending hours
(`override_meta`), the body says so — that's exactly the "final times" the
user wants to trust.

### 7.2 `navdata_updated` — from `navdata_auto_update()`

Integration: emit per card, after `ctx.invoke(navdata_install, ...)` returns
successfully. The plan loop already knows, per database: the old installed
cycle (`installed_cycles[avdb.name]["issue"]`), the new `issue.name`,
`issue.effective_at`, and whether it was the current cycle or an upcoming
pre-download (the `available_issues` branch). Keep `(avdb, series, issue,
old_issue, is_upcoming)` in the plan tuples so the event can render:

```
🗺 Navdata installed — card 3F2A-11C0 (G3X Touch, N12345)
NavData:   2606 → 2607        (effective 2026-07-16)
Obstacles: 2606 → 2607        (effective 2026-07-16)
Terrain:   already current (26D2)
Pre-loaded next cycle: Charts 2608 (effective 2026-08-13)
Card is ready to fly.
```

`navdata_install()` itself stays notification-free; it's also a manual
interactive command, and the auto flow that wraps it owns the event. (A
manual `navdata install` run prints to the terminal the user is already
looking at.)

### 7.3 `navdata_update_failed` / `garmin_auth_expired`

The end-of-run `issues` summary block becomes the `navdata_update_failed`
body verbatim. The auth guard at the top of `navdata_auto_update()` emits
`garmin_auth_expired` before `sys.exit(1)`:

```
⚠ AVCardTool: Garmin login expired
Automatic database updates are stopped. Cards will NOT receive new
cycles until you run:  avcardtool navdata login
```

Rate-limited to one per 24 h (last-sent timestamp in
`<data_dir>/notifications/state.json`) because the daily timer would
otherwise repeat it forever.

## 8. Keeping inserted cards current: replace the sleep loop with a timer

Replace `avcardtool-navdata-watch@.service` (per-device bash sleep loop)
with one system-wide timer pair:

```ini
# avcardtool-navdata-check.timer
[Timer]
OnCalendar=*-*-* 03:00
RandomizedDelaySec=1800
Persistent=true

# avcardtool-navdata-check.service  (oneshot)
ExecStart=/usr/local/bin/avcardtool navdata auto-update
```

`navdata auto-update` with no device argument already scans every mounted
FAT32 card (`SDCardDetector.scan_for_cards()`), so the service needs no
templating and no udev coupling. Why this is better than the loop:

- **`Persistent=true`** — a Pi that was off at 03:00 runs the check at next
  boot. The sleep loop has no such semantics.
- **Predictable timing** — checks happen at ~3 a.m. local, so a new cycle
  published overnight is on the card before a morning flight, instead of
  "insertion time + N×24 h". `RandomizedDelaySec` keeps a fleet of installs
  from stampeding flyGarmin at the same second.
- **Immune to restarts** — the weekly self-update restarting services no
  longer resets a 24 h countdown.
- **Simpler** — one unit instead of one loop process per inserted card; no
  bash-in-ExecStart; hardening directives apply to a one-shot instead of a
  perpetual process.

Kept as-is: the udev rule still fires `avcardtool-navdata@<dev>` on
insertion for the immediate check, and `avcardtool-processor@<dev>` for
flight logs. The udev rule drops `avcardtool-navdata-watch@%k.service` from
`SYSTEMD_WANTS`; `install.sh` stops/removes the watch units on upgrade
(same pattern it already uses for the legacy `g3x-processor@` units) and
installs + enables the timer when navdata auto-update is enabled.

Daily is the right default cadence: Garmin cycles change every 28 days
(NavData) / ~monthly (charts, obstacles), and the pre-download branch
already fetches the next cycle ahead of its effective date, so a daily
check gives multi-day slack before a stale cycle could ever be flown.
The timer's `OnCalendar` is user-editable via a systemd drop-in for anyone
who wants more or less.

With §7.2/7.3 in place, this loop is finally observable: the card quietly
kept itself current → the user gets the "card is ready" push; the loop is
broken (auth, network, Garmin API change) → the user gets an ERROR push
instead of finding out in the run-up.

## 9. Implementation plan

**Phase 1 — core + the two headline notifications**
1. `notifications/` package: base types, manager, `ntfy` + `webhook` backends.
2. `NotificationsConfig` in `core/config.py` (+ `to_dict`/`load`/`validate`).
3. Emit `flights_processed` from `auto_process()`; `navdata_updated`,
   `navdata_update_failed`, `garmin_auth_expired` from `navdata_auto_update()`.
4. `avcardtool notify-test` command.
5. Unit tests: manager filtering/retry/never-raises (mock `requests`),
   backend payload formatting, event rendering from a canned `stats` dict
   and plan list.

**Phase 2 — timer migration + setup UX**
1. Add `avcardtool-navdata-check.{service,timer}`; update udev rule;
   `install.sh` migration (remove watch units, enable timer).
2. Setup wizard "Notifications" section; README/ARCHITECTURE updates.
3. `email` backend (stdlib smtplib).

**Phase 3 — robustness + long tail**
1. Offline outbox with drain-on-next-run (§6).
2. `pushover`, `telegram` backends.
3. Optional extras if wanted later: per-backend `min_severity`, weekly
   "still alive, all current" heartbeat (default off).

## 10. Open questions

1. **ntfy topic = shared-secret** — is the wizard-generated random topic on
   ntfy.sh acceptable as the default, or should the wizard push harder
   toward self-hosting/auth tokens for anyone uneasy about tail numbers and
   engine times transiting a public relay?
2. **Upload-failure severity** — a run where flights uploaded to 2 of 3
   services currently maps to `flights_processed` at WARNING. Should a
   *total* upload failure (0 of N) escalate to its own ERROR event instead?
3. **Check cadence** — is daily at 03:00 right, or should the default be
   twice daily given the pre-download branch already provides slack?
