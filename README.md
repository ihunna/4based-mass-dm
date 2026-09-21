# 4based Bulk Messenger

A dashboard for logging into 4based creator accounts, scraping users, and sending bulk messages.

## What it does

1. **Login creators** — Add creator accounts (and scraper/user accounts). The bot logs them in, stores session data, and keeps them available from the Creators and Users pages.
2. **Scrape users** — Run a scraper task to search 4based for client accounts and save them as targets.
3. **Bulk message users** — Run a messaging task so logged-in creators send DMs to scraped targets they have not already messaged. Messages can be free or paid, with optional media and captions.

Supporting pieces:

- Role-based admin login (super admin / admin)
- Proxies, captions, and other configs
- Live console, task list, and the ability to stop a running task
- Optional proxy flush so a task uses a new IP instead of a stored one

## Getting started

1. Log in with an admin account.
2. Add **creators** and **user** (scraper) accounts.
3. Put proxies (and captions if needed) under Configs.
4. Start a **scraper** task from New task to collect targets.
5. Start a **messaging** task to bulk-message those targets.
6. Watch progress in the console and on the Tasks page.

## Support

Email `ihunnaemmanuel@gmail.com` or Telegram [https://t.me/hustleoclok](https://t.me/hustleoclok).
