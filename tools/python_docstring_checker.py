#!/usr/bin/env python3
"""Check Python source files for Google-style docstrings.

Usage:
    python_docstring_checker.py [options] PATH [PATH ...]

Exit codes:
    0  All files pass.
    1  One or more violations found, or no Python files found.
"""

import ast
import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

_KNOWN_SECTIONS = [
    "Args", "Arguments",
    "Returns", "Return",
    "Raises",
    "Yields", "Yield",
    "Attributes",
    "Note", "Notes",
    "Example", "Examples",
    "References",
    "See Also",
    "Todo",
]

_SECTION_NORMALIZE = {
    "Arguments": "Args",
    "Return": "Returns",
    "Yield": "Yields",
}

_SECTION_RE = re.compile(
    r"^(" + "|".join(re.escape(s) for s in _KNOWN_SECTIONS) + r"):\s*$",
    re.MULTILINE,
)

# Matches an item entry line with exactly 4 spaces of indent:
#   name (optional_type): description
_ARG_ENTRY_RE = re.compile(r"^ {4}(\*{0,2}\w+)\s*(?:\([^)]*\))?\s*:\s*\S")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """Runtime configuration built from CLI arguments.

    Attributes:
        ignore_private: Skip functions whose names start with a single underscore.
        ignore_magic: Skip __dunder__ methods.
        ignore_raises: Do not require a Raises: section.
        ignore_attributes: Do not require an Attributes: section on classes.
        no_module_docstring: Do not require module-level docstrings.
        summary_only: Only require that a docstring exists; skip section checks.
    """

    ignore_private: bool = False
    ignore_magic: bool = False
    ignore_raises: bool = False
    ignore_attributes: bool = False
    no_module_docstring: bool = False
    summary_only: bool = False


@dataclass
class ParsedDocstring:
    """Structured representation of a parsed Google-style docstring.

    Attributes:
        summary: The first-line one-liner summary.
        blank_after_summary: True if the line after the summary is blank (or absent).
        sections: Mapping of normalised section name to a list of documented item names.
    """

    summary: str
    blank_after_summary: bool
    sections: dict = field(default_factory=dict)


@dataclass
class Violation:
    """A single docstring violation found in a source file.

    Attributes:
        filepath: Absolute or relative path to the source file.
        line: Line number of the def/class keyword.
        qualified_name: Dot-separated qualified name (e.g. MyClass.my_method).
        kind: One of 'module', 'class', 'function', 'method'.
        issues: Human-readable list of problems found.
    """

    filepath: str
    line: int
    qualified_name: str
    kind: str
    issues: list

    def format(self) -> str:
        """Return a formatted string representation of this violation.

        Returns:
            Multi-line string with header and bulleted issues.
        """
        header = f"{self.filepath}:{self.line}: [{self.kind}] {self.qualified_name}"
        bullets = "\n".join(f"  - {issue}" for issue in self.issues)
        return f"{header}\n{bullets}"


# ---------------------------------------------------------------------------
# Google docstring parser
# ---------------------------------------------------------------------------

class GoogleDocstringParser:
    """Parser for Google-style docstrings."""

    @staticmethod
    def parse(docstring: str) -> ParsedDocstring:
        """Parse a cleaned docstring string into a structured form.

        Args:
            docstring: The docstring text as returned by ast.get_docstring(clean=True).

        Returns:
            A ParsedDocstring with summary, blank_after_summary, and sections filled in.
        """
        lines = docstring.split("\n")
        summary = lines[0].strip()

        if len(lines) < 2:
            blank_after_summary = True
        else:
            blank_after_summary = not lines[1].strip()

        sections: dict = {}
        current_section = None
        current_items: list = []

        for line in lines[1:]:
            section_match = _SECTION_RE.match(line)
            if section_match:
                if current_section is not None:
                    normalized = _SECTION_NORMALIZE.get(current_section, current_section)
                    sections[normalized] = current_items
                current_section = section_match.group(1)
                current_items = []
            elif current_section:
                item_match = _ARG_ENTRY_RE.match(line)
                if item_match:
                    raw_name = item_match.group(1)
                    current_items.append(raw_name.lstrip("*"))

        if current_section is not None:
            normalized = _SECTION_NORMALIZE.get(current_section, current_section)
            sections[normalized] = current_items

        return ParsedDocstring(
            summary=summary,
            blank_after_summary=blank_after_summary,
            sections=sections,
        )


# ---------------------------------------------------------------------------
# Signature analysis helpers
# ---------------------------------------------------------------------------

def _direct_walk(node: ast.AST, node_types, _is_root: bool = True):
    """Walk an AST node without descending into nested function definitions.

    Args:
        node: The AST node to walk.
        node_types: A type or tuple of types to match.
        _is_root: Internal flag; callers should not set this.

    Yields:
        Matching AST nodes that are direct descendants (not inside nested functions).
    """
    if not _is_root and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return
    if isinstance(node, node_types):
        yield node
    for child in ast.iter_child_nodes(node):
        yield from _direct_walk(child, node_types, _is_root=False)


def get_decorator_names(fn_node: ast.AST) -> set:
    """Return the set of decorator names applied to a function or class node.

    Args:
        fn_node: An ast.FunctionDef, ast.AsyncFunctionDef, or ast.ClassDef node.

    Returns:
        Set of decorator name strings (e.g. {'staticmethod', 'property'}).
    """
    names = set()
    for dec in fn_node.decorator_list:
        if isinstance(dec, ast.Name):
            names.add(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.add(dec.attr)
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                names.add(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                names.add(dec.func.attr)
    return names


def is_private(name: str) -> bool:
    """Return True if name starts with exactly one underscore (not two).

    Args:
        name: The function or method name string.

    Returns:
        True for _private style names; False otherwise.
    """
    return name.startswith("_") and not name.startswith("__")


def is_magic(name: str) -> bool:
    """Return True if name is a dunder method (__name__).

    Args:
        name: The function or method name string.

    Returns:
        True for __dunder__ style names; False otherwise.
    """
    return name.startswith("__") and name.endswith("__")


def get_required_params(
    fn_node: ast.AST,
    is_method: bool,
    is_static: bool,
    is_classmethod: bool,
) -> list:
    """Return list of parameter names that should appear in the Args: section.

    Args:
        fn_node: An ast.FunctionDef or ast.AsyncFunctionDef node.
        is_method: True if the function is defined inside a class.
        is_static: True if decorated with @staticmethod.
        is_classmethod: True if decorated with @classmethod.

    Returns:
        Ordered list of parameter name strings, excluding self/cls where appropriate.
    """
    args_obj = fn_node.args
    all_positional = list(getattr(args_obj, "posonlyargs", [])) + list(args_obj.args)

    if is_method and not is_static:
        all_positional = all_positional[1:]

    names = [a.arg for a in all_positional]
    names += [a.arg for a in args_obj.kwonlyargs]
    if args_obj.vararg:
        names.append(args_obj.vararg.arg)
    if args_obj.kwarg:
        names.append(args_obj.kwarg.arg)
    return names


def returns_non_none(fn_node: ast.AST) -> bool:
    """Return True if the function likely returns a non-None value.

    Args:
        fn_node: An ast.FunctionDef or ast.AsyncFunctionDef node.

    Returns:
        True if a Returns: section should be required.
    """
    ret_ann = fn_node.returns
    if ret_ann is not None:
        if isinstance(ret_ann, ast.Constant) and ret_ann.value is None:
            return False
        if isinstance(ret_ann, ast.Name) and ret_ann.id == "None":
            return False
        return True

    for ret_node in _direct_walk(fn_node, ast.Return):
        if ret_node.value is not None:
            if not (isinstance(ret_node.value, ast.Constant) and ret_node.value.value is None):
                return True
    return False


def has_raises(fn_node: ast.AST) -> bool:
    """Return True if the function raises at least one named exception.

    Bare re-raise statements (raise with no argument) are excluded.

    Args:
        fn_node: An ast.FunctionDef or ast.AsyncFunctionDef node.

    Returns:
        True if a Raises: section should be required.
    """
    for raise_node in _direct_walk(fn_node, ast.Raise):
        if raise_node.exc is not None:
            return True
    return False


def has_yields(fn_node: ast.AST) -> bool:
    """Return True if the function contains yield or yield from statements.

    Args:
        fn_node: An ast.FunctionDef or ast.AsyncFunctionDef node.

    Returns:
        True if a Yields: section should be required instead of Returns:.
    """
    for _ in _direct_walk(fn_node, (ast.Yield, ast.YieldFrom)):
        return True
    return False


def _is_self_attr(node: ast.AST) -> bool:
    """Return True if node represents self.something.

    Args:
        node: An AST node to inspect.

    Returns:
        True if this is an attribute access on 'self'.
    """
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def get_class_attributes(class_node: ast.ClassDef) -> set:
    """Collect public attribute names from a class definition.

    Looks at class-level annotated assignments and self.x assignments in __init__.

    Args:
        class_node: An ast.ClassDef node.

    Returns:
        Set of public attribute name strings (not starting with underscore).
    """
    attrs: set = set()

    for item in class_node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            name = item.target.id
            if not name.startswith("_"):
                attrs.add(name)

    for item in class_node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            for stmt in ast.walk(item):
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if _is_self_attr(target) and not target.attr.startswith("_"):
                            attrs.add(target.attr)
                if isinstance(stmt, ast.AnnAssign):
                    if _is_self_attr(stmt.target) and not stmt.target.attr.startswith("_"):
                        attrs.add(stmt.target.attr)

    return attrs


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------

def _validate_common(docstring, config: Config):
    """Validate common docstring requirements shared by all node kinds.

    Args:
        docstring: The raw docstring string, or None if absent.
        config: Active Config instance.

    Returns:
        Tuple of (ParsedDocstring or None, list of issue strings).
    """
    if docstring is None:
        return None, ["Missing docstring"]

    parsed = GoogleDocstringParser.parse(docstring)

    issues = []
    if not parsed.summary:
        issues.append("First line is blank; a one-line summary is required on the first line")

    return parsed, issues


def validate_function_docstring(
    docstring,
    required_params: list,
    check_returns: bool,
    check_raises: bool,
    returns_section_name: str,
    config: Config,
) -> list:
    """Validate a function or method docstring against Google style rules.

    Args:
        docstring: The raw docstring text, or None if absent.
        required_params: List of parameter names that must appear in Args:.
        check_returns: True if a Returns: or Yields: section is required.
        check_raises: True if a Raises: section is required.
        returns_section_name: Either 'Returns' or 'Yields'.
        config: Active Config instance.

    Returns:
        List of human-readable issue strings (empty if compliant).
    """
    parsed, issues = _validate_common(docstring, config)
    if parsed is None or config.summary_only:
        return issues

    if required_params:
        if "Args" not in parsed.sections:
            issues.append(
                f"Missing Args: section; parameters not documented: {', '.join(required_params)}"
            )
        else:
            documented = set(parsed.sections["Args"])
            undocumented = [p for p in required_params if p not in documented]
            if undocumented:
                issues.append(
                    f"Parameter(s) not documented in Args: section: {', '.join(undocumented)}"
                )
            extra = documented - set(required_params)
            if extra:
                issues.append(
                    f"Args: section documents non-existent parameter(s): {', '.join(sorted(extra))}"
                )

    if check_returns:
        has_section = (
            returns_section_name in parsed.sections
            or "Returns" in parsed.sections
            or "Yields" in parsed.sections
        )
        if not has_section:
            issues.append(
                f"Missing {returns_section_name}: section (function returns a non-None value)"
            )

    if check_raises:
        if "Raises" not in parsed.sections:
            issues.append("Missing Raises: section (function raises exceptions)")

    return issues


def validate_class_docstring(
    docstring,
    attrs: set,
    config: Config,
) -> list:
    """Validate a class docstring against Google style rules.

    Args:
        docstring: The raw docstring text, or None if absent.
        attrs: Set of public attribute names declared in the class.
        config: Active Config instance.

    Returns:
        List of human-readable issue strings (empty if compliant).
    """
    parsed, issues = _validate_common(docstring, config)
    if parsed is None or config.summary_only or config.ignore_attributes:
        return issues

    if attrs:
        if "Attributes" not in parsed.sections:
            issues.append(
                f"Missing Attributes: section; class has attributes: {', '.join(sorted(attrs))}"
            )
        else:
            documented = set(parsed.sections["Attributes"])
            undocumented = attrs - documented
            if undocumented:
                issues.append(
                    f"Attribute(s) not documented in Attributes: section: "
                    f"{', '.join(sorted(undocumented))}"
                )

    return issues


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------

class DocstringVisitor(ast.NodeVisitor):
    """AST visitor that checks every module, class, and function for docstrings.

    Attributes:
        violations: Accumulated list of Violation instances found during traversal.
        filepath: Path to the file being visited (used in Violation records).
        config: Active Config instance.
    """

    def __init__(self, filepath: str, config: Config) -> None:
        """Initialise the visitor.

        Args:
            filepath: Path string of the file being checked.
            config: Active Config instance controlling validation strictness.
        """
        self.violations: list = []
        self.filepath = filepath
        self.config = config
        self._scope_stack: list = []
        self._class_stack: list = []

    def _qualified_name(self) -> str:
        """Return the current dot-separated qualified name from the scope stack.

        Returns:
            Dot-joined scope stack string, e.g. 'MyClass.my_method'.
        """
        return ".".join(self._scope_stack)

    def visit_Module(self, node: ast.Module) -> None:
        """Check the module-level docstring.

        Args:
            node: The ast.Module node representing the file.
        """
        if not self.config.no_module_docstring:
            docstring = ast.get_docstring(node, clean=True)
            lineno = node.body[0].lineno if node.body else 1
            parsed, issues = _validate_common(docstring, self.config)
            if issues:
                self.violations.append(
                    Violation(self.filepath, lineno, "<module>", "module", issues)
                )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Check a class docstring and then recurse into its body.

        Args:
            node: The ast.ClassDef node.
        """
        self._scope_stack.append(node.name)
        self._class_stack.append(node)

        docstring = ast.get_docstring(node, clean=True)
        attrs = get_class_attributes(node)
        issues = validate_class_docstring(docstring, attrs, self.config)
        if issues:
            self.violations.append(
                Violation(self.filepath, node.lineno, self._qualified_name(), "class", issues)
            )

        self.generic_visit(node)
        self._class_stack.pop()
        self._scope_stack.pop()

    def _visit_function(self, node) -> None:
        """Check a function or method docstring.

        Args:
            node: An ast.FunctionDef or ast.AsyncFunctionDef node.
        """
        name = node.name

        if self.config.ignore_private and is_private(name):
            return
        if self.config.ignore_magic and is_magic(name):
            return

        is_method = bool(self._class_stack)
        decs = get_decorator_names(node)
        is_static = "staticmethod" in decs
        is_classmethod_dec = "classmethod" in decs
        is_property = bool({"property", "setter", "deleter", "getter"} & decs)
        is_overload = "overload" in decs

        if is_overload:
            return

        self._scope_stack.append(name)
        kind = "method" if is_method else "function"

        if is_property:
            required_params = []
            check_ret = False
            check_raises_flag = False
            returns_name = "Returns"
        else:
            required_params = get_required_params(node, is_method, is_static, is_classmethod_dec)
            fn_yields = has_yields(node)
            fn_returns = returns_non_none(node)
            check_ret = fn_returns or fn_yields
            check_raises_flag = has_raises(node) and not self.config.ignore_raises
            returns_name = "Yields" if fn_yields and not fn_returns else "Returns"

        docstring = ast.get_docstring(node, clean=True)
        issues = validate_function_docstring(
            docstring,
            required_params,
            check_ret,
            check_raises_flag,
            returns_name,
            self.config,
        )
        if issues:
            self.violations.append(
                Violation(self.filepath, node.lineno, self._qualified_name(), kind, issues)
            )

        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Dispatch to _visit_function for synchronous functions.

        Args:
            node: The ast.FunctionDef node.
        """
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Dispatch to _visit_function for async functions.

        Args:
            node: The ast.AsyncFunctionDef node.
        """
        self._visit_function(node)


# ---------------------------------------------------------------------------
# File & path utilities
# ---------------------------------------------------------------------------

def collect_python_files(paths: list) -> list:
    """Expand a list of file/directory paths into a sorted list of .py files.

    Args:
        paths: List of path strings provided on the command line.

    Returns:
        Sorted list of Path objects pointing to .py files.
    """
    result = []
    for p_str in paths:
        p = Path(p_str)
        if not p.exists():
            print(f"Warning: {p} does not exist, skipping", file=sys.stderr)
        elif p.is_file():
            if p.suffix == ".py":
                result.append(p)
            else:
                print(f"Warning: {p} is not a .py file, skipping", file=sys.stderr)
        elif p.is_dir():
            result.extend(sorted(p.rglob("*.py")))
    return result


def check_file(filepath: Path, config: Config) -> list:
    """Parse and check a single Python source file for docstring violations.

    Args:
        filepath: Path to the .py file to check.
        config: Active Config instance.

    Returns:
        List of Violation instances found in the file.
    """
    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Warning: cannot read {filepath}: {exc}", file=sys.stderr)
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as exc:
        print(
            f"Warning: syntax error in {filepath}:{exc.lineno}: {exc.msg}",
            file=sys.stderr,
        )
        return []

    visitor = DocstringVisitor(str(filepath), config)
    visitor.visit(tree)
    return visitor.violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the CLI.

    Returns:
        Configured argparse.ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="python_docstring_checker.py",
        description="Check Python source files for Google-style docstrings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s src/\n"
            "  %(prog)s --ignore-magic --ignore-raises mymodule.py\n"
            "  %(prog)s --summary-only src/ tests/"
        ),
    )
    parser.add_argument(
        "paths",
        metavar="PATH",
        nargs="+",
        help="File or directory paths to check (.py files; directories are searched recursively)",
    )
    parser.add_argument(
        "--ignore-private",
        action="store_true",
        help="Skip functions/methods whose names start with a single underscore",
    )
    parser.add_argument(
        "--ignore-magic",
        action="store_true",
        help="Skip __dunder__ methods such as __init__, __repr__, etc.",
    )
    parser.add_argument(
        "--ignore-raises",
        action="store_true",
        help="Do not require a Raises: section for functions that raise exceptions",
    )
    parser.add_argument(
        "--ignore-attributes",
        action="store_true",
        help="Do not require an Attributes: section on class docstrings",
    )
    parser.add_argument(
        "--no-module-docstring",
        action="store_true",
        help="Do not require module-level docstrings",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only verify that a docstring exists; skip Args/Returns/Raises section checks",
    )
    return parser


def main() -> None:
    """Entry point: parse arguments, check files, report violations, and exit.

    Returns:
        Does not return; calls sys.exit(0) or sys.exit(1).
    """
    args = build_arg_parser().parse_args()
    config = Config(
        ignore_private=args.ignore_private,
        ignore_magic=args.ignore_magic,
        ignore_raises=args.ignore_raises,
        ignore_attributes=args.ignore_attributes,
        no_module_docstring=args.no_module_docstring,
        summary_only=args.summary_only,
    )

    files = collect_python_files(args.paths)
    if not files:
        print("No Python files found.", file=sys.stderr)
        sys.exit(1)

    all_violations: list = []
    for filepath in files:
        all_violations.extend(check_file(filepath, config))

    if all_violations:
        for v in all_violations:
            print(v.format())
            print()
        file_count = len({v.filepath for v in all_violations})
        print(
            f"{len(all_violations)} violation(s) found across {file_count} file(s)."
        )
        sys.exit(1)
    else:
        print(f"All OK. {len(files)} file(s) checked, no violations found.")
        sys.exit(0)


if __name__ == "__main__":
    main()
