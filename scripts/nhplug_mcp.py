"""Launch the official NH MCP with separate live-readonly and mock hosts."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values


def build_environment(
    env_file: Path, mode: str, account: str, inherited: dict[str, str], *,
    guarded_paper_root: Path | None = None,
) -> dict[str, str]:
    if mode not in {"readonly", "paper"}:
        raise ValueError("mode must be readonly or paper; live orders are unavailable")
    if guarded_paper_root is not None and mode != "paper":
        raise ValueError("guarded paper server requires paper mode")
    values = dotenv_values(env_file, interpolate=False)
    for name in ("APP_KEY", "APP_SECRET"):
        if not (values.get(name) or "").strip():
            raise ValueError(f"missing {name} in the selected credential file")
    # Parse assignments only: sourcing .envrc would execute shell code and export unrelated keys.
    env = {key: value for key, value in inherited.items()
           if key in {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
    env.update({
        "NHPLUG_APP_KEY": values["APP_KEY"].strip(),
        "NHPLUG_APP_SECRET": values["APP_SECRET"].strip(),
        "NHPLUG_DEFAULT_ACCOUNT": account,
        "NHPLUG_BASE_URL": f"https://{'moapi' if mode == 'paper' else 'api'}.nhplug.com:8443",
        "NHPLUG_AUTH_URL": "https://api.nhplug.com:8443",
        "NHPLUG_ENABLE_TRADING": "true" if mode == "paper" else "false",
        "NHPLUG_ALLOW_HOSTS": "",
    })
    if guarded_paper_root is not None:
        env["NHPLUG_PAPER_ETF_STOP_PATH"] = str(
            guarded_paper_root.expanduser().resolve() / "nhplug/shared-etf-paper/STOP"
        )
    return env


def launch(node: str, server: Path, env: dict[str, str], *, windows: bool = os.name == "nt") -> None:
    """Become Node. Windows has no such exec (it starts a new process and ends this one, so the MCP client loses its pipe):
    there this process waits with the same standard streams and ends with Node's exit code, without a console window."""
    if not windows:
        os.chdir(server.parent.parent)
        return os.execve(node, [node, str(server)], env)
    sys.exit(subprocess.run([node, str(server)], env=env, cwd=server.parent.parent,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("readonly", "paper"), required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--account", default="")
    parser.add_argument("--guarded-paper", action="store_true")
    args = parser.parse_args()
    if args.guarded_paper and args.mode != "paper":
        parser.error("guarded paper server requires paper mode")
    runtime = Path(os.environ.get("VIBE_TRADING_HOME") or Path.home() / ".vibe-trading").expanduser().resolve()
    integration = "nhplug-mcp-etf7" if args.guarded_paper else "nhplug-mcp"
    server = runtime / "integrations" / integration / "dist/index.js"
    node = shutil.which("node")
    if not node or not server.is_file():
        parser.error("Node.js or the official NH MCP installation is missing")
    try:
        env = build_environment(args.env_file, args.mode, args.account, dict(os.environ),
                                guarded_paper_root=runtime if args.guarded_paper else None)
    except (ValueError, OSError):
        parser.error("NH credentials could not be loaded; check APP_KEY and APP_SECRET in --env-file")
    launch(node, server, env)


if __name__ == "__main__":
    main()
