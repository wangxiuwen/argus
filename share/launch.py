#!/usr/bin/env python3
"""argus launch — run a coding agent or client against the local Argus server.

Sets the right environment for the tool and execs it, so the tool talks to
Argus instead of a cloud API. Tools speaking the OpenAI protocol get
OPENAI_BASE_URL; tools speaking the Anthropic protocol get ANTHROPIC_BASE_URL
pointed at the Anthropic-compatible bridge.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request

API = os.environ.get("ARGUS_API", "http://127.0.0.1:8090")
BRIDGE = os.environ.get("ARGUS_BRIDGE", "http://127.0.0.1:8092")
KEY = "argus"

# protocol: "openai" uses the server directly, "anthropic" goes through the bridge
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
        "args": lambda model: ["--openai-api-base", f"{API}/v1", "--openai-api-key", KEY,
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
        "desc": "Anthropic's coding tool (via the Argus Anthropic bridge)",
    },
    "shell": {
        "name": "Shell", "bin": os.environ.get("SHELL", "/bin/zsh"), "protocol": "openai",
        "install": None,
        "desc": "A shell with the API environment already set",
    },
}


def server_model():
    try:
        with urllib.request.urlopen(API + "/v1/models", timeout=3) as r:
            return json.load(r)["data"][0]["id"]
    except OSError:
        return None


def bridge_up():
    try:
        urllib.request.urlopen(BRIDGE + "/health", timeout=2).read()
        return True
    except OSError:
        return False


def list_tools():
    print("argus launch <tool> — run a tool against your local model\n")
    for key, t in TOOLS.items():
        have = shutil.which(t["bin"]) is not None
        mark = "✓" if have else "·"
        print(f"  {mark} {key:9s} {t['name']:16s} {t['desc']}")
        if not have and t["install"]:
            print(f"    {'':9s} not installed — {t['install']}")
    print("\n✓ = found on your PATH")


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
        print(f"Argus isn't answering at {API} — start it first: argus start")
        return 1

    binary = shutil.which(tool["bin"]) or (tool["bin"] if os.path.isabs(tool["bin"]) else None)
    if not binary:
        print(f"{tool['name']} is not installed.")
        if tool["install"]:
            print(f"install it with:  {tool['install']}")
        return 1

    env = dict(os.environ)
    if tool["protocol"] == "anthropic":
        if not bridge_up():
            print("starting the Anthropic bridge…")
            subprocess.Popen([os.path.expanduser("~/.local/bin/argus"), "bridge", "start"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            for _ in range(30):
                if bridge_up():
                    break
                import time
                time.sleep(0.5)
            if not bridge_up():
                print("the bridge did not come up — see: argus log")
                return 1
        env["ANTHROPIC_BASE_URL"] = BRIDGE
        env["ANTHROPIC_AUTH_TOKEN"] = KEY
        env["ANTHROPIC_API_KEY"] = KEY
        env["ANTHROPIC_MODEL"] = model
        env["ANTHROPIC_SMALL_FAST_MODEL"] = model
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        where = BRIDGE
    else:
        env["OPENAI_BASE_URL"] = f"{API}/v1"
        env["OPENAI_API_BASE"] = f"{API}/v1"
        env["OPENAI_API_KEY"] = KEY
        env["OPENAI_MODEL"] = model
        where = f"{API}/v1"

    args = [binary]
    if callable(tool.get("args")):
        args += tool["args"](model)
    args += extra

    print(f"launching {tool['name']} → {where}  (model: {model})")
    os.execve(binary, args, env)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
