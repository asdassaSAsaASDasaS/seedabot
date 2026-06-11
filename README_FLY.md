Deploying to Fly.io (quick guide)

1. Install flyctl: https://fly.io/docs/hands-on/install-flyctl/

2. Login and create an app:

```bash
flyctl auth login
flyctl launch --name seedemanalise-bot --no-deploy
```

3. Set environment variables on Fly (use your real BOT_TOKEN and ADMIN_CHAT_ID):

```bash
flyctl secrets set BOT_TOKEN=your_bot_token ADMIN_CHAT_ID=123456789 DISABLE_DASHBOARD=1
```

4. Deploy using the Dockerfile in repo:

```bash
flyctl deploy
```

5. Monitor logs:

```bash
flyctl logs -a seedemanalise-bot -f
```

Notes:
- We run the bot in polling mode; Fly will keep the service running.
- If you prefer webhooks, configure an HTTPS endpoint and set webhook in the bot code.
