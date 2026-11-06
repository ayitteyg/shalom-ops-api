from django.core.management.base import BaseCommand
from apps.ar.models import Site


SITES = [
    {"code": "ACH", "name": "Achimota S/S",         "has_creditors": True},
    {"code": "AKS", "name": "Achimota Ksi",         "has_creditors": True},
    {"code": "ELG", "name": "East Legon",           "has_creditors": True},
    {"code": "PWJ", "name": "Palmwine Junction S/S","has_creditors": True},
    {"code": "OFK", "name": "Ofankor S/S",          "has_creditors": True},
    {"code": "BOH", "name": "Bohye S/S",            "has_creditors": False},
]


class Command(BaseCommand):
    help = "Seed the Site table with the six active sites."

    def handle(self, *args, **options):
        created, updated = 0, 0
        for entry in SITES:
            obj, was_created = Site.objects.update_or_create(
                code=entry["code"],
                defaults={
                    "name": entry["name"],
                    "has_creditors": entry["has_creditors"],
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created: {obj}"))
            else:
                updated += 1
                self.stdout.write(f"Updated: {obj}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Created: {created}, Updated: {updated}."
        ))