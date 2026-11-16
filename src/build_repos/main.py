import json
from sys import argv

from build_repos.repo_initializer import RepoInitializer
from utils.utils import DATA_DIR, PROJECT_ROOT, ensure_directory


def validate_argv(argv):
    if len(argv) > 2:
        print("Usage: poetry run python src/build_repos/main.py [limit]")
        exit(1)
    if len(argv) == 1:
        print("No limit provided, defaulting to 16.")
        return 16  # Default limit

    try:
        limit = int(argv[1])
        if limit <= 0:
            print("Limit must be a positive integer.")
            exit(1)
        if limit > 16:
            print("Limit exceeds 16, this might take a lot of searching and time.")
        if limit > 100:
            print("Limit exceeds the maximum allowed value of 100.")
            exit(1)
        return limit
    except ValueError:
        print("Invalid limit. Please provide a valid integer.")
        exit(1)


def main():
    limit = validate_argv(argv)

    output_dir = ensure_directory(DATA_DIR / "build_repos" / "output")
    base_dir = ensure_directory(PROJECT_ROOT / "repos")

    input_file = DATA_DIR / "build_database" / "output" / "projects.json"
    output_file = output_dir / "repo_snapshots.json"

    # Ensure output directory exists
    ensure_directory(output_file.parent)

    with open(input_file) as f:
        projects = json.load(f)

    initializer = RepoInitializer(
        base_dir=base_dir,
        output_file=output_file,
    )

    initializer.initialize_all(projects, limit=limit)


if __name__ == "__main__":
    main()
