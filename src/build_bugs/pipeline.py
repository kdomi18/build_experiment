import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from git import Repo
from openai import OpenAI
from tqdm import tqdm

from build_bugs.function_extraction import extract_affected_functions
from utils.utils import DATA_DIR, ensure_directory

PY310_RELEASE = datetime(2021, 10, 4, tzinfo=UTC)


class BugPipeline:
    # ---------------------------------------------------------
    # 0. Initialization
    # ---------------------------------------------------------
    def __init__(self):
        """Initialize paths, metadata, and configuration."""
        self.input_file = DATA_DIR / "build_repos" / "output" / "repo_snapshots.json"
        self.output_dir = ensure_directory(DATA_DIR / "build_bugs" / "output")
        self.output_file = self.output_dir / "bugs.json"

        self.llm = OpenAI(
            base_url="https://llms.innkube.fim.uni-passau.de",
        )
        self.user_prompt_part = """
You are evaluating candidate bug-fix commits.

Your task:
Determine whether this commit primarily fixes a bug in
existing functionality.

Answer ONLY with:

YES
or
NO

Reject:
- documentation changes
- comment changes
- formatting changes
- refactoring
- dependency updates
- CI/build changes
- feature additions
- new APIs or new methods

Accept:
- correcting incorrect behaviour
- fixing crashes
- fixing exceptions
- fixing incorrect logic
- fixing security vulnerabilities
"""
        self.SYSTEM_PROMPT = """
You are a software engineering evaluator.

Determine whether a commit is primarily a bug fix.

Return exactly one token:

YES
or
NO
"""
        self.llm_cache_version = "1.0"
        self.llm_model = "qwen35-397b"
        self.prompt_hash = hashlib.sha256(
            (self.SYSTEM_PROMPT + self.user_prompt_part).encode()
        ).hexdigest()
        self.llm_cache = self._load_llm_cache(self.output_dir / "llm_cache.json")

        self.metadata = {
            "total_repos": 0,
            "total_bugs": 0,
            "bugs_in_repo": [],
            "repos_failed": [],
            "found_fix_commits": 0,
            "usable_fix_commits": 0,
            "llm_rejected": 0,
            "timestamp": datetime.now().isoformat(),
        }

    def _llm_cache_valid(self, cache):
        """Check if LLM cache is valid based on version, model, and prompts."""
        return (
            cache.get("version") == self.llm_cache_version
            and cache.get("model") == self.llm_model
            and cache.get("prompt_hash") == self.prompt_hash
        )

    def _load_llm_cache(self, path):
        """Load LLM validation cache from disk."""
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            if self._llm_cache_valid(data):
                print("LLM cache loaded and valid.")
                return data

        print("No valid LLM cache found. Starting fresh.")
        return {
            "version": self.llm_cache_version,
            "model": self.llm_model,
            "prompt_hash": self.prompt_hash,
            "answers": {},
        }

    def _save_llm_cache(self, path):
        """Save LLM validation cache to disk."""
        with open(path, "w") as f:
            json.dump(self.llm_cache, f, indent=4)

    # ---------------------------------------------------------
    # 1. Public API — main entry point
    # ---------------------------------------------------------
    def run(self):
        """
        Main pipeline:
        - Load repo snapshots
        - Count fix commits
        - Process repos sequentially
        - Aggregate results + metadata
        """

        with open(self.input_file) as f:
            snapshots = json.load(f)

        total_fix_commits, cache = self.count_all_fix_commits(snapshots)
        bugs = {}

        with tqdm(
            total=total_fix_commits,
            desc=f"Processing {total_fix_commits} fix commits",
        ) as pbar:
            for name, snapshot in snapshots.items():
                print(f"\nProcessing repository: {name}")
                try:
                    repo_cache = cache.get(name)

                    if not repo_cache:
                        continue

                    fix_pairs = repo_cache.get("fix_pairs", [])

                    repo_bugs, found_fix, usable_fix = self._process_repo(
                        snapshot, pbar, fix_pairs, bugs_per_repo_limit=8
                    )

                    if repo_bugs:
                        print(f"Found {len(repo_bugs)} usable bugs in {name}.")
                        bugs.update(repo_bugs)

                    self.metadata["found_fix_commits"] += found_fix
                    self.metadata["usable_fix_commits"] += usable_fix
                    self.metadata["bugs_in_repo"].append((name, len(repo_bugs)))

                except Exception as e:
                    self.metadata["repos_failed"].append(
                        {
                            "name": name,
                            "error": str(e),
                        }
                    )

        self._save_llm_cache(self.output_dir / "llm_cache.json")

        self.metadata["total_repos"] = len(self.metadata["bugs_in_repo"])
        self.metadata["total_bugs"] = len(bugs)

        with open(self.output_file, "w") as f:
            print(f"Writing {len(bugs)} bugs to {self.output_file}")
            json.dump(bugs, f, indent=4)

        self._write_metadata()
        self._describe()

    # ---------------------------------------------------------
    # 2. Repo-level processing
    # ---------------------------------------------------------
    def _process_repo(self, snapshot: dict, pbar, fix_pairs=[], bugs_per_repo_limit=5):
        """
        Process a single repository:
        - Checkout pinned commit
        - Determine cutoff commit
        - Collect fix commits
        - Validate limited candidate bug-fix commits
        - Extract modules/functions
        - Extract dependencies
        """
        repo_path = Path(snapshot["local_path"])
        repo = Repo(repo_path)
        branch = snapshot.get("default_branch") or "main"

        repo.git.checkout(branch)

        pinned_commit = repo.commit(snapshot["pinned_commit"])

        found_fix = len(fix_pairs)
        bugs = {}
        usable_fix = 0
        seen_fix = 0

        for buggy_commit_hash, fix_commit_hash in fix_pairs:
            if usable_fix >= bugs_per_repo_limit:
                # Update progress bar for remaining commits
                pbar.update(found_fix - seen_fix)
                break
            pbar.update(1)
            seen_fix += 1
            buggy_commit = repo.commit(buggy_commit_hash)
            fix_commit = repo.commit(fix_commit_hash)

            changed_files = self._is_valid_bug(
                repo,
                buggy_commit,
                fix_commit,
            )

            if not changed_files:
                continue

            bug_id = f"{snapshot['name']}::{buggy_commit.hexsha[:12]}-{fix_commit.hexsha[:12]}"

            if not self._llm_validate_bug(
                repo,
                buggy_commit,
                fix_commit,
                bug_id,
            ):
                self.metadata["llm_rejected"] += 1
                continue

            modules = self._extract_modules(
                repo,
                buggy_commit,
                fix_commit,
            )

            if not modules:
                continue

            usable_fix += 1

            bugs[bug_id] = self._build_bug_entry(
                repo,
                snapshot,
                buggy_commit,
                fix_commit,
                modules,
            )

        try:
            repo.git.checkout(branch)
            repo.git.reset("--hard", snapshot["pinned_commit"])
        except Exception:
            pass

        return bugs, found_fix, usable_fix

    # ---------------------------------------------------------
    # 3. Fix-commit discovery
    # ---------------------------------------------------------
    def _determine_cutoff(self, repo, pinned_commit):
        """Find earliest commit after Python 3.10 release."""
        cutoff = None

        for commit in repo.iter_commits(pinned_commit.hexsha):
            commit_time = commit.committed_datetime

            if commit_time >= PY310_RELEASE:
                cutoff = commit
            else:
                break

        return cutoff

    def _collect_fix_commits(
        self,
        repo,
        pinned_commit,
        cutoff_commit,
    ):
        """Return list of (buggy_commit, fix_commit) pairs."""

        keywords = [
            "fix",
            "bug",
            "patch",
            "security",
            "cve",
        ]
        fix_commits = []
        rev_range = f"{cutoff_commit.hexsha}..{pinned_commit.hexsha}"

        for commit in repo.iter_commits(rev=rev_range):
            # Skip merge commits
            if len(commit.parents) != 1:
                continue

            msg = commit.message.lower()

            # Skip revert commits
            if msg.startswith("revert"):
                continue

            # Skip bump or release commits
            if "bump" in msg or "release" in msg:
                continue

            if any(k in msg for k in keywords):
                buggy_commit = commit.parents[0]
                fix_commits.append((buggy_commit.hexsha, commit.hexsha))

        return fix_commits

    def count_all_fix_commits(self, snapshots):
        """
        Lightweight pass to count fix commits
        for tqdm progress bar. Cache info for fast reuse.
        """
        total = 0
        cache = {}

        for name, snapshot in snapshots.items():
            cache[name] = {}
            repo = Repo(snapshot["local_path"])
            pinned_commit = repo.commit(snapshot["pinned_commit"])

            cutoff_commit = self._determine_cutoff(
                repo,
                pinned_commit,
            )

            if cutoff_commit is None:
                continue

            fix_pairs = self._collect_fix_commits(
                repo,
                pinned_commit,
                cutoff_commit,
            )
            cache[name]["fix_pairs"] = fix_pairs

            total += len(fix_pairs)

        return total, cache

    # ---------------------------------------------------------
    # 4. Bug validation + extraction
    # ---------------------------------------------------------

    def _llm_validate_bug(
        self,
        repo,
        buggy_commit,
        fix_commit,
        bug_id,
    ):
        """
        Ask an LLM whether this commit represents
        a bug fix in existing functionality.

        Returns:
            bool
        """

        MAX_DIFF_CHARS = 20000

        diff_text = repo.git.diff(
            buggy_commit.hexsha,
            fix_commit.hexsha,
            "--",
            "*.py",
        )

        if len(diff_text) > MAX_DIFF_CHARS:
            diff_text = diff_text[:MAX_DIFF_CHARS]

        user_prompt = f"""
{self.user_prompt_part}

Commit message:
{fix_commit.message}

Diff:
{diff_text}
"""

        if bug_id in self.llm_cache["answers"]:
            cached = self.llm_cache["answers"][bug_id]
            return cached == "YES"

        response = self.llm.chat.completions.create(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )

        message = response.choices[0].message
        content = getattr(message, "content", None)
        if content is None:
            self.llm_cache["answers"][bug_id] = "NO"
            return False

        answer = content.strip().upper()

        if answer == "YES":
            self.llm_cache["answers"][bug_id] = "YES"
            return True

        if answer == "NO":
            self.llm_cache["answers"][bug_id] = "NO"
            return False

        self.llm_cache["answers"][bug_id] = "NO"
        return False

    def _is_valid_bug(
        self,
        repo,
        buggy_commit,
        fix_commit,
    ):
        """
        Check if commit touches real code
        (not tests/setup/init files).
        """
        diff = fix_commit.diff(
            buggy_commit,
            create_patch=True,
        )
        changed_files = []

        for d in diff:
            path = d.b_path or d.a_path

            if not path:
                continue

            if not Path(path).suffix == ".py":
                continue

            lowered = path.lower()

            if "test" in lowered:
                continue

            if Path(path).name == "setup.py":
                continue

            if Path(path).name == "__init__.py":
                continue

            changed_files.append(path)

        return changed_files

    def _extract_modules(
        self,
        repo,
        buggy_commit,
        fix_commit,
    ):
        """
        Extract affected modules + functions
        using diff + AST analysis.
        """
        diff = fix_commit.diff(
            buggy_commit,
            create_patch=True,
        )
        modules = []

        for d in diff:
            path = d.b_path or d.a_path

            if not path:
                continue

            if not Path(path).suffix == ".py":
                continue

            lowered = path.lower()

            if "test" in lowered:
                continue

            if Path(path).name == "setup.py":
                continue

            if Path(path).name == "__init__.py":
                continue

            affected = extract_affected_functions(
                repo,
                d,
                buggy_commit,
                fix_commit,
            )

            # Skip modules with no changed functions
            if not affected:
                continue

            module_path = Path(path).as_posix()

            modules.append(
                {
                    "module_path": module_path,
                    "functions_affected": sorted(affected),
                }
            )

        return modules

    # ---------------------------------------------------------
    # 5. Python 3.10 compatibility + dependency extraction + resolve modules
    # ---------------------------------------------------------
    def _get_python310_comp(self, commit):
        """
        Inspect project metadata files
        for Python 3.10 compatibility.
        """
        files_to_check = [
            "pyproject.toml",
            "setup.cfg",
            "setup.py",
        ]

        for fname in files_to_check:
            try:
                blob = commit.tree / fname
                text = blob.data_stream.read().decode(
                    "utf-8",
                    errors="ignore",
                )
            except Exception:
                continue

            # --- pyproject.toml ---
            if "requires-python" in text or "python" in text:
                if "3.10" in text or ">=3.10" in text or ">3.9" in text:
                    return ("yes", fname)

                if "3.9" in text and "3.10" not in text:
                    return ("no", fname)

            # --- setup.cfg ---
            if (
                "classifiers" in text
                and "Programming Language :: Python :: 3.10" in text
            ):
                return ("yes", fname)

            if "python_requires" in text:
                if ">=3.10" in text or ">3.9" in text:
                    return ("yes", fname)

                if "<3.10" in text or "3.9" in text:
                    return ("no", fname)

            # --- setup.py ---
            if "Programming Language :: Python :: 3.10" in text:
                return ("yes", fname)

            if "python_requires" in text:
                if ">=3.10" in text or ">3.9" in text:
                    return ("yes", fname)

                if "<3.10" in text or "3.9" in text:
                    return ("no", fname)

        return ("undecided", None)

    def _resolve_module_names(
        self,
        python_path: Path,
        repo_path: Path,
        modules: list,
    ):
        """
        Resolve importable module names after the project has been
        installed into the virtual environment.
        """

        for module in modules:
            module_path = module["module_path"]

            parts = list(Path(module_path).with_suffix("").parts)

            candidates = [".".join(parts[i:]) for i in range(len(parts))]

            resolved_name = None

            for candidate in candidates:
                script = f"""
import importlib
import pathlib
import sys

try:
    mod = importlib.import_module("{candidate}")
    print(pathlib.Path(mod.__file__).resolve())
except Exception:
    sys.exit(1)
"""
                result = subprocess.run(
                    [str(python_path), "-c", script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                if result.returncode != 0:
                    continue

                imported_file = Path(result.stdout.strip()).resolve()
                expected_file = (repo_path / module_path).resolve()

                if imported_file == expected_file:
                    resolved_name = candidate
                    break

            if resolved_name is None:
                # Fall back to previous behaviour.
                resolved_name = (
                    Path(module_path).with_suffix("").as_posix().replace("/", ".")
                )

            module["module_name"] = resolved_name

        return modules

    def _extract_dependencies(
        self,
        repo,
        repo_path: Path,
        fix_commit,
        modules,
    ):
        """
        Create venv, install project, reslove modules
        run pip freeze.

        Returns:
            (deps, modules, status, error, step)
        """
        python310 = (
            Path.home() / ".pyenv" / "versions" / "3.10.16" / "bin" / "python3.10"
        )

        if not python310.exists():
            return (
                [],
                modules,
                "python_missing",
                f"Python 3.10.16 not found at {python310}",
                "startup",
            )

        current_head = repo.head.commit.hexsha
        temp_dir = Path(tempfile.mkdtemp(prefix="depvenv_"))
        venv_dir = temp_dir / "venv"

        try:
            repo.git.checkout(fix_commit.hexsha)

            # Step 1: Create venv
            subprocess.run(
                [
                    str(python310),
                    "-m",
                    "venv",
                    str(venv_dir),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            pip_path = venv_dir / "bin" / "pip"

            python_path = venv_dir / "bin" / "python"

            # Step 2: Install project
            try:
                subprocess.run(
                    [
                        str(pip_path),
                        "install",
                        "-e",
                        str(repo_path),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except Exception as e:
                return (
                    [],
                    modules,
                    "install_failed",
                    str(e),
                    "install",
                )

            # Step 3: make sure module_name can be imported
            modules = self._resolve_module_names(
                python_path,
                repo_path,
                modules,
            )

            # Step 4: Freeze
            try:
                freeze = subprocess.run(
                    [
                        str(pip_path),
                        "freeze",
                        "--exclude-editable",
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except Exception as e:
                return (
                    [],
                    modules,
                    "freeze_failed",
                    str(e),
                    "freeze",
                )

            deps = [line.strip() for line in freeze.stdout.splitlines() if line.strip()]

            return (
                deps,
                modules,
                "success",
                None,
                "freeze",
            )

        except Exception as e:
            return (
                [],
                modules,
                "venv_failed",
                str(e),
                "create_venv",
            )

        finally:
            try:
                repo.git.checkout(current_head)
            except Exception:
                pass

            shutil.rmtree(
                temp_dir,
                ignore_errors=True,
            )

    # ---------------------------------------------------------
    # 6. Build final bug entry
    # ---------------------------------------------------------
    def _build_bug_entry(
        self,
        repo,
        snapshot,
        buggy_commit,
        fix_commit,
        modules,
    ):
        """Assemble the final bug dictionary."""
        status, evidence_file = self._get_python310_comp(fix_commit)

        (
            deps,
            modules,
            dep_status,
            dep_error,
            dep_step,
        ) = self._extract_dependencies(
            repo,
            Path(snapshot["local_path"]),
            fix_commit,
            modules,
        )

        return {
            "github_url": snapshot["github_url"],
            "fixed_commit": fix_commit.hexsha,
            "buggy_commit": buggy_commit.hexsha,
            "python310_status": status,
            "python310_evidence_file": evidence_file,
            "picked_because": self._reason_for_pick(fix_commit),
            "modules_affected": modules,
            "dependencies": deps,
            "dependency_status": dep_status,
            "dependency_error": dep_error,
            "dependency_step": dep_step,
        }

    def _reason_for_pick(self, commit):
        """Explain why this commit was considered a fix."""
        msg = commit.message.lower()

        if "cve" in msg:
            return "commit_msg_mentions_cve_fix"

        if "security" in msg:
            return "commit_msg_mentions_security"

        if "fix" in msg:
            return "commit_msg_mentions_fix"

        return "commit_msg_matches_generic_bug_pattern"

    # ---------------------------------------------------------
    # 7. Metadata + reporting
    # ---------------------------------------------------------
    def _write_metadata(self):
        """Write metadata.json and LLM cache to disk."""
        with open(self.output_dir / "metadata.json", "w") as f:
            json.dump(self.metadata, f, indent=4)

        with open(self.output_dir / "llm_cache.json", "w") as f:
            json.dump(self.llm_cache, f, indent=4)

    def _describe(self):
        """Print summary to console."""
        print("\nBug Pipeline Summary:")
        print(f"Repos processed: {len(self.metadata['bugs_in_repo'])}")
        print(f"Repos failed: {len(self.metadata['repos_failed'])}")
        print(f"Total fix commits found: {self.metadata['found_fix_commits']}")
        print(f"Total fix commits rejected by LLM: {self.metadata['llm_rejected']}")
        print(f"Usable fix commits: {self.metadata['usable_fix_commits']}")
        print(f"Total bugs extracted: {self.metadata['total_bugs']}")

        print()
