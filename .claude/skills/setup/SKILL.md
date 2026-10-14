---
name: setup
description: >
  First-run setup: pick interview and content languages, create the runtime home, register a
  profile, write credentials.env, and connect each platform. Covers the Meta tester-role
  dance for Threads and Instagram, the Higgsfield connector for TikTok, a BYO GCP project
  for YouTube, and the logged-in browser session for Naver. Use when the user has just
  cloned the repo, is adding a platform, or is hitting auth errors like ENV_MISSING,
  CONNECTOR_ID_MISSING or NAVER_SESSION_MISSING. 트리거 — "/setup", "셋업", "처음 설정",
  "키 넣어야 해", "플랫폼 연결", "로그인 안 됨".
---

# Setup

You walk the user through setup. The CLI writes files; you never hand-edit state.

## Hard rules

- **Never read back a secret.** Do not `cat credentials.env`, do not echo a pasted token,
  do not put a value in a commit, a log or a chat message. You confirm by key name only.
- Tell the user to paste tokens **into the file themselves** (or use their editor). If they
  paste one into chat, say it is now burned and must be rotated.
- `~/.contents-factory/` is written only by `factory` commands. No Write, no Edit.
- Non-secret account ids go in `platforms.json`, not in `credentials.env`.

## Step 1: home and language

```
uv sync
factory init
factory i18n get interview.common.intro
```

Ask two things and write them into `factory.toml`:

- `ui_lang` (ko/en): the language you ask questions in.
- `content_lang`: the language the output is written in. Separate on purpose.

## Step 2: profile

```
factory profile list
factory profile add <name> --from _example
factory profile show <name>
```

`_example` is a demo voice. Tell the user to replace `persona.md`, `brand.md`,
`forbidden.txt` and `closers.txt` with their own, then run `/profile` for the full
interview. Edit profile files directly (they live in the repo or the home profiles dir and
are not runtime state); everything under `queue/`, `briefs/` and `state/` is CLI-only.

## Step 3: platform keys

Keys go in `~/.contents-factory/profiles/<name>/credentials.env`, one `KEY=value` per line,
`chmod 600`. The README table is canonical; this is the same list with the setup path.

| Platform | credentials.env | platforms.json |
|---|---|---|
| Threads | `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` | `slots`, `daily_cap`, `late_policy` |
| Instagram | `IG_ACCESS_TOKEN`, `IG_USER_ID` | same |
| TikTok | `HIGGSFIELD_MCP_TOKEN` | `connector_id` |
| YouTube | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | `slots` |
| Naver blog | (none) | `blog_id` |
| Media hosting | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | - |
| Text generation | `ANTHROPIC_API_KEY` | - |

Verify without revealing anything:

```
factory publish threads --item q_... --dry-run    # ENV_MISSING names the missing keys
```

## Step 4: Threads and Instagram, the tester-role dance

A Meta app in development mode only lets **tester-role accounts** through OAuth. Getting a
new account connected, in order, and every step is required:

1. **App roles**: Meta app dashboard → App roles → Roles → **Add people** → pick
   *Threads tester* (and *Instagram tester* for IG) → enter the target account.
2. **Accept the invite from inside the platform, not from email**: log in as the target
   account → Threads app → **Settings → Website permissions → Invites** (IG: Settings →
   Apps and websites → Tester invites) → accept. An unaccepted invite looks identical to no
   invite at all: OAuth fails with a generic error.
3. **OAuth**: run the authorization URL for the app and sign in as that account. The
   **redirect URL must match the registered one character for character** — trailing slash,
   http vs https, port, everything. A mismatch is the single most common failure.
4. Exchange the code for a long-lived token, put it in `credentials.env` as
   `THREADS_ACCESS_TOKEN`, and record `THREADS_TOKEN_ISSUED_AT=YYYY-MM-DD`.
5. Confirm with a dry run, then one real post from a test account.

Tokens live 60 days. `factory token refresh --platform threads --profile <name>` renews
them; the tick warns from 7 days out. Refreshing is the single most common outage point in
the system it was ported from, so do not skip the warning.

If the app is already review-approved for public use, steps 1 and 2 are unnecessary.
Confirm by actually connecting a non-tester account before telling the user it works.

## Step 5: TikTok

Direct TikTok API posting from an unreviewed app can only produce private posts, so the
route is the Higgsfield MCP connector.

1. Connect the TikTok account in Higgsfield (`tiktok_connect`).
2. Read the connector id and put it in `platforms.json` under `tiktok.connector_id`.
3. Put the MCP token in `credentials.env` as `HIGGSFIELD_MCP_TOKEN`.
4. `factory publish tiktok --item q_... --dry-run` should show `tiktok_prepare_publish`
   with your connector id masked to its first four characters.

## Step 6: YouTube

Bring your own GCP project. An unverified project keeps every upload private no matter what
you pass, which is fine for review-then-schedule but must be said out loud.

1. GCP → OAuth consent screen → add the Google account as a test user.
2. Create an OAuth client (Desktop app). Scopes: `youtube.upload`, `youtube.readonly`.
3. Run the loopback flow once, store the refresh token in `credentials.env`.
4. `publishAt` only works while `privacyStatus` is private and the timestamp is in the
   future. A past timestamp is dropped rather than turned into an instant public post.

## Step 7: Naver blog

There is no public write API. Publishing drives the logged-in editor through the `aside`
browser.

1. Install `aside` and sign in to Naver in that browser, once.
2. Put the blog id in `platforms.json` under `naver_blog.blog_id`.
3. Leave exactly one editor tab open, or none. Two editor tabs is `MULTIPLE_EDITOR_TABS`.
4. No session means `NAVER_SESSION_MISSING` and the item stays queued. That is not a
   failure to retry blindly; it is a prompt to log in.

## Step 8: media hosting

Threads, Instagram and the TikTok import route all fetch media from a public URL, so local
paths cannot be posted. Follow the README "BYO media bucket" section: the user's own
Supabase project, a public bucket, a 7-day cleanup job. Or they fill
`media[].public_url` themselves and the factory passes it through.

## Step 9: scheduler

```
factory schedule install --runner launchd     # macOS
factory schedule install --runner cron        # Linux
factory schedule status
factory schedule tick --dry-run
```

Finish by reading back, in three lines: which profile, which platforms are connected, and
what the next seven days look like. Never list key values.
