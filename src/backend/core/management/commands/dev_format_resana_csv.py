from django.core.management.base import BaseCommand

from core.resana.resana_backend import ResanaBackend
from core.models import Workspace
import csv
import os
import json
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

class Command(BaseCommand):
    help = "(Dev command) it loads the Resana CSV file to map email to target organization"

    def cleanup(self, str: str):
        return str.lower().replace("’", "'").replace("&#039;", "'")

    def handle(self, *args, **options):
        self.stdout.write(f"{os.path.dirname(__file__)}")

        mappings = []
        header = []

        # Open the CSV file and read its content
        with open(f"{os.path.dirname(__file__)}/mappings_resana_nv.csv", mode='r', encoding='utf-8') as file:
            csv_reader = csv.reader(file, delimiter=';')
            # Skip the header row
            header = next(csv_reader)
            # Initialize an empty list to store the third column values
            third_column_values = []
            for row in csv_reader:
                # Append the third column value to the list
                third_column_values.append(row[2])
                mappings.append({
                    "domain": row[0].replace("*", ""),
                    "uuid": "xxx",
                    "domain_validator": row[1],
                    "name": row[2]
                })

        # Get distinct values from the list
        distinct_values = sorted(list(set(third_column_values)), key=len)
        distinct_values_to_uuids = {}

        # Print the distinct values
        self.stdout.write(f"Distinct values from the third column")
        for value in distinct_values:
            print(value)

        # Get Resana organizations
        resana_backend = ResanaBackend()
        organizations = resana_backend.get_organizations()
        self.stdout.write(f"Resana Organizations:")
        organization_name_to_data = {}
        for organization in organizations:
            print(organization["uuid"] + " -> " + organization["name"])
            organization_name_to_data[self.cleanup(organization["name"])] = organization

        self.stdout.write(f"organization_name_to_data:")
        for key in organization_name_to_data:
            print(key)

        
        # Make sure every row has a valid mapping
        self.stdout.write(f"Mapping ...")
        for value in distinct_values:
            resana_data = organization_name_to_data[self.cleanup(value)]
            print("Value:")
            print(value)
            print(resana_data)


        new_file = [header + ["organization_uuid"]]
        for mapping in mappings:
            domain = mapping["domain"]
            validate_email(f"example@{domain}")
            mapping["uuid"] = organization_name_to_data[self.cleanup(mapping["name"])]["uuid"]
            new_file.append([
                mapping["domain"],
                mapping["domain_validator"],
                mapping["name"],
                mapping["uuid"],
            ])

        with open(f"{os.path.dirname(__file__)}/mappings_resana_nv.new.csv", mode='wt', encoding='utf-8') as fp:    
            writer = csv.writer(fp, delimiter=";")
            # writer.writerow(["your", "header", "foo"])  # write header
            writer.writerows(new_file)