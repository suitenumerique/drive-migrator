#!/usr/bin/env python3
# ruff: noqa: T201
"""
Generate sample workspaces under data/demo-data for the filesystem source
backend (core.sources.filesystem.FileSystemSourceBackend).

The data/demo-data mount is read-only inside the app-dev/celery-dev
containers (docker-compose.yml), so this script writes directly on the
host, matching the emails of the Keycloak demo accounts defined in
docker/auth/realm.json (impress@impress.world, agent@demo.fr). It has no
Django dependency and does not run inside a container.

Usage: python3 src/backend/demo/generate_demo_data.py [--root PATH] [--seed N]
"""

import argparse
import csv
import random
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = REPO_ROOT / "data" / "demo-data"

WORKSPACES_BY_USER = {
    "migrator@migrator.world": [
        "Espace RH",
        "Projet Urbanisme 2024",
        "Communication interne",
        "Budget 2025",
    ],
    "agent@demo.fr": [
        "Conseil Municipal",
        "Services Techniques",
        "Formation du personnel",
        "Archives 2023",
    ],
}

DOCUMENT_TITLES = [
    "Compte-rendu de réunion",
    "Note de synthèse",
    "Rapport d'activité",
    "Budget prévisionnel",
    "Plan de formation",
    "Charte de communication",
    "Bilan annuel",
    "Note d'orientation",
]

LOREM_SENTENCES = [
    "La présente note synthétise les échanges tenus lors de la dernière réunion.",
    "Les crédits alloués permettront de couvrir les dépenses prévues sur l'exercice.",
    "Un point d'étape sera présenté lors de la prochaine séance du conseil.",
    "Les services concernés ont été associés à l'élaboration de ce document.",
    "Ce rapport présente un bilan des actions menées au cours de l'année écoulée.",
]

FIRST_NAMES = [
    "Jean",
    "Sophie",
    "Luc",
    "Camille",
    "Thomas",
    "Anne",
    "Michel",
    "Isabelle",
]
LAST_NAMES = [
    "Dupont",
    "Martin",
    "Bernard",
    "Leroy",
    "Petit",
    "Moreau",
    "Faure",
    "Laurent",
]


def slugify(title: str) -> str:
    return title.lower().replace(" ", "-").replace("'", "")


def write_document(path: Path, title: str, rng: random.Random) -> None:
    body = "\n\n".join(rng.sample(LOREM_SENTENCES, k=2))
    path.write_text(f"{title}\n{'=' * len(title)}\n\n{body}\n", encoding="utf-8")


def write_members_csv(path: Path, rng: random.Random) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for _ in range(rng.randint(2, 4)):
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            email = f"{first[0].lower()}.{last.lower()}@example.fr"
            writer.writerow([last, first, email])


def generate(root: Path, seed: int, force: bool) -> None:
    if root.exists() and any(root.iterdir()):
        if not force:
            sys.stderr.write(
                f"{root} already exists and is not empty. "
                "Pass --force to wipe and regenerate it."
            )
            sys.exit(1)
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    for email, workspace_names in WORKSPACES_BY_USER.items():
        for name in workspace_names:
            workspace_dir = root / email / name
            workspace_dir.mkdir(parents=True)

            write_members_csv(workspace_dir / "_users.csv", rng)

            for title in rng.sample(DOCUMENT_TITLES, k=rng.randint(2, 4)):
                filename = f"{slugify(title)}.txt"
                write_document(workspace_dir / filename, title, rng)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Wipe --root first if it already exists and is not empty",
    )
    args = parser.parse_args()

    generate(args.root, args.seed, args.force)
    sys.stdout.write(f"Generated demo data under {args.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
