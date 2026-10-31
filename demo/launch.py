"""
Starts the demo shop on this computer and opens it in the browser.

"Start Demo" runs this with the Python that comes inside the demo folder. Everything the demo
saves (database, photos, the demo mailbox) goes to the "data" folder next to it: delete that
folder to start over.

    launch.py           start the shop and open the browser
    launch.py --check   set everything up, test a few pages and exit (used by CI)
"""

import io
import os
import re
import secrets
import socket
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.request import urlopen

APP = Path(__file__).resolve().parent.parent  # the shop's code
ROOT = APP.parent                             # the demo folder
DATA = ROOT / "data"
ACCOUNTS = """  Customer:   maria@demo.gr
  Business:   church@demo.gr
  Admin:      admin@demo.gr   (admin panel: {url}admin/)
  Password for all: demo1234"""


def free_port():
    for port in [*range(8000, 8020), 0]:
        with socket.socket() as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
                return candidate.getsockname()[1]
            except OSError:
                continue


def prepare(port):
    """Point Django at the data folder, then create the database and demo data on first start."""
    DATA.mkdir(exist_ok=True)
    key_file = DATA / "secret-key.txt"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(50))
    os.environ.update({
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DEMO_MODE": "1",
        "DEBUG": "0",
        "SECRET_KEY": key_file.read_text().strip(),
        "SQLITE_PATH": str(DATA / "db.sqlite3"),
        "MEDIA_ROOT": str(DATA / "media"),
        "ALLOWED_HOSTS": "127.0.0.1,localhost",
        "SITE_URL": f"http://127.0.0.1:{port}",
    })
    sys.path.insert(0, str(APP))
    import django
    from django.core.management import call_command

    django.setup()
    first_start = not (DATA / "db.sqlite3").exists()
    if first_start:
        print("Preparing the demo shop for the first time, this takes a few seconds...")
    call_command("migrate", interactive=False, verbosity=0)
    call_command("setup_demo", stdout=io.StringIO())


def check(port):
    """Open the main pages, as a customer and as the admin, then over real HTTP."""
    from django.test import Client
    from waitress.server import create_server

    from accounts.models import User
    from config.wsgi import application

    pages = {None: ["/el/", "/en/", "/el/shop/", "/el/demo/mailbox/", "/el/account/login/"],
             "maria@demo.gr": ["/el/account/", "/el/account/orders/", "/el/account/requests/"],
             "admin@demo.gr": ["/admin/", "/admin/orders/order/", "/admin/accounts/user/"]}
    for email, urls in pages.items():
        client = Client(HTTP_HOST="127.0.0.1")
        if email:
            client.force_login(User.objects.get(email=email))
        for url in urls:
            status = client.get(url).status_code
            print(f"{status} {url}")
            assert status == 200, url

    server = create_server(application, host="127.0.0.1", port=port)
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    home = urlopen(f"{base}/el/").read().decode()
    files = [re.search(r'href="(/static/css/[^"]+)"', home).group(1),
             re.search(r'src="(/media/[^"]+)"', home).group(1)]
    for path in files:
        with urlopen(base + path) as response:
            print(f"{response.status} {path} over HTTP")
            assert response.status == 200, path
    print("Demo check passed.")  # the server thread ends with the program


def serve(port):
    from waitress import serve as waitress_serve

    from config.wsgi import application

    url = f"http://127.0.0.1:{port}/"
    print(f"""
==============================================================
  Athos Icons demo is running:  {url}el/

  Keep this window open while you try the shop.
  Close it to stop the demo.

{ACCOUNTS.format(url=url)}

  Emails are not sent: open "Demo mailbox" at the top of the shop.
==============================================================
""", flush=True)
    threading.Timer(1.5, webbrowser.open, [f"{url}el/"]).start()
    waitress_serve(application, host="127.0.0.1", port=port, threads=8, _quiet=True)


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    port = free_port()
    prepare(port)
    if "--check" in sys.argv:
        check(port)
    else:
        serve(port)


if __name__ == "__main__":
    main()
