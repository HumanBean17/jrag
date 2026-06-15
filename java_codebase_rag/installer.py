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

# MCP server name constant
_MCP_SERVER_NAME = "java-codebase-rag"

# Exit code constants
EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_FATAL = 2


class ArtifactResult(NamedTuple):
    """Result of deploying a single artifact."""

    path: Path
    success: bool
    error: str | None


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


def resolve_mcp_command(*, non_interactive: bool) -> str:
    """Resolve the absolute path to java-codebase-rag-mcp.

    Returns the path string for use as MCP 'command' value.

    Args:
        non_interactive: If True, exit with code 2 when not found

    Returns:
        Absolute path to java-codebase-rag-mcp executable

    Raises:
        SystemExit(2): If not found and non-interactive, or user aborts
    """
    mcp_path = shutil.which("java-codebase-rag-mcp")

    if mcp_path:
        return mcp_path

    # Not found on PATH
    if non_interactive:
        print("Error: `java-codebase-rag-mcp` not found on PATH.")
        print("Ensure `java-codebase-rag` is installed, then re-run with `--non-interactive --agent <host>`.")
        raise SystemExit(2)

    # Interactive: prompt user for path
    print("Warning: `java-codebase-rag-mcp` not found on PATH.")
    user_path = prompt(
        "text",
        "Enter the full path to java-codebase-rag-mcp (or 'abort'):",
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
            "Enter the full path to java-codebase-rag-mcp (or 'abort'):",
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
        os.rename(tmp_name, config_path)
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
) -> list[ArtifactResult]:
    """Deploy artifacts (MCP config, skill, agent) to selected hosts.

    Args:
        hosts: List of HostConfig objects to deploy to
        scope: Installation scope ("project" or "user")
        cwd: Current working directory
        non_interactive: If True, skip overwrite prompts
        mcp_command: Resolved absolute path to java-codebase-rag-mcp

    Returns:
        List of ArtifactResult objects for each deployment
    """
    results = []

    for host in hosts:
        # Deploy MCP config
        mcp_config_path = host.mcp_config_path(scope, cwd)
        mcp_result = _deploy_mcp_config(
            mcp_config_path,
            host,
            non_interactive=non_interactive,
            mcp_command=mcp_command,
        )
        results.append(mcp_result)

        # Deploy skill
        skills_dir = host.skills_dir(scope, cwd)
        skill_dest = skills_dir / "explore-codebase" / "SKILL.md"
        skill_result = _deploy_file(
            skill_dest,
            "skills/explore-codebase/SKILL.md",
            artifact_type="skill",
            non_interactive=non_interactive,
        )
        results.append(skill_result)

        # Deploy agent
        agents_dir = host.agents_dir(scope, cwd)
        agent_dest = agents_dir / "explorer-rag-enhanced.md"
        agent_result = _deploy_file(
            agent_dest,
            "agents/explorer-rag-enhanced.md",
            artifact_type="agent",
            non_interactive=non_interactive,
        )
        results.append(agent_result)

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

    elapsed = time.time() - started
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
) -> bool:
    """Run init if index directory has no artifacts. Return True if init was run.

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
        True if init was run, False if skipped
    """
    from java_codebase_rag.config import (
        index_dir_has_existing_artifacts,
        resolve_operator_config,
    )
    from java_codebase_rag.pipeline import run_build_ast_graph, run_cocoindex_update

    has_existing, _ = index_dir_has_existing_artifacts(index_dir)
    if has_existing:
        print("Index already exists. Run `java-codebase-rag reprocess` to rebuild.")
        return False

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

    started = time.time()
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
        if coco.returncode != 0:
            print(
                f"Error: CocoIndex update failed with code {coco.returncode}",
                file=sys.stderr,
            )
            index_ok = False
        else:
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
    finally:
        if renderer is not None:
            renderer.stop()
            _index_progress_footer("install", started, ok=index_ok)
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


def detect_configured_hosts(cwd: Path) -> list[tuple[HostConfig, str]]:
    """Scan project + user config files for java-codebase-rag MCP entries.

    Args:
        cwd: Current working directory (for project-scope configs)

    Returns:
        List of (host_config, scope) tuples where scope is "project" or "user"
    """
    detected = []

    # Check all hosts in both project and user scopes
    for host_name, host_config in HOSTS.items():
        # Check project scope
        project_mcp_path = host_config.mcp_config_path("project", cwd)
        if _has_java_codebase_rag_entry(project_mcp_path):
            detected.append((host_config, "project"))

        # Check user scope
        user_mcp_path = host_config.mcp_config_path("user", cwd)
        if _has_java_codebase_rag_entry(user_mcp_path):
            detected.append((host_config, "user"))

    return detected


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
) -> list[ArtifactResult]:
    """Overwrite skill and agent files from package data. Skip MCP if entry is correct.

    Args:
        host: HostConfig for the agent host
        scope: Installation scope ("project" or "user")
        cwd: Current working directory
        force: If True, overwrite all files even if matching
        dry_run: If True, print changes without writing

    Returns:
        List of ArtifactResult objects for each artifact
    """
    results = []

    # Refresh skill file
    skills_dir = host.skills_dir(scope, cwd)
    skill_dest = skills_dir / "explore-codebase" / "SKILL.md"
    skill_result = _refresh_file(
        skill_dest,
        "skills/explore-codebase/SKILL.md",
        artifact_type="skill",
        force=force,
        dry_run=dry_run,
    )
    results.append(skill_result)

    # Refresh agent file
    agents_dir = host.agents_dir(scope, cwd)
    agent_dest = agents_dir / "explorer-rag-enhanced.md"
    agent_result = _refresh_file(
        agent_dest,
        "agents/explorer-rag-enhanced.md",
        artifact_type="agent",
        force=force,
        dry_run=dry_run,
    )
    results.append(agent_result)

    # Refresh MCP config (update command path if needed)
    mcp_config_path = host.mcp_config_path(scope, cwd)
    mcp_result = _refresh_mcp_config(mcp_config_path, host, force=force, dry_run=dry_run)
    results.append(mcp_result)

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
            os.rename(tmp_name, config_path)
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
    for host_config, scope in configured_hosts:
        print(f"\nRefreshing {host_config.name} ({scope} scope)...")
        results = refresh_artifacts(host_config, scope, cwd, force=force, dry_run=dry_run)
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
    )
    from java_codebase_rag.pipeline import run_cocoindex_update, run_incremental_graph

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

        started = time.time()
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
            if coco.returncode != 0:
                print(
                    f"Error: Lance index update failed with code {coco.returncode}",
                    file=sys.stderr,
                )
                index_ok = False
            else:
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
                    # Artifacts above already refreshed; the graph catch-up is
                    # best-effort here. Surface a truthful, actionable message
                    # instead of leaving the graph silently stale or claiming
                    # the feature is unimplemented. Goes to stderr (indexing
                    # progress framing), not the wizard's stdout summary.
                    print(
                        f"\nWarning: incremental graph update failed (exit {g.returncode}). "
                        "Run `java-codebase-rag reprocess` for a full rebuild.",
                        file=sys.stderr,
                    )
        finally:
            if renderer is not None:
                renderer.stop()
                _index_progress_footer("update", started, ok=index_ok)
        if not index_ok:
            return 1
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
    resolved_model = resolve_model(model, non_interactive=non_interactive)

    # Stage 3-4: Agent host + scope selection
    try:
        hosts = select_hosts(non_interactive=non_interactive, cli_agents=agents)
        selected_scope = select_scope(non_interactive=non_interactive, cli_scope=scope)
    except SystemExit as e:
        return e.code

    # Stage 5: Artifact deployment
    mcp_command = resolve_mcp_command(non_interactive=non_interactive)
    results = deploy_artifacts(
        hosts,
        selected_scope,
        source_root,
        non_interactive=non_interactive,
        mcp_command=mcp_command,
    )

    # Check for partial failures
    partial_failures = [r for r in results if not r.success]
    if partial_failures:
        print("Warning: Some artifacts failed to deploy:")
        for r in partial_failures:
            print(f"  {r.path}: {r.error}")
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

    # Run init if index directory is empty
    index_dir = (source_root / ".java-codebase-rag").resolve()
    run_init_if_needed(
        source_root,
        index_dir,
        resolved_model,
        non_interactive=non_interactive,
        quiet=quiet,
        verbose=verbose,
    )

    return 0
