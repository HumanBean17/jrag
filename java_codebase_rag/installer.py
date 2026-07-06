"""Interactive installer module for java-codebase-rag.

This module provides the `install` subcommand that walks users through:
1. Java source detection
2. Embedding model selection
3. Agent host selection
4. Scope selection (project/user)
5. Artifact deployment (MCP config, skill, agent)
6. YAML config generation and indexing
"""

import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple

import yaml

Scope = Literal["project", "user"]
Surface = Literal["mcp", "cli"]

# MCP server name constant
_MCP_SERVER_NAME = "java-codebase-rag"

# Marker file written at install time so a CLI-only install (no MCP entry) is
# still visible to ``update``. Lives at the project/source root alongside
# ``.java-codebase-rag.yml``. JSON shape:
#   {"version": 1, "hosts": [{"host": "claude-code", "scope": "project",
#                             "surface": "mcp"|"cli"}, ...]}
_MARKER_FILE_NAME = ".java-codebase-rag.hosts"
_MARKER_FILE_VERSION = 1

# Exit code constants
EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_FATAL = 2


class ArtifactResult(NamedTuple):
    """Result of deploying a single artifact."""

    path: Path
    success: bool
    error: str | None


class ConfiguredHost(NamedTuple):
    """A host installed on this machine: which host, which scope, which surface.

    Replaces the prior 2-tuple ``(HostConfig, scope)`` returned by
    ``detect_configured_hosts`` so ``update`` can route the refresh through the
    correct ``Surface`` (an MCP-surface install refreshes MCP+skill+agent; a
    CLI-surface install refreshes the CLI skill+agent only).
    """

    host: "HostConfig"
    scope: Scope
    surface: Surface


@dataclass(frozen=True)
class HostConfig:
    """Configuration for an agent host."""

    name: str  # "claude-code", "qwen-code", "gigacode"
    dir_name: str  # ".claude", ".qwen", ".gigacode"
    mcp_project: str  # ".mcp.json", ".qwen/settings.json", ".gigacode/settings.json"
    mcp_user: str  # ".claude.json", ".qwen/settings.json", ".gigacode/settings.json"

    def scope_path(self, scope: Scope, cwd: Path) -> Path:
        """Return the host directory for the given scope."""
        if scope == "project":
            return cwd / self.dir_name
        else:  # user
            return Path.home() / self.dir_name

    def mcp_config_path(self, scope: Scope, cwd: Path) -> Path:
        """Return the full path to the MCP config file."""
        if scope == "project":
            return cwd / self.mcp_project
        else:  # user
            return Path.home() / self.mcp_user

    def skills_dir(self, scope: Scope, cwd: Path) -> Path:
        """Return the skills directory path."""
        return self.scope_path(scope, cwd) / "skills"

    def agents_dir(self, scope: Scope, cwd: Path) -> Path:
        """Return the agents directory path."""
        return self.scope_path(scope, cwd) / "agents"


HOSTS: dict[str, HostConfig] = {
    "claude-code": HostConfig(
        name="claude-code",
        dir_name=".claude",
        mcp_project=".mcp.json",
        mcp_user=".claude.json",
    ),
    "qwen-code": HostConfig(
        name="qwen-code",
        dir_name=".qwen",
        mcp_project=".qwen/settings.json",
        mcp_user=".qwen/settings.json",
    ),
    "gigacode": HostConfig(
        name="gigacode",
        dir_name=".gigacode",
        mcp_project=".gigacode/settings.json",
        mcp_user=".gigacode/settings.json",
    ),
}


# ---------------------------------------------------------------------------
# ArtifactManifest — single source of truth for which artifacts each surface
# ships. Iterated by both ``deploy_artifacts`` and ``refresh_artifacts`` so
# adding/removing an artifact is one edit, not two.
#
# Each entry is a 3-tuple ``(kind, package_path, dest_relative)``:
#   - ``kind``: "mcp" dispatches to ``_deploy_mcp_config`` / ``_refresh_mcp_config``
#     (the MCP config path is host/scope-resolved inside those helpers —
#     ``package_path`` and ``dest_relative`` are unused for this kind).
#   - ``kind``: "skill" | "agent" dispatches to ``_deploy_file`` / ``_refresh_file``.
#   - ``package_path``: relative path under ``install_data/``.
#   - ``dest_relative``: relative path under ``host.scope_path(scope, cwd)``.
#
# The ``mcp`` surface carries the MCP config entry; the ``cli`` surface does
# NOT (a CLI install never registers an MCP server).
# ---------------------------------------------------------------------------
ArtifactManifestEntry = tuple[str, str, str]

ARTIFACT_MANIFEST: dict[Surface, list[ArtifactManifestEntry]] = {
    "mcp": [
        ("mcp", "", ""),
        ("skill", "skills/explore-codebase/SKILL.md", "skills/explore-codebase/SKILL.md"),
        ("agent", "agents/explorer-rag-enhanced.md", "agents/explorer-rag-enhanced.md"),
    ],
    "cli": [
        ("skill", "skills/explore-codebase-cli/SKILL.md", "skills/explore-codebase-cli/SKILL.md"),
        ("agent", "agents/explorer-rag-cli.md", "agents/explorer-rag-cli.md"),
    ],
}


def prompt(
    prompt_type: str,
    message: str,
    *,
    choices=None,
    default=None,
) -> list[str] | str | bool:
    """Interactive prompt that dispatches to questionary on TTY, returns default otherwise.

    Args:
        prompt_type: Type of prompt ("checkbox", "select", "text", "confirm")
        message: Prompt message to display
        choices: List of choices (for checkbox/select)
        default: Default value to return when not interactive

    Returns:
        - checkbox: list[str] of selected values
        - select: str of selected value
        - text: str of entered text
        - confirm: bool (True/False)
    """
    if not sys.stdin.isatty():
        return default

    # Lazy import questionary only when needed (TTY)
    import questionary
    from prompt_toolkit.styles import Style

    # Strip default ANSI colors — rely on ●/○ indicators only, no fg/bg highlights
    # noinherit prevents prompt_toolkit from merging in questionary's default fg colors
    no_color_style = Style(
        [
            ("highlighted", "noinherit"),
            ("selected", "noinherit"),
            ("pointer", "noinherit bold"),
        ]
    )

    try:
        if prompt_type == "checkbox":
            return questionary.checkbox(message, choices=choices, style=no_color_style).ask()
        elif prompt_type == "select":
            return questionary.select(message, choices=choices, style=no_color_style).ask()
        elif prompt_type == "text":
            return questionary.text(message, default=default, style=no_color_style).ask()
        elif prompt_type == "confirm":
            return questionary.confirm(message, style=no_color_style).ask()
        else:
            raise ValueError(f"Unknown prompt_type: {prompt_type}")
    except KeyboardInterrupt:
        # User Ctrl+C is a clean abort, not a traceback
        raise SystemExit(2)


def detect_java_directories(source_root: Path) -> list[Path]:
    """Return Maven/Gradle module roots. If root has build file, returns [Path('.')].

    Checks if source_root itself contains a build file (pom.xml, build.gradle, build.gradle.kts).
    If YES: returns [Path(".")] — the entire project is indexed as one unit.
    If NO: scans immediate children for directories containing build files.

    Args:
        source_root: Root directory to scan for Java projects

    Returns:
        List of detected module roots (relative to source_root)

    Raises:
        SystemExit(2): If no build files found in source_root or immediate children
    """
    build_files = ["pom.xml", "build.gradle", "build.gradle.kts"]

    # Check if source_root itself has a build file
    for bf in build_files:
        if (source_root / bf).is_file():
            return [Path(".")]

    # Scan immediate children for build files
    detected = []
    for child in source_root.iterdir():
        if not child.is_dir():
            continue
        # Check if this child directory has a build file
        for bf in build_files:
            if (child / bf).is_file():
                detected.append(Path(child.name))
                break

    if not detected:
        print(f"Error: No Java build files (pom.xml, build.gradle, build.gradle.kts) found in {source_root} or its immediate children.")
        raise SystemExit(2)

    return detected


def confirm_source_root(cwd: Path, *, non_interactive: bool) -> Path:
    """Show cwd as source root, let user accept or change it. Returns resolved source_root.

    Args:
        cwd: Current working directory (default source root)
        non_interactive: If True, return cwd without prompting

    Returns:
        Resolved source root path
    """
    if non_interactive:
        return cwd

    message = f"Source root [{cwd}]:"
    user_input = prompt("text", message, default=str(cwd))

    if not user_input or user_input == str(cwd):
        return cwd

    # Expand ~ and $HOME
    expanded = os.path.expandvars(user_input.strip())
    expanded = os.path.expanduser(expanded)
    result = Path(expanded)

    # Validate path exists and is a directory
    while not result.is_dir():
        print(f"Error: Path {result} does not exist or is not a directory.")
        user_input = prompt("text", "Source root:", default=str(cwd))
        if not user_input or user_input == str(cwd):
            return cwd
        expanded = os.path.expandvars(user_input.strip())
        expanded = os.path.expanduser(expanded)
        result = Path(expanded)

    return result.resolve()


def resolve_model(model_input: str | None, *, non_interactive: bool) -> str:
    """Resolve embedding model path or 'auto'.

    Args:
        model_input: User-provided model path or None
        non_interactive: If True, return "auto" without prompting

    Returns:
        Resolved model string ("auto" or a valid path)
    """
    if model_input:
        # Expand ~ and $HOME
        expanded = os.path.expandvars(model_input.strip())
        expanded = os.path.expanduser(expanded)
        model_path = Path(expanded)

        if model_path.exists():
            return str(model_path)

        # Path not found
        if non_interactive:
            print(f"Warning: Model path {model_input} not found, falling back to 'auto'.")
            return "auto"

        confirmed = prompt(
            "confirm",
            f"Model path {model_input} not found. Use 'auto' instead?",
        )
        if confirmed:
            return "auto"
        else:
            # Re-prompt for model path
            new_input = prompt("text", "Enter model path (or 'auto'):", default="auto")
            if new_input == "auto" or not new_input:
                return "auto"
            return resolve_model(new_input, non_interactive=non_interactive)

    if non_interactive:
        return "auto"

    # Interactive with no CLI input: prompt for model
    user_input = prompt("text", "Embedding model path (or 'auto'):", default="auto")
    if user_input == "auto" or not user_input:
        return "auto"
    return resolve_model(user_input, non_interactive=False)


def select_hosts(*, non_interactive: bool, cli_agents: list[str] | None) -> list[HostConfig]:
    """Select agent hosts from checkbox or CLI flags. Returns list of selected HostConfig.

    Args:
        non_interactive: If True, use CLI flags only
        cli_agents: List of agent names from CLI flags

    Returns:
        List of selected HostConfig objects

    Raises:
        SystemExit(2): If no agents selected or invalid agent name
    """
    if cli_agents:
        # Validate agent names
        for agent in cli_agents:
            if agent not in HOSTS:
                print(f"Error: Unknown agent '{agent}'. Valid agents: {', '.join(HOSTS.keys())}")
                raise SystemExit(2)
        return [HOSTS[agent] for agent in cli_agents]

    if non_interactive:
        print("Error: --agent flag is required in non-interactive mode.")
        print(f"Valid agents: {', '.join(HOSTS.keys())}")
        raise SystemExit(2)

    # Interactive: show checkbox with claude-code pre-selected (most common)
    # Changed from all pre-selected to avoid confusion
    host_names = list(HOSTS.keys())
    choices = [
        {"name": name, "value": name, "checked": (name == "claude-code")}
        for name in host_names
    ]

    print("Note: You can select multiple agent hosts with Space. Navigate with arrow keys.")
    selected = prompt("checkbox", "Select agent hosts to configure:", choices=choices)

    if not selected:
        # User unselected all - prompt to re-select or abort
        retry = prompt(
            "confirm",
            "At least one agent host is required. Re-select hosts?",
        )
        if retry:
            return select_hosts(non_interactive=False, cli_agents=None)
        else:
            raise SystemExit(2)

    # Show confirmation of what will be deployed
    print(f"Will deploy to: {', '.join(selected)}")
    return [HOSTS[name] for name in selected]


def select_microservices(
    java_dirs: list[Path],
    *,
    non_interactive: bool,
    preselected: list[str] | None = None,
) -> list[str] | None:
    """Show an interactive checklist of detected microservices, all pre-checked.

    Returns None when all are selected (-> microservice_roots omitted, index
    everything) or a non-empty subset list. Never returns [].

    Args:
        java_dirs: Detected module roots (relative Path names) from
            detect_java_directories. Caller must pass len >= 2.
        non_interactive: If True, return None (all) without prompting.
        preselected: On re-run, the prior microservice_roots subset to pre-check.
    """
    # Defensive guard: caller gates on len >= 2, but stay safe if called directly.
    if len(java_dirs) < 2:
        return None

    dir_names = [str(d) for d in java_dirs]

    if non_interactive:
        return None

    preselected_set = set(preselected) if preselected else None
    choices = [
        {
            "name": name,
            "value": name,
            "checked": (name in preselected_set) if preselected_set is not None else True,
        }
        for name in dir_names
    ]

    print("Note: Select which modules to index. Toggle with Space, confirm with Enter.")
    selected = prompt(
        "checkbox",
        "Select microservices to index:",
        choices=choices,
        default=dir_names,  # non-TTY fallback returns all -> caller omits key
    )

    if not selected:
        retry = prompt(
            "confirm",
            "At least one module is required. Re-select?",
        )
        if retry:
            return select_microservices(java_dirs, non_interactive=False, preselected=preselected)
        raise SystemExit(2)

    selected_set = set(selected)
    if selected_set == set(dir_names):
        return None
    # Preserve detection order for deterministic YAML output.
    return [name for name in dir_names if name in selected_set]


def select_scope(*, non_interactive: bool, cli_scope: str | None) -> Scope:
    """Select 'project' or 'user' scope.

    Args:
        non_interactive: If True, return "project" without prompting
        cli_scope: Scope from CLI flag

    Returns:
        Selected scope ("project" or "user")
    """
    if cli_scope:
        if cli_scope not in ("project", "user"):
            print(f"Error: Invalid scope '{cli_scope}'. Must be 'project' or 'user'.")
            raise SystemExit(2)
        return cli_scope  # type: ignore

    if non_interactive:
        return "project"

    # Interactive: prompt for scope
    print("Note: 'project' scope stores configs in the project directory.")
    print("      'user' scope stores configs in your home directory.")
    selected = prompt(
        "select",
        "Select installation scope:",
        choices=["project", "user"],
    )

    if not selected:
        return "project"

    print(f"Selected scope: {selected}")
    return selected  # type: ignore


def select_surface(
    *,
    non_interactive: bool,
    cli_surface: str | None,
    prefill: Surface | None = None,
) -> Surface:
    """Select 'mcp' or 'cli' surface (PR-JRAG-5).

    The MCP surface registers the stdio MCP server (today's behavior). The CLI
    surface ships the ``jrag`` console-script skill+subagent instead — no MCP
    entry is registered.

    Args:
        non_interactive: If True, honor ``cli_surface`` (default ``"mcp"``).
        cli_surface: Surface from the ``--surface`` CLI flag.
        prefill: On re-run, the surface recorded in the existing marker file.
            When set and the user does not pick otherwise, this is preserved.

    Returns:
        Selected surface (``"mcp"`` or ``"cli"``).

    Raises:
        SystemExit(2): if ``cli_surface`` is invalid.
    """
    if cli_surface:
        if cli_surface not in ("mcp", "cli"):
            print(f"Error: Invalid surface '{cli_surface}'. Must be 'mcp' or 'cli'.")
            raise SystemExit(2)
        return cli_surface  # type: ignore

    if non_interactive:
        # Default to MCP for back-comat when no flag is passed.
        return "mcp"

    print(
        "Note: 'mcp' surface registers the java-codebase-rag MCP server (5 tools: "
        "search/find/describe/neighbors/resolve)."
    )
    print(
        "      'cli' surface deploys the `jrag` console-script skill+subagent "
        "(one command per intent, no MCP server)."
    )

    choices = ["mcp", "cli"]
    if prefill is not None:
        # Surface the prior choice first so the user can keep it with Enter.
        choices = [prefill] + [c for c in ("mcp", "cli") if c != prefill]
        default = prefill
    else:
        default = "mcp"

    selected = prompt(
        "select",
        "Select agent surface:",
        choices=choices,
        default=default,
    )

    if not selected:
        return default
    return selected  # type: ignore


def resolve_mcp_command(*, non_interactive: bool, surface: Surface = "mcp") -> str:
    """Resolve the absolute path to the runtime binary for the chosen surface.

    - ``surface="mcp"`` (today's behavior): resolve ``java-codebase-rag-mcp``;
      on missing + non-interactive, exit with code 2.
    - ``surface="cli"``: resolve the ``jrag`` console script instead. The CLI
      surface registers no MCP server, so the MCP binary is irrelevant —
      never raise ``SystemExit(2)`` for a missing MCP binary on this surface.
      If ``jrag`` is missing, fall through to the interactive prompt (or
      non-interactive exit) parameterized for ``jrag``.

    Args:
        non_interactive: If True, exit with code 2 when the target binary
            is not found.
        surface: Which surface's binary to resolve.

    Returns:
        Absolute path to the resolved executable.

    Raises:
        SystemExit(2): If not found and non-interactive, or user aborts.
    """
    binary_name, display_name = _surface_binary(surface)
    resolved = shutil.which(binary_name)

    if resolved:
        return resolved

    # Not found on PATH
    if non_interactive:
        print(f"Error: `{display_name}` not found on PATH.")
        if surface == "mcp":
            print(
                "Ensure `java-codebase-rag` is installed, then re-run with "
                "`--non-interactive --agent <host>`."
            )
        else:
            print(
                "Ensure `java-codebase-rag` is installed (provides the `jrag` "
                "console script), then re-run with `--non-interactive --agent <host>`."
            )
        raise SystemExit(2)

    # Interactive: prompt user for path
    print(f"Warning: `{display_name}` not found on PATH.")
    user_path = prompt(
        "text",
        f"Enter the full path to {display_name} (or 'abort'):",
        default="abort",
    )

    if user_path == "abort" or not user_path:
        raise SystemExit(2)

    # Expand and validate the provided path
    expanded = os.path.expandvars(user_path.strip())
    expanded = os.path.expanduser(expanded)
    path_obj = Path(expanded)

    while not path_obj.is_file():
        print(f"Error: Path {path_obj} does not exist or is not a file.")
        user_path = prompt(
            "text",
            f"Enter the full path to {display_name} (or 'abort'):",
            default="abort",
        )
        if user_path == "abort" or not user_path:
            raise SystemExit(2)
        expanded = os.path.expandvars(user_path.strip())
        expanded = os.path.expanduser(expanded)
        path_obj = Path(expanded)

    # Check if executable
    if not os.access(path_obj, os.X_OK):
        print(f"Warning: {path_obj} is not executable. This may cause issues.")

    return str(path_obj.resolve())


def _surface_binary(surface: Surface) -> tuple[str, str]:
    """Return ``(shutil_which_target, user_display_name)`` for a surface.

    The CLI surface resolves the ``jrag`` console script (no MCP server is
    registered, so the MCP binary is irrelevant). The MCP surface keeps
    today's behavior.
    """
    if surface == "cli":
        return ("jrag", "jrag")
    return ("java-codebase-rag-mcp", "java-codebase-rag-mcp")


def merge_mcp_config(config_path: Path, host: HostConfig, *, mcp_command: str) -> bool:
    """Read, merge, write MCP config. Returns True if entry was added/updated.

    Args:
        config_path: Path to MCP config file
        host: HostConfig for the agent host
        mcp_command: Resolved absolute path to java-codebase-rag-mcp

    Returns:
        True if entry was added/updated, False if no change needed

    Raises:
        ValueError: If existing config file cannot be parsed as JSON
    """
    # Read existing config (or start with empty dict)
    if config_path.is_file():
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse {config_path}: {e}") from e
    else:
        config = {}

    # Ensure mcpServers key exists
    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # Prepare new entry
    new_entry = {"command": mcp_command, "type": "stdio"}
    existing_entry = config["mcpServers"].get(_MCP_SERVER_NAME)

    # Check if entry already exists with same config
    if existing_entry == new_entry:
        return False

    # Merge/update entry
    config["mcpServers"][_MCP_SERVER_NAME] = new_entry

    # Write atomically (write to tmp, then rename)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            delete=False,
        ) as tmp:
            json.dump(config, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name

        # Atomic rename
        os.replace(tmp_name, config_path)
        return True
    except (IOError, OSError) as e:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise RuntimeError(f"Failed to write {config_path}: {e}") from e


def _read_package_artifact(relative_path: str) -> str:
    """Read a shipped artifact from package data. Returns UTF-8 text."""
    from importlib.resources import files

    package = files("java_codebase_rag.install_data")
    return package.joinpath(relative_path).read_text(encoding="utf-8")


def deploy_artifacts(
    hosts: list[HostConfig],
    scope: Scope,
    cwd: Path,
    *,
    non_interactive: bool,
    mcp_command: str,
    surface: Surface = "mcp",
) -> list[ArtifactResult]:
    """Deploy artifacts (MCP config, skill, agent) to selected hosts.

    Iterates ``ARTIFACT_MANIFEST[surface]`` so both surfaces share one source
    of truth. The keyword-only ``surface`` defaults to ``"mcp"`` so existing
    direct-call sites in tests keep working unchanged.

    Args:
        hosts: List of HostConfig objects to deploy to
        scope: Installation scope ("project" or "user")
        cwd: Current working directory
        non_interactive: If True, skip overwrite prompts
        mcp_command: Resolved absolute path to the runtime binary
            (``java-codebase-rag-mcp`` for ``mcp`` surface; ``jrag`` for
            ``cli`` surface — unused for the latter since CLI ships no MCP
            config).
        surface: Which artifact set to deploy (default ``"mcp"`` for back-comat).

    Returns:
        List of ArtifactResult objects for each deployment
    """
    results = []
    manifest = ARTIFACT_MANIFEST[surface]

    for host in hosts:
        for kind, package_path, dest_relative in manifest:
            if kind == "mcp":
                # Only the MCP surface carries this entry; the CLI manifest
                # has no "mcp" row by construction.
                mcp_config_path = host.mcp_config_path(scope, cwd)
                result = _deploy_mcp_config(
                    mcp_config_path,
                    host,
                    non_interactive=non_interactive,
                    mcp_command=mcp_command,
                )
            else:
                dest_path = host.scope_path(scope, cwd) / dest_relative
                result = _deploy_file(
                    dest_path,
                    package_path,
                    artifact_type=kind,
                    non_interactive=non_interactive,
                )
            results.append(result)

    return results


def _deploy_mcp_config(
    config_path: Path,
    host: HostConfig,
    *,
    non_interactive: bool,
    mcp_command: str,
) -> ArtifactResult:
    """Deploy MCP config file."""
    try:
        # Ensure parent directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Check writability
        if not _is_writable(config_path.parent):
            return ArtifactResult(
                path=config_path,
                success=False,
                error=f"Directory not writable: {config_path.parent}",
            )

        # Merge config (returns True if updated, False if already current)
        merge_mcp_config(config_path, host, mcp_command=mcp_command)
        return ArtifactResult(path=config_path, success=True, error=None)
    except ValueError as e:
        return ArtifactResult(path=config_path, success=False, error=str(e))
    except Exception as e:
        return ArtifactResult(path=config_path, success=False, error=str(e))


def _deploy_file(
    dest_path: Path,
    package_relative_path: str,
    *,
    artifact_type: str,
    non_interactive: bool,
) -> ArtifactResult:
    """Deploy a single file from package data to destination."""
    try:
        # Ensure parent directory exists
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Check writability
        if not _is_writable(dest_path.parent):
            return ArtifactResult(
                path=dest_path,
                success=False,
                error=f"Directory not writable: {dest_path.parent}",
            )

        # Read package data
        content = _read_package_artifact(package_relative_path)

        # Check if file exists
        if dest_path.is_file():
            # Check if content is identical
            existing_content = dest_path.read_text(encoding="utf-8")
            if content == existing_content:
                return ArtifactResult(path=dest_path, success=True, error=None)

            # File exists with different content - prompt for overwrite
            if non_interactive:
                # Skip in non-interactive mode
                return ArtifactResult(
                    path=dest_path,
                    success=False,
                    error="File exists (skipped in non-interactive mode)",
                )

            # Interactive: prompt for overwrite
            choice = prompt(
                "select",
                f"{artifact_type.capitalize()} file exists at {dest_path}",
                choices=[
                    {"name": "Overwrite", "value": "overwrite"},
                    {"name": "Skip", "value": "skip"},
                    {"name": "Abort", "value": "abort"},
                ],
            )

            if choice == "skip":
                return ArtifactResult(
                    path=dest_path,
                    success=False,
                    error="Skipped by user",
                )
            elif choice == "abort":
                raise SystemExit(2)

        # Write file
        dest_path.write_text(content, encoding="utf-8")
        return ArtifactResult(path=dest_path, success=True, error=None)
    except SystemExit:
        raise
    except Exception as e:
        return ArtifactResult(path=dest_path, success=False, error=str(e))


def _is_writable(path: Path) -> bool:
    """Check if a directory is writable."""
    try:
        test_file = path / ".write_test_java_codebase_rag"
        test_file.touch()
        test_file.unlink()
        return True
    except (OSError, IOError):
        return False


def generate_yaml_config(
    source_root: Path,
    model: str,
    microservice_roots: list[str] | None,
    existing_yaml: dict | None,
) -> str:
    """Generate .java-codebase-rag.yml content from installer answers.

    Args:
        source_root: Source root directory
        model: Embedding model path or "auto"
        microservice_roots: List of microservice roots (None means all)
        existing_yaml: Existing YAML data for re-run update mode

    Returns:
        YAML configuration string
    """
    # Start with existing YAML or empty dict
    config = existing_yaml.copy() if existing_yaml else {}

    # Write microservice_roots only if subset selected
    if microservice_roots:
        config["microservice_roots"] = microservice_roots
    elif "microservice_roots" in config:
        # Remove if not needed (was set before but user wants all)
        del config["microservice_roots"]

    # Write embedding.model only if not auto
    if model != "auto":
        if "embedding" not in config:
            config["embedding"] = {}
        config["embedding"]["model"] = model
    elif "embedding" in config and "model" in config["embedding"]:
        # Remove model if using auto
        if config["embedding"] == {"model": model}:
            del config["embedding"]
        else:
            config["embedding"].pop("model", None)

    # Seed cross-service resolution safe-by-default: only evidence-backed cross-service
    # edges survive (see _is_brownfield_sourced in build_ast_graph). setdefault preserves
    # an explicit user choice (e.g. `auto`) on re-run update.
    config.setdefault("cross_service_resolution", "brownfield_only")

    # Keys NOT written by installer (preserved if present):
    # - source_root (config.py resolves from walk-up discovery)
    # - index_dir (config.py defaults to <source_root>/.java-codebase-rag)
    # - embedding.device (user can add manually)
    # - hints.enabled (defaults to True in config.py)
    # - brownfield_overrides (user-managed)

    return yaml.dump(config, default_flow_style=False, sort_keys=False)


def update_gitignore(cwd: Path) -> None:
    """Add .java-codebase-rag/ to .gitignore if not already present.

    Args:
        cwd: Current working directory
    """
    gitignore_path = cwd / ".gitignore"

    # Check if git repo
    if not (cwd / ".git").is_dir():
        return

    # Read existing .gitignore or create new
    if gitignore_path.is_file():
        lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    # Check for pattern (with or without trailing slash)
    pattern_to_check = ".java-codebase-rag"
    already_present = any(
        line.strip().rstrip("/") == pattern_to_check or line.strip() == f"{pattern_to_check}/"
        for line in lines
    )

    if not already_present:
        lines.append("")
        lines.append("# java-codebase-rag index directory")
        lines.append(".java-codebase-rag/")
        gitignore_path.write_text("\n".join(lines), encoding="utf-8")


def _index_progress_header(subcommand: str, source_root: Path, index_dir: Path) -> None:
    """Print the stderr header framing the indexing sub-step (install/update).

    Mirrors the operator commands' ``_pipeline_header`` but lives in the
    installer because the wizard's stdout framing differs. This brackets ONLY
    the indexing sub-step — the wizard's prompts stay outside it on stdout.
    """
    from java_codebase_rag.cli_format import bold

    print(
        bold(
            f"java-codebase-rag {subcommand} · source={source_root.resolve()} "
            f"· index={index_dir.resolve()}"
        ),
        file=sys.stderr,
        flush=True,
    )


def _index_progress_footer(subcommand: str, started: float, *, ok: bool) -> None:
    """Print the stderr footer closing the indexing sub-step framing."""
    from java_codebase_rag.cli_format import bold, styled_check, styled_cross

    elapsed = time.perf_counter() - started
    marker = styled_check() if ok else styled_cross()
    print(
        f"{marker} {bold(f'java-codebase-rag {subcommand} · finished in {elapsed:.2f}s')}",
        file=sys.stderr,
        flush=True,
    )


def run_init_if_needed(
    source_root: Path,
    index_dir: Path,
    model: str,
    *,
    non_interactive: bool,
    quiet: bool,
    verbose: bool = False,
) -> bool | None:
    """Run init if index directory has no artifacts.

    The indexing sub-step (CocoIndex update + AST graph build) renders the
    unified ``Vectors → Optimize → Graph`` progress on **stderr** in default
    mode (same renderer the operator commands use); the wizard's conversational
    stdout is untouched by this function. ``--quiet`` is silent; ``--verbose``
    raw-relays subprocess output. The indexing chatter that used to print to
    stdout (``Creating index…`` / ``Index created successfully.``) now lives
    on stderr framing so stdout stays the wizard payload.

    Args:
        source_root: Source root directory
        index_dir: Index directory path
        model: Embedding model path or "auto"
        non_interactive: If True, suppress prompts
        quiet: If True, suppress progress output
        verbose: If True, raw-relay subprocess output (no Live region)

    Returns:
        True if init ran and succeeded; False if it ran and failed (cocoindex or
        graph build returned non-zero); None if skipped because the index already
        exists. Callers must distinguish ``False`` (failure) from ``None`` (skip)
        so a failed index does not report success (issue #351).
    """
    from java_codebase_rag.config import (
        index_dir_has_existing_artifacts,
        resolve_operator_config,
        write_config_source_pointer,
    )
    from java_codebase_rag.pipeline import is_cocoindex_preflight_blocker, run_build_ast_graph, run_cocoindex_update

    has_existing, _ = index_dir_has_existing_artifacts(index_dir)
    if has_existing:
        print("Index already exists. Run `java-codebase-rag reprocess` to rebuild.")
        return None  # skipped, not failed

    cfg = resolve_operator_config(
        source_root=source_root,
        cli_index_dir=None,  # use default (<source_root>/.java-codebase-rag)
        cli_embedding_model=model if model != "auto" else None,
    )
    cfg.apply_to_os_environ()
    env = cfg.subprocess_env()

    # Indexing sub-step: render unified progress on stderr in default mode only
    # (quiet = silent; verbose = raw relay, no Live region). The renderer wraps
    # just this sub-step, not the surrounding wizard.
    on_progress, on_progress_console = None, None
    renderer = None
    if not quiet and not verbose:
        from java_codebase_rag.progress import build_index_progress_context

        renderer, on_progress, on_progress_console = build_index_progress_context()

    started = time.perf_counter()
    if renderer is not None:
        _index_progress_header("install", cfg.source_root, cfg.index_dir)
        renderer.start()
    index_ok = True
    try:
        coco = run_cocoindex_update(
            env,
            full_reprocess=False,
            quiet=quiet,
            verbose=verbose,
            on_progress=on_progress,
            on_progress_console=on_progress_console,
        )
        # Graph-only install (cocoindex absent, e.g. macOS Intel): skip the vectors phase
        # and build the graph rather than failing install. A genuine non-zero cocoindex
        # exit still fails.
        vectors_skipped = is_cocoindex_preflight_blocker(coco)
        if coco.returncode != 0 and not vectors_skipped:
            print(
                f"Error: CocoIndex update failed with code {coco.returncode}",
                file=sys.stderr,
            )
            index_ok = False
        else:
            if vectors_skipped:
                print(
                    "java-codebase-rag: vectors skipped — vector stack not installed on this "
                    "platform (graph-only mode). Building graph only; semantic search is unavailable.",
                    file=sys.stderr,
                )
            g = run_build_ast_graph(
                source_root=cfg.source_root,
                ladybug_path=cfg.ladybug_path,
                verbose=verbose,
                quiet=quiet,
                env=env,
                on_progress=on_progress,
                on_progress_console=on_progress_console,
            )
            if g.returncode != 0:
                print(
                    f"Error: AST graph build failed with code {g.returncode}",
                    file=sys.stderr,
                )
                index_ok = False
    except BaseException:
        # An exception from cocoindex/graph means the index did not succeed;
        # flip the footer marker before re-raising so it renders a red cross
        # (mirrors cli._run_with_pipeline_progress's BaseException handler).
        index_ok = False
        raise
    finally:
        if renderer is not None:
            renderer.stop()
            _index_progress_footer("install", started, ok=index_ok)
    if index_ok:
        # Remember which YAML built this index so discovery from a sibling/cwd
        # can relocate the config (e.g. a config beside, not inside, the tree).
        write_config_source_pointer(
            index_dir=cfg.index_dir, yaml_config_path=cfg.yaml_config_path
        )
    return index_ok


def handle_rerun(cwd: Path, *, non_interactive: bool) -> dict | None:
    """If .java-codebase-rag.yml exists, offer update/fresh-start. Return existing YAML data or None.

    Args:
        cwd: Current working directory
        non_interactive: If True, default to "Update" mode

    Returns:
        Parsed existing YAML data if updating, None if starting fresh
    """
    config_path = cwd / ".java-codebase-rag.yml"

    if not config_path.is_file():
        return None

    try:
        with open(config_path, "r") as f:
            existing_config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        print(f"Warning: Failed to parse existing config: {e}")
        return None

    if non_interactive:
        # Default to update mode in non-interactive
        print(f"Found existing config at {config_path}")
        return existing_config

    # Interactive: show current values and ask
    print(f"Found existing config at {config_path}")
    print("Current configuration:")
    for key, value in existing_config.items():
        print(f"  {key}: {value}")

    choice = prompt(
        "select",
        "Choose an action:",
        choices=[
            {"name": "Update (keep existing values)", "value": "update"},
            {"name": "Start fresh (new config)", "value": "fresh"},
            {"name": "Abort", "value": "abort"},
        ],
    )

    if choice == "abort":
        raise SystemExit(2)
    elif choice == "fresh":
        return None
    else:  # update
        return existing_config


def detect_configured_hosts(cwd: Path) -> list[ConfiguredHost]:
    """Detect hosts installed under ``cwd`` (project) and ``$HOME`` (user).

    Reads the marker file (``.java-codebase-rag.hosts``) written at install
    time. Falls back to the legacy MCP-entry scan with ``surface="mcp"`` when
    the marker is absent (pre-marker installs from earlier versions).

    The marker is the single source of truth for CLI-surface installs (which
    register no MCP entry); without it, a CLI-only install would be invisible
    to ``update`` (the legacy scan only finds MCP entries).

    Args:
        cwd: Current working directory (project root for project-scope configs)

    Returns:
        List of ``ConfiguredHost(host, scope, surface)`` tuples in marker order
        (or MCP-scan order in the legacy fallback path).
    """
    marker_hosts = _read_hosts_marker(cwd)
    if marker_hosts is not None:
        return marker_hosts

    # Legacy fallback: scan MCP entries + assume ``mcp`` surface. Pre-marker
    # installs only ever shipped the MCP surface, so this back-comat mapping
    # is exact.
    detected: list[ConfiguredHost] = []
    for host_name, host_config in HOSTS.items():
        # Check project scope
        project_mcp_path = host_config.mcp_config_path("project", cwd)
        if _has_java_codebase_rag_entry(project_mcp_path):
            detected.append(ConfiguredHost(host_config, "project", "mcp"))

        # Check user scope
        user_mcp_path = host_config.mcp_config_path("user", cwd)
        if _has_java_codebase_rag_entry(user_mcp_path):
            detected.append(ConfiguredHost(host_config, "user", "mcp"))

    return detected


def _marker_path(cwd: Path) -> Path:
    """Return the marker file path for a project root."""
    return cwd / _MARKER_FILE_NAME


def _write_hosts_marker(
    project_root: Path, configured: list[ConfiguredHost]
) -> None:
    """Write the marker file recording the installed host/scope/surface set.

    Round-trips with ``_read_hosts_marker``. Silently overwrites an existing
    marker so re-runs (install over an existing install) reflect the latest
    wizard answers.
    """
    payload = {
        "version": _MARKER_FILE_VERSION,
        "hosts": [
            {"host": ch.host.name, "scope": ch.scope, "surface": ch.surface}
            for ch in configured
        ],
    }
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=project_root,
            prefix=f".{_MARKER_FILE_NAME}.",
            delete=False,
        ) as tmp:
            json.dump(payload, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name
        # os.replace (not os.rename): on Windows, os.rename raises when the
        # destination exists — the documented re-run path overwrites the prior
        # marker. os.replace atomically overwrites cross-platform (PR #371
        # fixed this same pattern elsewhere).
        os.replace(tmp_name, _marker_path(project_root))
    except (IOError, OSError) as e:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        # Non-fatal: ``update`` will fall back to the MCP-entry scan. Surface
        # a warning so the operator notices, but do not abort the install.
        print(f"Warning: failed to write {_marker_path(project_root)}: {e}")


def _read_hosts_marker(cwd: Path) -> list[ConfiguredHost] | None:
    """Read the marker file. Return ``None`` if missing or unparseable.

    On parse/version errors, returns ``None`` so the caller falls back to the
    MCP-entry scan rather than crashing mid-update.
    """
    marker = _marker_path(cwd)
    if not marker.is_file():
        return None
    try:
        with open(marker, "r") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return None

    if not isinstance(payload, dict):
        return None

    raw_hosts = payload.get("hosts", [])
    if not isinstance(raw_hosts, list):
        return None

    configured: list[ConfiguredHost] = []
    for entry in raw_hosts:
        if not isinstance(entry, dict):
            return None
        host_name = entry.get("host")
        scope = entry.get("scope")
        surface = entry.get("surface", "mcp")
        if host_name not in HOSTS:
            return None
        if scope not in ("project", "user"):
            return None
        if surface not in ("mcp", "cli"):
            return None
        configured.append(
            ConfiguredHost(HOSTS[host_name], scope, surface)  # type: ignore[arg-type]
        )

    return configured


def _has_java_codebase_rag_entry(config_path: Path) -> bool:
    """Check if MCP config file has a java-codebase-rag entry.

    Args:
        config_path: Path to MCP config file

    Returns:
        True if file exists and contains java-codebase-rag in mcpServers
    """
    if not config_path.is_file():
        return False

    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return False

    mcp_servers = config.get("mcpServers", {})
    return _MCP_SERVER_NAME in mcp_servers


def refresh_artifacts(
    host: HostConfig,
    scope: str,
    cwd: Path,
    *,
    force: bool,
    dry_run: bool,
    surface: Surface = "mcp",
) -> list[ArtifactResult]:
    """Overwrite skill and agent files from package data. Skip MCP if entry is correct.

    Iterates ``ARTIFACT_MANIFEST[surface]`` so both surfaces share one source
    of truth (PR-JRAG-5). The keyword-only ``surface`` defaults to ``"mcp"``
    so existing direct-call sites in tests keep working unchanged.

    Args:
        host: HostConfig for the agent host
        scope: Installation scope ("project" or "user")
        cwd: Current working directory
        force: If True, overwrite all files even if matching
        dry_run: If True, print changes without writing
        surface: Which artifact set to refresh (default ``"mcp"`` for back-comat).

    Returns:
        List of ArtifactResult objects for each artifact
    """
    results = []
    manifest = ARTIFACT_MANIFEST[surface]

    for kind, package_path, dest_relative in manifest:
        if kind == "mcp":
            # Refresh MCP config (update command path if needed).
            # NOTE: only the MCP surface has a "mcp" row in its manifest —
            # ``_refresh_mcp_config`` (and therefore ``resolve_mcp_command``)
            # is NEVER reached on the CLI surface by construction. The CLI
            # surface ships no MCP entry, so there is nothing to refresh.
            mcp_config_path = host.mcp_config_path(scope, cwd)
            result = _refresh_mcp_config(mcp_config_path, host, force=force, dry_run=dry_run)
        else:
            dest_path = host.scope_path(scope, cwd) / dest_relative
            result = _refresh_file(
                dest_path,
                package_path,
                artifact_type=kind,
                force=force,
                dry_run=dry_run,
            )
        results.append(result)

    return results


def _refresh_file(
    dest_path: Path,
    package_relative_path: str,
    *,
    artifact_type: str,
    force: bool,
    dry_run: bool,
) -> ArtifactResult:
    """Refresh a single file from package data.

    Args:
        dest_path: Destination file path
        package_relative_path: Path relative to install_data
        artifact_type: Type of artifact (for error messages)
        force: If True, overwrite even if matching
        dry_run: If True, print without writing

    Returns:
        ArtifactResult with success status
    """
    try:
        # Read package data
        package_content = _read_package_artifact(package_relative_path)

        # Check if file exists
        if dest_path.is_file():
            existing_content = dest_path.read_text(encoding="utf-8")

            # Skip if content matches and not forcing
            if package_content == existing_content and not force:
                return ArtifactResult(path=dest_path, success=True, error=None)

            # Content differs or force mode
            if dry_run:
                print(f"Would update {artifact_type} file at {dest_path}")
                return ArtifactResult(path=dest_path, success=True, error=None)

        elif dry_run:
            print(f"Would create {artifact_type} file at {dest_path}")
            return ArtifactResult(path=dest_path, success=True, error=None)

        # Ensure parent directory exists
        if not dry_run:
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Check writability
            if not _is_writable(dest_path.parent):
                return ArtifactResult(
                    path=dest_path,
                    success=False,
                    error=f"Directory not writable: {dest_path.parent}",
                )

        # Write file (skip in dry_run mode)
        if not dry_run:
            dest_path.write_text(package_content, encoding="utf-8")
            print(f"Updated {artifact_type} file at {dest_path}")

        return ArtifactResult(path=dest_path, success=True, error=None)

    except Exception as e:
        return ArtifactResult(path=dest_path, success=False, error=str(e))


def _refresh_mcp_config(
    config_path: Path,
    host: HostConfig,
    *,
    force: bool,
    dry_run: bool,
) -> ArtifactResult:
    """Refresh MCP config entry (update command path if needed).

    Args:
        config_path: Path to MCP config file
        host: HostConfig for the agent host
        force: If True, update even if matching
        dry_run: If True, print without writing

    Returns:
        ArtifactResult with success status
    """
    try:
        # Resolve current MCP command path
        # Catch SystemExit because resolve_mcp_command raises it when binary not found
        try:
            mcp_command = resolve_mcp_command(non_interactive=True)
        except SystemExit:
            return ArtifactResult(
                path=config_path,
                success=False,
                error="java-codebase-rag-mcp not found on PATH",
            )

        # Prepare new entry
        new_entry = {"command": mcp_command, "type": "stdio"}

        # Read existing config
        if config_path.is_file():
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
            except json.JSONDecodeError as e:
                return ArtifactResult(
                    path=config_path,
                    success=False,
                    error=f"Failed to parse {config_path}: {e}",
                )
        else:
            config = {}

        # Ensure mcpServers key exists
        if "mcpServers" not in config:
            config["mcpServers"] = {}

        existing_entry = config["mcpServers"].get(_MCP_SERVER_NAME)

        # Check if entry already matches (skip unless force)
        if existing_entry == new_entry and not force:
            return ArtifactResult(path=config_path, success=True, error=None)

        # Entry differs or force mode
        if dry_run:
            print(f"Would update MCP config at {config_path}")
            return ArtifactResult(path=config_path, success=True, error=None)

        # Merge/update entry
        config["mcpServers"][_MCP_SERVER_NAME] = new_entry

        # Ensure parent directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Check writability
        if not _is_writable(config_path.parent):
            return ArtifactResult(
                path=config_path,
                success=False,
                error=f"Directory not writable: {config_path.parent}",
            )

        # Write atomically
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=config_path.parent,
                prefix=f".{config_path.name}.",
                delete=False,
            ) as tmp:
                json.dump(config, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_name = tmp.name

            # Atomic rename
            os.replace(tmp_name, config_path)
            print(f"Updated MCP config at {config_path}")
            return ArtifactResult(path=config_path, success=True, error=None)

        except (IOError, OSError) as e:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            raise RuntimeError(f"Failed to write {config_path}: {e}") from e

    except SystemExit as e:
        # Catch SystemExit from resolve_mcp_command and other exits
        return ArtifactResult(path=config_path, success=False, error=f"Command failed: {e.code}")
    except Exception as e:
        return ArtifactResult(path=config_path, success=False, error=str(e))


def run_update(
    *,
    force: bool,
    dry_run: bool,
    cwd: Path | None = None,
    quiet: bool = False,
    verbose: bool = False,
) -> int:
    """Run the update pipeline. Returns exit code.

    The indexing sub-step (Lance catch-up + incremental graph) renders the
    unified ``Vectors → Optimize → Graph`` progress on **stderr** in default
    mode and no longer runs with ``quiet=True`` (the reason ``update`` was
    silent). ``--quiet`` is silent; ``--verbose`` raw-relays subprocess output.
    The wizard's host-detection / refresh / summary stdout is preserved; only
    the indexing chatter that used to print to stdout moves onto the stderr
    renderer framing.

    Args:
        force: If True, overwrite all artifacts even if matching
        dry_run: If True, print changes without writing
        cwd: Current working directory (defaults to Path.cwd())
        quiet: If True, suppress progress output
        verbose: If True, raw-relay subprocess output (no Live region)

    Returns:
        Exit code (0=success, 1=partial, 2=fatal)
    """
    if cwd is None:
        cwd = Path.cwd()
    cwd = cwd.resolve()

    # Detect configured hosts
    configured_hosts = detect_configured_hosts(cwd)

    if not configured_hosts:
        print("No configured agent hosts found.")
        print("Run `java-codebase-rag install` first.")
        return EXIT_FATAL

    print(f"Found {len(configured_hosts)} configured host(s).")

    # Refresh artifacts for each host
    all_results = []
    for host_config, scope, surface in configured_hosts:
        print(f"\nRefreshing {host_config.name} ({scope} scope, surface={surface})...")
        results = refresh_artifacts(
            host_config,
            scope,
            cwd,
            force=force,
            dry_run=dry_run,
            surface=surface,
        )
        all_results.extend(results)

    # Check for partial failures
    partial_failures = [r for r in all_results if not r.success]
    has_artifact_failures = len(partial_failures) > 0
    if partial_failures:
        print("\nWarning: Some artifacts failed to update:")
        for r in partial_failures:
            print(f"  {r.path}: {r.error}")

    # Check if index exists
    from java_codebase_rag.config import (
        discover_project_root,
        index_dir_has_existing_artifacts,
        resolve_operator_config,
        write_config_source_pointer,
    )
    from java_codebase_rag.pipeline import is_cocoindex_preflight_blocker, run_cocoindex_update, run_incremental_graph

    project_root = discover_project_root(cwd)
    if project_root is None:
        print("\nNo project configuration found (.java-codebase-rag.yml).")
        print("Skipping index update.")
        return EXIT_PARTIAL if has_artifact_failures else EXIT_SUCCESS

    # Resolve configuration. Pass source_root=None so the YAML ``source_root``
    # field is honored exactly like increment/init/reprocess — passing the
    # discovered config dir here routes resolve_operator_config into the
    # explicit-override branch that SKIPS the YAML field, which made `update`
    # point cocoindex at the config dir (no Java) against the real index and
    # mass-delete it. Discovery still runs against the CLI's cwd.
    try:
        cfg = resolve_operator_config(source_root=None, cli_index_dir=None)
        index_dir = cfg.index_dir
    except Exception as e:
        print(f"\nWarning: Failed to resolve configuration: {e}")
        print("Skipping index update.")
        return EXIT_PARTIAL if has_artifact_failures else EXIT_SUCCESS

    # Check if index has existing artifacts
    index_exists, _ = index_dir_has_existing_artifacts(index_dir)

    if not index_exists:
        print("\nNo index found.")
        print("Run `java-codebase-rag install` to create one.")
        return EXIT_PARTIAL if has_artifact_failures else EXIT_SUCCESS

    # Run increment: LanceDB catch-up + incremental graph rebuild.
    # Mirrors `java-codebase-rag increment` so both index layers stay current.
    # The "graph not implemented" warning belongs only on the vectors-only path
    # (increment --vectors-only), where the graph step is deliberately skipped.
    if not dry_run:
        cfg.apply_to_os_environ()
        env = cfg.subprocess_env()

        # Indexing sub-step: render unified progress on stderr in default mode
        # only (quiet = silent; verbose = raw relay). No longer runs quiet=True
        # — that was why `update` was silent. The renderer wraps just this
        # sub-step; the wizard's summary stdout below is outside it.
        on_progress, on_progress_console = None, None
        renderer = None
        if not quiet and not verbose:
            from java_codebase_rag.progress import build_index_progress_context

            renderer, on_progress, on_progress_console = build_index_progress_context()

        started = time.perf_counter()
        if renderer is not None:
            _index_progress_header("update", cfg.source_root, cfg.index_dir)
            renderer.start()
        index_ok = True
        try:
            coco = run_cocoindex_update(
                env,
                full_reprocess=False,
                quiet=quiet,
                verbose=verbose,
                on_progress=on_progress,
                on_progress_console=on_progress_console,
            )
            # Graph-only install (cocoindex absent): skip the vectors catch-up and run the
            # graph catch-up only. A genuine non-zero cocoindex exit still fails.
            vectors_skipped = is_cocoindex_preflight_blocker(coco)
            if coco.returncode != 0 and not vectors_skipped:
                print(
                    f"Error: Lance index update failed with code {coco.returncode}",
                    file=sys.stderr,
                )
                index_ok = False
            else:
                if vectors_skipped:
                    print(
                        "java-codebase-rag: vectors skipped — vector stack not installed on this "
                        "platform (graph-only mode). Running graph catch-up only.",
                        file=sys.stderr,
                    )
                g = run_incremental_graph(
                    source_root=cfg.source_root,
                    ladybug_path=cfg.ladybug_path,
                    verbose=verbose,
                    quiet=quiet,
                    env=env,
                    on_progress=on_progress,
                    on_progress_console=on_progress_console,
                )
                if g.returncode != 0:
                    # The graph catch-up is best-effort: `update`'s primary job
                    # is refreshing shipped artifacts + vectors (cocoindex). A
                    # graph failure surfaces a truthful, actionable Warning on
                    # stderr but does NOT flip index_ok (which drives both the
                    # footer marker and the return code) — exit 0 with a green
                    # check + the Warning line carrying the graph caveat.
                    print(
                        f"\nWarning: incremental graph update failed (exit {g.returncode}). "
                        "Run `java-codebase-rag reprocess` for a full rebuild.",
                        file=sys.stderr,
                    )
        except BaseException:
            # An exception from cocoindex/graph means the index did not succeed;
            # flip the footer marker before re-raising so it renders a red cross
            # (mirrors cli._run_with_pipeline_progress's BaseException handler).
            index_ok = False
            raise
        finally:
            if renderer is not None:
                renderer.stop()
                _index_progress_footer("update", started, ok=index_ok)
        if not index_ok:
            return 1
        # Refresh the config pointer so a config moved/renamed since the last
        # index is relocated correctly by discovery from a sibling/cwd.
        write_config_source_pointer(
            index_dir=cfg.index_dir, yaml_config_path=cfg.yaml_config_path
        )
    else:
        print("\nWould run incremental index update (Lance + graph).")

    # Print summary
    print("\nUpdate complete.")
    successful = [r for r in all_results if r.success]
    print(f"Updated {len(successful)} artifact(s).")

    return 1 if has_artifact_failures else 0


def run_install(
    *,
    non_interactive: bool,
    agents: list[str] | None,
    scope: str | None,
    model: str | None,
    surface: str | None = None,
    source_root: Path | None = None,
    quiet: bool = False,
    verbose: bool = False,
) -> int:
    """Run the install pipeline. Returns exit code.

    Args:
        non_interactive: If True, skip all prompts
        agents: List of agent names from CLI flags
        scope: Scope from CLI flag
        model: Model from CLI flag
        surface: Surface from CLI flag (``"mcp"`` or ``"cli"``; default ``"mcp"``)
        source_root: Source root path (defaults to cwd if None)
        quiet: If True, suppress output
        verbose: If True, raw-relay subprocess indexing output (no Live region)

    Returns:
        Exit code (0=success, 1=partial, 2=fatal)
    """
    # Stage 0: Determine source root
    cwd = Path.cwd() if source_root is None else source_root
    cwd = cwd.resolve()

    # Stage 0.5: Check for existing config (re-run detection)
    existing_config = handle_rerun(cwd, non_interactive=non_interactive)

    # Stage 1: Java source detection (with confirmation in interactive mode)
    source_root = confirm_source_root(cwd, non_interactive=non_interactive)

    # Detect Java directories
    try:
        java_dirs = detect_java_directories(source_root)
    except SystemExit as e:
        return e.code

    # Stage 1 (Case B): interactive microservice selection (only when 2+ detected)
    try:
        selected_roots = (
            select_microservices(
                java_dirs,
                non_interactive=non_interactive,
                preselected=existing_config.get("microservice_roots") if existing_config else None,
            )
            if len(java_dirs) >= 2
            else None
        )
    except SystemExit as e:
        return e.code

    # Stage 2: Embedding model
    from java_codebase_rag.pipeline import vector_stack_installed

    if not vector_stack_installed():
        # Graph-only install (macOS Intel): no torch/lancedb, so there is no vector
        # index to embed into — the embedding-model choice is inert here. Skip the
        # prompt and let init build the graph (vectors phase auto-skipped).
        print(
            "Skipping embedding model selection: vector stack not installed on this "
            "platform (graph-only mode)."
        )
        resolved_model = "auto"
    else:
        resolved_model = resolve_model(model, non_interactive=non_interactive)

    # Stage 3-4: Agent host + scope + surface selection
    prior_surface = _prior_surface_from_marker(cwd)
    try:
        hosts = select_hosts(non_interactive=non_interactive, cli_agents=agents)
        selected_scope = select_scope(non_interactive=non_interactive, cli_scope=scope)
        selected_surface = select_surface(
            non_interactive=non_interactive,
            cli_surface=surface,
            prefill=prior_surface,
        )
    except SystemExit as e:
        return e.code

    # Stage 5: Artifact deployment (manifest iterates the chosen surface)
    mcp_command = resolve_mcp_command(
        non_interactive=non_interactive, surface=selected_surface
    )
    results = deploy_artifacts(
        hosts,
        selected_scope,
        source_root,
        non_interactive=non_interactive,
        mcp_command=mcp_command,
        surface=selected_surface,
    )

    # Check for partial failures
    partial_failures = [r for r in results if not r.success]
    if partial_failures:
        print("Warning: Some artifacts failed to deploy:")
        for r in partial_failures:
            print(f"  {r.path}: {r.error}")
        # Severity model: only MCP config (.json/.yml/.yaml) deploy failures are
        # critical (return 1) -- a broken MCP config means the server cannot start.
        # Skill/agent (.md / dir) failures are downgraded to non-critical: the
        # server still runs and the affected host simply lacks those hints. Issue
        # #351's "treat skill/agent deploy failures as critical for the affected
        # host" is intentionally DEFERRED here -- promoting them to critical is a
        # product decision (recoverable vs. fatal) best made explicitly, not bundled.
        if all(
            r.success
            for r in results
            if r.path.suffix in [".json", ".yml", ".yaml"]
        ):
            # MCP configs succeeded - non-critical
            print("Continuing (MCP configs deployed successfully)...")
        else:
            # Critical failures
            return 1

    # Record the host/scope/surface set so a later ``update`` can route the
    # refresh through the right surface — critical for CLI-only installs (no
    # MCP entry to scan).
    configured = [
        ConfiguredHost(h, selected_scope, selected_surface) for h in hosts
    ]
    _write_hosts_marker(source_root, configured)

    # Stage 6: Index + finish
    # Generate YAML config
    yaml_content = generate_yaml_config(
        source_root,
        resolved_model,
        microservice_roots=selected_roots,
        existing_yaml=existing_config,
    )

    # Write YAML config
    config_path = source_root / ".java-codebase-rag.yml"
    config_path.write_text(yaml_content, encoding="utf-8")

    # Update .gitignore
    update_gitignore(source_root)

    if not quiet:
        print("Configuration written to", config_path)

    # Run init if index directory is empty. run_init_if_needed returns True (ran
    # OK), False (ran and failed — cocoindex/graph non-zero exit), or None
    # (skipped: index already exists). A failed index must NOT report success in
    # CI/automation; a skip is not a failure (issue #351).
    index_dir = (source_root / ".java-codebase-rag").resolve()
    init_outcome = run_init_if_needed(
        source_root,
        index_dir,
        resolved_model,
        non_interactive=non_interactive,
        quiet=quiet,
        verbose=verbose,
    )
    if init_outcome is False:
        return 1
    return 0


def _prior_surface_from_marker(cwd: Path) -> Surface | None:
    """Return the (single) surface recorded in the existing marker, if any.

    On multi-surface installs (rare but possible across hosts), returns the
    first recorded surface — the wizard prefill is a UX nicety, not a contract.
    Returns ``None`` when no marker exists (fresh install) or the marker is
    unparseable.
    """
    configured = _read_hosts_marker(cwd)
    if not configured:
        return None
    return configured[0].surface
