from dataclasses import dataclass


@dataclass(frozen=True)
class RepoSnapshot:
    name: str
    github_url: str
    local_path: str

    pinned_commit: str
    default_branch: str | None
    refs: dict[str, str]


def to_dict(snapshot: RepoSnapshot) -> dict:
    return snapshot.__dict__
