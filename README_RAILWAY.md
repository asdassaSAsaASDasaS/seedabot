# Deploying to Railway

This guide explains how to deploy **The Green Oasis** Telegram Bot and the Streamlit Admin Dashboard to Railway in a single service. They will run together and share the same persistent SQLite database.

## Prerequisites

1. A Railway account: [https://railway.app](https://railway.app)
2. The GitHub repository of your bot connected to your Railway account.

---

## Deployment Steps

### 1. Create a New Project on Railway
1. Go to [Railway Dashboard](https://railway.app/dashboard) and click **New Project**.
2. Select **Deploy from GitHub repository** and choose your repository.
3. Click **Deploy Now** (it might fail initially because variables aren't set yet; we will configure them next).

### 2. Configure Environment Variables
In your Railway Service dashboard, navigate to the **Variables** tab and add the following keys from your `.env` file:
*   `BOT_TOKEN`: Your Telegram Bot Token (e.g., `8736431220:...`).
*   `ADMIN_CHAT_ID`: Your Telegram Chat ID (e.g., `1222599704`).
*   `UPI_ID`: Your UPI ID (e.g., `7259398790-4@ybl`).
*   `ADMIN_USERNAME`: Your Telegram Admin account username (e.g., `ishanaf19`).
*   `DB_DIR`: Set this to `/data` (to match our persistent volume mount directory).
*   `PORT`: Railway automatically injects this, but you can set it to `8501`.

### 3. Add a Persistent Volume (For SQLite)
To ensure your database (`store.db`) is not deleted when the bot restarts or redeploys:
1. In the Railway project canvas (workspace view), click the **+ New** button.
2. Select **Volume** to create a persistent disk.
3. Once created, click on your main bot service card, go to the **Settings** tab, scroll down to **Volumes**, and click **Mount Volume**.
4. Set the **Mount Path** to `/data`.
5. Redeploy your service.

### 4. Expose the Streamlit Dashboard (Optional)
If you want to view the Streamlit web dashboard online:
1. Go to your bot service card in Railway.
2. Click **Settings** tab.
3. Scroll down to the **Networking** section.
4. Click **Generate Domain** (or set up a custom domain). 
5. Railway will give you a public URL (e.g., `https://seedemanalise-production.up.railway.app`) that loads your Streamlit Admin Panel securely!
