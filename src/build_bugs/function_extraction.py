import ast

from git import Diff, Repo


class _FunctionRangeExtractor(ast.NodeVisitor):
    def __init__(self):
        self.functions = []
        self.class_stack = []

    def visit_ClassDef(self, node):
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node):
        if self.class_stack:
            name = f"{self.class_stack[-1]}.{node.name}"
        else:
            name = node.name

        self.functions.append(
            {
                "name": name,
                "start": node.lineno,
                "end": getattr(node, "end_lineno", node.lineno),
            }
        )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        if self.class_stack:
            name = f"{self.class_stack[-1]}.{node.name}"
        else:
            name = node.name

        self.functions.append(
            {
                "name": name,
                "start": node.lineno,
                "end": getattr(node, "end_lineno", node.lineno),
            }
        )
        self.generic_visit(node)


def _build_function_index(source: str):
    """
    Parse AST and return list of function name + line ranges.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    extractor = _FunctionRangeExtractor()
    extractor.visit(tree)
    return extractor.functions


def _parse_changed_lines(diff: Diff) -> set[int]:
    """
    Extract added/modified line numbers from unified diff.
    """
    changed_lines = set()

    if not diff.diff:
        return changed_lines

    raw = diff.diff
    if isinstance(raw, (bytes, bytearray, memoryview)):
        diff_text = bytes(raw).decode("utf-8", errors="ignore")
    else:
        diff_text = raw

    current_line = None

    for line in diff_text.splitlines():
        # Hunk header: @@ -a,b +c,d @@
        if line.startswith("@@"):
            try:
                plus_part = line.split("+")[1]
                plus_line = plus_part.split(",")[0].split(" ")[0]
                current_line = int(plus_line)
            except Exception:
                current_line = None
            continue

        if current_line is None:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            changed_lines.add(current_line)
            current_line += 1
        elif not line.startswith("-"):
            current_line += 1

    return changed_lines


def _get_source_from_repo(repo: Repo, path: str, commit):
    try:
        blob = commit.tree / path
        return blob.data_stream.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def extract_affected_functions(
    repo: Repo, diff: Diff, buggy_commit, fix_commit
) -> set[str]:
    """
    Return the set of public function names whose line ranges intersect
    with changed lines.
    """

    if not diff.b_path:
        return set()

    # Load old and new source
    old_source = _get_source_from_repo(repo, diff.b_path, buggy_commit)
    new_source = _get_source_from_repo(repo, diff.b_path, fix_commit)

    if not new_source:
        return set()

    # Build function index from the *new* version
    functions = _build_function_index(new_source)

    # Determine changed lines
    changed_lines = _parse_changed_lines(diff)

    affected = set()

    for fn in functions:
        if any(fn["start"] <= line <= fn["end"] for line in changed_lines):
            # Ignore private functions
            if not fn["name"].startswith("_") and "._" not in fn["name"]:
                affected.add(fn["name"])

    return affected
