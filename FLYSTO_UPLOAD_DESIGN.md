# FlySto upload: authentication and "only upload what's new"

Design notes and the assumptions behind them, kept so we can revisit them.
Code: `src/avcardtool/flight_data/uploaders/flysto.py`.

## Authentication

- Uses a static **API token** created at <https://www.flysto.net/account/api>,
  sent as `Authorization: Bearer <token>` (config key `api_token`).
- Legacy OAuth settings (`client_id`, `client_secret`, `refresh_token`,
  `flysto-auth`) still work when no `api_token` is set.
- An earlier Cloudflare Worker auth relay was built and then removed once the
  token-based API made it unnecessary.

## Skipping flights FlySto already has

FlySto does not deduplicate. Before uploading, the uploader calls
`GET /public-api/logs?type=all`, takes the **latest date** among the newest
page of logs (up to 250), and uploads a local log only if its first data point
is on a **later UTC day** than that. Otherwise the upload is skipped and
reported as successful, so the file is marked processed. Disable with
`check_duplicates: false` under the flysto uploader.

### Assumptions (revisit if any stops being true)

1. **The SD card is loaded about once a day**, so "one flight a day" is good
   enough. Consequences: if two flights share a day, the second is skipped once
   the first is on FlySto, and within a single run only the first is uploaded
   because the check isn't re-run after an upload.
2. **The latest date is account-wide, not per aircraft.** With several
   aircraft, an upload for one can cause an older log from another to be skipped.
   Fix if needed: filter the query by FlySto's `aircraft` ID.
3. **Day boundaries are UTC** on both sides. Local logs are treated as UTC (as
   elsewhere in the code); FlySto's dates come from its takeoff/landing ISO
   times (UTC), falling back to the display `date` field (`12 Oct 2026`).
4. **FlySto's ordering is unknown** (flight date vs. upload time), so we take
   the max date over the newest page rather than trusting the first result. A
   very old backfill can't hide newer flights, but if more than 250 logs were
   uploaded after the newest flight, the true latest could be missed.
5. **A query failure, or an empty account, means "upload"** (fail open): the
   risk is a duplicate, never a lost flight.
6. **The API token also authorizes `POST /public-api/log-upload`.** The
   public-API doc we have only covers listing logs; this was assumed and should
   be confirmed with a real upload.
7. **Logs with no takeoff/landing** (e.g. engine-start only) are dated from
   FlySto's `date` field and count toward "latest day".

### Alternatives considered

- Matching each flight to a FlySto log by aircraft and whether a takeoff or
  landing falls inside the local log's time window (with a couple of minutes of
  slack). More precise, handles several flights a day, but needs pagination
  over the date range and more code. Implemented and then replaced by the
  simpler latest-day check; see git history if it's needed again.
