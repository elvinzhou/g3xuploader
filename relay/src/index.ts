// FlySto auth relay: bridges FlySto's browser-based OAuth code grant to
// headless devices. It only ever handles OAuth tokens, never flight data.

export interface Env {
  AUTH_KV: KVNamespace;
  FLYSTO_CLIENT_ID: string;
  FLYSTO_CLIENT_SECRET: string;
  FLYSTO_BASE_URL: string;
  RELAY_PUBLIC_URL: string;
}

interface Tokens {
  access_token: string;
  refresh_token?: string;
  expires_in: number;
  token_type?: string;
  expires_at?: number;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const key = (id: string) => `session:${id}`;
const withExpiry = (t: Tokens): Tokens => ({
  ...t, expires_at: Math.floor(Date.now() / 1000) + t.expires_in,
});

async function tokenRequest(env: Env, params: Record<string, string>): Promise<Response> {
  return fetch(`${env.FLYSTO_BASE_URL}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: env.FLYSTO_CLIENT_ID,
      client_secret: env.FLYSTO_CLIENT_SECRET,
      ...params,
    }),
  });
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    try {
      if (req.method === "POST" && url.pathname === "/api/session/new") {
        const id = crypto.randomUUID();
        await env.AUTH_KV.put(key(id), JSON.stringify({ status: "pending", created_at: Date.now() }),
          { expirationTtl: 300 });
        return json({ session_id: id, auth_url: `${env.RELAY_PUBLIC_URL}/auth?session_id=${id}`, expires_in: 300 }, 201);
      }

      if (req.method === "GET" && url.pathname === "/auth") {
        const id = url.searchParams.get("session_id");
        if (!id || !(await env.AUTH_KV.get(key(id)))) return new Response("Session invalid or expired", { status: 404 });
        const dest = new URL(`${env.FLYSTO_BASE_URL}/oauth/authorize`);
        dest.search = new URLSearchParams({
          response_type: "code", client_id: env.FLYSTO_CLIENT_ID,
          redirect_uri: `${env.RELAY_PUBLIC_URL}/callback`, state: id,
        }).toString();
        return Response.redirect(dest.toString(), 302);
      }

      if (req.method === "GET" && url.pathname === "/callback") {
        const code = url.searchParams.get("code");
        const state = url.searchParams.get("state");
        if (!code || !state || !(await env.AUTH_KV.get(key(state)))) return new Response("Bad request", { status: 400 });
        const up = await tokenRequest(env, {
          grant_type: "authorization_code", code, redirect_uri: `${env.RELAY_PUBLIC_URL}/callback`,
        });
        if (!up.ok) return new Response("Token exchange failed", { status: 502 });
        const tokens = withExpiry(await up.json() as Tokens);
        await env.AUTH_KV.put(key(state), JSON.stringify({ status: "complete", tokens }), { expirationTtl: 120 });
        return new Response(
          `<!DOCTYPE html><html><head><title>Authenticated</title></head>` +
          `<body style="font-family:sans-serif;text-align:center;padding:2rem;"><h2>Device Linked Successfully</h2>` +
          `<p>You can close this tab and return to your terminal.</p></body></html>`,
          { headers: { "Content-Type": "text/html" } });
      }

      if (req.method === "GET" && url.pathname === "/api/session/poll") {
        const id = url.searchParams.get("session_id");
        const raw = id ? await env.AUTH_KV.get(key(id)) : null;
        if (!id || !raw) return json({ status: "expired" }, 404);
        const data = JSON.parse(raw);
        if (data.status === "complete") {
          await env.AUTH_KV.delete(key(id));
          return json({ status: "complete", tokens: data.tokens });
        }
        return json({ status: "pending" });
      }

      if (req.method === "POST" && url.pathname === "/api/token/refresh") {
        const body = await req.json().catch(() => ({})) as { refresh_token?: string };
        if (!body.refresh_token) return json({ error: "refresh_token required" }, 400);
        const up = await tokenRequest(env, { grant_type: "refresh_token", refresh_token: body.refresh_token });
        if (!up.ok) return new Response(await up.text(), { status: up.status, headers: { "Content-Type": "application/json" } });
        return json(withExpiry(await up.json() as Tokens));
      }

      return new Response("Not found", { status: 404 });
    } catch (e) {
      return json({ error: "internal error" }, 500);
    }
  },
};
