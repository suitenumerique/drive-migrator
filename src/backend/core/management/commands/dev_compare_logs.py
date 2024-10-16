import re

from django.core.management.base import BaseCommand

from core.models import Workspace
from core.osmose.osmose_backend import OsmoseManager

from demo.management.commands.create_demo import Timeit

REGEX = r"File\: (.+) \d+.+\((\d+)\)"
# REGEX = r"File(.+)"


class Command(BaseCommand):
    help = "(Dev command) its takes task logs to compare it with an Osmose workspace, to verify if all files are present."

    def add_arguments(self, parser):
        parser.add_argument("logs_file", nargs="+", type=str)
        parser.add_argument("workspace_uid", nargs="+", type=str)

    def handle(self, *args, **options):
        logs_file = options["logs_file"][0]
        workspace_uid = options["workspace_uid"][0]
        workspace = Workspace.objects.get(id=workspace_uid)
        self.stdout.write(f"Logs file: {logs_file}")

        log_files = self._get_files_from_logs(workspace, logs_file)
        self.stdout.write(f"Files in logs: {len(log_files)}")

        osmose_files = self._get_files_from_osmose(workspace)
        self.stdout.write(f"Osmose files: {len(osmose_files)}")

        if len(log_files) != len(osmose_files):
            self.stdout.write("Number of files in logs and Osmose are not equal")

        # Verify that each Osmose file is present in the logs
        self.stdout.write("Scanning for missing files...")
        missing_files = []
        with Timeit(self.stdout, "Comparing files"):
            for file in osmose_files:
                if file not in [f[0] for f in log_files]:
                    missing_files.append(file)

        if len(missing_files) > 0:
            self.stdout.write(f"Missing files: {len(missing_files)}")
            for file in missing_files:
                self.stdout.write(file)
        else:
            self.stdout.write(self.style.SUCCESS("All files are present :) !"))

        # Verify that there are no duplicate files in Osmose
        duplicates = [item for item in osmose_files if osmose_files.count(item) > 1]
        duplicates_map = {}
        overflow_count = 0
        for file in duplicates:
            if file in duplicates_map:
                duplicates_map[file] += 1
                overflow_count += 1
            else:
                duplicates_map[file] = 1

        if len(duplicates) > 0:
            self.stdout.write(
                f"Duplicate files in Osmose: {len(duplicates_map.items())}"
            )
            self.stdout.write(f"Overflow: {overflow_count}")
            for file, count in duplicates_map.items():
                self.stdout.write(f"{file} - {count} times")
        else:
            self.stdout.write(self.style.SUCCESS("No duplicate files in Osmose :) !"))

        # When there are duplicates, we need to take into account the overflow
        real_files_count_expected = len(osmose_files) - overflow_count
        if real_files_count_expected != len(log_files):
            self.stdout.write(
                self.style.ERROR(
                    f"Number of files in logs and Osmose are not equal even taking into account overflow. Expected: {real_files_count_expected}, Actual: {len(log_files)}"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Number of files in logs and Osmose are equal taking into account overflow :) !"
                )
            )

    def _get_files_from_osmose(self, workspace):
        backend = OsmoseManager().get_backend()
        folder = backend.get_workspace_documents_structure(workspace)

        def get_files(root, folder):
            current_path = root + "/" + folder.name
            files = []
            for file in folder.files:
                files.append(current_path + "/" + file.raw_data["originalFilename"])
            for child in folder.children:
                files.extend(get_files(current_path, child))
            return files

        def refine(files):
            out = []
            for file in files:
                refined_file = file.replace("/None", "")
                refined_file = refined_file.replace("/" + workspace.title, "")
                out.append(refined_file)
            return out

        osmose_files = refine(get_files("", folder))
        return osmose_files

    def _get_files_from_logs(self, workspace, log_file):
        flag = False
        files = []
        with open(log_file) as f:
            for line in f:
                if "Listing workspace dir" in line:
                    flag = True
                    self.stdout.write("Flag set to True")
                if not flag:
                    continue
                match = re.findall(REGEX, line)
                if match:
                    file = match[0][0]
                    size = match[0][1]
                    # at this point file is like /tmp/workspace_<uid>/<workspace title>/<actual path>,
                    # by spliting it by workspace title we get the actual path
                    splitted = file.split(workspace.title)
                    file = splitted[1]
                    files.append((file, size))
        return files
