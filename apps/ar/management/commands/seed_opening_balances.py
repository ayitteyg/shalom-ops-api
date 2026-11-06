from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.ar.models import Site, Creditor, OpeningBalance


GO_LIVE_DATE = "2026-09-30"

BALANCES = [
    # ---------------- Achimota S/S (ACH) ----------------
    ("ACH", "H.R.B.T",                          "8575.33"),
    ("ACH", "Aayalolo",                         "9177.57"),
    ("ACH", "KAMAN SUPPLY CHAIN",               "5137.45"),
    ("ACH", "Zoompak",                          "79786.32"),
    ("ACH", "Ent. Comm",                        "72790.31"),
    ("ACH", "Central Logistics",                "17375.00"),
    ("ACH", "Sagis",                            "120653.70"),
    ("ACH", "GHANA REVENUE AUTHORITY",          "11199.67"),
    ("ACH", "Tumffuor",                         "78536.37"),
    ("ACH", "SAYCHEERS LIMITED",                "6835.23"),
    ("ACH", "PROFIN GHANA LTD.",                "5946.20"),
    ("ACH", "BUILDAF COMPANY LTD(MR.ABU)",      "16807.74"),
    ("ACH", "Zoompak (Current)",                "509856.03"),
    ("ACH", "Fedco",                            "20936.17"),
    ("ACH", "Ryzen Pharma",                     "9934.32"),
    ("ACH", "Coolwest",                         "1150.50"),
    ("ACH", "Geosource",                        "10559.46"),
    ("ACH", "YOKWA GAS LIMITED",                "36404.15"),
    ("ACH", "YP Company",                       "54785.87"),
    ("ACH", "Empowerment",                      "10393.35"),
    ("ACH", "PAPA",                             "3000.00"),
    ("ACH", "ALL STAR INSURANCE",               "590.00"),
    ("ACH", "MIN LOGISTICS LTD",                "229383.97"),
    ("ACH", "Desimone",                         "419845.69"),

    # ---------------- Achimota Kumasi / Shalom 2 (AKS) ----------------
    ("AKS", "GH-Bauxite",                       "38181.08"),
    ("AKS", "Apenkwa/ Yafra",                   "148877.42"),
    ("AKS", "Richmorgan",                       "56632.50"),
    ("AKS", "Manuel Kwap",                      "12900.00"),
    ("AKS", "GH-Express",                       "970415.93"),
    ("AKS", "Gexel",                            "0.00"),
    ("AKS", "Trinity Oil",                      "0.00"),
    ("AKS", "UTV/Peace Fm",                     "1788.00"),
    ("AKS", "Mr Martin",                        "0.00"),
    ("AKS", "Mad Florence",                     "0.00"),
    ("AKS", "Mr Yaro",                          "42831.58"),
    ("AKS", "Mr Leslie",                        "0.00"),
    ("AKS", "Alhaji Farouk",                    "0.00"),
    ("AKS", "Twum Daniel",                      "0.00"),

    # ---------------- East Legon (ELG) ----------------
    ("ELG", "Oak Plaza Hotel",                  "36313.64"),
    ("ELG", "Terraefuoco",                      "21650.34"),
    ("ELG", "Starbites Ent",                    "47357.08"),
    ("ELG", "Papas Pizza",                      "75792.55"),
    ("ELG", "Phlenu Delivery",                  "0.00"),
    ("ELG", "Exclusive Signature",              "780.00"),
    ("ELG", "Piercing System",                  "12070.85"),
    ("ELG", "Turkish Grill",                    "10220.53"),
    ("ELG", "Ernest Chemist",                   "0.00"),
    ("ELG", "ICGC",                             "0.00"),
    ("ELG", "Ashesei University",               "0.00"),

    # ---------------- Palmwine Junction (PWJ) ----------------
    ("PWJ", "Robert Azu & Partners",            "9258.81"),
    ("PWJ", "Desimone",                         "189054.60"),
    ("PWJ", "Inlaks",                           "1200.00"),
    ("PWJ", "Zenith Bank",                      "15351.00"),
    ("PWJ", "Sakom",                            "4965.00"),
    ("PWJ", "Sahara (Old)",                     "332486.64"),
    ("PWJ", "Sahara",                           "11145.49"),
    ("PWJ", "Frank Baidoo",                     "0.00"),

    # ---------------- Ofankor (OFK) ----------------
    ("OFK", "A2 Glass",                         "51669.32"),
    ("OFK", "Felix Kyei",                       "4561.50"),
    ("OFK", "Fresh up",                         "11400.00"),
    ("OFK", "Grace Mountain",                   "39469.20"),
    ("OFK", "Kofi Kyere",                       "4335.00"),
    ("OFK", "Capacity & Recruitment",           "185916.34"),
    ("OFK", "Octoglow",                         "0.00"),
    ("OFK", "Phytoriker",                       "21673.10"),
    ("OFK", "Trade Cross",                      "9808.50"),
    ("OFK", "Trinity",                          "13680.00"),
    ("OFK", "Mary Lucy",                        "0.00"),
    ("OFK", "Opportunity Int",                  "0.00"),
]
class Command(BaseCommand):
    help = "Seed opening balances at go-live date (2026-09-30)."

    def handle(self, *args, **options):
        created, skipped, errors = 0, 0, 0
        total = Decimal("0.00")

        signed_off_at = timezone.now()

        for site_code, creditor_name, amount_str in BALANCES:
            try:
                site = Site.objects.get(code=site_code)
            except Site.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f"[SKIP] Site not found: {site_code}"
                ))
                errors += 1
                continue

            try:
                creditor = Creditor.objects.get(site=site, name=creditor_name)
            except Creditor.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f"[SKIP] Creditor not found: {site_code} / {creditor_name}"
                ))
                errors += 1
                continue

            # Idempotent: skip if an opening balance already exists
            if OpeningBalance.objects.filter(creditor=creditor).exists():
                skipped += 1
                continue

            amount = Decimal(amount_str)
            OpeningBalance.objects.create(
                creditor=creditor,
                as_of_date=GO_LIVE_DATE,
                amount=amount,
                signed_off_by="system: seed_opening_balances",
                signed_off_at=signed_off_at,
                locked=True,
                reason_note="Seeded from accountant's go-live list.",
            )
            created += 1
            total += amount

            self.stdout.write(self.style.SUCCESS(
                f"[OK] {site_code} / {creditor_name}: {amount}"
            ))

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Created: {created}, Skipped: {skipped}, Errors: {errors}."
        ))
        self.stdout.write(self.style.SUCCESS(
            f"Total opening AR seeded: {total}"
        ))