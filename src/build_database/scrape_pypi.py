import csv
import json
import re
from itertools import islice

import requests

from utils.utils import DATA_DIR, ensure_directory

INPUT_DATA_DIR = ensure_directory(DATA_DIR / "build_database" / "input")
OUTPUT_DATA_DIR = ensure_directory(DATA_DIR / "build_database" / "output")

METADATA = {
    "total_projects": -1,
    "github_url": [],
    "no_github_url": [],
    "python10_compatible": [],
    "not_python10_compatible": [],
}

FUNC_CALLED = False


def _get_top_n_project_names(n=100):
    path = INPUT_DATA_DIR / "top-1000-pypi-packages_2026-05-10.csv"

    with open(path, newline="") as csvfile:
        reader = csv.reader(csvfile)
        next(reader, None)  # skip header safely

        return [row[1] for row in islice(reader, n)]


def _get_project_urls(data, iteration=0):
    if (
        data is None or iteration > 10 or not isinstance(data, dict)
    ):  # Prevent infinite recursion
        return "NO DATA"
    if "project_urls" in data.keys():
        return data["project_urls"]
    else:
        for _, value in data.items():
            if isinstance(value, dict):
                result = _get_project_urls(value, iteration + 1)
                if result != "NO DATA":
                    return result


def _try_regex(data: dict, name: str) -> str:
    print(f"Trying github regex for project {name}...")
    info = str(data)
    matches = re.findall(rf"https?://github\.com/[^\s]+{name}", info)
    if matches:
        return matches[0]
    print(f"Warning: No GitHub URL found for project {name}")
    METADATA["no_github_url"].append(name)
    return "<unknown>"


def _get_github_url(data: dict, name: str) -> str:
    possible_keys = [
        "Homepage",
        "Source",
        "Repository",
        "Source Code",
        "Code",
        "Home",
        "Github",
    ]
    possible_keys += [
        key.lower() for key in possible_keys
    ]  # Add lowercase versions of the keys
    project_urls = _get_project_urls(data)
    if project_urls == "NO DATA":
        return _try_regex(data, name)
    if project_urls is None or not isinstance(project_urls, dict):
        return _try_regex(data, name)
    for key in possible_keys:
        if key in project_urls.keys():
            url = project_urls[key]
            if "github.com" in url:
                METADATA["github_url"].append(name)
                return url
    return _try_regex(data, name)


def _is_python10_compatible(data: dict, name: str) -> bool:
    classifiers = data["info"].get("classifiers", [])
    for classifier in classifiers:
        if "Programming Language :: Python :: 3.10" in classifier:
            METADATA["python10_compatible"].append(name)
            return True
    METADATA["not_python10_compatible"].append(name)
    return False


def _get_all_pypi_info(project_name: str):
    url = f"https://pypi.org/pypi/{project_name}/json"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        return data
    except (requests.RequestException, KeyError):
        print(f"Error fetching data for project: {project_name}")
        return None


def extract_into_json(n: int = 50):
    global FUNC_CALLED
    FUNC_CALLED = True
    METADATA["total_projects"] = n
    project_infos = {}
    for project_name in _get_top_n_project_names(n):
        # print(f"Processing project: {project_name}")
        project_data = _get_all_pypi_info(project_name)
        if project_data:
            project_info = {
                "github_url": _get_github_url(project_data, project_name),
                "python10_compatible": _is_python10_compatible(
                    project_data, project_name
                ),
            }
            project_infos[project_name] = project_info
        else:
            print(f"Failed to fetch data for project: {project_name}")

    with open(OUTPUT_DATA_DIR / "projects.json", "w") as json_file:
        json.dump(project_infos, json_file, indent=4)


def read_metadata_from_file():
    with open(OUTPUT_DATA_DIR / "metadata.json", "r") as json_file:
        return json.load(json_file)


def describe(metadata: dict):
    if not FUNC_CALLED:
        metadata = read_metadata_from_file()
    else:
        save_metadata(metadata)
    github_url_list = metadata.get("github_url", [])
    no_github_url_list = metadata.get("no_github_url", [])
    python10_compatible_list = metadata.get("python10_compatible", [])
    not_python10_compatible_list = metadata.get("not_python10_compatible", [])
    print()
    print("projects.json -- Metadata Summary:")
    print(f"Total projects processed: {metadata.get('total_projects', 'N/A')}")
    print(f"Projects with a valid GitHub URL: {len(github_url_list)}")
    print(f"Projects without a GitHub URL: {len(no_github_url_list)}")
    print(f"Projects with Python 3.10 compatibility: {len(python10_compatible_list)}")
    print(
        f"Projects without Python 3.10 compatibility: {len(not_python10_compatible_list)}"
    )
    print()
    print(
        f"Projects with valid GitHub URL that are Python 3.10 compatible: {len(set(github_url_list) & set(python10_compatible_list))}"
    )
    print(
        f"Projects without GitHub URL that are Python 3.10 compatible: {len(set(no_github_url_list) & set(python10_compatible_list))}"
    )
    print(
        f"Projects with valid GitHub URL that are not Python 3.10 compatible: {len(set(github_url_list) & set(not_python10_compatible_list))}"
    )
    print(
        f"Projects without GitHub URL that are not Python 3.10 compatible: {len(set(no_github_url_list) & set(not_python10_compatible_list))}"
    )


def save_metadata(metadata: dict):
    with open(OUTPUT_DATA_DIR / "metadata.json", "w") as json_file:
        json.dump(metadata, json_file, indent=4)


if __name__ == "__main__":
    # extract_into_json(1000)
    describe(METADATA)
    # print(_get_all_pypi_info("jupyter"), file=open(OUTPUT_DATA_DIR / "random_project.json", "w"))
