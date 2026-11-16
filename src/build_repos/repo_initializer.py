import json
from datetime import datetime
from pathlib import Path

from git import Repo

from build_repos.repo_snapshot import RepoSnapshot, to_dict
from utils.utils import ensure_directory


class RepoInitializer:
    def __init__(self, base_dir: Path, output_file: Path):
        self.base_dir = base_dir
        self.output_file = output_file

        ensure_directory(self.base_dir)
        ensure_directory(self.output_file.parent)

        # Metadata container
        self.metadata = {
            "total_projects": 0,
            "cloned_repos": [],
            "existing_repos": [],
            "default_branch_detected": [],
            "default_branch_missing": [],
            "errors": [],
            "timestamp": datetime.now().isoformat(),
        }

    def initialize_all(self, projects: dict, limit: int = -1):
        snapshots = {}
        self.metadata["total_projects"] = (
            len(projects) if limit == -1 else min(len(projects), limit)
        )

        for name, meta in projects.items():
            if limit != -1 and len(snapshots) >= limit:
                break

            try:
                snapshot = self._initialize_repo(name, meta["github_url"])
                snapshots[name] = to_dict(snapshot)

                # Track default branch detection
                if snapshot.default_branch:
                    self.metadata["default_branch_detected"].append(name)
                else:
                    self.metadata["default_branch_missing"].append(name)

            except Exception as e:
                self.metadata["errors"].append({"name": name, "error": str(e)})

        # Write snapshots.json
        self._write(snapshots)

        # Write metadata.json
        self._write_metadata()

        # Print summary
        self._describe()

    def _write(self, data: dict):
        with open(self.output_file, "w") as f:
            json.dump(data, f, indent=4)

    def _write_metadata(self):
        metadata_path = self.output_file.parent / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(self.metadata, f, indent=4)

    def _describe(self):
        print("\nRepo Build Metadata Summary:")
        print(f"Total projects processed: {self.metadata['total_projects']}")
        print(f"Repos cloned: {len(self.metadata['cloned_repos'])}")
        print(f"Repos already existed: {len(self.metadata['existing_repos'])}")
        print(
            f"Default branch detected: {len(self.metadata['default_branch_detected'])}"
        )
        print(f"Default branch missing: {len(self.metadata['default_branch_missing'])}")
        print(f"Errors: {len(self.metadata['errors'])}")
        print()

    def _initialize_repo(self, name: str, url: str) -> RepoSnapshot:
        path = self.base_dir / name

        if path.exists():
            repo = Repo(path)
            self.metadata["existing_repos"].append(name)
        else:
            repo = Repo.clone_from(url, path)
            self.metadata["cloned_repos"].append(name)

        refs = {r.name: r.commit.hexsha for r in repo.remotes.origin.refs}

        default_branch = self._detect_default(refs)

        # Compute pinned commit based on default branch
        pinned_commit = None
        if default_branch:
            key = f"origin/{default_branch}"
            pinned_commit = refs.get(key)

        # Fallback: use HEAD if default branch commit not found
        if pinned_commit is None:
            pinned_commit = repo.head.commit.hexsha

        return RepoSnapshot(
            name=name,
            github_url=url,
            local_path=str(path),
            pinned_commit=pinned_commit,
            default_branch=default_branch,
            refs=refs,
        )

    def _detect_default(self, refs: dict) -> str:
        head_key = "origin/HEAD"
        if head_key in refs:
            try:
                repo = Repo(self.base_dir)
                head_ref = repo.remotes.origin.refs.HEAD
                return head_ref.reference.name.replace("origin/", "")
            except Exception:
                pass

        for candidate in ["main", "master"]:
            key = f"origin/{candidate}"
            if key in refs:
                return candidate

        for ref in refs:
            if ref.startswith("origin/") and ref != "origin/HEAD":
                return ref.replace("origin/", "")

        return "main"
