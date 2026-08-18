#!/usr/bin/env python3
"""mira launch — run a coding agent or client against the local Mira server.

Sets the right environment for the tool and execs it, so the tool talks to
Mira instead of a cloud API. Both protocols go through the compatibility
bridge, which pins client requests to the model already loaded by Mira.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

API = os.environ.get("ARGUS_API", "http://127.0.0.1:8090")
BRIDGE = os.environ.get("ARGUS_BRIDGE", "http://127.0.0.1:8092")
CONFIGURED_MODEL = os.environ.get("ARGUS_MODEL")
KEY = "mira"
try:
    MAX_TOKENS = int(os.environ.get("ARGUS_MAX_TOKENS", "4096"))
except ValueError:
    MAX_TOKENS = 4096

# The protocol controls which client environment variables are populated.
TOOLS = {
    "codex": {
        "name": "Codex CLI", "bin": "codex", "protocol": "openai",
        "install": "npm i -g @openai/codex",
        "desc": "OpenAI's coding agent",
    },
    "aider": {
        "name": "aider", "bin": "aider", "protocol": "openai",
        "install": "pip install aider-install && aider-install",
        "desc": "Pair programming in your terminal",
        "args": lambda model, base: ["--openai-api-base", base, "--openai-api-key", KEY,
                                     "--model", f"openai/{model}"],
    },
    "opencode": {
        "name": "OpenCode", "bin": "opencode", "protocol": "openai",
        "install": "npm i -g opencode-ai",
        "desc": "Open-source coding agent",
    },
    "claude": {
        "name": "Claude Code", "bin": "claude", "protocol": "anthropic",
        "install": "npm i -g @anthropic-ai/claude-code",
        "desc": "Anthropic's coding tool (via the Mira Anthropic bridge)",
    },
    "shell": {
        "name": "Shell", "bin": os.environ.get("SHELL", "/bin/zsh"), "protocol": "openai",
        "install": None,
        "desc": "A shell with the API environment already set",
    },
}


def server_model():
    try:
        with urllib.request.urlopen(API + "/health", timeout=3) as r:
            return json.load(r)["loaded_model"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return CONFIGURED_MODEL


def server_context_limit():
    try:
        with urllib.request.urlopen(API + "/health", timeout=3) as r:
            health = json.load(r)
        value = health.get("effective_context_limit") or health.get("loaded_context_size")
        value = int(value)
        return value if value > 0 else 32768
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 32768


def bridge_up():
    try:
        urllib.request.urlopen(BRIDGE + "/health", timeout=2).read()
        return True
    except OSError:
        return False


def ensure_bridge():
    """Start the compatibility bridge if it isn't up yet."""
    if bridge_up():
        return True
    subprocess.Popen([os.path.expanduser("~/.local/bin/argus"), "bridge", "start"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(30):
        if bridge_up():
            return True
        time.sleep(0.5)
    return False


def list_tools():
    print("mira launch <tool> — run a tool against your local model\n")
    for key, t in TOOLS.items():
        have = shutil.which(t["bin"]) is not None
        mark = "✓" if have else "·"
        print(f"  {mark} {key:9s} {t['name']:16s} {t['desc']}")
        if not have and t["install"]:
            print(f"    {'':9s} not installed — {t['install']}")
    print("\n✓ = found on your PATH")


def opencode_config(model, base, context_limit=32768):
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": "mira/local",
        "provider": {
            "mira": {
                "name": "Mira Local",
                "npm": "@ai-sdk/openai-compatible",
                "options": {"apiKey": KEY, "baseURL": base},
                "models": {
                    "local": {
                        "name": model,
                        "limit": {"context": context_limit, "output": MAX_TOKENS},
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    },
                },
            },
        },
    }


def command_args(key, binary, tool, model, where, extra, claude_full=False,
                 opencode_full=False):
    """Build the command line without launching it (also keeps this testable)."""
    args = [binary]
    if callable(tool.get("args")):
        args += tool["args"](model, where)
    # A normal Claude Code startup can inject dozens of user/MCP tools and a
    # very large system prompt.  That is fine for hosted models, but makes a
    # local model spend minutes in prefill.  Bare mode retains Claude's core
    # coding tools while skipping those customizations.
    if key == "claude" and not claude_full and "--bare" not in extra:
        args.append("--bare")
    if key == "opencode" and not opencode_full and "--pure" not in extra:
        args.append("--pure")
    if key == "codex":
        # Codex ignores OPENAI_MODEL for its visible session model, and using
        # an arbitrary model with the built-in `openai` provider is rejected
        # for ChatGPT-authenticated users before a local request is sent.
        # Register the Mira bridge as an explicit Responses-compatible local
        # provider for this process only; the user's Codex config is untouched.
        args += [
            "--config", 'model_provider="mira"',
            "--config", 'model_providers.mira.name="Mira Local"',
            "--config", f"model_providers.mira.base_url={json.dumps(where)}",
            "--config", 'model_providers.mira.env_key="OPENAI_API_KEY"',
            "--config", 'model_providers.mira.wire_api="responses"',
            "--config", "model_providers.mira.requires_openai_auth=false",
        ]
        if "--model" not in extra and "-m" not in extra:
            args += ["--model", model]
    return args + extra


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "list"):
        list_tools()
        return 0

    key = argv[0]
    extra = argv[1:]
    if key not in TOOLS:
        print(f"unknown tool: {key}")
        list_tools()
        return 1
    tool = TOOLS[key]

    model = server_model()
    if not model:
        print(f"Mira isn't answering at {API} — start it first: mira start")
        return 1

    binary = shutil.which(tool["bin"]) or (tool["bin"] if os.path.isabs(tool["bin"]) else None)
    if not binary:
        print(f"{tool['name']} is not installed.")
        if tool["install"]:
            print(f"install it with:  {tool['install']}")
        return 1

    env = dict(os.environ)
    context_limit = server_context_limit()
    opencode_full = os.environ.get("ARGUS_OPENCODE_FULL") == "1"
    if tool["protocol"] == "anthropic":
        if not ensure_bridge():
            print("the bridge did not come up — see: argus log")
            return 1
        env["ANTHROPIC_BASE_URL"] = BRIDGE
        env["ANTHROPIC_AUTH_TOKEN"] = KEY
        env["ANTHROPIC_API_KEY"] = KEY
        # deliberately NOT setting ANTHROPIC_MODEL to the repo id: the client
        # refuses model names it doesn't recognize, and the bridge pins every
        # request to the loaded model anyway
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        where = BRIDGE
    else:
        # go through the bridge for OpenAI clients too: it pins the model, so a
        # client sending its own model name cannot make the server swap models
        base = f"{BRIDGE}/v1" if ensure_bridge() else f"{API}/v1"
        env["OPENAI_BASE_URL"] = base
        env["OPENAI_API_BASE"] = base
        env["OPENAI_API_KEY"] = KEY
        env["OPENAI_MODEL"] = model
        where = base

        if key == "opencode":
            # OpenCode ignores OPENAI_BASE_URL as a provider override, so its
            # local provider must always be explicit. Full mode keeps user
            # customizations, but this late inline layer still pins the model
            # to Mira instead of silently returning to a cloud provider.
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
                opencode_config(model, base, context_limit))
            if not opencode_full:
                # The default mode uses an isolated XDG home so cloud
                # credentials, MCPs and project config cannot leak in.
                root = os.path.expanduser("~/.local/state/argus/opencode")
                for kind in ("config", "data", "cache", "state"):
                    env[f"XDG_{kind.upper()}_HOME"] = os.path.join(root, kind)
                env["OPENCODE_CONFIG_DIR"] = os.path.join(root, "config")
                env["OPENCODE_AUTH_CONTENT"] = "{}"
                env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
                env["OPENCODE_DISABLE_DEFAULT_PLUGINS"] = "1"
                env["OPENCODE_DISABLE_EXTERNAL_SKILLS"] = "1"
                env["OPENCODE_DISABLE_CLAUDE_CODE"] = "1"

    args = command_args(key, binary, tool, model, where, extra,
                        claude_full=os.environ.get("ARGUS_CLAUDE_FULL") == "1",
                        opencode_full=opencode_full)

    print(f"launching {tool['name']} → {where}  (model: {model})")
    os.execve(binary, args, env)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
