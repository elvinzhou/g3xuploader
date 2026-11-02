# FlySto auth relay (advanced)

Optional Cloudflare Worker that lets headless devices authorize with FlySto.
It holds `FLYSTO_CLIENT_SECRET`; the device never sees it. Flight logs are
uploaded directly to FlySto and never touch the relay.

```bash
npm install
npx wrangler kv namespace create AUTH_KV   # put the id in wrangler.toml
# also set FLYSTO_CLIENT_ID and RELAY_PUBLIC_URL in wrangler.toml
npx wrangler secret put FLYSTO_CLIENT_SECRET
npx wrangler deploy
```

Register `<RELAY_PUBLIC_URL>/callback` as the redirect URI of your FlySto app,
then set `auth_relay_url` under `flight_data.uploaders.flysto` in the device's
config and run `avcardtool flight flysto-login`.
