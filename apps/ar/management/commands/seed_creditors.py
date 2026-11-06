from django.core.management.base import BaseCommand
from apps.ar.models import Site, Creditor


CREDITORS = [
    # ---------------- Achimota S/S (ACH) ----------------
    ("ACH", "ALL STAR INSURANCE"),
    ("ACH", "BUILDAF COMPANY LTD(MR.ABU)"),
    ("ACH", "GHANA REVENUE AUTHORITY"),
    ("ACH", "KAMAN SUPPLY CHAIN"),
    ("ACH", "MIN LOGISTICS LTD"),
    ("ACH", "Miscellaneous"),
    ("ACH", "PAPA"),
    ("ACH", "PROFIN GHANA LTD."),
    ("ACH", "SAYCHEERS LIMITED"),
    ("ACH", "TAMPICO"),
    ("ACH", "H.R.B.T"),
    ("ACH", "YOKWA GAS LIMITED"),
    ("ACH", "Aayalolo"),
    ("ACH", "Zoompak"),
    ("ACH", "Ent. Comm"),
    ("ACH", "Central Logistics"),
    ("ACH", "Sagis"),
    ("ACH", "Tumffuor"),
    ("ACH", "Zoompak (Current)"),
    ("ACH", "Fedco"),
    ("ACH", "Ryzen Pharma"),
    ("ACH", "Coolwest"),
    ("ACH", "Geosource"),
    ("ACH", "YP Company"),
    ("ACH", "Empowerment"),
    ("ACH", "Desimone"),

    # ---------------- Achimota Kumasi / Shalom 2 (AKS) ----------------
    ("AKS", "Apenkwa/ Yafra"),
    ("AKS", "Ferina Construction"),
    ("AKS", "GH-Bauxite"),
    ("AKS", "GH-Express"),
    ("AKS", "Gexel"),
    ("AKS", "Manuel Kwap"),
    ("AKS", "Miscellaneous"),
    ("AKS", "Mr Yaro"),
    ("AKS", "Richmorgan"),
    ("AKS", "Trinity Oil"),
    ("AKS", "UTV/Peace Fm"),
    ("AKS", "Mr Martin"),
    ("AKS", "Mad Florence"),
    ("AKS", "Mr Leslie"),
    ("AKS", "Alhaji Farouk"),
    ("AKS", "Twum Daniel"),

    # ---------------- East Legon (ELG) ----------------
    ("ELG", "Ernest Chemist"),
    ("ELG", "Exclusive Signature"),
    ("ELG", "Guardlock"),
    ("ELG", "ICGC"),
    ("ELG", "Miscellaneous"),
    ("ELG", "Oak Plaza Hotel"),
    ("ELG", "Papas Pizza"),
    ("ELG", "Phlenu Delivery"),
    ("ELG", "Piercing System"),
    ("ELG", "Starbites Ent"),
    ("ELG", "Terraefuoco"),
    ("ELG", "Turkish Grill"),
    ("ELG", "Ashesei University"),

    # ---------------- Palmwine Junction (PWJ) ----------------
    ("PWJ", "Miscellaneous"),
    ("PWJ", "Robert Azu & Partners"),
    ("PWJ", "Sahara"),
    ("PWJ", "Sahara (Old)"),
    ("PWJ", "Desimone"),
    ("PWJ", "Inlaks"),
    ("PWJ", "Zenith Bank"),
    ("PWJ", "Sakom"),
    ("PWJ", "Frank Baidoo"),

    # ---------------- Ofankor (OFK) ----------------
    ("OFK", "A2 Glass"),
    ("OFK", "Capacity & Recruitment"),
    ("OFK", "Felix Kyei"),
    ("OFK", "Fresh up"),
    ("OFK", "Grace Mountain"),
    ("OFK", "Kofi Kyere"),
    ("OFK", "Mary Lucy"),
    ("OFK", "Miscellaneous"),
    ("OFK", "Octoglow"),
    ("OFK", "Phytoriker"),
    ("OFK", "Trade Cross"),
    ("OFK", "Trinity"),
    ("OFK", "Opportunity Int"),
]


class Command(BaseCommand):
    help = "Seed creditors per site (merged web + seed data)."

    def handle(self, *args, **options):
        created, skipped = 0, 0
        missing_sites = set()

        for site_code, name in CREDITORS:
            try:
                site = Site.objects.get(code=site_code)
            except Site.DoesNotExist:
                missing_sites.add(site_code)
                continue

            _, was_created = Creditor.objects.get_or_create(
                site=site,
                name=name,
                defaults={"status": "active"},
            )
            if was_created:
                created += 1
            else:
                skipped += 1

        if missing_sites:
            self.stdout.write(self.style.ERROR(
                f"Sites missing: {', '.join(sorted(missing_sites))}. "
                f"Run seed_sites first."
            ))

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Created: {created}, Skipped (already exist): {skipped}."
        ))