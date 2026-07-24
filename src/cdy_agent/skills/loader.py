from __future__ import annotations

import re
from pathlib import Path
from types import MappingProxyType

import yaml


from .models import (
    DiscoveredSkill,
    ResourceCategory,
    SkillDiagnostic,
    SkillDiscovery,
    SkillMetadata,
    SkillResource,
    _ResourceIdentity,
)
from ..tools.filesystem import resolve_workspace

MAX_SKILL_BYTES = 256 * 1024
MAX_RESOURCES = 512
RESOURCE_CATEGORIES = ("scripts", "references", "assets")
NAME_PATTERN = re.compile(
    r"(?=.{1,64}\Z)[a-z0-9]+(?:-[a-z0-9]+)*\Z"
)
FRONTMATTER_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}


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
    resolved_directory = directory.resolve()
    resources = _discover_resources(resolved_directory, workspace)
    return DiscoveredSkill(
        metadata, resolved_directory, instructions, resources
    )


def _discover_resources(
    directory: Path, workspace: Path
) -> tuple[SkillResource, ...]:
    resources: list[SkillResource] = []
    for category in RESOURCE_CATEGORIES:
        category_root = directory / category
        try:
            category_root.lstat()
        except FileNotFoundError:
            continue
        _require_safe(category_root, workspace, directory=True)
        pending = [category_root]
        while pending:
            current = pending.pop()
            _require_safe(current, workspace, directory=True)
            for path in sorted(
                current.iterdir(), key=lambda item: item.name, reverse=True
            ):
                if path.is_symlink():
                    raise InvalidSkillError(
                        "Skill paths must not be symbolic links."
                    )
                if path.is_dir():
                    _require_safe(path, workspace, directory=True)
                    _require_within(path, directory)
                    pending.append(path)
                    continue
                if not path.is_file():
                    raise InvalidSkillError(
                        "Skill resources must be regular files."
                    )
                _require_safe(path, workspace, directory=False)
                _require_within(path, directory)
                if len(resources) >= MAX_RESOURCES:
                    raise InvalidSkillError(
                        "Skill contains more than 512 resources."
                    )
                identity = _resource_identity(path)
                resources.append(
                    SkillResource(
                        category=category,
                        relative_path=path.relative_to(directory).as_posix(),
                        path=path.resolve(),
                        size=identity.size,
                        _identity=identity,
                    )
                )
    return tuple(
        sorted(
            resources,
            key=lambda resource: (
                resource.category,
                resource.relative_path,
            ),
        )
    )


def revalidate_skill(skill: DiscoveredSkill, workspace: Path) -> None:
    try:
        resolved_workspace = resolve_workspace(workspace)
    except ValueError as error:
        raise InvalidSkillError("Workspace is invalid.") from error
    _require_safe(skill.directory, resolved_workspace, directory=True)
    skill_file = skill.directory / "SKILL.md"
    _require_safe(skill_file, resolved_workspace, directory=False)
    if skill_file.stat().st_size > MAX_SKILL_BYTES:
        raise InvalidSkillError("SKILL.md exceeds 256 KiB.")


def revalidate_resource(
    skill: DiscoveredSkill,
    resource: SkillResource,
    workspace: Path,
    *,
    expected_category: ResourceCategory | None = None,
) -> Path:
    try:
        resolved_workspace = resolve_workspace(workspace)
    except ValueError as error:
        raise InvalidSkillError("Workspace is invalid.") from error
    if resource.category not in RESOURCE_CATEGORIES:
        raise InvalidSkillError("Resource category is invalid.")
    if (
        expected_category is not None
        and resource.category != expected_category
    ):
        raise InvalidSkillError("Resource category is not allowed.")
    revalidate_skill(skill, resolved_workspace)
    category_root = skill.directory / resource.category
    _require_safe(category_root, resolved_workspace, directory=True)
    _require_safe(resource.path, resolved_workspace, directory=False)
    resolved = resource.path.resolve(strict=True)
    try:
        resolved.relative_to(category_root.resolve(strict=True))
    except ValueError as error:
        raise InvalidSkillError(
            "Skill resource is outside its category."
        ) from error
    if _resource_identity(resolved) != resource._identity:
        raise InvalidSkillError("Skill resource changed.")
    return resolved


def _resource_identity(path: Path) -> _ResourceIdentity:
    status = path.stat()
    return _ResourceIdentity(
        device=status.st_dev,
        inode=status.st_ino,
        size=status.st_size,
        modified_ns=status.st_mtime_ns,
        metadata_changed_ns=status.st_ctime_ns,
    )


def _require_within(path: Path, directory: Path) -> None:
    try:
        path.resolve(strict=True).relative_to(directory)
    except ValueError as error:
        raise InvalidSkillError(
            "Skill resource is outside its Skill directory."
        ) from error


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
    if not isinstance(values, dict) or not all(
        isinstance(key, str) for key in values
    ):
        raise InvalidSkillError("name and description are required.")
    fields = set(values)
    if not {"name", "description"}.issubset(fields):
        raise InvalidSkillError("name and description are required.")
    if not fields.issubset(FRONTMATTER_FIELDS):
        raise InvalidSkillError("Unknown Skill metadata fields are not allowed.")
    name = values["name"]
    description = values["description"]
    if not isinstance(name, str) or not isinstance(description, str):
        raise InvalidSkillError("Metadata fields must be strings.")
    name = name.strip()
    description = description.strip()
    if NAME_PATTERN.fullmatch(name) is None:
        raise InvalidSkillError("Skill name is invalid.")
    if not description or len(description) > 1024:
        raise InvalidSkillError("Skill description must be 1 to 1024 characters.")

    license_value = _optional_string(values, "license")
    compatibility = _optional_string(values, "compatibility")
    if compatibility is not None and len(compatibility) > 500:
        raise InvalidSkillError(
            "Skill compatibility must be 1 to 500 characters."
        )
    allowed_tools = _optional_string(values, "allowed-tools")
    metadata = values.get("metadata", {})
    if not isinstance(metadata, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in metadata.items()
    ):
        raise InvalidSkillError("Skill metadata must map strings to strings.")

    instructions = "\n".join(lines[closing + 1 :]).strip()
    if not instructions:
        raise InvalidSkillError("Skill instructions must not be empty.")
    return (
        SkillMetadata(
            name=name,
            description=description,
            license=license_value,
            compatibility=compatibility,
            metadata=MappingProxyType(dict(metadata)),
            allowed_tools=allowed_tools,
        ),
        instructions,
    )


def _optional_string(values: dict[object, object], field: str) -> str | None:
    if field not in values:
        return None
    value = values[field]
    if not isinstance(value, str):
        raise InvalidSkillError(f"Skill {field} must be a string.")
    value = value.strip()
    if not value:
        raise InvalidSkillError(f"Skill {field} must not be empty.")
    return value
