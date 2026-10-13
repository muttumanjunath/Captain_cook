from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "configure-hermes-factory-skills.ps1"
SKILL_NAMES = (
    "captain-factory-discover",
    "captain-factory-brief-codex",
    "captain-factory-execute-team",
    "captain-factory-evaluate-team",
    "captain-factory-improve-team",
    "captain-factory-report-captain",
)
TEXT_ASSET_SUFFIXES = {
    ".bash",
    ".cjs",
    ".conf",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".md",
    ".mjs",
    ".ps1",
    ".psd1",
    ".psm1",
    ".py",
    ".pyi",
    ".scss",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}


def _write_fake_hermes(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "fake_hermes.py"
    fake.write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            import json
            import os
            from pathlib import Path
            import sys

            import yaml


            args = sys.argv[1:]
            home = Path(os.environ["HERMES_HOME"])
            config_path = home / "config.yaml"


            def load_config() -> dict:
                if not config_path.exists():
                    return {}
                return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


            def save_config(config: dict) -> None:
                home.mkdir(parents=True, exist_ok=True)
                config_path.write_text(
                    yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
                )


            def external_dirs(config: dict) -> list[str]:
                value = config.get("skills", {}).get("external_dirs", [])
                return [value] if isinstance(value, str) else list(value)


            def resolved_external_dirs(config: dict) -> list[Path]:
                roots = []
                for value in external_dirs(config):
                    root = Path(os.path.expandvars(value)).expanduser()
                    roots.append(root.resolve() if root.is_absolute() else (home / root).resolve())
                return roots


            def discovered_skills(root: Path):
                if not root.is_dir():
                    return
                for skill_file in sorted(root.rglob("SKILL.md")):
                    relative_parts = skill_file.relative_to(root).parts
                    if any(
                        part
                        in {
                            ".git",
                            ".github",
                            ".hub",
                            ".archive",
                            ".venv",
                            "venv",
                            "node_modules",
                            "site-packages",
                            "__pycache__",
                            ".tox",
                            ".nox",
                            ".pytest_cache",
                            ".mypy_cache",
                            ".ruff_cache",
                        }
                        for part in relative_parts
                    ):
                        continue
                    content = skill_file.read_text(encoding="utf-8-sig")
                    frontmatter = {}
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) == 3:
                            frontmatter = yaml.safe_load(parts[1]) or {}
                    yield str(frontmatter.get("name") or skill_file.parent.name), skill_file


            home.mkdir(parents=True, exist_ok=True)
            with (home / "calls.log").open("a", encoding="utf-8") as log:
                log.write(json.dumps(args) + "\\n")

            # Mirrors the pinned Hermes CLI: `config get` reads the user config
            # merged over built-in defaults, `config set` coerces booleans and
            # integers, and both `get` and `unset` exit 1 for a key that is not
            # set. skills.creation_nudge_interval has no built-in default.
            DEFAULTS = {
                "skills": {
                    "external_dirs": [],
                    "write_approval": False,
                    "guard_agent_created": False,
                }
            }
            MISSING = object()


            def merged(base: dict, override: dict) -> dict:
                result = dict(base)
                for key, value in override.items():
                    if isinstance(value, dict) and isinstance(result.get(key), dict):
                        result[key] = merged(result[key], value)
                    else:
                        result[key] = value
                return result


            def lookup(config: dict, dotted: str):
                node = config
                for part in dotted.split("."):
                    if not isinstance(node, dict) or part not in node:
                        return MISSING
                    node = node[part]
                return node


            def coerce(dotted: str, raw: str):
                if dotted == "skills.external_dirs":
                    return json.loads(raw)
                lowered = raw.lower()
                if lowered in {"true", "yes", "on"}:
                    return True
                if lowered in {"false", "no", "off"}:
                    return False
                if raw.isdigit():
                    return int(raw)
                return raw


            if args[:2] == ["config", "get"]:
                value = lookup(merged(DEFAULTS, load_config()), args[2])
                if value is MISSING:
                    print(f"Config key not set: {args[2]}", file=sys.stderr)
                    raise SystemExit(1)
                print(json.dumps(value))
            elif args[:2] == ["config", "set"]:
                config = load_config()
                *parents, leaf = args[2].split(".")
                node = config
                for part in parents:
                    node = node.setdefault(part, {})
                node[leaf] = coerce(args[2], args[3])
                save_config(config)
                print(f"set {args[2]}")
            elif args[:2] == ["config", "unset"]:
                config = load_config()
                *parents, leaf = args[2].split(".")
                node = config
                for part in parents:
                    node = node.get(part, {}) if isinstance(node, dict) else {}
                if not isinstance(node, dict) or leaf not in node:
                    print(f"Config key not set: {args[2]}", file=sys.stderr)
                    raise SystemExit(1)
                del node[leaf]
                save_config(config)
                print(f"unset {args[2]}")
            elif args[:2] == ["skills", "list"]:
                config = load_config()
                disabled = set(config.get("skills", {}).get("disabled", []))
                seen = set()
                for source, root in [("local", home / "skills")] + [
                    ("external", path) for path in resolved_external_dirs(config)
                ]:
                    for name, skill_file in discovered_skills(root) or ():
                        if name in seen:
                            continue
                        seen.add(name)
                        if name in disabled:
                            continue
                        print(f"{name} | {source} | enabled | {skill_file.parent.resolve()}")
            elif args[:2] == ["bundles", "create"]:
                name = args[2]
                skills = [args[index + 1] for index, value in enumerate(args) if value == "--skill"]
                description = args[args.index("--description") + 1]
                instruction = args[args.index("--instruction") + 1]
                bundle_dir = home / "skill-bundles"
                bundle_dir.mkdir(parents=True, exist_ok=True)
                (bundle_dir / f"{name}.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "name": name,
                            "description": description,
                            "skills": skills,
                            "instruction": instruction,
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                print(f"Created bundle: /{name}")
            elif args[:2] == ["bundles", "show"]:
                name = args[2]
                bundle = yaml.safe_load(
                    (home / "skill-bundles" / f"{name}.yaml").read_text(encoding="utf-8")
                )
                print(f"/{name}")
                for skill in bundle["skills"]:
                    print(f"- {skill}")
            elif args[:2] == ["bundles", "delete"]:
                name = args[2]
                bundle_path = home / "skill-bundles" / f"{name}.yaml"
                if not bundle_path.exists():
                    print(f"No bundle at {bundle_path}", file=sys.stderr)
                    raise SystemExit(1)
                bundle_path.unlink()
                print(f"Deleted bundle: /{name}")
            else:
                raise SystemExit(f"unsupported fake Hermes command: {args}")
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    command = bin_dir / "hermes.ps1"
    command.write_text(
        f'& "{sys.executable}" "{fake}" @args\nexit $LASTEXITCODE\n',
        encoding="utf-8",
    )
    return bin_dir


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    if shutil.which("pwsh") is None:
        pytest.skip("PowerShell 7 is required")
    bin_dir = _write_fake_hermes(tmp_path)
    hermes_home = tmp_path / "hermes"
    env = dict(os.environ)
    env["HERMES_HOME"] = str(hermes_home)
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    return env, hermes_home


def _command(
    repository_root: Path,
    *,
    remove: bool = False,
    hermes_executable: Path | None = None,
) -> list[str]:
    command = [
        "pwsh",
        "-NoProfile",
        "-File",
        str(SCRIPT),
        "-RepositoryRoot",
        str(repository_root),
    ]
    if hermes_executable is not None:
        command.extend(("-HermesExecutable", str(hermes_executable)))
    if remove:
        command.append("-Remove")
    return command


def _copy_skill_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    destination = repository / "agenten" / "agent_factory" / "skills"
    shutil.copytree(ROOT / "agenten" / "agent_factory" / "skills", destination)
    return repository


def _rewrite_skill_text_line_endings(repository: Path, newline: bytes) -> None:
    skill_root = repository / "agenten" / "agent_factory" / "skills"
    for path in skill_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_ASSET_SUFFIXES:
            continue
        canonical = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        path.write_bytes(canonical.replace(b"\n", newline))


def test_configure_script_preserves_external_dirs_and_is_idempotent(
    tmp_path: Path,
) -> None:
    env, hermes_home = _environment(tmp_path)
    unrelated = (tmp_path / "unrelated-skills").resolve()
    unrelated.mkdir()
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"skills": {"external_dirs": [str(unrelated)]}}),
        encoding="utf-8",
    )

    first = subprocess.run(
        _command(ROOT), env=env, text=True, capture_output=True, check=True
    )
    second = subprocess.run(
        _command(ROOT), env=env, text=True, capture_output=True, check=True
    )

    skill_root = str((ROOT / "agenten" / "agent_factory" / "skills").resolve())
    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["skills"]["external_dirs"] == [str(unrelated), skill_root]
    assert config["skills"]["external_dirs"].count(skill_root) == 1
    bundle = yaml.safe_load(
        (hermes_home / "skill-bundles" / "captain-agent-factory-loop.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert tuple(bundle["skills"]) == SKILL_NAMES
    assert "configured" in first.stdout.lower()
    assert "already configured" in second.stdout.lower()


LEARNING_BASELINE = "captain-hermes-learning-baseline.json"


def test_configure_script_enables_hermes_learning_with_approval(
    tmp_path: Path,
) -> None:
    env, hermes_home = _environment(tmp_path)

    first = subprocess.run(
        _command(ROOT), env=env, text=True, capture_output=True, check=True
    )
    second = subprocess.run(
        _command(ROOT), env=env, text=True, capture_output=True, check=True
    )

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    # Learning stays on, but every skill write Hermes makes (/learn, the
    # background review fork, Captain one-shot runs) is staged for approval.
    assert config["skills"]["write_approval"] is True
    assert config["skills"]["guard_agent_created"] is True
    assert config["skills"]["creation_nudge_interval"] == 10
    assert "/skills pending" in first.stdout
    assert "configured" in first.stdout.lower()
    assert "already configured" in second.stdout.lower()


def test_remove_restores_learning_settings_recorded_before_first_configure(
    tmp_path: Path,
) -> None:
    env, hermes_home = _environment(tmp_path)
    unrelated = (tmp_path / "unrelated-skills").resolve()
    unrelated.mkdir()
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "skills": {
                    "external_dirs": [str(unrelated)],
                    "write_approval": False,
                    "creation_nudge_interval": 15,
                },
                "memory": {"write_approval": True},
            }
        ),
        encoding="utf-8",
    )

    subprocess.run(_command(ROOT), env=env, text=True, capture_output=True, check=True)
    # A second configure must not replace the recorded pre-Captain baseline
    # with Captain's own values.
    subprocess.run(_command(ROOT), env=env, text=True, capture_output=True, check=True)
    assert (hermes_home / LEARNING_BASELINE).exists()

    removed = subprocess.run(
        _command(ROOT, remove=True),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["skills"]["external_dirs"] == [str(unrelated)]
    assert config["skills"]["write_approval"] is False
    assert config["skills"]["creation_nudge_interval"] == 15
    assert config["skills"].get("guard_agent_created", False) is False
    assert config["memory"] == {"write_approval": True}
    assert not (hermes_home / LEARNING_BASELINE).exists()
    assert "learning" in removed.stdout.lower()


def test_remove_unsets_learning_keys_that_were_absent_before_configure(
    tmp_path: Path,
) -> None:
    env, hermes_home = _environment(tmp_path)

    subprocess.run(_command(ROOT), env=env, text=True, capture_output=True, check=True)
    subprocess.run(
        _command(ROOT, remove=True), env=env, text=True, capture_output=True, check=True
    )

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8")) or {}
    skills = config.get("skills", {})
    assert "creation_nudge_interval" not in skills
    assert skills.get("write_approval", False) is False
    assert skills.get("guard_agent_created", False) is False
    assert not (hermes_home / LEARNING_BASELINE).exists()


def test_configure_script_rejects_changed_or_missing_released_skill(
    tmp_path: Path,
) -> None:
    env, hermes_home = _environment(tmp_path)
    repository = _copy_skill_repository(tmp_path)
    _rewrite_skill_text_line_endings(repository, b"\n")
    changed = (
        repository
        / "agenten"
        / "agent_factory"
        / "skills"
        / "captain-factory-discover"
        / "SKILL.md"
    )
    changed.write_text(changed.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")

    result = subprocess.run(
        _command(repository), env=env, text=True, capture_output=True
    )

    assert result.returncode != 0
    assert "digest mismatch" in (result.stdout + result.stderr).lower()
    assert not (hermes_home / "config.yaml").exists()
    assert not (hermes_home / LEARNING_BASELINE).exists()


@pytest.mark.parametrize(
    "newline", (b"\n", b"\r\n", b"\r"), ids=("lf", "crlf", "cr")
)
def test_configure_script_accepts_released_text_assets_across_line_endings(
    tmp_path: Path,
    newline: bytes,
) -> None:
    env, hermes_home = _environment(tmp_path)
    repository = _copy_skill_repository(tmp_path)
    _rewrite_skill_text_line_endings(repository, newline)

    result = subprocess.run(
        _command(repository), env=env, text=True, capture_output=True, check=True
    )

    assert "configured" in result.stdout.lower()
    assert (hermes_home / "skill-bundles" / "captain-agent-factory-loop.yaml").is_file()


def _directory_manifest_digest(directory: Path) -> str:
    command = """
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $env:CAPTAIN_DIGEST_SCRIPT, [ref]$tokens, [ref]$errors
    )
    $functionAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq 'Get-DirectoryManifestDigest'
        },
        $true
    )
    if ($null -eq $functionAst) {
        throw 'Digest function was not found.'
    }
    $extensionsAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
                $node.Left.Extent.Text -eq '$textAssetExtensions'
        },
        $true
    )
    if ($null -eq $extensionsAst) {
        throw 'Text asset extension assignment was not found.'
    }
    Invoke-Expression $extensionsAst.Extent.Text
    Invoke-Expression $functionAst.Extent.Text
    Get-DirectoryManifestDigest -Directory $env:CAPTAIN_DIGEST_DIRECTORY
    """
    env = dict(os.environ)
    env["CAPTAIN_DIGEST_SCRIPT"] = str(SCRIPT)
    env["CAPTAIN_DIGEST_DIRECTORY"] = str(directory)
    result = subprocess.run(
        ("pwsh", "-NoProfile", "-Command", command),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def test_python_skill_asset_digest_is_line_ending_independent(tmp_path: Path) -> None:
    lf_root = tmp_path / "lf"
    crlf_root = tmp_path / "crlf"
    for root, content in (
        (lf_root, b"print('skill helper')\n"),
        (crlf_root, b"print('skill helper')\r\n"),
    ):
        helper = root / "scripts" / "helper.py"
        helper.parent.mkdir(parents=True)
        helper.write_bytes(content)

    assert _directory_manifest_digest(lf_root) == _directory_manifest_digest(crlf_root)


def test_unknown_template_binary_digest_remains_byte_exact(tmp_path: Path) -> None:
    lf_root = tmp_path / "lf"
    cr_root = tmp_path / "cr"
    for root, content in (
        (lf_root, b"\x00payload\nbytes\xff"),
        (cr_root, b"\x00payload\rbytes\xff"),
    ):
        payload = root / "templates" / "payload.bin"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(content)

    assert _directory_manifest_digest(lf_root) != _directory_manifest_digest(cr_root)


@pytest.mark.parametrize("source", ("local", "external"))
def test_configure_script_rejects_nested_frontmatter_shadow(
    tmp_path: Path, source: str
) -> None:
    env, hermes_home = _environment(tmp_path)
    shadow_root = (
        hermes_home / "skills" if source == "local" else tmp_path / "shadow-skills"
    )
    shadow = shadow_root / "nested" / "alias-directory"
    shadow.mkdir(parents=True)
    (shadow / "SKILL.md").write_text(
        f"---\nname: {SKILL_NAMES[0]}\ndescription: shadow\n---\n",
        encoding="utf-8",
    )
    if source == "external":
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(
            yaml.safe_dump({"skills": {"external_dirs": [str(shadow_root.resolve())]}}),
            encoding="utf-8",
        )

    result = subprocess.run(_command(ROOT), env=env, text=True, capture_output=True)

    assert result.returncode != 0
    assert "shadow" in (result.stdout + result.stderr).lower()
    assert not (hermes_home / "skill-bundles").exists()


def test_configure_script_rejects_folded_yaml_frontmatter_shadow(
    tmp_path: Path,
) -> None:
    env, hermes_home = _environment(tmp_path)
    shadow = hermes_home / "skills" / "nested" / "alias-directory"
    shadow.mkdir(parents=True)
    (shadow / "SKILL.md").write_text(
        f"---\nname: >-\n  {SKILL_NAMES[0]}\ndescription: shadow\n---\n",
        encoding="utf-8",
    )

    result = subprocess.run(_command(ROOT), env=env, text=True, capture_output=True)

    assert result.returncode != 0
    assert "shadow" in (result.stdout + result.stderr).lower()
    assert not (hermes_home / "skill-bundles").exists()


def test_configure_script_fails_closed_on_invalid_shadow_frontmatter(
    tmp_path: Path,
) -> None:
    env, hermes_home = _environment(tmp_path)
    shadow = hermes_home / "skills" / "nested" / "invalid-frontmatter"
    shadow.mkdir(parents=True)
    (shadow / "SKILL.md").write_text(
        "---\nname: [unterminated\n---\n",
        encoding="utf-8",
    )

    result = subprocess.run(_command(ROOT), env=env, text=True, capture_output=True)

    assert result.returncode != 0
    assert "safely parse" in (result.stdout + result.stderr).lower()
    assert not (hermes_home / "config.yaml").exists()
    assert not (hermes_home / "skill-bundles").exists()


def test_configure_script_preserves_hermes_home_relative_external_dir(
    tmp_path: Path,
) -> None:
    env, hermes_home = _environment(tmp_path)
    relative = Path("team-skills")
    (hermes_home / relative).mkdir(parents=True)
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"skills": {"external_dirs": [relative.as_posix()]}}),
        encoding="utf-8",
    )

    subprocess.run(_command(ROOT), env=env, text=True, capture_output=True, check=True)

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["skills"]["external_dirs"] == [
        relative.as_posix(),
        str((ROOT / "agenten" / "agent_factory" / "skills").resolve()),
    ]


def test_configure_script_rejects_disabled_skill_before_bundle(tmp_path: Path) -> None:
    env, hermes_home = _environment(tmp_path)
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"skills": {"disabled": [SKILL_NAMES[0]]}}), encoding="utf-8"
    )

    result = subprocess.run(_command(ROOT), env=env, text=True, capture_output=True)

    assert result.returncode != 0
    assert "missing or disabled" in (result.stdout + result.stderr).lower()
    assert not (hermes_home / "skill-bundles").exists()


def test_remove_deletes_only_repository_path_and_factory_bundle(tmp_path: Path) -> None:
    env, hermes_home = _environment(tmp_path)
    unrelated = (tmp_path / "unrelated-skills").resolve()
    unrelated.mkdir()
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"skills": {"external_dirs": [str(unrelated)]}}),
        encoding="utf-8",
    )
    subprocess.run(_command(ROOT), env=env, text=True, capture_output=True, check=True)

    removed = subprocess.run(
        _command(ROOT, remove=True),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["skills"]["external_dirs"] == [str(unrelated)]
    assert not (
        hermes_home / "skill-bundles" / "captain-agent-factory-loop.yaml"
    ).exists()
    assert "removed" in removed.stdout.lower()

    removed_again = subprocess.run(
        _command(ROOT, remove=True),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["skills"]["external_dirs"] == [str(unrelated)]
    assert "already removed" in removed_again.stdout.lower()


def test_remove_succeeds_when_key_path_and_bundle_are_absent(tmp_path: Path) -> None:
    env, hermes_home = _environment(tmp_path)

    result = subprocess.run(
        _command(ROOT, remove=True),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "already removed" in result.stdout.lower()
    calls = (hermes_home / "calls.log").read_text(encoding="utf-8")
    assert '["bundles", "delete", "captain-agent-factory-loop"]' in calls
    assert not (hermes_home / "config.yaml").exists()


def test_remove_twice_is_idempotent_with_real_hermes_cli(tmp_path: Path) -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("PowerShell 7 is required")
    hermes = shutil.which("hermes")
    if hermes is None:
        pytest.skip("Hermes CLI is required")
    hermes_home = tmp_path / "real-hermes"
    env = dict(os.environ)
    env["HERMES_HOME"] = str(hermes_home)
    command = _command(ROOT, remove=True, hermes_executable=Path(hermes))

    first = subprocess.run(command, env=env, text=True, capture_output=True)
    second = subprocess.run(command, env=env, text=True, capture_output=True)

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert "already removed" in first.stdout.lower()
    assert "already removed" in second.stdout.lower()
    assert not (hermes_home / "config.yaml").exists()
    assert not (hermes_home / "skill-bundles").exists()


def test_runbook_documents_setup_verification_and_scoped_rollback() -> None:
    text = (ROOT / "docs" / "AGENT_FACTORY_RUNBOOK.md").read_text(encoding="utf-8")

    for phrase in (
        "configure-hermes-factory-skills.ps1",
        "configure-official-n8n-skills.ps1",
        "n8n-skills@n8n-io",
        "using-n8n-skills-official",
        "codex mcp get n8n --json",
        "hermes skills list --enabled-only",
        "hermes bundles show captain-agent-factory-loop",
        "-Remove",
        "preserves unrelated external directories",
        "skills.write_approval",
        "/skills pending",
        "/skills approve",
        "/learn",
    ):
        assert phrase.lower() in text.lower()
    assert "hermes skills reset" not in text.lower()
