# build_experiment

`build_experiment` is a multi‑stage pipeline for constructing Bugs in Python Projects (BIPP), a dataset of real‑world Python bugs that will be used to analyse the failure rate and reasons of [Pynguin](https://pypi.org/project/pynguin/). Starting from popular PyPI packages, it extracts repositories, identifies bug‑fix commits, reconstructs dependency environments, and produces an XML dataset that can be run on pynguin-experiments project.

## Pipeline Overview
1. **PyPI dataset construction**
2. **Repository cloning and snapshotting**
3. **Bug‑fix commit extraction & dependency reconstruction**
4. **Experiment dataset generation (XML)**

---

## Stage 1 — Build Database (PyPI Scraping)

**Purpose:** Collect metadata for top‑N PyPI packages, including GitHub URLs and Python 3.10 compatibility.

**Input:** `data/build_database/input/top-1000-pypi-packages_2026-05-10.csv`
**Output:**
- `data/build_database/output/projects.json`
- `data/build_database/output/metadata.json`

**Key steps:**
- Query `https://pypi.org/pypi/{project}/json`
- Extract GitHub URLs from `project_urls` or via fallback regex
- Detect Python 3.10 compatibility using PyPI classifiers
- Track metadata: processed projects, missing URLs, compatibility counts

**Example input entry:**
```csv
download_count,project
2672873026,"boto3"
```

**Example output JSON entry:**
```json
"boto3": {
    "github_url": "https://github.com/boto/boto3",
    "python10_compatible": true
},
```

---

## Stage 2 — Build Repositories

**Purpose:** Clone GitHub repositories and create structured snapshots.

**Input:** `data/build_database/output/projects.json`
**Output:**
- `data/build_repos/output/repo_snapshots.json`
- `data/build_repos/output/metadata.json`

**Process:**
- Clone or reuse repositories under `repos/`
- Detect default branch via:
  1. `origin/HEAD`
  2. `main` / `master`
  3. First available remote branch
  4. Fallback: `"main"`
- Select pinned commit = latest commit on default branch (or `HEAD`)
- Store snapshot: project name, URL, local path, default branch, pinned commit, refs
- Record metadata: cloned repos, reused repos, branch detection success, errors

**Example input entry:**
```json
"boto3": {
    "github_url": "https://github.com/boto/boto3",
    "python10_compatible": true
},
```

**Example output JSON entry:**
```json
"boto3": {
    "name": "boto3",
    "github_url": "https://github.com/boto/boto3",
    "local_path": "/home/domi/domi-MA/code/db/build_experiment/repos/boto3",
    "pinned_commit": "f2ccf9f3738028afa9d5a6545e52f8520a31afe1",
    "default_branch": "master",
    "refs": {
        "origin/HEAD": "5e2df6190560507ab8ff05b4ea7712d0c4bfaf48",
        "origin/develop": "5e2df6190560507ab8ff05b4ea7712d0c4bfaf48",
        "origin/master": "f2ccf9f3738028afa9d5a6545e52f8520a31afe1",
        ...
    }
},
```

---

## Stage 3 — Build Bugs Dataset

**Purpose:** Identify bug‑fix commits and extract structured bug information.

**Input:** `data/build_repos/output/repo_snapshots.json`
**Output:**
- `data/build_bugs/output/bugs.json`
- `data/build_bugs/output/metadata.json`

### Fix commit discovery
- Scan commits after Python 3.10 release (`2021‑10‑04`)
- Exclude merges, reverts, releases, dependency bumps
- Include commits containing keywords: `fix`, `bug`, `patch`, `security`, `cve`
- Pair each fix commit with its parent: `(buggy_commit, fix_commit)`

### Validation
Reject commits that:
- Don’t modify `.py` files
- Only touch tests
- Modify only `setup.py` / `__init__.py`
- Produce no meaningful diff

### Affected code extraction
- Compute diffs between buggy and fix commits
- Map changed files to modules
- Use AST analysis to extract modified functions

### Python 3.10 compatibility
Inspect:
- `pyproject.toml`
- `setup.cfg`
- `setup.py`

Classify as:
- **yes** — explicitly compatible
- **no** — explicitly incompatible
- **undecided** — no clear signal

### Dependency reconstruction
For each fix commit:
1. Checkout fix commit
2. Create Python 3.10.16 virtualenv
3. `pip install -e .`
4. `pip freeze --exclude-editable`

Record:
- Dependency list
- Status (`success`, `install_failed`, etc.)
- Error messages and failing step

### Metadata
Track: processed repos, failures, discovered fix commits, usable commits, extracted bugs, timestamp.

**Example input entry:**
```json
"boto3": {
    "name": "boto3",
    "github_url": "https://github.com/boto/boto3",
    "local_path": "/home/domi/domi-MA/code/db/build_experiment/repos/boto3",
    "pinned_commit": "f2ccf9f3738028afa9d5a6545e52f8520a31afe1",
    "default_branch": "master",
    "refs": {
        "origin/HEAD": "5e2df6190560507ab8ff05b4ea7712d0c4bfaf48",
        "origin/develop": "5e2df6190560507ab8ff05b4ea7712d0c4bfaf48",
        "origin/master": "f2ccf9f3738028afa9d5a6545e52f8520a31afe1",
        ...
    }
},
```

**Example output JSON entry:**
```json
"boto3::29eceebbbade-74f13987385e": {
    "github_url": "https://github.com/boto/boto3",
    "python310_status": "yes",
    "python310_evidence_file": "setup.py",
    "picked_because": "commit_msg_mentions_fix",
    "modules_affected": [
        {
            "module_name": "boto3.s3.inject",
            "functions_affected": [
                "bucket_copy",
                "copy",
                "object_copy"
            ]
        }
    ],
    "dependencies": [
        "botocore==1.42.97",
        "jmespath==1.1.0",
        "python-dateutil==2.9.0.post0",
        "s3transfer==0.16.1",
        "six==1.17.0",
        "urllib3==2.7.0"
    ],
    "dependency_status": "success",
    "dependency_error": null,
    "dependency_step": "freeze"
},
```

---

## Stage 4 — Build Experiment Dataset

**Purpose:** Convert bug data into an XML dataset for downstream experimentation.

**Input:** `data/build_bugs/output/bugs.json`
**Output:**
- `data/build_experiment/output/experiment.xml`
- `data/build_experiment/output/metadata.json`

### Transformation
- Convert each bug entry into a `ProjectData` object
- Bug identifier: `project_name_<fix_commit>`
- Map modules → affected functions
- Preserve dependencies and repo metadata

### XML structure
Each bug becomes:

```xml
<project>
  <name>...</name>
  <version>...</version>
  <repository>...</repository>
  <sources>kd-projects/<local_path></sources>
  <modules>
    <module>
      <name>module.name</name>
      <only_cover>function_name</only_cover>
      <dependency>package==version</dependency>
    </module>
  </modules>
</project>
```
### Filtering
- Default limit: first 20 bugs
- Disable with limit = -1

### Metadata
Includes:
- Total projects
- Total bugs
- Total modules
- Avg. bugs per project
- Avg. modules per bug
- Distributions for reporting

**Example input entry:**
```json
"boto3::29eceebbbade-74f13987385e": {
    "github_url": "https://github.com/boto/boto3",
    "python310_status": "yes",
    "python310_evidence_file": "setup.py",
    "picked_because": "commit_msg_mentions_fix",
    "modules_affected": [
        {
            "module_name": "boto3.s3.inject",
            "functions_affected": [
                "bucket_copy",
                "copy",
                "object_copy"
            ]
        }
    ],
    "dependencies": [
        "botocore==1.42.97",
        "jmespath==1.1.0",
        "python-dateutil==2.9.0.post0",
        "s3transfer==0.16.1",
        "six==1.17.0",
        "urllib3==2.7.0"
    ],
    "dependency_status": "success",
    "dependency_error": null,
    "dependency_step": "freeze"
},
```

**Example output XML entry:**
```xml
<project>
    <name>boto3_74f13987385e</name>
    <version>74f13987385e</version>
    <repository>https://github.com/boto/boto3</repository>
    <sources>kd-projects/boto3_74f13987385e</sources>
    <modules>
        <module>
        <name>boto3.s3.inject</name>
        <only_cover>bucket_copy</only_cover>
        <only_cover>copy</only_cover>
        <only_cover>object_copy</only_cover>
        <dependency>botocore==1.42.97</dependency>
        <dependency>jmespath==1.1.0</dependency>
        <dependency>python-dateutil==2.9.0.post0</dependency>
        <dependency>s3transfer==0.16.1</dependency>
        <dependency>six==1.17.0</dependency>
        <dependency>urllib3==2.7.0</dependency>
        </module>
    </modules>
</project>
```

# CHANGELOG: 
- add `buggy_commit` tag to output XML entries to help `analyse-pynguin-failures` in its subsequent analysis
```
    <buggy_commit>5984e3b25f4fdee64aad20e98668c402f7ed5041</buggy_commit>
```