"""
python manage.py setup_shop          → creates shop settings and the default text pages
python manage.py setup_shop --demo   → also adds demo categories and products (with drawn placeholder images)
"""

import random
from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from PIL import Image, ImageColor, ImageDraw, ImageFilter

from catalog.models import (Category, Material, Product, ProductImage, ProductSize, Size, Technique, Variation,
                            VariationOption, money)
from core.models import Page, SiteSettings

# Common icon sizes to start with; rename or delete them in the admin (Catalogue → Sizes).
SIZES = ["14 x 18 cm", "20 x 26 cm", "25 x 35 cm", "30 x 40 cm"]
# Materials and techniques to start with (Greek, English); edit them in the admin (Catalogue).
MATERIALS = [("Ξύλο", "Wood"), ("Φύλλο χρυσού 22 καρατίων", "22-carat gold leaf"), ("Φύλλο ασημιού", "Silver leaf"),
             ("Καμβάς", "Canvas"), ("Ασήμι 925", "Sterling silver 925"), ("Επάργυρο", "Silver-plated"),
             ("Επίχρυσο", "Gold-plated")]
TECHNIQUES = [("Βυζαντινή", "Byzantine"), ("Κρητική σχολή", "Cretan school"), ("Ρωσική", "Russian"),
              ("Αυγοτέμπερα (χειροποίητη)", "Egg tempera (hand-painted)"), ("Ακρυλικά", "Acrylics"),
              ("Ξυλόγλυπτη", "Carved wood"), ("Εκτύπωση σε ξύλο", "Print on wood"), ("Ψηφιδωτό", "Mosaic")]
# Choices customers make on the product page: (Greek, English, [(option, option in English, price change %)]).
# The percentages are examples; set your own in the admin (Catalogue → Customer choices).
VARIATIONS = [
    ("Επιφάνεια", "Surface", [("Επίπεδη", "Flat", 0), ("Σκαφτή", "Carved (recessed)", 30)]),
    ("Σχήμα", "Shape", [("Ορθογώνια", "Rectangular", 0), ("Θολωτή", "Arched", 10), ("Οβάλ", "Oval", 15)]),
]
# Demo products that come in several sizes: (size, share of the product's price).
DEMO_SIZES = [("14 x 18 cm", Decimal("0.6")), ("20 x 26 cm", Decimal("1")), ("30 x 40 cm", Decimal("1.6"))]

PAGES = [
    ("about", "Σχετικά με εμάς", "About us",
     "Γράψτε εδώ την ιστορία σας: ποιοι είστε, πώς φτιάχνονται οι εικόνες σας, από πού προέρχονται.\n\n"
     "Μπορείτε να αλλάξετε αυτό το κείμενο από τη διαχείριση → Σελίδες.",
     "Tell your story here: who you are, how your icons are made and where they come from.\n\n"
     "You can edit this text in the admin → Pages."),
    ("business", "Για επιχειρήσεις", "For businesses",
     "Συνεργαζόμαστε με καταστήματα, ναούς, μονές και επιχειρήσεις δώρων.\n\n"
     "Ανοίξτε επαγγελματικό λογαριασμό με τα στοιχεία της επιχείρησής σας (ΑΦΜ, ΔΟΥ). Μετά την έγκριση "
     "θα βλέπετε τιμές χονδρικής χωρίς ΦΠΑ και θα μπορείτε να παραγγέλνετε μεγαλύτερες ποσότητες.\n\n"
     "Για ειδικές ποσότητες ή εικόνες κατά παραγγελία, στείλτε μας ένα αίτημα.",
     "We work with shops, churches, monasteries and gift businesses.\n\n"
     "Open a business account with your company details (VAT number). After approval you will see "
     "wholesale prices excluding VAT and can order larger quantities.\n\n"
     "For special quantities or made-to-order icons, send us a custom request."),
    ("shipping", "Αποστολές & πληρωμές", "Shipping & payment",
     "Πώς λειτουργεί:\n\n"
     "1. Στέλνετε το αίτημα παραγγελίας (χωρίς πληρωμή).\n"
     "2. Ελέγχουμε τη διαθεσιμότητα και το κόστος αποστολής και σας στέλνουμε email με το τελικό ποσό.\n"
     "3. Πληρώνετε με τραπεζική κατάθεση, με τον αριθμό παραγγελίας ως αιτιολογία.\n"
     "4. Αποστέλλουμε την παραγγελία και σας στέλνουμε τον αριθμό αποστολής.\n\n"
     "Συμπληρώστε εδώ χρόνους και κόστη αποστολής, καθώς και την πολιτική επιστροφών.",
     "How it works:\n\n"
     "1. You send the order request (no payment yet).\n"
     "2. We check availability and shipping and email you the final amount.\n"
     "3. You pay by bank transfer, using the order number as the reference.\n"
     "4. We ship the order and send you the tracking number.\n\n"
     "Add your delivery times, shipping costs and returns policy here."),
    ("terms", "Όροι χρήσης", "Terms of use",
     "ΠΡΟΣΟΧΗ: Αντικαταστήστε αυτό το κείμενο με τους όρους χρήσης του καταστήματός σας "
     "(στοιχεία επιχείρησης, ΑΦΜ, τρόποι πληρωμής, δικαίωμα υπαναχώρησης 14 ημερών, επιστροφές).",
     "NOTE: Replace this text with your shop's terms (company details, VAT number, payment, "
     "the 14-day right of withdrawal, returns)."),
    ("privacy", "Πολιτική απορρήτου", "Privacy policy",
     "ΠΡΟΣΟΧΗ: Αντικαταστήστε αυτό το κείμενο με την πολιτική απορρήτου σας (GDPR).\n\n"
     "Το κατάστημα αποθηκεύει μόνο όσα στοιχεία χρειάζονται για τις παραγγελίες (όνομα, email, τηλέφωνο, "
     "διεύθυνση) και χρησιμοποιεί μόνο απαραίτητα cookies (σύνδεση, καλάθι).",
     "NOTE: Replace this text with your privacy policy (GDPR).\n\n"
     "The shop only stores the details needed for orders (name, email, phone, address) and only uses "
     "essential cookies (login, cart)."),
]

CATEGORIES = [
    ("Εικόνες Παναγίας", "Icons of the Theotokos", "#1f3f73"),
    ("Ιησούς Χριστός", "Jesus Christ", "#7a1f1f"),
    ("Άγιοι", "Saints", "#2f5a3a"),
    ("Άγγελοι & Αρχάγγελοι", "Angels & Archangels", "#6b2c55"),
    ("Δωδεκάορτο", "Great Feasts", "#7a4b12"),
]

PRODUCTS = [
    # name el, name en, category index, price, discount, dimensions
    ("Παναγία Πορταΐτισσα", "Panagia Portaitissa", 0, "45.00", 0, "20 x 26 cm"),
    ("Παναγία Γλυκοφιλούσα", "Panagia Glykofilousa", 0, "52.00", 15, "24 x 30 cm"),
    ("Παναγία Οδηγήτρια", "Panagia Hodegetria", 0, "38.00", 0, "18 x 24 cm"),
    ("Χριστός Παντοκράτωρ", "Christ Pantocrator", 1, "65.00", 0, "30 x 40 cm"),
    ("Χριστός Ελεήμων", "Christ the Merciful", 1, "48.00", 10, "20 x 26 cm"),
    ("Άγιος Νικόλαος", "Saint Nicholas", 2, "42.00", 0, "20 x 26 cm"),
    ("Άγιος Γεώργιος", "Saint George", 2, "55.00", 0, "24 x 30 cm"),
    ("Άγιος Δημήτριος", "Saint Demetrios", 2, "55.00", 20, "24 x 30 cm"),
    ("Αρχάγγελος Μιχαήλ", "Archangel Michael", 3, "49.00", 0, "20 x 26 cm"),
    ("Αρχάγγελος Γαβριήλ", "Archangel Gabriel", 3, "49.00", 0, "20 x 26 cm"),
    ("Ο Ευαγγελισμός", "The Annunciation", 4, "72.00", 0, "30 x 40 cm"),
    ("Η Γέννηση του Χριστού", "The Nativity", 4, "72.00", 0, "30 x 40 cm"),
]


def draw_placeholder(robe, seed, size=(900, 1125)):
    """A simple, clearly-fake 'icon' so the demo shop does not look empty."""
    rnd = random.Random(seed)
    w, h = size
    img = Image.new("RGB", size, "#6b4f22")
    d = ImageDraw.Draw(img)
    # gold leaf background with subtle vertical gradient
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(214 - 40 * t), int(176 - 40 * t), int(96 - 30 * t)))
    # frame
    d.rectangle([18, 18, w - 18, h - 18], outline="#5a3d12", width=10)
    d.rectangle([44, 44, w - 44, h - 44], outline="#f1d58f", width=3)
    # halo
    cx, cy, r = w // 2, int(h * 0.33), int(w * 0.2)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#f0d27e", outline="#8a6420", width=6)
    # head
    hr = int(r * 0.55)
    d.ellipse([cx - hr, cy - hr + 10, cx + hr, cy + hr + 10], fill="#b98a5e")
    # robe
    d.polygon([(cx, cy + hr - 10), (int(w * 0.16), h - 70), (int(w * 0.84), h - 70)], fill=robe)
    d.polygon([(cx, cy + hr + 30), (int(w * 0.34), h - 70), (int(w * 0.66), h - 70)],
              fill=tuple(int(c * 0.6) for c in ImageColor.getrgb(robe)))
    # gold ornaments
    for _ in range(18):
        x, y = rnd.randint(70, w - 70), rnd.randint(int(h * 0.62), h - 90)
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill="#f1d58f")
    img = img.filter(ImageFilter.SMOOTH)
    out = BytesIO()
    img.save(out, "JPEG", quality=88)
    return ContentFile(out.getvalue(), name=f"demo-{seed}.jpg")


class Command(BaseCommand):
    help = "Create shop settings and default pages (and optionally demo products)."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="Also add demo categories and products.")

    def handle(self, *args, **options):
        SiteSettings.load()
        for i, (slug, title, title_en, body, body_en) in enumerate(PAGES):
            _page, created = Page.objects.get_or_create(
                slug=slug, defaults={"title": title, "title_en": title_en, "body": body, "body_en": body_en,
                                     "order": i, "show_in_footer": slug != "business"})
            if created:
                self.stdout.write(f"Created page: {slug}")
        if not Size.objects.exists():
            Size.objects.bulk_create(Size(name=name, order=i) for i, name in enumerate(SIZES))
        for model, items in [(Material, MATERIALS), (Technique, TECHNIQUES)]:
            if not model.objects.exists():
                model.objects.bulk_create(model(name=n, name_en=e, order=i) for i, (n, e) in enumerate(items))
        if not Variation.objects.exists():
            for i, (name, name_en, choices) in enumerate(VARIATIONS):
                variation = Variation.objects.create(name=name, name_en=name_en, order=i)
                VariationOption.objects.bulk_create(
                    VariationOption(variation=variation, name=n, name_en=e, price_change=change, order=j)
                    for j, (n, e, change) in enumerate(choices))

        if not options["demo"]:
            self.stdout.write(self.style.SUCCESS("Shop is set up. Run with --demo to add sample products."))
            return

        categories = []
        for i, (name, name_en, color) in enumerate(CATEGORIES):
            cat, created = Category.objects.get_or_create(name=name, defaults={"name_en": name_en, "order": i})
            if created:
                cat.image = draw_placeholder(color, f"cat{i}", size=(900, 675))
                cat.save()
            categories.append((cat, color))

        for i, (name, name_en, ci, price, discount, dims) in enumerate(PRODUCTS):
            existing = Product.objects.filter(name=name).first()
            if existing:
                self.configure_demo(existing)  # demo products made before sizes and choices existed
                continue
            cat, color = categories[ci]
            product = Product.objects.create(
                name=name, name_en=name_en, category=cat, price=Decimal(price), discount_percent=discount,
                dimensions=dims,
                description=f"Δείγμα προϊόντος: {name}. Αντικαταστήστε με τη δική σας περιγραφή.",
                description_en=f"Demo product: {name_en}. Replace with your own description.",
                is_featured=i % 3 == 0, is_available=i != 9,
            )
            ProductImage.objects.create(product=product, image=draw_placeholder(color, i))
            product.materials.set(Material.objects.filter(name__in=["Ξύλο", "Φύλλο χρυσού 22 καρατίων"]))
            product.techniques.set(Technique.objects.filter(
                name__in=["Βυζαντινή" if i % 2 else "Κρητική σχολή", "Αυγοτέμπερα (χειροποίητη)"]))
            product.save(update_fields=["search_text"])
            self.configure_demo(product)
            self.stdout.write(f"Created demo product: {name}")
        self.stdout.write(self.style.SUCCESS("Demo data added. Delete it from the admin before going live."))

    def configure_demo(self, product):
        """Give a demo product three sizes and every customer choice, unless it already has some."""
        if product.sizes.exists() or product.options.exists():
            return
        for size_name, share in DEMO_SIZES:
            size, _created = Size.objects.get_or_create(name=size_name)
            ProductSize.objects.create(product=product, size=size, price=money(product.price * share))
        product.options.set(VariationOption.objects.all())
        product.price = min(option.price for option in product.sizes.all())
        product.dimensions = ""
        product.save()
