"""The effective prompt-cache lifetime of a subagent, by Claude Code's documented precedence.

https://code.claude.com/docs/en/prompt-caching.md#choose-the-ttl-yourself, first match wins:
  1. FORCE_PROMPT_CACHING_5M=1
  2. the env var CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL
  3. the setting subagentPromptCacheTtl
  4. the agent definition's experimental.cacheTtl
  5. ENABLE_PROMPT_CACHING_1H=1
  6. the default for subagents: 5 minutes
Each control takes "5m" or "1h"; Claude Code ignores any other value, so here it falls through.

Anything that cannot be read where it matters counts as unknown, and unknown counts as 5 minutes:
a waiting rule injected by mistake costs a little, a missing one costs a full cache rewrite.
Standard library only; nothing here writes anywhere.
"""
import json
import os
import re
import sys
from pathlib import Path

FIVE, HOUR = "5m", "1h"
VALID = (FIVE, HOUR)


class _Unreadable:
    def __repr__(self):
        return "UNREADABLE"


UNREADABLE = _Unreadable()  # a value that exists but could not be read
_NOT_FOUND = object()


# --- the decision (pure) ----------------------------------------------------------------------

def _truthy(value):
    return value is not None and value.strip().lower() in ("1", "true", "yes", "on")


def decide(env, setting, agent_ttl):
    """Return (ttl, reason).

    env: the environment (a mapping). setting and agent_ttl: "5m", "1h", None (absent or an
    ignored value), or UNREADABLE.
    """
    # 1. Broad truthiness: reading "true" as set can only add a rule, never drop one.
    if _truthy(env.get("FORCE_PROMPT_CACHING_5M")):
        return FIVE, "FORCE_PROMPT_CACHING_5M is set"
    # 2.
    value = (env.get("CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL") or "").strip()
    if value in VALID:
        return value, "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"
    # 3.
    if setting is UNREADABLE:
        return FIVE, "unknown: a settings file could not be read"
    if setting in VALID:
        return setting, "setting subagentPromptCacheTtl"
    # 4.
    if agent_ttl is UNREADABLE:
        return FIVE, "unknown: the agent definition could not be read"
    if agent_ttl in VALID:
        return agent_ttl, "agent definition experimental.cacheTtl"
    # 5. Only the documented "1": a looser reading could drop a rule that is needed.
    if (env.get("ENABLE_PROMPT_CACHING_1H") or "").strip() == "1":
        return HOUR, "ENABLE_PROMPT_CACHING_1H=1"
    # 6.
    return FIVE, "default for subagents"


# --- where things live ------------------------------------------------------------------------

def config_dir(env):
    return Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


def managed_dirs():
    if sys.platform == "darwin":
        return [Path("/Library/Application Support/ClaudeCode")]
    if sys.platform.startswith("win"):
        return [Path(r"C:\Program Files\ClaudeCode")]
    return [Path("/etc/claude-code")]


def git_root(start):
    for d in [start, *start.parents]:
        if (d / ".git").exists():
            return d
    return None


def project_dir(env, cwd):
    return Path(env.get("CLAUDE_PROJECT_DIR") or cwd or os.getcwd())


def _unique(paths):
    seen, out = set(), []
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def settings_files(env, cwd):
    """Settings files, highest precedence first: managed, project local, project, user.
    (A --settings flag is invisible to a hook.)"""
    files = []
    for d in managed_dirs():
        files.append(d / "managed-settings.json")
        drop_in = d / "managed-settings.d"
        if drop_in.is_dir():
            files.extend(sorted(drop_in.glob("*.json")))
    proj = project_dir(env, cwd)
    root = git_root(proj)
    files.append(proj / ".claude" / "settings.local.json")
    if root:
        files.append(root / ".claude" / "settings.local.json")
    files.append(proj / ".claude" / "settings.json")
    files.append(config_dir(env) / "settings.json")
    return _unique(files)


def read_setting(files):
    """The first valid subagentPromptCacheTtl in precedence order, None, or UNREADABLE when a
    file that could hold it could not be read before a value was found."""
    for f in files:
        try:
            if not f.is_file():
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return UNREADABLE
        if not isinstance(data, dict):
            return UNREADABLE
        value = data.get("subagentPromptCacheTtl")
        if isinstance(value, str) and value.strip() in VALID:
            return value.strip()
    return None


# --- agent files ------------------------------------------------------------------------------

class FrontmatterError(ValueError):
    pass


def _scalar(raw):
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end > 0 else raw[1:]
    return raw.split(" #", 1)[0].strip()


def parse_frontmatter(text):
    """Return {"name": str or None, "cacheTtl": "5m", "1h" or None} from an agent file.

    A minimal reader for the two keys Embers needs, not a YAML parser. Raises FrontmatterError
    when the frontmatter is not closed or `experimental` has a shape it does not know.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {"name": None, "cacheTtl": None}
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise FrontmatterError("frontmatter not closed")
    body = lines[1:end]
    name, ttl = None, None
    top = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")
    i = 0
    while i < len(body):
        m = top.match(body[i])
        i += 1
        if not m:
            continue
        key, rest = m.group(1), m.group(2).strip()
        if key == "name":
            name = _scalar(rest) or None
        elif key == "experimental":
            if rest.startswith("{"):
                if not rest.rstrip().endswith("}"):
                    raise FrontmatterError("experimental: unclosed flow map")
                for pair in rest.strip()[1:-1].split(","):
                    k, sep, v = pair.partition(":")
                    if sep and _scalar(k) == "cacheTtl":
                        ttl = _scalar(v)
            elif rest and not rest.startswith("#"):
                raise FrontmatterError("experimental: not a map")
            else:
                while i < len(body) and (not body[i].strip() or body[i][:1] in (" ", "\t")):
                    sub = re.match(r"^\s+cacheTtl\s*:(.*)$", body[i])
                    if sub:
                        ttl = _scalar(sub.group(1))
                    i += 1
    return {"name": name, "cacheTtl": ttl if ttl in VALID else None}


def _read_agent(path):
    return parse_frontmatter(path.read_text(encoding="utf-8"))


def find_named(directory, agent_type):
    """In a user, project or managed agents directory (scanned recursively), the cacheTtl of the
    file whose frontmatter `name` is agent_type: "5m", "1h", None, UNREADABLE or _NOT_FOUND."""
    if not directory.is_dir():
        return _NOT_FOUND
    suspect = False
    for f in sorted(directory.rglob("*.md")):
        try:
            fm = _read_agent(f)
        except (OSError, UnicodeDecodeError, FrontmatterError):
            # Unreadable: count it only if it seems to define this very agent.
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if re.search(r"^name\s*:\s*['\"]?" + re.escape(agent_type) + r"['\"]?\s*$", text, re.M):
                suspect = True
            continue
        if fm["name"] == agent_type:
            return fm["cacheTtl"]
    return UNREADABLE if suspect else _NOT_FOUND


def agent_dirs(env, cwd):
    """Non-plugin agent directories, highest precedence first: managed, project (closest to the
    working directory first, up to the repository root), user. --agents JSON is invisible here,
    and it ignores experimental anyway."""
    dirs = [d / ".claude" / "agents" for d in managed_dirs()]
    start = Path(cwd or os.getcwd())
    proj = project_dir(env, cwd)
    for base in (start, proj):
        root = git_root(base)
        for d in [base, *base.parents]:
            dirs.append(d / ".claude" / "agents")
            if d == root:
                break
    user = config_dir(env) / "agents"
    dirs = [d for d in _unique(dirs) if str(d) != str(user)]
    return dirs + [user]


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _marketplace_source(location, plugin):
    """A plugin's directory inside a marketplace checkout, from its relative-path source."""
    data = _load_json(Path(location) / ".claude-plugin" / "marketplace.json")
    if not isinstance(data, dict):
        return None
    meta = data.get("metadata") or {}
    for entry in data.get("plugins") or []:
        if not isinstance(entry, dict) or entry.get("name") != plugin:
            continue
        source = entry.get("source")
        if not isinstance(source, str):
            return None
        if source not in (".", "./") and "/" not in source and meta.get("pluginRoot"):
            return Path(location) / meta["pluginRoot"] / source
        return Path(location) / source
    return None


def plugin_roots(plugin, env, own_root=None):
    """Directories that may hold the plugin named `plugin`."""
    roots = []
    if own_root:
        manifest = _load_json(Path(own_root) / ".claude-plugin" / "plugin.json") or {}
        if manifest.get("name") == plugin:
            roots.append(Path(own_root))
    base = Path(env.get("CLAUDE_CODE_PLUGIN_CACHE_DIR") or config_dir(env) / "plugins")
    installed = (_load_json(base / "installed_plugins.json") or {}).get("plugins") or {}
    known = _load_json(base / "known_marketplaces.json") or {}
    markets = [key.partition("@")[2] for key in installed if key.partition("@")[0] == plugin]
    for market in markets or list(known):
        info = known.get(market) if isinstance(known.get(market), dict) else {}
        source = info.get("source") if isinstance(info.get("source"), dict) else {}
        location = info.get("installLocation")
        if source.get("source") == "directory" and location:
            # A marketplace added from a local path loads its plugins in place, from the
            # source directory; any copy in the plugin cache is not what runs.
            found = _marketplace_source(location, plugin)
            if found and found.is_dir():
                roots.append(found)
            continue
        for e in installed.get(f"{plugin}@{market}") or []:
            path = e.get("installPath") if isinstance(e, dict) else None
            if path and Path(path).is_dir():
                roots.append(Path(path))
        if not markets and location:  # not installed: look in the marketplace checkout
            found = _marketplace_source(location, plugin)
            if found and found.is_dir():
                roots.append(found)
    return _unique([r.resolve() for r in roots])


def _agents_in_dir(directory):
    """(file, subfolders) for each .md file under a directory, scanned recursively."""
    return [(f, list(f.relative_to(directory).parent.parts))
            for f in sorted(directory.rglob("*.md"))]


def _plugin_agent_files(root):
    """(file, subfolders) for each agent file of a plugin, as Claude Code names them.

    Claude Code loads the default agents/ directory and, as well, every path the manifest's
    `agents` field lists (a string or a list): a directory is scanned recursively, with
    subfolders as namespaces relative to it; a file is loaded if it ends in .md, with none.
    """
    manifest = _load_json(root / ".claude-plugin" / "plugin.json")
    listed = manifest.get("agents") if isinstance(manifest, dict) else None
    listed = [listed] if isinstance(listed, str) else listed if isinstance(listed, list) else []
    found = _agents_in_dir(root / "agents") if (root / "agents").is_dir() else []
    for entry in listed:
        if not isinstance(entry, str):
            continue
        path = root / entry
        if path.is_dir():
            found.extend(_agents_in_dir(path))
        elif path.is_file() and path.name.endswith(".md"):
            found.append((path, []))
    seen, out = set(), []
    for f, folders in found:
        key = (str(f.resolve()), tuple(folders))
        if key not in seen:
            seen.add(key)
            out.append((f, folders))
    return out


def plugin_agent_ttl(agent_type, env, own_root=None):
    plugin = agent_type.split(":", 1)[0]
    values = []
    for root in plugin_roots(plugin, env, own_root):
        for f, folders in _plugin_agent_files(root):
            try:
                fm = _read_agent(f)
            except (OSError, UnicodeDecodeError, FrontmatterError):
                fm = None
            name = (fm or {}).get("name")
            if not name or len(name) > 256:
                name = f.stem
            if ":".join([plugin, *folders, name]) == agent_type:
                values.append(UNREADABLE if fm is None else fm["cacheTtl"])
    if not values:
        return None
    if any(v is UNREADABLE for v in values) or len(set(values)) > 1:
        return UNREADABLE  # unreadable, or two copies that disagree
    return values[0]


def agent_ttl(agent_type, env, cwd=None, own_root=None):
    """The agent definition's experimental.cacheTtl: "5m", "1h", None or UNREADABLE."""
    if not agent_type:
        return None
    if ":" in agent_type:  # plugin-scoped; user and project names cannot hold a colon
        return plugin_agent_ttl(agent_type, env, own_root)
    for d in agent_dirs(env, cwd):
        found = find_named(d, agent_type)
        if found is not _NOT_FOUND:
            return found
    return None  # a built-in agent, or one this hook cannot see


def effective_ttl(agent_type, env=None, cwd=None, own_root=None):
    """(ttl, reason) for a subagent of this type, from the real environment and files."""
    env = os.environ if env is None else env
    try:
        setting = read_setting(settings_files(env, cwd))
    except Exception:
        setting = UNREADABLE
    try:
        agent = agent_ttl(agent_type, env, cwd, own_root)
    except Exception:
        agent = UNREADABLE
    return decide(env, setting, agent)
