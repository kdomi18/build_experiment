import dataclasses
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from sys import argv

from utils.utils import DATA_DIR, ensure_directory


@dataclasses.dataclass(frozen=True)
class ModuleData:
    path: str
    functions: set[str]


@dataclasses.dataclass(frozen=True)
class ProjectData:
    """Data on each project."""

    father: str  # used to link related projects for metadata analysis
    name: str
    version: str
    repo_url: str
    modules: dict[str, ModuleData]  # module -> module data
    project_path: str
    dependencies: list[str]
    buggy_commit: str


def convert_entry_to_project_data(
    entry_key: str,
    entry_data: dict,
) -> ProjectData:
    """
    Converts an input JSON entry into ProjectData.
    """

    project_name, commit_range = entry_key.split("::")

    # Keep only second hash chunk
    fix_commit = commit_range.split("-")[1]
    full_name = f"{project_name}_{fix_commit}"

    modules: dict[str, ModuleData] = {}

    for module in entry_data["modules_affected"]:
        module_name = module["module_name"]
        module_path = module["module_path"]
        functions = set(module.get("functions_affected", []))

        modules[module_name] = ModuleData(
            path=module_path,
            functions=functions,
        )

    return ProjectData(
        father=project_name,
        name=full_name,
        version=entry_data["fixed_commit"],
        repo_url=entry_data["github_url"],
        modules=modules,
        project_path=full_name,
        dependencies=entry_data.get("dependencies", []),
        buggy_commit=entry_data["buggy_commit"],
    )


def write_project_xml(
    project_data: dict[str, ProjectData],
    output_dir: Path,
    skipped_dependency_failures: int,
    limit: int = 100,
) -> None:
    """Writes XML and metadata files."""

    xml_file = output_dir / "experiment.xml"
    metadata_file = output_dir / "metadata.json"

    output_dir.mkdir(parents=True, exist_ok=True)

    projects = ET.Element(
        "projects",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "https://www.pynguin.eu/projects.xsd",
        },
    )

    # -------------------------
    # Metadata counters
    # -------------------------
    bug_count = 0
    module_count = 0

    bugs_per_project = {}
    modules_per_project = {}
    modules_per_bug = {}

    # -------------------------
    # Main loop
    # -------------------------
    for _, data in project_data.items():
        # limit counts only usable bugs
        if bug_count >= limit and limit != -1:
            break

        bug_id = data.name
        project_name = data.father

        bug_count += 1

        bugs_per_project[project_name] = bugs_per_project.get(project_name, 0) + 1

        project = ET.SubElement(projects, "project")

        ET.SubElement(project, "name").text = data.name
        ET.SubElement(project, "version").text = data.version
        ET.SubElement(project, "buggy_commit").text = data.buggy_commit
        ET.SubElement(project, "repository").text = data.repo_url

        source_path = data.project_path.removeprefix("repos/")
        ET.SubElement(
            project,
            "sources",
        ).text = f"kd-projects/{source_path}"

        modules_el = ET.SubElement(project, "modules")

        modules_in_this_bug = 0

        for module_name in sorted(data.modules):
            module_count += 1
            modules_in_this_bug += 1

            modules_per_project[project_name] = (
                modules_per_project.get(project_name, 0) + 1
            )

            mod = ET.SubElement(modules_el, "module")

            module_data = data.modules[module_name]

            ET.SubElement(mod, "name").text = module_name
            ET.SubElement(mod, "path").text = module_data.path

            for func_name in sorted(module_data.functions):
                ET.SubElement(mod, "only_cover").text = func_name

            for dep in sorted(set(data.dependencies)):
                ET.SubElement(mod, "dependency").text = dep

        modules_per_bug[bug_id] = modules_in_this_bug

    # -------------------------
    # Write XML
    # -------------------------
    tree = ET.ElementTree(projects)
    ET.indent(tree, space="  ", level=0)
    tree.write(xml_file, encoding="utf-8", xml_declaration=True)

    # -------------------------
    # Write metadata
    # -------------------------
    metadata = {
        "total_projects": len(bugs_per_project),
        "total_bugs": bug_count,
        "total_modules": module_count,
        "skipped_dependency_failures": skipped_dependency_failures,
        "avg_bugs_per_project": (
            bug_count / len(bugs_per_project) if bugs_per_project else 0
        ),
        "avg_modules_per_bug": (module_count / bug_count if bug_count else 0),
        "bugs_per_project": bugs_per_project,
        "modules_per_project": modules_per_project,
        "modules_per_bug": modules_per_bug,
    }

    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)

    print(f"Written XML to {xml_file} and metadata to {metadata_file}")


def validate_argv(argv):
    if len(argv) > 2:
        print("Usage: poetry run python src/build_experiment/main.py [limit]")
        exit(1)
    if len(argv) == 1:
        print("No limit provided, defaulting to -1 (use all).")
        return -1  # Default limit

    try:
        limit = int(argv[1])
        if limit <= 0 and limit != -1:
            print("Limit must be a positive integer or -1 (use all).")
            exit(1)
        if limit > 1000:
            print("Limit exceeds the maximum allowed value of 1000.")
            exit(1)
        return limit
    except ValueError:
        print("Invalid limit. Please provide a valid integer.")
        exit(1)


def main():
    limit = validate_argv(argv)

    input_dir = ensure_directory(DATA_DIR / "build_bugs" / "output")
    bugs_file = input_dir / "bugs.json"

    output_dir = ensure_directory(DATA_DIR / "build_experiment" / "output")

    with open(bugs_file, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    projects = {}
    skipped_dependency_failures = 0

    for entry_key, entry_data in raw_data.items():
        if entry_data["dependency_status"] != "success":
            skipped_dependency_failures += 1
            continue

        project = convert_entry_to_project_data(
            entry_key,
            entry_data,
        )
        projects[project.name] = project

    # write only the first <limit> projects to avoid overloading the experiment
    # set limit=-1 to write all
    write_project_xml(
        projects,
        output_dir,
        skipped_dependency_failures,
        limit=limit,
    )


if __name__ == "__main__":
    main()
