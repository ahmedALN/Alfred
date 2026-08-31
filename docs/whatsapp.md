# Messaging Alfred from your phone

There are two ways, and they are very different. Pick one.

| | **Your own chat** | **Business number** |
| --- | --- | --- |
| Setup | one command, one code | Meta account, tunnel, ~20 min |
| Where messages land | your "Message yourself" chat | a chat with a business number |
| Exposed to the internet | nothing | a webhook, behind a tunnel |
| Can message you any time | yes | only within 24h of your last message |
| Supported by Meta | **no** | yes |
| Risk to your account | **your number could be banned** | none |

The first is what most "WhatsApp bots" do: it links as a companion
device, exactly like WhatsApp Web, and WhatsApp's terms do not permit
unofficial clients. It is far nicer to use and it is your account on the
line. The second is Meta's supported route and carries no such risk.

Alfred supports both. If a link exists it is used; otherwise the
business route is used if configured; otherwise the channel is off.

---

# Option 1 - your own chat (linked device)

```bash
python -m src.whatsapp pair +447700900123
```

It prints a short code like `BR65-843N`. On your phone:

**WhatsApp → Settings → Linked Devices → Link a device → Link with phone
number instead** → type the code.

That is the whole setup. Put your number in `.env` so Alfred knows who
is allowed to talk to it:

```
ALFRED_WHATSAPP_ALLOWED=+447700900123
```

Then start Alfred. It prints:

```
[Message] WhatsApp linked to +447700900123 - messaging your own chat.
```

Message yourself on WhatsApp and Alfred answers in the same chat.

- `python -m src.whatsapp status` - is it linked?
- `python -m src.whatsapp unlink` - forget it here. **Also log the
  device out on your phone**; deleting the file does not revoke it.

Alfred ignores its own messages in that chat, so it does not end up
talking to itself.

---


# Option 2 - a business number (Meta Cloud API)

Meta's supported route. No risk to your personal account, at the cost of
a business number and a tunnel.

## What this channel is

Anyone who can post into it can run anything on this PC. Two separate
things therefore have to be true before a message is acted on:

1. **Meta signed it.** Every payload is checked against your app secret,
   so a stranger who finds your webhook address cannot use it. Without
   `ALFRED_WHATSAPP_APP_SECRET` set, Alfred refuses every message rather
   than trusting unsigned ones.
2. **You sent it.** The sender's number must be on
   `ALFRED_WHATSAPP_ALLOWED`. Anyone else is ignored in silence - not
   answered, because a reply confirms the number is live and reaches
   something worth attacking.

Neither is enough alone. Alfred still never types passwords, PINs or
card numbers, and if you send one it will tell you to delete the
message.

The webhook listens on `127.0.0.1` only. Nothing is opened on your
router - the tunnel makes an outbound connection and forwards inward.

## 1. Meta setup

You need a Meta account and a phone number for the business sender.
**It has to be a number that is not already on WhatsApp** - not your
personal one. Meta gives you a free test number to start with, which is
enough to try this.

1. Go to <https://developers.facebook.com> → **My Apps** → **Create App**
   → type **Business**.
2. Add the **WhatsApp** product.
3. Under **WhatsApp → API Setup**, note:
   - **Temporary access token** (24 hours - see step 4 for a lasting one)
   - **Phone number ID** (the sender, not your mobile)
   - Add *your own* mobile under **To** as a recipient, and confirm the
     code WhatsApp sends you.
4. For a token that does not expire daily: **Business Settings → Users →
   System Users** → add one with admin rights → **Generate token** →
   select your app and the `whatsapp_business_messaging` permission.
5. **App Settings → Basic → App Secret** → *Show*. This is the value
   that proves a message came from Meta.

## 2. A public address for the webhook

Meta pushes messages to an HTTPS address, so your PC needs one. A
Cloudflare tunnel is free and opens no ports.

```bash
winget install --id Cloudflare.cloudflared
```

Then, leaving it running:

```bash
cloudflared tunnel --url http://127.0.0.1:8770
```

It prints a `https://something.trycloudflare.com` address. That address
changes each time you restart it; a named tunnel (`cloudflared tunnel
create alfred`) gives you a fixed one, which is worth doing once this
works.

## 3. Tell Alfred

In `.env`:

```
ALFRED_WHATSAPP_TOKEN=...           # from step 1.3 or 1.4
ALFRED_WHATSAPP_PHONE_ID=...        # the sender's phone number ID
ALFRED_WHATSAPP_APP_SECRET=...      # from step 1.5 - required
ALFRED_WHATSAPP_VERIFY_TOKEN=pick-any-string
ALFRED_WHATSAPP_ALLOWED=+447700900123   # YOUR mobile, comma-separated
```

`ALFRED_WHATSAPP_VERIFY_TOKEN` is a password you invent; Meta echoes it
back once to prove you own the address.

Start Alfred. It prints:

```
[Webhook] listening on 127.0.0.1:8770/webhook
[Message] WhatsApp ready for 1 number(s).
```

## 4. Point Meta at it

**WhatsApp → Configuration → Webhook → Edit**:

- **Callback URL**: `https://something.trycloudflare.com/webhook`
- **Verify token**: whatever you put in `ALFRED_WHATSAPP_VERIFY_TOKEN`

Click **Verify and save**. Alfred prints `[Webhook] subscription
verified`. Then **Manage** the webhook fields and subscribe to
**messages**.

Message the sender number from your phone. Alfred replies "On it." and
gets to work.

## The one real limitation

WhatsApp only allows free-form messages **within 24 hours of your last
message to it**. Inside that window Alfred can tell you anything.
Outside it, Meta rejects the send and Alfred logs:

```
[WhatsApp] outside the 24-hour window
```

Sending anything - even "hi" - reopens it for another day. Messaging you
first after a long silence needs a pre-approved message template, which
is a Meta review process; worth setting up only if you want alerts after
days of quiet.

## If something is wrong

| What you see | What it means |
| --- | --- |
| No `[Webhook] listening` line | `ALFRED_WHATSAPP_TOKEN`/`_PHONE_ID` not set |
| `needs ALFRED_WHATSAPP_APP_SECRET` | Set it; unsigned messages are never trusted |
| `ALFRED_WHATSAPP_ALLOWED is empty` | Add your number, or everything is refused |
| Meta says the callback failed | The tunnel is not running, or the URL is stale |
| `refused a payload with a bad signature` | The app secret does not match the app |
| `refused a message from '...'` | That number is not on the allowlist |
| Nothing arrives, no errors | Subscribe to the **messages** field in Meta |
