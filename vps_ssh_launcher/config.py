"""Local target configuration, profile selection and authentication precedence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

from .contracts import MIN_PORT, MAX_PORT

APP_CONFIG_DIR = "vps-ssh-launcher"
APP_CONFIG_FILE = "target.json"
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def coerce_port(value: Any, *, context: str) -> int:
    """Normalize a port value and fail fast on invalid input."""
    if isinstance(value, bool):
        raise ValueError(
            f"{context}: port must be an integer {MIN_PORT}-{MAX_PORT}, got {value!r}."
        )
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.strip().isdigit():
        port = int(value.strip())
    else:
        raise ValueError(
            f"{context}: port must be an integer {MIN_PORT}-{MAX_PORT}, got {value!r}."
        )
    if not MIN_PORT <= port <= MAX_PORT:
        raise ValueError(
            f"{context}: port must be an integer {MIN_PORT}-{MAX_PORT}, got {port!r}."
        )
    return port


def _cli_password_arg(args: Any) -> str | None:
    password = getattr(args, "password", None)
    return password if isinstance(password, str) and password else None


def _cli_key_arg(args: Any) -> str | None:
    key = getattr(args, "key", None)
    return key.strip() if isinstance(key, str) and key.strip() else None


def _allow_agent_arg(args: Any) -> bool:
    return bool(getattr(args, "allow_agent", False))


def _has_cli_auth_override(args: Any) -> bool:
    return bool(
        _allow_agent_arg(args)
        or _cli_password_arg(args) is not None
        or _cli_key_arg(args) is not None
    )


def resolve_auth_for_entry(
    entry: dict[str, Any],
    args: Any,
    *,
    config_dir: Path | None = None,
) -> tuple[str | None, str | None]:
    """Resolve password/key after applying CLI authentication overrides."""
    cli_key = _cli_key_arg(args)
    if cli_key is not None:
        return None, cli_key

    cli_password = _cli_password_arg(args)
    if cli_password is not None:
        return cli_password, None

    if _allow_agent_arg(args):
        return None, None

    profile_key = _resolve_key(entry, config_dir=config_dir)
    if profile_key is not None:
        return None, profile_key
    return _resolve_password(entry), None


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"Unable to read config file: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file: {path}") from exc
    if not isinstance(config, dict):
        raise ValueError("Config root must be a JSON object.")
    return cast(dict[str, Any], config)


def _user_config_path() -> Path:
    if os.name == "nt":
        base_dir = os.environ.get("APPDATA")
        if base_dir:
            return Path(base_dir) / APP_CONFIG_DIR / APP_CONFIG_FILE
    return Path.home() / ".config" / APP_CONFIG_DIR / APP_CONFIG_FILE


def resolve_default_config_path(script_dir: Path) -> Path | None:
    """Prefer user-local override, then legacy repo-local config."""
    candidates = (
        _user_config_path(),
        script_dir / "target.json",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _resolve_password(entry: dict[str, Any]) -> str | None:
    """Resolve password: prefer password_env, then plaintext password."""
    env_name = entry.get("password_env")
    if env_name:
        env_name = cast(str, env_name).strip()
        pw = os.environ.get(env_name)
        if not pw:
            raise ValueError(f"Environment variable '{env_name}' is not set or empty.")
        return pw
    password = entry.get("password")
    return password if isinstance(password, str) else None


def _resolve_key(
    entry: dict[str, Any], *, config_dir: Path | None = None
) -> str | None:
    """Resolve SSH key path, optionally relative to config directory."""
    key = entry.get("key")
    if not isinstance(key, str):
        return None
    key_text = key.strip()
    if not key_text:
        return None
    key_path = Path(key_text).expanduser()
    if config_dir is not None and not key_path.is_absolute():
        return str(config_dir / key_path)
    return str(key_path)


def validate_profile(
    entry: dict[str, Any],
    name: str,
    *,
    require_auth: bool = True,
) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"Profile '{name}' must be an object.")
    host = entry.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ValueError(f"Profile '{name}': 'host' is required and must be a string.")
    coerce_port(entry.get("port", 22), context=f"Profile '{name}'")
    user = entry.get("user")
    if not isinstance(user, str) or not user.strip():
        raise ValueError(f"Profile '{name}': 'user' is required and must be a string.")
    password = entry.get("password")
    password_env = entry.get("password_env")
    key = entry.get("key")

    if password is not None and (not isinstance(password, str) or not password):
        raise ValueError(f"Profile '{name}': 'password' must be a non-empty string.")
    if password_env is not None and (
        not isinstance(password_env, str) or not password_env.strip()
    ):
        raise ValueError(
            f"Profile '{name}': 'password_env' must be a non-empty string."
        )
    if key is not None and (not isinstance(key, str) or not key.strip()):
        raise ValueError(f"Profile '{name}': 'key' must be a non-empty string.")

    if require_auth:
        has_auth = password is not None or password_env is not None or key is not None
        if not has_auth:
            raise ValueError(
                f"Profile '{name}': no auth method. Set 'password', 'password_env', or 'key'."
            )


def _print_available_profiles(profiles: dict[str, Any], names: list[str]) -> None:
    print("Available VPS profiles:")
    for i, n in enumerate(names, 1):
        p = profiles[n]
        print(
            f"  {i}. {n} "
            f"({p.get('user', '?')}@{p.get('host', '?')}:{p.get('port', 22)})"
        )


def _read_profile_choice() -> str:
    try:
        return input("Select VPS (number or name): ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        raise ValueError("Cancelled.") from None


def _resolve_profile_choice(
    choice: str,
    profiles: dict[str, Any],
    names: list[str],
) -> str:
    if not choice:
        raise ValueError("No profile selected.")
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(names):
            return names[idx - 1]
        raise ValueError(f"Number out of range: {idx}")
    if choice in profiles:
        return choice
    raise ValueError(f"Unknown profile: {choice}")


def _select_interactive_profile(profiles: dict[str, Any]) -> str:
    names = list(profiles)
    _print_available_profiles(profiles, names)
    return _resolve_profile_choice(_read_profile_choice(), profiles, names)


def select_profile(
    profiles: dict[str, Any],
    default_name: str | None,
    requested_name: str | None,
) -> str:
    if requested_name:
        if requested_name not in profiles:
            raise ValueError(
                f"Unknown profile '{requested_name}'. Available: {', '.join(profiles)}"
            )
        return requested_name

    if default_name:
        if default_name not in profiles:
            raise ValueError(
                f"Default profile '{default_name}' not found. "
                f"Available: {', '.join(profiles)}"
            )
        return default_name

    if len(profiles) == 1:
        return next(iter(profiles))

    stdin_is_interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if not stdin_is_interactive:
        raise ValueError(
            "Multiple profiles found and no default/profile selected. "
            "Pass --profile or set 'default' in target.json. "
            f"Available: {', '.join(profiles)}"
        )

    return _select_interactive_profile(profiles)


def _select_config_entry(
    config: dict[str, Any],
    requested_profile: str | None,
) -> tuple[str, dict[str, Any]]:
    if "profiles" not in config:
        if requested_profile is not None:
            raise ValueError(
                "Explicit --profile requires a named profiles configuration."
            )
        return "(root)", config

    profiles = config["profiles"]
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("'profiles' must be a non-empty object.")
    profiles = cast(dict[str, Any], profiles)
    default_name = config.get("default")
    if default_name is not None and not isinstance(default_name, str):
        raise ValueError("'default' must be a string when set.")
    name = select_profile(profiles, default_name, requested_profile)
    return name, cast(dict[str, Any], profiles[name])


def apply_config(args: argparse.Namespace) -> None:
    """Merge config file into CLI args (CLI takes precedence)."""
    direct_target_complete = bool(
        isinstance(args.host, str)
        and args.host.strip()
        and isinstance(args.user, str)
        and args.user.strip()
        and _has_cli_auth_override(args)
    )
    if args.config is None and args.profile is None and direct_target_complete:
        return

    default_config = resolve_default_config_path(SOURCE_ROOT)
    config_path = args.config or (str(default_config) if default_config else None)
    if not config_path:
        return

    config_file = Path(config_path).expanduser()
    config = load_config(config_file)
    name, entry = _select_config_entry(config, args.profile)

    cli_key = _cli_key_arg(args)
    cli_has_auth_override = _has_cli_auth_override(args)

    validate_profile(entry, name, require_auth=not cli_has_auth_override)

    if args.host is None:
        args.host = cast(str, entry["host"]).strip()

    if args.port is None:
        args.port = coerce_port(entry.get("port", 22), context=f"Profile '{name}'")
    else:
        args.port = coerce_port(args.port, context="CLI --port")

    if args.user is None:
        args.user = cast(str, entry["user"]).strip()
    profile_password, profile_key = resolve_auth_for_entry(
        entry,
        args,
        config_dir=config_file.parent,
    )
    if args.password is None:
        args.password = profile_password
    if cli_key is not None:
        args.key = cli_key
    elif args.key is None:
        args.key = profile_key
