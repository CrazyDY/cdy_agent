from __future__ import annotations

import re
from pathlib import Path

import yaml


from .models import DiscoveredSkill, SkillDiagnostic, SkillDiscovery, SkillMetadata
from ..tools.filesystem import resolve_workspace

MAX_SKILL_BYTES = 256 * 1024
MAX_TOOLS_BYTES = 1024 * 1024
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class InvalidSkillError(ValueError):
    pass


class _SkillMetadataLoader(yaml.SafeLoader):
    pass


def _construct_mapping_without_duplicates(
    loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False
) -> dict[object, object]:
    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(
            None, None, "expected a mapping node", node.start_mark
        )
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_SkillMetadataLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_without_duplicates,
)


def discover_skills(workspace: Path) -> SkillDiscovery:
    workspace = resolve_workspace(workspace)
    root = workspace / ".cdy-agent" / "skills"
    try:
        root.lstat()
    except FileNotFoundError:
        return SkillDiscovery((), ())
    except OSError:
        diagnostic = SkillDiagnostic(
            "skills", "invalid_skills_root", "Skills root is invalid."
        )
        return SkillDiscovery((), (diagnostic,))
    try:
        _require_safe(root, workspace, directory=True)
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except (InvalidSkillError, OSError):
        diagnostic = SkillDiagnostic(
            "skills", "invalid_skills_root", "Skills root is invalid."
        )
        return SkillDiscovery((), (diagnostic,))

    skills: list[DiscoveredSkill] = []
    diagnostics: list[SkillDiagnostic] = []
    for entry in entries:
        try:
            skills.append(_load_entry(entry, workspace))
        except (InvalidSkillError, OSError, UnicodeDecodeError) as error:
            diagnostics.append(SkillDiagnostic(entry.name, "invalid_skill", str(error)))
    return SkillDiscovery(tuple(skills), tuple(diagnostics))


def revalidate_tools_file(skill: DiscoveredSkill, workspace: Path) -> None:
    if skill.tools_path is None:
        return
    try:
        resolved_workspace = resolve_workspace(workspace)
    except ValueError as error:
        raise InvalidSkillError("Workspace is invalid.") from error
    _require_safe(skill.tools_path, resolved_workspace, directory=False)
    if skill.tools_path.stat().st_size > MAX_TOOLS_BYTES:
        raise InvalidSkillError("tools.py exceeds 1 MiB.")


def _load_entry(directory: Path, workspace: Path) -> DiscoveredSkill:
    _require_safe(directory, workspace, directory=True)
    skill_file = directory / "SKILL.md"
    _require_safe(skill_file, workspace, directory=False)
    raw = skill_file.read_bytes()
    if len(raw) > MAX_SKILL_BYTES:
        raise InvalidSkillError("SKILL.md exceeds 256 KiB.")
    metadata, instructions = _parse_skill(raw.decode("utf-8"))
    if metadata.name != directory.name:
        raise InvalidSkillError("Skill name must match its directory.")
    tools_path = directory / "tools.py"
    if tools_path.exists() or tools_path.is_symlink():
        _require_safe(tools_path, workspace, directory=False)
        if tools_path.stat().st_size > MAX_TOOLS_BYTES:
            raise InvalidSkillError("tools.py exceeds 1 MiB.")
    else:
        tools_path = None
    return DiscoveredSkill(metadata, directory.resolve(), instructions, tools_path)


def _require_safe(path: Path, workspace: Path, *, directory: bool) -> None:
    if path.is_symlink():
        raise InvalidSkillError("Skill paths must not be symbolic links.")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise InvalidSkillError("Skill path is outside the workspace.") from error
    if directory and not resolved.is_dir():
        raise InvalidSkillError("Expected a directory.")
    if not directory and not resolved.is_file():
        raise InvalidSkillError("Expected a regular file.")


def _parse_skill(text: str) -> tuple[SkillMetadata, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise InvalidSkillError("SKILL.md must start with metadata.")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise InvalidSkillError("SKILL.md metadata is not closed.") from error
    try:
        values = yaml.load(
            "\n".join(lines[1:closing]),
            Loader=_SkillMetadataLoader,
        )
    except yaml.YAMLError as error:
        raise InvalidSkillError("SKILL.md metadata is invalid YAML.") from error
    if (
        not isinstance(values, dict)
        or not all(isinstance(key, str) for key in values)
        or set(values) != {"name", "description"}
    ):
        raise InvalidSkillError("name and description are required.")
    name = values["name"]
    description = values["description"]
    if not isinstance(name, str) or not isinstance(description, str):
        raise InvalidSkillError("Metadata fields must be strings.")
    name = name.strip()
    description = description.strip()
    if NAME_PATTERN.fullmatch(name) is None:
        raise InvalidSkillError("Skill name is invalid.")
    if not description or len(description) > 500:
        raise InvalidSkillError("Skill description must be 1 to 500 characters.")
    instructions = "\n".join(lines[closing + 1 :]).strip()
    if not instructions:
        raise InvalidSkillError("Skill instructions must not be empty.")
    return SkillMetadata(name, description), instructions
