"""All configuration comes from environment variables (set them in Render's dashboard)."""
import hashlib
import json
import os


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


APP_PASSWORD = env("APP_PASSWORD")
# Token-signing secret. If you don't set one it's derived from the password,
# so tokens survive Render restarts either way.
APP_SECRET = env("APP_SECRET") or hashlib.sha256(("salt-v1:" + APP_PASSWORD).encode()).hexdigest()
TOKEN_TTL_DAYS = int(env("TOKEN_TTL_DAYS", "30"))

# CORS: put your GitHub Pages origin here, e.g. https://yourname.github.io
ALLOWED_ORIGINS = [o.strip() for o in env("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

DATA_DIR = env("DATA_DIR", "/tmp/agent")

GITHUB_TOKEN = env("GITHUB_TOKEN")          # the PAT
STATE_REPO = env("STATE_REPO")              # e.g. "yourname/agent-state" (private, auto-created)
GIT_USER_NAME = env("GIT_USER_NAME", "Mobile Agent")
GIT_USER_EMAIL = env("GIT_USER_EMAIL", "agent@users.noreply.github.com")

DEFAULT_MODEL = env("DEFAULT_MODEL")        # "provider:model-id"; auto-picked if empty
SELF_REPO = env("SELF_REPO", "dmrakyz/pocket-agent")   # this app's own source repo, for the "improve Pocket Agent" chat
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
INSTAGRAM_ACCESS_TOKEN = env("INSTAGRAM_ACCESS_TOKEN")      # Instagram User token of the professional account (Instagram API with Instagram Login)
INSTAGRAM_APP_SECRET = env("INSTAGRAM_APP_SECRET")          # Meta app secret: used to verify webhook signatures
INSTAGRAM_VERIFY_TOKEN = env("INSTAGRAM_VERIFY_TOKEN")      # optional; one is derived if empty
INSTAGRAM_ALLOWED_IDS = {x for x in env("INSTAGRAM_ALLOWED_IDS").replace(" ", "").split(",") if x.isdigit()}
ETSY_API_KEY = env("ETSY_API_KEY")                # Etsy app keystring (Open API v3)
ETSY_SHARED_SECRET = env("ETSY_SHARED_SECRET")  # optional: Etsy asks for keystring:secret on newer apps
LEMONSQUEEZY_API_KEY = env("LEMONSQUEEZY_API_KEY")   # Lemon Squeezy dashboard → Settings → API
LEMONSQUEEZY_STORE_ID = env("LEMONSQUEEZY_STORE_ID")   # optional; defaults to the first store
RESEND_API_KEY = env("RESEND_API_KEY")          # enables send_email (HTTPS; Render free blocks SMTP)
MAIL_FROM = env("MAIL_FROM", "Pocket Agent <onboarding@resend.dev>")
MAIL_REPLY_TO = env("MAIL_REPLY_TO")
MAIL_ALLOWED_TO = env("MAIL_ALLOWED_TO")        # optional comma list of addresses/domains; empty = any (still asks every time)
EGRESS_PROXY = env("EGRESS_PROXY")              # http(s)/socks5 URL, e.g. http://user:pass@host:port. Browser traffic goes out through it; web_fetch uses it when a site refuses the server directly
MAIL_API_BASE = env("MAIL_API_BASE", "https://api.resend.com")
TELEGRAM_ALLOWED_IDS = {int(x) for x in env("TELEGRAM_ALLOWED_IDS").replace(" ", "").split(",") if x.lstrip("-").isdigit()}
PUBLIC_URL = (env("PUBLIC_URL") or env("RENDER_EXTERNAL_URL")).rstrip("/")
IMAGE_MODEL = env("IMAGE_MODEL", "black-forest-labs/flux.1-schnell")   # text-to-image model on NVIDIA
IMAGE_BASE_URL = env("IMAGE_BASE_URL", "https://ai.api.nvidia.com/v1/genai")
VISION_MODEL = env("VISION_MODEL")            # model used by view_image; auto-picked if empty
FALLBACK_MODEL = env("FALLBACK_MODEL")      # used when the main model keeps failing; auto-picked if empty
COMPACT_AT_TOKENS = int(env("COMPACT_AT_TOKENS", "120000"))
MAX_STEPS = int(env("MAX_STEPS", "60"))     # tool-loop iterations per user turn
BASH_TIMEOUT = int(env("BASH_TIMEOUT", "120"))
VERIFY_GATE = env("VERIFY_GATE", "1") not in ("0", "false", "off", "no")   # a turn that changed code may not end until a check passed
BASH_MEM_MB = int(env("BASH_MEM_MB", "300"))     # a bash command (all its processes together) is killed above this; 0 = off
TOOL_OUTPUT_LIMIT = int(env("TOOL_OUTPUT_LIMIT", "30000"))
AUTOPILOT_STEPS = int(env("AUTOPILOT_STEPS", "300"))      # step budget when Autopilot is on
MAX_SUBAGENTS = int(env("MAX_SUBAGENTS", "12"))            # per user turn, across all nesting levels
SUBAGENT_PARALLEL = int(env("SUBAGENT_PARALLEL", "4"))     # how many run at the same time
MAX_SUBAGENT_DEPTH = int(env("MAX_SUBAGENT_DEPTH", "2"))   # 1 = subagents can't spawn more
MAX_UPLOAD_MB = int(env("MAX_UPLOAD_MB", "50"))
# Requests per minute per provider. Limits apply per key for providers with key pools.
# Kimi's free API tier is commonly 2 RPM per account; each independent account gets its own window.
# Override: LLM_RPM='{"nvidia": 30, "kimi": 2, "openrouter": 0}' (0 = unlimited)
def rpm_limits() -> dict:
    d = {"nvidia": 35, "kimi": 2}
    raw = env("LLM_RPM")
    if raw:
        try:
            d.update(json.loads(raw))
        except json.JSONDecodeError:
            print("WARNING: LLM_RPM is not valid JSON, ignoring")
    return d

# Search: DuckDuckGo needs no key. Optional upgrades:
TAVILY_API_KEY = env("TAVILY_API_KEY")
BRAVE_API_KEY = env("BRAVE_API_KEY")


def providers() -> dict:
    """OpenAI-compatible providers. Anything with a key set shows up in the model picker."""
    p = {}
    kimi_keys = list(dict.fromkeys([k.strip() for k in (env("KIMI_API_KEY") + "," + env("KIMI_API_KEYS")).split(",") if k.strip()]))
    if kimi_keys:      # same per-key rotation and cooldown behavior as NVIDIA
        p["kimi"] = {"base_url": env("KIMI_BASE_URL", "https://api.moonshot.ai/v1"),
                     "api_key": kimi_keys[0], "api_keys": kimi_keys}
    nkeys = list(dict.fromkeys([k.strip() for k in (env("NVIDIA_API_KEY") + "," + env("NVIDIA_API_KEYS")).split(",") if k.strip()]))
    if nkeys:      # several keys rotate: each has its own rate limit (see llm.KeyPool)
        p["nvidia"] = {"base_url": env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
                       "api_key": nkeys[0], "api_keys": nkeys}
    if env("OPENROUTER_API_KEY"):
        p["openrouter"] = {"base_url": "https://openrouter.ai/api/v1", "api_key": env("OPENROUTER_API_KEY")}
    if env("OPENAI_API_KEY"):
        p["openai"] = {"base_url": env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                       "api_key": env("OPENAI_API_KEY")}
    if env("ANTHROPIC_API_KEY"):
        p["anthropic"] = {"base_url": "https://api.anthropic.com/v1", "api_key": env("ANTHROPIC_API_KEY")}
    if env("GEMINI_API_KEY"):
        p["gemini"] = {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                       "api_key": env("GEMINI_API_KEY")}
    # Anything else: PROVIDERS_JSON='{"name": {"base_url": "...", "api_key": "..."}}'
    extra = env("PROVIDERS_JSON")
    if extra:
        try:
            p.update(json.loads(extra))
        except json.JSONDecodeError:
            print("WARNING: PROVIDERS_JSON is not valid JSON, ignoring")
    return p


# Remote (HTTP) MCP servers: MCP_SERVERS='[{"name":"x","url":"https://...","headers":{"Authorization":"Bearer ..."}}]'
def mcp_servers() -> list:
    raw = env("MCP_SERVERS")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("WARNING: MCP_SERVERS is not valid JSON, ignoring")
        return []


SECRET_ENV_KEYS = {
    "APP_PASSWORD", "APP_SECRET", "GITHUB_TOKEN", "NVIDIA_API_KEY", "NVIDIA_API_KEYS", "KIMI_API_KEY", "TURSO_TOKEN", "TURSO_URL", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "PROVIDERS_JSON", "MCP_SERVERS", "TAVILY_API_KEY", "BRAVE_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "RESEND_API_KEY",
    "ETSY_API_KEY",
    "LEMONSQUEEZY_API_KEY",
    "ETSY_SHARED_SECRET",
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_APP_SECRET",
    "EGRESS_PROXY",
}
