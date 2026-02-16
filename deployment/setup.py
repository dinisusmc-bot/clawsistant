#!/usr/bin/env python3
import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

REQUIRED_KEYS = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENCLAW_GATEWAY_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_ALLOW_FROM",
]


def load_env(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing env file: {path}")
    env = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip().strip("\"").strip("'")
        value = os.path.expandvars(value)
        env[key.strip()] = value
    return env


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_file(path: Path, content: str, mode: int | None = None) -> None:
    ensure_dir(path.parent)
    path.write_text(content)
    if mode is not None:
        path.chmod(mode)


def render_template(template_path: Path, replacements: dict) -> str:
    content = template_path.read_text()
    for key, value in replacements.items():
        content = content.replace(key, value)
    return content


def copy_tree(src: Path, dest: Path) -> None:
    ensure_dir(dest)
    shutil.copytree(src, dest, dirs_exist_ok=True)


def mark_executable(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in (".sh", ".py"):
            path.chmod(0o755)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=False)


def has_command(name: str) -> bool:
    return shutil.which(name) is not None


def run_maybe_sudo(cmd: list[str]) -> None:
    if os.geteuid() == 0:
        run(cmd)
        return
    if has_command("sudo"):
        run(["sudo"] + cmd)
        return
    run(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw deployment setup")
    parser.add_argument("--start-postgres", action="store_true", help="Start Postgres via docker compose")
    args = parser.parse_args()

    env_path = BASE_DIR / ".env"
    try:
        env = load_env(env_path)
    except FileNotFoundError as exc:
        print(str(exc))
        print("Copy .env.example to .env and fill in values.")
        return 1

    missing = [key for key in REQUIRED_KEYS if not env.get(key)]
    if missing:
        print("Missing required .env values:")
        for key in missing:
            print(f"- {key}")
        return 1

    home = Path.home()
    openclaw_version = env.get("OPENCLAW_VERSION", "2026.2.9")
    openclaw_gateway_port = env.get("OPENCLAW_GATEWAY_PORT", "18789")
    openclaw_cli = env.get(
        "OPENCLAW_CLI_PATH",
        str(home / ".local" / "openclaw" / "node_modules" / "openclaw" / "dist" / "index.js"),
    )

    ensure_dir(home / "projects")
    ensure_dir(home / "tmp")

    agent_skills_repo = env.get("AGENT_SKILLS_REPO", "")
    agent_skills_dir = home / "projects" / "agent-skills"
    if agent_skills_repo and not agent_skills_dir.exists():
        run(["git", "clone", agent_skills_repo, str(agent_skills_dir)])

    workspace_src = TEMPLATES_DIR / "workspace"
    workspace_dest = home / ".openclaw" / "workspace"
    copy_tree(workspace_src, workspace_dest)
    mark_executable(workspace_dest)

    workspace_coder_src = TEMPLATES_DIR / "workspace-coder"
    workspace_coder_dest = home / ".openclaw" / ".openclaw" / "workspace-coder"
    copy_tree(workspace_coder_src, workspace_coder_dest)

    workspace_tester_src = TEMPLATES_DIR / "workspace-tester"
    workspace_tester_dest = home / ".openclaw" / ".openclaw" / "workspace-tester"
    copy_tree(workspace_tester_src, workspace_tester_dest)

    agents_src = TEMPLATES_DIR / "agents"
    agents_dest = home / ".openclaw" / ".openclaw" / "agents"
    copy_tree(agents_src, agents_dest)

    replacements = {
        "__HOME__": str(home),
        "__OPENAI_API_KEY__": env["OPENAI_API_KEY"],
        "__OPENAI_BASE_URL__": env["OPENAI_BASE_URL"],
        "__OPENCLAW_GATEWAY_TOKEN__": env["OPENCLAW_GATEWAY_TOKEN"],
        "__OPENCLAW_GATEWAY_PORT__": openclaw_gateway_port,
        "__OPENCLAW_VERSION__": openclaw_version,
        "__GENERATED_AT__": datetime.now(timezone.utc).isoformat(),
        "__TELEGRAM_BOT_TOKEN__": env["TELEGRAM_BOT_TOKEN"],
        "__TELEGRAM_CHAT_ID__": env["TELEGRAM_CHAT_ID"],
        "__TELEGRAM_ALLOW_FROM__": env["TELEGRAM_ALLOW_FROM"],
        "__OPENCLAW_CLI__": openclaw_cli,
        "__PATH__": os.environ.get("PATH", "/usr/bin:/bin"),
        "__OPENCLAW_HOME__": str(home / ".openclaw"),
    }

    openclaw_config = render_template(TEMPLATES_DIR / "openclaw.json", replacements)
    openclaw_config_path = home / ".openclaw" / ".openclaw" / "openclaw.json"
    write_file(openclaw_config_path, openclaw_config)

    summarize_config = (TEMPLATES_DIR / "summarize" / "config.json").read_text()
    write_file(home / ".summarize" / "config.json", summarize_config)

    telegram_env = "\n".join(
        [
            f"TELEGRAM_BOT_TOKEN={env['TELEGRAM_BOT_TOKEN']}",
            f"TELEGRAM_CHAT_ID={env['TELEGRAM_CHAT_ID']}",
            "",
        ]
    )
    write_file(home / ".telegram-env", telegram_env, mode=0o600)

    postgres_env_lines = [
        f"OPENCLAW_POSTGRES_HOST={env.get('OPENCLAW_POSTGRES_HOST', 'localhost')}",
        f"OPENCLAW_POSTGRES_PORT={env.get('OPENCLAW_POSTGRES_PORT', '5433')}",
        f"OPENCLAW_POSTGRES_DB={env.get('OPENCLAW_POSTGRES_DB', 'openclaw')}",
        f"OPENCLAW_POSTGRES_USER={env.get('OPENCLAW_POSTGRES_USER', 'openclaw')}",
        f"OPENCLAW_POSTGRES_PASSWORD={env.get('OPENCLAW_POSTGRES_PASSWORD', '')}",
        f"OPENAI_API_KEY={env.get('OPENAI_API_KEY', '')}",
        f"OPENAI_TOOLS_API_KEY={env.get('OPENAI_TOOLS_API_KEY', env.get('OPENAI_API_KEY', ''))}",
        f"OPENAI_BASE_URL={env.get('OPENAI_BASE_URL', 'http://ai-services:8010/v1')}",
        f"PRIMARY_TEXT_MODEL={env.get('PRIMARY_TEXT_MODEL', 'qwen3-coder')}",
        f"PRIMARY_VISION_MODEL={env.get('PRIMARY_VISION_MODEL', 'internvl')}",
        f"EMBEDDINGS_MODEL={env.get('EMBEDDINGS_MODEL', 'bge-small-en-v1.5')}",
        f"MAX_ATTEMPTS={env.get('MAX_ATTEMPTS', '2')}",
        f"TESTER_MAX_ATTEMPTS={env.get('TESTER_MAX_ATTEMPTS', '3')}",
        f"VERBOSE_TASK_LOGS={env.get('VERBOSE_TASK_LOGS', '1')}",
        f"TASK_HEARTBEAT_SEC={env.get('TASK_HEARTBEAT_SEC', '30')}",
        f"TEST_CLEANUP_AFTER={env.get('TEST_CLEANUP_AFTER', '1')}",
        f"TESTER_TIMEOUT={env.get('TESTER_TIMEOUT', '2400')}",
        f"TESTER_STEP_TIMEOUT={env.get('TESTER_STEP_TIMEOUT', '600')}",
        f"CHAT_ROUTER_PORT={env.get('CHAT_ROUTER_PORT', '18801')}",
        f"CHAT_ROUTER_URL={env.get('CHAT_ROUTER_URL', 'http://127.0.0.1:18801/route')}",
        f"CHAT_ROUTER_ASK_TIMEOUT_SEC={env.get('CHAT_ROUTER_ASK_TIMEOUT_SEC', '180')}",
        f"TELEGRAM_ACK_REACTION={env.get('TELEGRAM_ACK_REACTION', '✅')}",
        f"MODEL_HEALTH_RETRIES={env.get('MODEL_HEALTH_RETRIES', '2')}",
        f"MODEL_HEALTH_CONNECT_TIMEOUT_SEC={env.get('MODEL_HEALTH_CONNECT_TIMEOUT_SEC', '5')}",
        f"MODEL_HEALTH_MODELS_TIMEOUT_SEC={env.get('MODEL_HEALTH_MODELS_TIMEOUT_SEC', '20')}",
        f"MODEL_HEALTH_CHAT_TIMEOUT_SEC={env.get('MODEL_HEALTH_CHAT_TIMEOUT_SEC', '90')}",
        f"MODEL_HEALTH_EMBED_TIMEOUT_SEC={env.get('MODEL_HEALTH_EMBED_TIMEOUT_SEC', '60')}",
        "",
    ]
    write_file(home / ".env", "\n".join(postgres_env_lines), mode=0o600)

    systemd_src = TEMPLATES_DIR / "systemd"
    systemd_dest = home / ".config" / "systemd" / "user"
    ensure_dir(systemd_dest)
    for unit in systemd_src.iterdir():
        if unit.name == "openclaw-gateway.service":
            rendered = render_template(unit, replacements)
            write_file(systemd_dest / unit.name, rendered)
        else:
            shutil.copy2(unit, systemd_dest / unit.name)

    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", "openclaw-task-manager-db.timer"])
    run(["systemctl", "--user", "enable", "--now", "openclaw-telegram-commands.timer"])
    run(["systemctl", "--user", "enable", "--now", "openclaw-model-health.timer"])
    run(["systemctl", "--user", "enable", "--now", "openclaw-chat-router.service"])
    run(["systemctl", "--user", "enable", "--now", "openclaw-gateway.service"])

    # Install binaries for enabled skills (best-effort)
    if has_command("apt-get"):
        run_maybe_sudo(["apt-get", "update"])
        run_maybe_sudo(["apt-get", "install", "-y", "tmux"])

    if has_command("npm"):
        run_maybe_sudo(["npm", "i", "-g", "clawhub", "summarize"])

    if args.start_postgres:
        compose_file = BASE_DIR / "docker-compose.openclaw.yml"
        if compose_file.exists():
            run(["docker", "compose", "-f", str(compose_file), "up", "-d"])
        else:
            print("docker-compose.openclaw.yml not found; skipping Postgres startup")

    print("Deployment setup complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
