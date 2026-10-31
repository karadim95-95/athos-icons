# Athos Icons – online shop

A simple Greek/English shop for Byzantine icons, built with Django.

- **Customers:** browse icons, add them to a cart and send an **order request** (no online payment).
  They need an account with a **confirmed email**, so you can always reach them
  (see [Accounts and bot protection](#accounts-and-bot-protection)).
- **You (admin):** review each request, adjust prices or shipping if needed, then **accept** it.
  The customer automatically gets an email with the amount and your bank details. You then mark the order
  *paid → shipped (with tracking number)*. With [automatic tracking](#automatic-parcel-tracking) on, it then moves
  to *in transit → delivered* by itself. The customer gets an email at each step.
- **Custom icon requests:** customers describe the icon they want and can attach photos. You discuss it with them
  through messages and send a price offer. If they accept it online, an order is created and the payment email goes out.
- **Businesses:** shops, churches and monasteries register as businesses with their company and VAT
  details (ΑΦΜ/ΔΟΥ). After you approve them, they see **prices without VAT** and their orders show
  net amount + VAT.
- **Messages:** every order and request has a conversation thread (on the site and by email) for clarifications,
  price changes, etc.

## Order flow

```
Customer sends order request ──► Request received          (email to customer + to you)
You check it, edit prices/shipping, accept ──► Awaiting payment  (email with bank details + amount)
Money arrives, you mark it ──► Paid                         (email)
You ship, add tracking number ──► Shipped                   (email with tracking)
The courier picks it up        ──► In transit               (email)   ┐ automatic with tracking on,
The courier delivers it        ──► Delivered                (email)   ┘ otherwise you set them
                                   → you get an email, and the order moves to the archive
At any time: Declined / Cancelled (the customer can cancel while the request is still pending)
```

The admin home page shows a "Needs your attention" panel: new requests, orders awaiting payment, orders to ship,
unanswered messages and businesses to approve.

**Archive:** delivered orders leave the order list so it only shows work in progress. Nothing is deleted.
Find an old order with the search box (order number, name, email, phone, VAT number). Searches and filters
always include the archive. To browse the archive, use the *Order archive* button on the admin home page.
You can also archive orders by hand, e.g. cancelled ones, or bring them back, with the actions in the order list.
If you change a delivered order back to an earlier status, it returns to the active list.

---

## Run it on your computer

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
echo "DEBUG=1" > .env
.venv/bin/python manage.py migrate
.venv/bin/python manage.py setup_shop --demo     # default pages + 12 demo icons (remove --demo to skip them)
.venv/bin/python manage.py createsuperuser       # your admin login (email + password)
.venv/bin/python manage.py runserver
```

- Shop: http://localhost:8000
- Admin: http://localhost:8000/admin/

Locally, emails are printed in the terminal instead of being sent.

## First things to do in the admin

1. **Shop settings:** shop name, contact email, phone, **bank details (IBAN)**, VAT rate (24%), default shipping
   cost and free-shipping threshold, homepage photo.
2. **Pages:** replace the placeholder text of *About us*, *Shipping & payment*, *Terms*, *Privacy* and
   *For businesses*. The terms and privacy texts are placeholders and need real legal content.
3. **Catalogue → Sizes:** the sizes you offer (four common ones are there to start with; rename, reorder or add).
4. **Catalogue → Products:** delete the demo products and add yours. In a product, use *Add photos* to
   pick several photos at once, from your computer or straight from your phone. They are resized and rotated
   automatically.

### Prices

Fill in the price **incl. or excl. VAT**: the other box is calculated as you type (and again when you save).

**Sizes:** if an icon comes in several sizes, tick them in the product's *Sizes* section and give each its
price (incl. or excl. VAT; empty = the main price). Customers then pick a size on the product page, cards show
"from" the lowest price, and the size is written on the order. Icons in one size only just use *Dimensions*.

**Discounts:** click one of the buttons under *Discount %* (−10% … −50%, or *no discount*); the form shows
what customers will pay. For many products at once, select them in the product list and choose
*Discount 10% / 20% / 30% / 50%* or *Remove discount* from the actions.

| Field | Who sees it |
|---|---|
| Price incl. VAT | Individuals pay this. |
| Discount % | Applies to everyone; shows a "−20%" badge and the old price struck through. |
| Business price excl. VAT (optional) | Approved businesses pay this. If empty, they see the retail price minus VAT. Not used with sizes: businesses pay each size's price minus VAT. |

For an EU business outside Greece with a valid VAT number, set the order's VAT rate to 0 before accepting
(reverse charge).

### Receipts and invoices

Issue the receipt or invoice as usual (e.g. in AADE's timologio app), then open the order in the admin and
**drag the PDF onto the "Receipt / invoice" box** (or click it). It is saved and emailed to the customer at once,
and they can download it from their order page. Dropping a new file replaces the old one and emails it again.
The admin home page shows "Shipped orders without a receipt" so none is forgotten.

Receipts are kept in a private folder (`PRIVATE_ROOT`, inside the `shopdata` volume on the server): the web
server never serves it, and only the customer who placed the order (and you) can download the file.

### Automatic parcel tracking (ACS, Geniki Taxydromiki)

When you mark an order *Shipped*, choose the **courier** and enter the **tracking number** (the voucher number);
the tracking link is filled in for you. For ACS and Geniki Taxydromiki the shop then asks the courier directly
(when you save, then every 2 hours). It moves the order to *In transit* and *Delivered*, emailing the customer
each time, and shows the courier's latest update on the order page.

- If the courier reports a problem (failed delivery attempt, returned, lost or damaged parcel), **you** get an
  email and the order appears under "Shipments with a delivery problem" on the admin home page.
- To check right away, select orders in the list and use the *Check the couriers now* action.
- Other couriers: choose *Other* and paste the tracking link; you set *In transit* / *Delivered* by hand.

It uses each courier's own API for business customers (`orders/couriers/`), so you need a business contract
with the courier. Put the details it gives you in `.env` and restart (`docker compose up -d`):

| Courier | What to ask for | Settings |
|---|---|---|
| **ACS** | Your account manager (or customer support): access to **ACS Web Services** (REST API) with rights to `ACS_Trackingsummary` and `ACS_TrackingDetails`. If they lock it to an IP address, give them your server's IP. | `ACS_API_KEY`, `ACS_COMPANY_ID`, `ACS_COMPANY_PASSWORD`, `ACS_USER_ID`, `ACS_USER_PASSWORD` |
| **Geniki Taxydromiki** | Their sales department: access to the **JobServicesV2 web service** (user name, password, application key). They first give you a test user for `testvoucher.taxydromiki.gr`. | `GENIKI_USERNAME`, `GENIKI_PASSWORD`, `GENIKI_APP_KEY`, `GENIKI_TEST` (1 while using the test user) |

**Check the details** with a real voucher number from your account:
`docker compose exec web python manage.py check_courier acs 7400123456` (or `geniki 4012345678`). It prints the
parcel's stage, or the courier's error message (e.g. a wrong key).

Only vouchers sent from your own account can be tracked. Note from Geniki's documentation: they give **live**
credentials after you confirm that your system also creates, prints, cancels and finalises vouchers through
their API, so ask them whether tracking-only access is possible, or have the shop create vouchers too.

### Accounts and bot protection

- **Only customers with a confirmed email can order.** After signing up, the customer gets a link by email,
  valid for 24 hours (they can ask for a new one from their account).
  Until they open it they can browse and fill the cart, but they cannot send order requests, custom requests or
  messages. You hear about new business accounts only after they confirm.
- **Admin accounts cannot order.** When you browse the shop while logged in as admin, a red bar at the top says
  so. To try the shop, log out and use a separate customer account.
- **Accounts that are never confirmed are deleted after 7 days**, by the `jobs` service
  (`python manage.py delete_unverified_accounts` by hand).
- **Bots:** sign-up, contact and password-reset forms have a hidden trap field and reject forms sent within
  2 seconds. Each connection can try at most 5 sign-ups, 5 contact messages and 5 password resets per hour.
  After 10 wrong passwords, logins (including the admin login) pause for 15 minutes.
- **Per customer:** at most 10 orders and 10 custom requests a day, and 30 messages an hour.
- **Phone customers:** in *Customers*, use the *Confirm email* action to let someone order without the link.

### Business accounts: ΑΦΜ and ΔΟΥ

Business accounts must give their company name, **ΑΦΜ** and **ΔΟΥ**. Individuals don't need them.

- **Every ΑΦΜ is checked as it is typed in:** the last digit of a Greek ΑΦΜ is a check digit, so typos and
  made-up numbers are rejected straight away.
- **Official AADE check (recommended):** the shop can also ask AADE's registry whether the ΑΦΜ belongs to an
  **active business**. Numbers that don't are rejected. For the rest, the official name, ΔΟΥ, address and main
  activity are saved on the account, so you can compare them before you approve it, and the ΔΟΥ is corrected to
  the official spelling. To turn it on:
  1. Log in to aade.gr with your business TAXISnet codes and open
     *Αναζήτηση Βασικών Στοιχείων Μητρώου Επιχειρήσεων*.
  2. Register for the service, then create **special codes** (Ειδικοί κωδικοί). Save the password: it is shown once.
  3. Put them in `.env` as `AADE_USERNAME` and `AADE_PASSWORD`, then try it:
     `python manage.py check_afm 123456789` (use a real ΑΦΜ, e.g. your own).

  The service is free. Use it only to check your customers; AADE's terms forbid passing the data on.
  If AADE can't be reached, sign-up still works and the account notes that the check didn't happen.
- **Businesses from other EU countries** (e.g. Cyprus) enter their VAT number with the country code
  (CY12345678X). It is checked with the EU's free VIES service, and no ΔΟΥ is needed.
- **Once you approve a business, its ΑΦΜ is locked.** The customer can't change it in their details or at
  checkout; you can, in the admin.

**Stronger check (recommended once online):** Cloudflare Turnstile, a free "are you human" box that is
usually invisible. Create a widget at https://dash.cloudflare.com (Turnstile) for your domain and put the keys
in `.env` as `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY`. It loads a Cloudflare script on those three forms,
so mention Cloudflare in your privacy policy.

---

## Put it online

The simplest route is a small VPS (e.g. Hetzner, DigitalOcean, about €5/month) with Docker:

```bash
cp .env.example .env        # fill in domain, secret key, email (SMTP) settings
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Caddy gets the HTTPS certificate automatically. The `jobs` service runs every 2 hours: it checks the couriers
and removes unconfirmed accounts.
The database and photos live in the `shopdata` Docker volume.
**Back it up regularly**, for example:

```bash
docker compose exec web python -c "import sqlite3; sqlite3.connect('/data/db.sqlite3').backup(sqlite3.connect('/data/backup.sqlite3'))"
docker run --rm -v athos-icons_shopdata:/data -v "$PWD":/out alpine tar czf /out/shop-backup.tgz /data
```

(The volume name starts with the folder name; `docker volume ls` shows it.)

**Email:** the automatic emails need a real SMTP account (your hosting mailbox, Google Workspace, Brevo,
Mailgun, Postmark…). Use an address on your own domain and set up SPF/DKIM so the emails don't land in spam.

## Branches, GitHub Actions and releases

The code lives at https://github.com/ChrKar03/athos-icons (private).

| Branch | What it is |
|---|---|
| `dev` | Where every change goes first. The default branch. |
| `release` | What runs on the live shop. Only changes that passed the tests on `dev` go here. |

**Day to day:** commit to `dev` (or open a pull request into `dev`). When it's ready to go live,
open a pull request **from `dev` into `release`** on GitHub and merge it once the checks are green.

**GitHub Actions** (the *Actions* tab on GitHub):

| Workflow | When | What it does |
|---|---|---|
| CI | every push to `dev`, every pull request | Django checks, migrations, translations, all tests; builds the server image |
| Deploy | every merge into `release` | runs CI again, publishes the server image to GitHub Packages (`ghcr.io/chrkar03/athos-icons:release`), then deploys it, once the server is set up (below) |
| Demo release | when you push a version tag (`v0.2.0`) | builds the demo for Windows, Mac and Linux, tries each one on that system, publishes them on the *Releases* page |

### Automatic deployment (one-time server setup)

Until this is done, the Deploy workflow publishes the image and skips the last step.

1. On the server: install Docker, then create the folder and the settings file:
   ```bash
   mkdir -p /opt/athos-icons && cd /opt/athos-icons
   nano .env      # copy .env.example from GitHub and fill it in
   ```
2. Create an SSH key just for deployments (on your computer):
   `ssh-keygen -t ed25519 -f deploy_key -N ""`. Add `deploy_key.pub` to the server's
   `~/.ssh/authorized_keys`.
3. On GitHub: *Settings → Secrets and variables → Actions*:
   - **Secrets:** `DEPLOY_SSH_KEY` = the contents of `deploy_key` (the private key);
     `DEPLOY_KNOWN_HOSTS` = the output of `ssh-keyscan your-server-address`.
   - **Variables:** `DEPLOY_HOST` = the server address; `DEPLOY_USER` = the SSH user (default `root`);
     `DEPLOY_PATH` only if you used another folder than `/opt/athos-icons`.
4. Merge something into `release` (or run *Deploy* by hand from the Actions tab). The workflow copies
   `docker-compose.yml` and `Caddyfile` to the server, pulls the new image and restarts the shop.
   Then create your admin account once: `docker compose exec web python manage.py createsuperuser`.

Under *Settings → Environments → production* you can require your approval before every deployment.

### Demo for testers

A copy of the shop that anyone can run on Windows, Mac or Linux without installing anything: they unzip it
and double-click **Start Demo**. It comes with demo accounts (password `demo1234`) and orders in every stage,
and shows the emails it would send on a *Demo mailbox* page.

- **New demo release:** `git tag v0.2.0 && git push origin v0.2.0`. The zips appear on the *Releases* page
  after about 10 minutes. The repository is private, so to share them, download and send them yourself
  (e.g. by Drive), or invite the person to the repository.
- **Build it yourself:** `python demo/build.py` (or `python demo/build.py linux`) puts the zips in `dist/`.
- How it works: `demo/build.py` packs a standalone Python, the packages and the shop; `demo/launch.py`
  starts it on `http://127.0.0.1:8000` with `DEMO_MODE` on; `demo/files/` holds the start scripts and the
  instructions (`README.txt`, Greek and English).

The Mac and Windows versions are not signed by Apple or Microsoft, so the first start needs an extra click.
The instructions in the zip explain it.

## Translations

All texts are in English in the code, with Greek translations in `locale/el/LC_MESSAGES/django.po`.
After editing the `.po` file (any text editor or [Poedit](https://poedit.net/)):

```bash
.venv/bin/python manage.py compiletranslations
```

If you add new texts to templates, install gettext (`sudo apt install gettext`) and run
`python manage.py makemessages -l el` to add them to the `.po` file.

Product, category and page texts have a Greek and an optional English field in the admin. If the English
field is empty, the Greek text is shown.

## Tests

```bash
.venv/bin/python manage.py test tests
```

## Project layout

| Folder | What's inside |
|---|---|
| `catalog/` | Categories, products, photos, pricing |
| `orders/` | Cart, orders, status flow, custom requests, messages, **all emails** (`emails.py`, `services.py`), parcel tracking (`tracking.py`) |
| `accounts/` | Customer accounts (consumer / business), login, registration |
| `core/` | Shop settings, text pages, homepage, image resizing |
| `templates/` | All pages; `templates/emails/` holds the automatic emails |
| `static/css/shop.css` | The whole design. Colours and fonts are defined at the top |
| `demo/` | The downloadable demo: build script, launcher, start scripts |
| `.github/workflows/` | CI, deployment and demo release |
