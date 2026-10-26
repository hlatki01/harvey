"""Configuration loader for Harvey. Reads harvey.yaml + .env."""

import logging
import os
import re
from urllib.parse import urlparse
from datetime import date as _date, time as _time
from pathlib import Path

import yaml

from harvey.textio import read_user_text
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from harvey.paths import PROJECT_ROOT

logger = logging.getLogger("harvey.config")


class ConfigError(Exception):
    """Raised when Harvey's configuration is missing or invalid."""


class ConfigFileNotFoundError(ConfigError, FileNotFoundError):
    """Config file is missing. Subclasses FileNotFoundError for
    backward compatibility with existing callers/tests."""


class PersonaConfig(BaseModel):
    name: str
    company: str
    role: str
    email: str
    linkedin: str
    tone: str


class OfferConfig(BaseModel):
    primary: str = ""
    entry: str = ""
    goal: str = "book_call"  # book_call, start_trial, get_reply
    booking_method: str = "calendar_link"  # calendar_link, suggest_times, ask_preference, google_calendar
    booking_url: str = ""
    meeting_duration: str = "15 minutes"
    meeting_hours: str = "09:00-18:00"  # google_calendar offers slots inside these, weekdays
    meeting_owner: str = ""

    @field_validator("meeting_hours")
    @classmethod
    def _valid_hours(cls, v: str) -> str:
        v = v.strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d", v) or v[:5] >= v[6:]:
            raise ValueError(f"meeting_hours '{v}' must look like '09:00-18:00'.")
        return v


class ProductConfig(BaseModel):
    name: str
    description: str
    pricing: str
    key_benefits: list[str]
    objection_responses: dict[str, str]
    offer: OfferConfig = OfferConfig()


class ICPConfig(BaseModel):
    industries: list[str]
    company_size: str
    titles: list[str]
    geography: list[str]
    # Role keywords that indicate a company is in-market right now (a company
    # hiring a "Head of Growth" is buying growth tooling). Empty → falls back
    # to `titles`. Used for careers-page scanning and job-board discovery.
    hiring_signals: list[str] = []
    # Discovery needs a radius, not a place name. Maps each entry in
    # `geography` to "lat,lng,radius_km" — e.g.
    #   "Denver, CO": "39.7392,-104.9903,50"
    # Without one, listings providers can only match on the business name.
    geo_coordinates: dict[str, str] = {}


class WarmupConfig(BaseModel):
    """Cold-email ramp for a new domain or mailbox.

    Harvey doesn't run a warmup network itself; that needs a pool of real
    inboxes (Instantly, Lemwarm, Mailreach...). This is the other half: no
    cold email goes out for ``min_days`` after ``start_date``, then the cold
    cap climbs from ``start_sends`` by ``daily_increase`` a day until it
    reaches ``max_daily_sends``. Replies are never held. Empty
    ``start_date`` means no ramp.
    """

    start_date: str = ""      # YYYY-MM-DD the warmup service was switched on
    min_days: int = 14        # warmup-only days before any cold email
    start_sends: int = 5      # cold-email cap on the first sending day
    daily_increase: int = 3   # added to the cap each day after that
    dkim_selector: str = ""   # for `harvey warmup`, if not a common one

    @field_validator("start_date", "dkim_selector", mode="before")
    @classmethod
    def _blank_is_empty(cls, v, info):
        if v is None:
            return ""
        if info.field_name != "start_date":
            return v
        # YAML reads an unquoted 2026-10-01 as a date (with a time, a datetime).
        if isinstance(v, _date):
            return (v.date() if hasattr(v, "date") and callable(v.date) else v).isoformat()
        if isinstance(v, str) and len(v.strip()) > 10 and v.strip()[10] in " T":
            return v.strip()[:10]  # "2026-10-01 09:00": the day is what counts
        return "" if v is None else v

    @field_validator("min_days", "start_sends", "daily_increase", mode="before")
    @classmethod
    def _blank_is_default(cls, v, info):
        return cls.model_fields[info.field_name].default if v is None else v

    @field_validator("start_date")
    @classmethod
    def _valid_date(cls, v: str) -> str:
        v = (v or "").strip()
        if v:
            try:
                _date.fromisoformat(v)
            except ValueError:
                raise ValueError(f"warmup.start_date '{v}' must be YYYY-MM-DD.")
        return v

    @field_validator("min_days", "start_sends", "daily_increase")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("warmup values must be >= 0")
        return v

    def days_in(self, today: _date | None = None) -> int | None:
        """Days since warmup started, or None when no ramp is configured."""
        if not self.start_date:
            return None
        return ((today or _date.today()) - _date.fromisoformat(self.start_date)).days

    def daily_cap(self, ceiling: int, today: _date | None = None) -> int:
        """Today's cold-email cap under the ramp (never above ``ceiling``)."""
        days = self.days_in(today)
        if days is None:
            return ceiling
        if days < self.min_days:
            return 0
        return min(ceiling, self.start_sends + (days - self.min_days) * self.daily_increase)


class EmailChannelConfig(BaseModel):
    enabled: bool = True
    # "instantly" (legacy), "gmail" (Gmail/Workspace via API — recommended),
    # "microsoft" (Microsoft 365 / Outlook via Graph), or "smtp" (any
    # SMTP+IMAP mailbox: AgentMail, Fastmail, ...)
    provider: str = "instantly"
    max_daily_sends: int = 50
    # Mail sent to persona.email by someone Harvey never contacted becomes a
    # lead and gets a (reviewed) reply. Automated senders are ignored.
    inbound_leads: bool = False
    # When True, also send to catch-all ("risky") domains, not just verified
    # mailboxes. Off by default — catch-alls accept everything, so a bad
    # guess still bounces.
    send_to_risky: bool = False
    # Native providers queue mail for approval. Instantly cannot use that
    # outbox, so it holds campaigns and escalates replies while this is true.
    # Set false explicitly for full autopilot once you trust the output.
    require_approval: bool = True
    # Kill switch: pause all sending when bounces exceed this fraction of
    # sent mail (measured over the trailing sends). 0 disables the switch.
    max_bounce_rate: float = 0.05
    # New domain? Ramp cold email up slowly (see WarmupConfig). Native
    # providers only: Instantly has its own warmup and per-account limits.
    warmup: WarmupConfig = WarmupConfig()

    @field_validator("warmup", mode="before")
    @classmethod
    def _blank_warmup(cls, v):
        return WarmupConfig() if v is None else v

    @field_validator("max_daily_sends")
    @classmethod
    def _sends_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_daily_sends must be >= 0")
        return v

    def cold_daily_cap(self, today: _date | None = None) -> int:
        """Today's cap on cold (sequence) email: max_daily_sends, throttled
        by the warmup ramp. Replies count only toward max_daily_sends."""
        return self.warmup.daily_cap(self.max_daily_sends, today)


def cold_daily_cap(email_config, today: _date | None = None) -> int:
    """cold_daily_cap for any email config, including ones without a ramp."""
    warmup = getattr(email_config, "warmup", None)
    if isinstance(warmup, WarmupConfig):
        return warmup.daily_cap(email_config.max_daily_sends, today)
    return email_config.max_daily_sends


def legacy_sending_held(config) -> bool:
    """True when Instantly is the provider and approval is required.

    Instantly has no per-email approval queue, so with require_approval on
    Harvey holds campaign deployment and hands replies to a human.
    """
    email = getattr(getattr(config, "channels", None), "email", None)
    return (getattr(email, "provider", "") == "instantly"
            and bool(getattr(email, "require_approval", True)))


def sending_notice(config) -> str:
    """A plain-language warning when outbound sending is held by config, or ""."""
    if not legacy_sending_held(config):
        return ""
    return (
        "Instantly sending is held: channels.email.require_approval is true, and "
        "Instantly has no approval queue. Campaigns stay as drafts and replies go to "
        "a human (dashboard Activity tab). Set require_approval: false to let Instantly "
        "send automatically, or switch to gmail, microsoft or smtp to approve each email."
    )


class LinkedInChannelConfig(BaseModel):
    enabled: bool = False
    max_daily_connections: int = 20
    max_daily_messages: int = 10


class DemoConfig(BaseModel):
    """An example site for one kind of business. The first demo whose keywords
    appear in a lead's category is the one offered; the model never writes
    the URL itself."""

    label: str                  # how to say it: "barbearias"
    url: str
    keywords: list[str]         # lowercase fragments of the category, PT or EN


class WhatsAppChannelConfig(BaseModel):
    """WhatsApp through a self-hosted Evolution API instance (.env: EVOLUTION_*).

    Evolution drives an ordinary WhatsApp account over the unofficial Web
    protocol. Cold messages to people who don't have you saved get numbers
    banned: use a dedicated SIM, a low daily cap, and keep approval on.
    """

    enabled: bool = False
    max_daily_sends: int = 20          # opening messages and replies together
    max_new_per_cycle: int = 5         # new conversations drafted per heartbeat
    require_approval: bool = True
    # Answers to people who wrote first go out without review while opening
    # messages (the risky ones, to strangers) still wait for approval.
    auto_approve_replies: bool = False
    model: str = ""                # Claude model for chat decisions (e.g. "haiku"); empty = brain.model
    inbound_leads: bool = False    # a new number that writes first becomes a lead and is answered
    inbound_max_age_minutes: int = 15   # ...but only if the message is this fresh
    # A fast model decides; when it is unsure (not "high" confidence, or it cannot tell a
    # person from an automatic message) a stronger one reviews before anyone is bothered.
    review_ambiguous: bool = True
    review_model: str = ""         # empty = brain.model (the provider's default)
    poll_seconds: int = 5          # how often the live loop checks the chat for new messages
    # Finer than auto_approve_replies: answers whose chosen action is listed
    # here skip review (answer | qualify | send_demo | handoff).
    auto_approve_actions: list[str] = []
    # Example sites by kind of business, and the only links an answer may carry.
    demos: list[DemoConfig] = []
    # The only prices an answer may state (e.g. ["1.500", "297"]); any other
    # R$ amount blocks the message. Empty: no price may be stated.
    allowed_prices: list[str] = []
    default_country_code: str = "55"
    language: str = "Brazilian Portuguese"
    # Every opening message ends with this; replying to it is an opt-out.
    opt_out_line: str = "Se preferir não receber mensagens, responda SAIR."
    # Only businesses carrying ALL of these confirmed signals (e.g.
    # [NO_WEBSITE]) and none of the excluded ones. Empty: every business
    # that has a phone.
    require_signals: list[str] = []
    exclude_signals: list[str] = []

    @field_validator("max_daily_sends", "max_new_per_cycle")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be >= 0")
        return v

    @field_validator("model", "review_model", mode="before")
    @classmethod
    def _valid_model(cls, v) -> str:
        v = str(v or "").strip()
        if v and not _MODEL_NAME_RE.match(v):
            raise ValueError(f"'{v}' is not a valid model name.")
        return v


class ChannelsConfig(BaseModel):
    email: EmailChannelConfig = EmailChannelConfig()
    linkedin: LinkedInChannelConfig = LinkedInChannelConfig()
    whatsapp: WhatsAppChannelConfig = WhatsAppChannelConfig()


PRECLEARED_ADDRESS_SOURCES = ("published", "bought", "scraped", "given", "unknown")


def normalize_prospect_country(v) -> str:
    """A two-letter country code ("us" -> "US", "UK" -> "GB"), or "" for none.

    Raises ValueError on anything else, including the booleans YAML makes of
    an unquoted NO (Norway) or ON, so a typo is never silently ignored.
    """
    if isinstance(v, bool):
        raise ValueError(
            f"prospect_country was read as {v}. YAML turns an unquoted NO or "
            'ON into true/false, so quote the code, e.g. prospect_country: "NO".'
        )
    v = str(v or "").strip().upper()
    v = {"UK": "GB", "USA": "US"}.get(v, v)
    if v and not (len(v) == 2 and v.isascii() and v.isalpha()):
        raise ValueError(
            f"'{v}' is not a valid prospect_country. Use a two-letter "
            "country code such as US or GB, or leave it empty."
        )
    return v


class PreclearedConfig(BaseModel):
    """Optional per-recipient cold-email check through Precleared.

    Off by default. When on, each new recipient is checked once, before the
    first cold email, against UK PECR, US CAN-SPAM, Canada's CASL, Germany's
    UWG and France's rules. Any verdict other than ok_to_email holds the
    email back with the reason. See harvey/integrations/precleared.py.
    """

    enabled: bool = False
    # Where your business is based: GB or US. Required when enabled.
    sender_country: str = ""
    # Where prospect addresses come from, in Precleared's terms. Harvey
    # builds most addresses from a learned pattern, so the honest default
    # is "unknown"; use "published" only if yours are copied from the
    # businesses' own websites.
    address_source: str = "unknown"
    # Optional: the country your prospects are in (two letters, e.g. "US"),
    # if they are all in one. Precleared uses it only when it can't work out
    # the recipient's country from the address, company records or the
    # company's website. Leave empty if your prospects are spread out.
    prospect_country: str = ""

    @field_validator("sender_country", mode="before")
    @classmethod
    def _upper_country(cls, v) -> str:
        return str(v or "").strip().upper()

    @field_validator("address_source", mode="before")
    @classmethod
    def _valid_source(cls, v) -> str:
        v = str(v or "unknown").strip().lower()
        if v not in PRECLEARED_ADDRESS_SOURCES:
            raise ValueError(
                f"'{v}' is not a valid address_source. "
                f"Use one of: {', '.join(PRECLEARED_ADDRESS_SOURCES)}."
            )
        return v

    @field_validator("prospect_country", mode="before")
    @classmethod
    def _valid_prospect_country(cls, v) -> str:
        return normalize_prospect_country(v)

    @model_validator(mode="after")
    def _country_when_enabled(self):
        if self.enabled and self.sender_country not in ("GB", "US"):
            raise ValueError(
                "compliance.precleared.sender_country must be GB or US "
                "when the check is enabled."
            )
        return self


DEFAULT_OPT_OUT_LINE = 'Not interested? Reply "no thanks" and I won\'t email again.'
# The replies the Handler always honours as an opt-out on their own; an
# opt_out_line must ask for one of them ("Reply STOP", 'reply "no thanks"').
OPT_OUT_WORDS = ("no thanks", "no thank you", "unsubscribe", "stop", "remove me",
                 "opt out")
_ASKS_FOR_OPT_OUT_RE = re.compile(
    r"\breply(?:\s+with)?\s*[\"'\u201c\u2018]?\s*(?:"
    + "|".join(re.escape(w) for w in OPT_OUT_WORDS) + r")\b", re.IGNORECASE)


class ComplianceConfig(BaseModel):
    precleared: PreclearedConfig = PreclearedConfig()
    # Added under every cold (sequence) email at send time; never to replies.
    # CAN-SPAM requires a postal address you receive mail at (a street
    # address, PO box or virtual mailbox) and a working way to opt out.
    postal_address: str = ""
    # Must ask for wording Harvey recognises as an opt-out (OPT_OUT_WORDS).
    # "" leaves it off (and harvey warns).
    opt_out_line: str = DEFAULT_OPT_OUT_LINE

    @field_validator("postal_address", "opt_out_line", mode="before")
    @classmethod
    def _blank_is_empty(cls, v):
        return "" if v is None else v

    @field_validator("opt_out_line")
    @classmethod
    def _recognisable_opt_out(cls, v: str) -> str:
        v = v.strip()
        lowered = v.lower()
        if not v:
            return v
        if any(bit in lowered for bit in ("http", "www.", "click", "@")):
            raise ValueError(
                "opt_out_line can't contain a link or an email address: cold email "
                "allows one link, and opting out must be as simple as replying."
            )
        if not _ASKS_FOR_OPT_OUT_RE.search(v):
            raise ValueError(
                "opt_out_line must ask for a reply Harvey recognises as an opt-out, "
                'e.g. Reply "no thanks" / Reply STOP. Recognised: '
                + ", ".join(f'"{w}"' for w in OPT_OUT_WORDS) + "."
            )
        return v


def compliance_of(config) -> ComplianceConfig:
    """The compliance section, or the defaults when a config has none."""
    found = getattr(config, "compliance", None)
    return found if isinstance(found, ComplianceConfig) else ComplianceConfig()


def compliance_footer_lines(config) -> list[str]:
    """The lines added under every cold email: address, then opt-out line."""
    compliance = compliance_of(config)
    return [line for line in (compliance.postal_address.strip(),
                              compliance.opt_out_line.strip()) if line]


def compliance_notice(config) -> str:
    """A plain-language warning when cold email goes out without a postal
    address, or "". Sending isn't held for it: this is the nudge."""
    email = getattr(getattr(config, "channels", None), "email", None)
    if not getattr(email, "enabled", False):
        return ""
    compliance = compliance_of(config)
    missing = []
    if not compliance.postal_address.strip():
        missing.append("a postal address (compliance.postal_address: a street "
                       "address, PO box or virtual mailbox)")
    if not compliance.opt_out_line.strip():
        missing.append("a way to opt out (compliance.opt_out_line)")
    if not missing:
        return ""
    return (
        "Cold emails are going out without " + " or ".join(missing) + ". US law "
        "(CAN-SPAM) requires both in every cold email. Set them in harvey.yaml "
        "(or harvey.local.yaml if you trained Harvey)."
    )


class QuietHoursConfig(BaseModel):
    start: str = "22:00"
    end: str = "07:00"
    timezone: str = "America/New_York"

    @field_validator("start", "end")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        try:
            _time.fromisoformat(v)
        except ValueError:
            raise ValueError(
                f"'{v}' is not a valid time. Use 24h HH:MM format, e.g. '22:00'."
            )
        return v

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        import pytz

        if v not in pytz.all_timezones_set:
            raise ValueError(
                f"'{v}' is not a valid timezone. Use an IANA name like 'America/New_York'."
            )
        return v


class UsageConfig(BaseModel):
    max_daily_claude_percent: float = 80.0
    heartbeat_interval_minutes: int = 15
    quiet_hours: QuietHoursConfig = QuietHoursConfig()

    @field_validator("max_daily_claude_percent")
    @classmethod
    def _valid_percent(cls, v: float) -> float:
        if not 0 < v <= 100:
            raise ValueError("max_daily_claude_percent must be between 0 and 100")
        return v

    @field_validator("heartbeat_interval_minutes")
    @classmethod
    def _valid_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError("heartbeat_interval_minutes must be at least 1")
        return v


BRAIN_PROVIDERS = ("claude", "codex")
CODEX_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")

# Model names are passed to a CLI, so they must look like a model name and
# never like a flag ("-x") or anything a shell would treat specially.
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,127}$")


class BrainConfig(BaseModel):
    """Which subscription Harvey thinks with.

    ``claude`` (default) runs Claude Code headless on your Claude plan.
    ``codex`` runs Codex on your ChatGPT plan. There is never an automatic
    fallback between them, and an explicit ``model`` only affects Harvey —
    your own Claude Code / Codex settings are never read or changed.
    """

    provider: str = "claude"
    model: str = ""  # empty = the provider's own default
    reasoning_effort: str = ""  # codex only; empty = model default

    @field_validator("provider", mode="before")
    @classmethod
    def _valid_provider(cls, v) -> str:
        v = str(v or "claude").strip().lower()
        if v not in BRAIN_PROVIDERS:
            raise ValueError(
                f"'{v}' is not a supported brain provider. "
                f"Use one of: {', '.join(BRAIN_PROVIDERS)}."
            )
        return v

    @field_validator("model", mode="before")
    @classmethod
    def _valid_model(cls, v) -> str:
        v = str(v or "").strip()
        if v and not _MODEL_NAME_RE.match(v):
            raise ValueError(f"'{v}' is not a valid model name.")
        return v

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def _valid_effort(cls, v) -> str:
        v = str(v or "").strip().lower()
        if v and v not in CODEX_REASONING_EFFORTS:
            raise ValueError(
                f"'{v}' is not a valid reasoning effort. "
                f"Use one of: {', '.join(CODEX_REASONING_EFFORTS)}."
            )
        return v


class WriterConfig(BaseModel):
    # Run every draft through one extra editing-only brain call using the
    # Humanizer skill's instructions (~/.claude/skills/humanizer/SKILL.md,
    # or HARVEY_HUMANIZER_SKILL). Off by default: installing the skill for
    # your own Claude Code use never changes what Harvey sends on its own.
    humanizer: bool = False


class DashboardConfig(BaseModel):
    # Where invited users reach the dashboard: the HTTPS address of a reverse
    # proxy or tunnel that forwards to 127.0.0.1:5555. Empty keeps the
    # dashboard loopback-only, with the single local token.
    public_url: str = ""

    @field_validator("public_url", mode="before")
    @classmethod
    def _origin_only(cls, v):
        v = (v or "").strip().rstrip("/")
        if not v:
            return ""
        parsed = urlparse(v)
        local = parsed.hostname in ("localhost", "127.0.0.1")
        bad_scheme = parsed.scheme not in ("https", "http") or (parsed.scheme == "http" and not local)
        if bad_scheme or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("dashboard.public_url must be an https origin like https://painel.example.com")
        return v


class AutoDiscoveryConfig(BaseModel):
    """Let the heartbeat find new businesses by itself, within a hard budget."""
    enabled: bool = False        # explicit opt-in: discovery is the one stage that can spend money
    provider: str = "osm"        # free by default; see `harvey discover --providers`
    queries_per_run: int = 5     # industry x city queries each run takes, walking the whole list in turn
    min_hours_between_runs: float = 6.0
    max_spend_per_day: float = 0.0   # USD across all auto runs today; 0 = only free providers

    @field_validator("queries_per_run")
    @classmethod
    def _at_least_one(cls, v):
        if v < 1:
            raise ValueError("discovery.queries_per_run must be at least 1")
        return v


NOTIFY_EVENTS = ("hot_lead", "handoff", "approvals", "meeting", "system")


class NotifyConfig(BaseModel):
    """What needs you, pushed to one WhatsApp number through the Evolution
    instance (.env: EVOLUTION_*). Empty number: nothing is pushed. Messages
    from this number are never treated as a lead's."""

    whatsapp_number: str = ""
    events: list[str] = list(NOTIFY_EVENTS)
    # A conversation entering one of these stages is a hot lead.
    hot_stages: list[str] = ["engaged", "qualifying", "closing"]
    # At most one "waiting for approval" push per this many minutes.
    approvals_every_minutes: int = 60

    @field_validator("events")
    @classmethod
    def _known_events(cls, v: list[str]) -> list[str]:
        unknown = [e for e in v if e not in NOTIFY_EVENTS]
        if unknown:
            raise ValueError(f"unknown event(s) {unknown}; choose from {list(NOTIFY_EVENTS)}")
        return v

    @field_validator("hot_stages")
    @classmethod
    def _known_stages(cls, v: list[str]) -> list[str]:
        from harvey.models.conversation import STAGES
        unknown = [s for s in v if s not in STAGES]
        if unknown:
            raise ValueError(f"unknown stage(s) {unknown}; choose from {STAGES}")
        return v

    @field_validator("approvals_every_minutes")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be >= 1")
        return v


class CrmConfig(BaseModel):
    """Every conversation stage change, POSTed as JSON to a URL you choose
    (n8n, Make, Zapier, your CRM). .env HARVEY_WEBHOOK_SECRET, when set,
    signs the body: X-Harvey-Signature: sha256=<hmac>."""

    webhook_url: str = ""

    @field_validator("webhook_url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return v


class HarveyConfig(BaseModel):
    persona: PersonaConfig
    product: ProductConfig
    icp: ICPConfig
    channels: ChannelsConfig = ChannelsConfig()
    usage: UsageConfig = UsageConfig()
    brain: BrainConfig = BrainConfig()
    compliance: ComplianceConfig = ComplianceConfig()
    writer: WriterConfig = WriterConfig()
    dashboard: DashboardConfig = DashboardConfig()
    discovery: AutoDiscoveryConfig = AutoDiscoveryConfig()
    notify: NotifyConfig = NotifyConfig()
    crm: CrmConfig = CrmConfig()


class EnvConfig(BaseModel):
    instantly_api_key: str = ""
    # Discovery providers
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    dataforseo_sandbox: str = ""   # any truthy value routes to the free sandbox
    linkedin_email: str = ""
    linkedin_password: str = ""
    hunter_api_key: str = ""
    serper_api_key: str = ""
    google_places_api_key: str = ""
    # WhatsApp via Evolution API (channels.whatsapp.enabled)
    evolution_api_url: str = ""
    evolution_api_key: str = ""
    evolution_instance: str = ""
    reoon_api_key: str = ""
    zerobounce_api_key: str = ""
    # Native mail providers (channels.email.provider: gmail | smtp)
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    # Microsoft 365 (channels.email.provider: microsoft)
    m365_client_id: str = ""
    m365_tenant_id: str = ""
    # Optional: only used when compliance.precleared.enabled is true.
    precleared_api_key: str = ""
    harvey_webhook_secret: str = ""   # signs crm.webhook_url posts


def _format_validation_error(e: ValidationError) -> str:
    """Turn a pydantic ValidationError into a readable, actionable message."""
    lines = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_config(config_path: str | None = None) -> HarveyConfig:
    """Load Harvey configuration from YAML file.

    Raises ConfigError with a clear, actionable message on any problem.
    """
    if config_path is None:
        config_path = _find_config_file()

    try:
        data = yaml.safe_load(read_user_text(config_path))
    except FileNotFoundError:
        raise ConfigFileNotFoundError(
            f"Config file not found: {config_path}. "
            "Create one from harvey.yaml.example or run 'harvey setup'."
        )
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {config_path}:\n  {e}")
    except OSError as e:
        raise ConfigError(f"Could not read {config_path}: {e}")

    if data is None:
        raise ConfigError(f"{config_path} is empty. Run 'harvey setup' to configure Harvey.")
    if not isinstance(data, dict):
        raise ConfigError(
            f"{config_path} must contain a YAML mapping (key: value pairs), "
            f"got {type(data).__name__}."
        )

    try:
        return HarveyConfig(**data)
    except ValidationError as e:
        # Log a friendly, actionable summary, then re-raise the original
        # ValidationError so callers (and tests) keep the pydantic type.
        logger.error(
            f"Invalid configuration in {config_path}:\n{_format_validation_error(e)}\n"
            "Fix the fields above or re-run 'harvey setup'."
        )
        raise


def load_brain_config(config_path: str | None = None) -> BrainConfig:
    """Read just the ``brain:`` section, without validating the rest.

    Training runs before a product is configured (the template harvey.yaml
    still has placeholder values), but it must still think with the
    provider the user chose. A missing file or section means the Claude
    default; an invalid section raises rather than silently switching
    providers.
    """
    if config_path is None:
        try:
            config_path = _find_config_file()
        except ConfigFileNotFoundError:
            return BrainConfig()
    try:
        data = yaml.safe_load(read_user_text(config_path))
    except FileNotFoundError:
        return BrainConfig()
    except (yaml.YAMLError, OSError) as e:
        raise ConfigError(f"Could not read the brain settings in {config_path}: {e}")
    section = data.get("brain") if isinstance(data, dict) else None
    if section is None:
        return BrainConfig()
    if not isinstance(section, dict):
        raise ConfigError(f"'brain' in {config_path} must be a mapping.")
    try:
        return BrainConfig(**section)
    except ValidationError as e:
        raise ConfigError(
            f"Invalid brain settings in {config_path}:\n{_format_validation_error(e)}"
        )


def load_env() -> EnvConfig:
    """Load environment variables from .env file."""
    load_dotenv()
    env = EnvConfig(
        instantly_api_key=os.getenv("INSTANTLY_API_KEY", "").strip(),
        dataforseo_login=os.getenv("DATAFORSEO_LOGIN", "").strip(),
        dataforseo_password=os.getenv("DATAFORSEO_PASSWORD", "").strip(),
        dataforseo_sandbox=os.getenv("DATAFORSEO_SANDBOX", "").strip(),
        linkedin_email=os.getenv("LINKEDIN_EMAIL", "").strip(),
        linkedin_password=os.getenv("LINKEDIN_PASSWORD", "").strip(),
        hunter_api_key=os.getenv("HUNTER_API_KEY", "").strip(),
        serper_api_key=os.getenv("SERPER_API_KEY", "").strip(),
        google_places_api_key=os.getenv("GOOGLE_PLACES_API_KEY", "").strip(),
        evolution_api_url=os.getenv("EVOLUTION_API_URL", "").strip().rstrip("/"),
        evolution_api_key=os.getenv("EVOLUTION_API_KEY", "").strip(),
        evolution_instance=os.getenv("EVOLUTION_INSTANCE", "").strip(),
        reoon_api_key=os.getenv("REOON_API_KEY", "").strip(),
        zerobounce_api_key=os.getenv("ZEROBOUNCE_API_KEY", "").strip(),
        gmail_client_id=os.getenv("GMAIL_CLIENT_ID", "").strip(),
        gmail_client_secret=os.getenv("GMAIL_CLIENT_SECRET", "").strip(),
        smtp_host=os.getenv("SMTP_HOST", "").strip(),
        smtp_port=int(os.getenv("SMTP_PORT", "587").strip() or 587),
        smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
        smtp_password=os.getenv("SMTP_PASSWORD", "").strip(),
        imap_host=os.getenv("IMAP_HOST", "").strip(),
        imap_port=int(os.getenv("IMAP_PORT", "993").strip() or 993),
        imap_username=os.getenv("IMAP_USERNAME", "").strip(),
        imap_password=os.getenv("IMAP_PASSWORD", "").strip(),
        m365_client_id=os.getenv("M365_CLIENT_ID", "").strip(),
        m365_tenant_id=os.getenv("M365_TENANT_ID", "").strip(),
        precleared_api_key=os.getenv("PRECLEARED_API_KEY", "").strip(),
        harvey_webhook_secret=os.getenv("HARVEY_WEBHOOK_SECRET", "").strip(),
    )
    return env


def _find_config_file() -> str:
    """Search for Harvey's config.

    ``harvey.local.yaml`` wins when present. It is gitignored, so a fork can
    carry a real product configuration (trained on an actual company) while
    the tracked ``harvey.yaml`` stays an untrained template — nobody
    publishes their positioning, pricing, and prospect targeting by accident.
    """
    candidates = [
        Path.cwd() / "harvey.local.yaml",
        PROJECT_ROOT / "harvey.local.yaml",
        Path.cwd() / "harvey.yaml",
        Path.cwd().parent / "harvey.yaml",
        PROJECT_ROOT / "harvey.yaml",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    raise ConfigFileNotFoundError(
        "harvey.yaml not found in "
        + ", ".join(str(p.parent) for p in candidates)
        + ". Create one from harvey.yaml.example or run 'harvey setup'."
    )
