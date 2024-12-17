from django.core.management.base import BaseCommand

import csv
import os

from core.models import ResanaEmailMapping


class Command(BaseCommand):
    help = "(Dev command) it mounts in DB the Resana formatted CSV"

    def handle(self, *args, **options):
        if ResanaEmailMapping.objects.count() > 0:
            self.stdout.write("Resana mappings already mounted in DB")
            return

        self.stdout.write(f"{os.path.dirname(__file__)}")

        count = 0
        # Open the CSV file and read its content
        with open(f"{os.path.dirname(__file__)}/mappings_resana_nv.new.csv", mode='r', encoding='utf-8') as file:
            csv_reader = csv.reader(file, delimiter=';')
            # Skip the header row
            next(csv_reader)
            for row in csv_reader:
                mapping = ResanaEmailMapping(domain=row[0].strip(), resana_organization_name=row[2], resana_organization_uuid=row[3])
                mapping.save()
                count += 1
                self.stdout.write(f"Mapping {count} mounted")

        mapping = ResanaEmailMapping(domain="*", resana_organization_name="Migration DINUM", resana_organization_uuid="03-01-52e55072-47ec-665f-d787-1df57e98199b")
        mapping.save()
        self.stdout.write(f"{count} mappings mounted")
