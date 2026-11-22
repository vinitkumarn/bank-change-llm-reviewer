"""Python rules: signals, risk level, dollars at risk, hold, owner and actions.

Python owns every decision here. An extraction (regex today, the model later) only fills a
`Facts` object; the functions below turn request + master data + facts into a `Review`.
Dates are always measured from config.REFERENCE_DATE, never from the computer's clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from bankreview import config

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


# --- Records -----------------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    request_id: str
    vendor_id: int
    channel: str
    from_address: str
    reply_to: str
    already_applied: str  # "Y" | "N"
    body: str  # UNTRUSTED text


@dataclass(frozen=True)
class Contact:
    email: str
    phone_on_file: str
    last_update_date: date


@dataclass(frozen=True)
class BankAccount:
    account_token: str
    last4: str
    account_num_hash: str
    vendor_id: int
    is_active: str


@dataclass(frozen=True)
class Schedule:
    invoice_num: str
    amount_remaining: Decimal
    due_date: date


@dataclass(frozen=True)
class Master:
    vendor_id: int
    vendor_name: str
    country: str
    domain_on_file: str
    site_last_update_date: date
    contacts: tuple[Contact, ...]
    bank_accounts: tuple[BankAccount, ...]  # ALL suppliers' accounts (for SHARED_ACCOUNT)
    schedules: tuple[Schedule, ...]  # this supplier only

    @property
    def phone_on_file(self) -> str:
        return self.contacts[0].phone_on_file if self.contacts else ""


@dataclass(frozen=True)
class Facts:
    """What an extraction found in the request text. No decisions in here."""

    bank_numbers: tuple[str, ...] = ()
    account_hashes: tuple[str, ...] = ()
    new_bank_countries: tuple[str, ...] = ()
    account_holder_names: tuple[str, ...] = ()
    phone_numbers: tuple[str, ...] = ()
    urgency_quotes: tuple[str, ...] = ()
    skip_verification_quotes: tuple[str, ...] = ()
    grey_quotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Signal:
    code: str
    level: str
    evidence: str


@dataclass
class Review:
    case_id: str
    by: str
    status: str  # OK | NEEDS_HUMAN
    risk_level: str
    signals: list[Signal]
    dollars_at_risk: Decimal
    hold: str  # Y | N
    owner: str
    actions: list[str] = field(default_factory=list)
    confidence: str = ""  # set by Python (claude reviewer only): HIGH | MEDIUM | LOW
    explanation: str = ""  # the model's plain-language explanation (claude reviewer only)

    @property
    def signal_codes(self) -> list[str]:
        return sorted({s.code for s in self.signals})

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id, "by": self.by, "status": self.status,
            "risk_level": self.risk_level, "signals": self.signal_codes,
            "evidence": {s.code: s.evidence for s in self.signals},
            "dollars_at_risk": str(self.dollars_at_risk), "hold": self.hold,
            "owner": self.owner, "actions": self.actions,
            **({"confidence": self.confidence} if self.confidence else {}),
            **({"explanation": self.explanation} if self.explanation else {}),
        }


# --- Small helpers -----------------------------------------------------------------

def email_address(addr: str) -> str:
    """'Name <a@b.com>' -> 'a@b.com' (lowercase). Empty stays empty."""
    addr = (addr or "").strip()
    if "<" in addr:
        addr = addr.rsplit("<", 1)[1].split(">", 1)[0]
    return addr.strip().lower()


def email_domain(addr: str) -> str:
    email = email_address(addr)
    return email.rsplit("@", 1)[1] if "@" in email else ""


def phone_digits(phone: str) -> str:
    """Digits only: '+1 (408) 555-0101' -> '14085550101'."""
    return re.sub(r"\D", "", phone or "")


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


HOMOGLYPHS = (("rn", "m"), ("vv", "w"), ("0", "o"), ("1", "l"), ("3", "e"))


def _unglyph(domain: str) -> str:
    for fake, real in HOMOGLYPHS:
        domain = domain.replace(fake, real)
    return domain


def _norm_name(name: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", name.lower()).split())


def _sender_domains(req: Request) -> list[str]:
    return [d for d in (email_domain(req.from_address), email_domain(req.reply_to)) if d]


def _signal(code: str, evidence: str) -> Signal:
    return Signal(code, config.SIGNAL_LEVELS[code], evidence)


# --- Signals (one small function each) ---------------------------------------------

def domain_mismatch(req: Request, master: Master, facts: Facts) -> Signal | None:
    on_file = master.domain_on_file.lower()
    for d in _sender_domains(req):
        if d in config.FREE_MAIL_DOMAINS:
            return _signal("DOMAIN_MISMATCH", f"free mail domain {d}")
        if d != on_file:
            return _signal("DOMAIN_MISMATCH", f"domain {d} is not the domain on file {on_file}")
    return None


def lookalike_domain(req: Request, master: Master, facts: Facts) -> Signal | None:
    on_file = master.domain_on_file.lower()
    for d in _sender_domains(req):
        if d == on_file:
            continue
        if levenshtein(d, on_file) <= config.LOOKALIKE_MAX_EDIT_DISTANCE:
            return _signal("LOOKALIKE_DOMAIN", f"{d} is within edit distance "
                           f"{config.LOOKALIKE_MAX_EDIT_DISTANCE} of {on_file}")
        if _unglyph(d) == _unglyph(on_file):
            return _signal("LOOKALIKE_DOMAIN", f"{d} uses look-alike characters for {on_file}")
        if d.startswith(on_file + "."):
            return _signal("LOOKALIKE_DOMAIN", f"{d} hides {on_file} inside another domain")
    return None


def country_change(req: Request, master: Master, facts: Facts) -> Signal | None:
    for c in facts.new_bank_countries:
        if c != master.country:
            return _signal("COUNTRY_CHANGE", f"new bank country {c}, supplier country "
                           f"{master.country}")
    return None


def shared_account(req: Request, master: Master, facts: Facts) -> Signal | None:
    wanted = set(facts.account_hashes)
    for acct in master.bank_accounts:
        if acct.account_num_hash in wanted and acct.vendor_id != master.vendor_id:
            return _signal("SHARED_ACCOUNT", f"account ...{acct.last4} ({acct.account_token}) "
                           f"belongs to vendor {acct.vendor_id}")
    return None


def holder_mismatch(req: Request, master: Master, facts: Facts) -> Signal | None:
    supplier = _norm_name(master.vendor_name)
    for name in facts.account_holder_names:
        if _norm_name(name) != supplier:
            return _signal("HOLDER_MISMATCH", f"holder '{name}' is not '{master.vendor_name}'")
    return None


def skip_verification(req: Request, master: Master, facts: Facts) -> Signal | None:
    if facts.skip_verification_quotes:
        return _signal("SKIP_VERIFICATION", f"quote: '{facts.skip_verification_quotes[0]}'")
    on_file = {phone_digits(c.phone_on_file) for c in master.contacts}
    for phone in facts.phone_numbers:
        if phone_digits(phone) not in on_file:
            return _signal("SKIP_VERIFICATION", f"gives a phone number not on file: {phone}")
    return None


def urgency(req: Request, master: Master, facts: Facts) -> Signal | None:
    if facts.urgency_quotes:
        return _signal("URGENCY", f"quote: '{facts.urgency_quotes[0]}'")
    return None


def recent_master_change(req: Request, master: Master, facts: Facts,
                         reference_date: date = config.REFERENCE_DATE) -> Signal | None:
    cutoff = reference_date - timedelta(days=config.RECENT_CHANGE_DAYS)
    dates = [("site", master.site_last_update_date)] + [
        ("contact", c.last_update_date) for c in master.contacts]
    for what, d in dates:
        if cutoff <= d <= reference_date:
            return _signal("RECENT_MASTER_CHANGE", f"{what} changed on {d.isoformat()}")
    return None


def new_contact(req: Request, master: Master, facts: Facts) -> Signal | None:
    sender = email_address(req.from_address)
    if not sender:  # letters have no sender address
        return None
    if sender not in {c.email.lower() for c in master.contacts}:
        return _signal("NEW_CONTACT", f"sender {sender} is not a contact on file")
    return None


SIGNAL_FUNCTIONS = (domain_mismatch, lookalike_domain, country_change, shared_account,
                    holder_mismatch, skip_verification, urgency, recent_master_change,
                    new_contact)


def evaluate_signals(req: Request, master: Master, facts: Facts) -> list[Signal]:
    return [s for fn in SIGNAL_FUNCTIONS if (s := fn(req, master, facts)) is not None]


# --- Decisions ---------------------------------------------------------------------

def risk_level(signals: list[Signal]) -> str:
    """HIGH if any HIGH or two or more MEDIUM; MEDIUM if one MEDIUM; else LOW."""
    levels = [s.level for s in signals]
    if HIGH in levels or levels.count(MEDIUM) >= 2:
        return HIGH
    return MEDIUM if MEDIUM in levels else LOW


def higher_level(*levels: str) -> str:
    """Never-downgrade: the highest of the given risk levels."""
    return max(levels, key=config.RISK_LEVELS.index)


def dollars_at_risk(schedules: tuple[Schedule, ...] | list[Schedule],
                    reference_date: date = config.REFERENCE_DATE,
                    exposure_days: int = config.EXPOSURE_DAYS) -> Decimal:
    """Open amounts due on or before reference_date + exposure_days (overdue included)."""
    end = reference_date + timedelta(days=exposure_days)
    total = sum((s.amount_remaining for s in schedules
                 if s.amount_remaining > 0 and s.due_date <= end), Decimal("0.00"))
    return total.quantize(config.MONEY_QUANT)


def hold(level: str, already_applied: str) -> str:
    """Hold payments ONLY when HIGH and the new account is already active."""
    return "Y" if level == HIGH and already_applied == "Y" else "N"


def owner(level: str, grey: bool) -> str:
    return config.OWNER_SM_TREASURY if level == HIGH or grey else config.OWNER_SM


def actions(level: str, hold_flag: str, phone_on_file: str, grey: bool) -> list[str]:
    out = [
        f"Call back the supplier on the phone number ON FILE ({phone_on_file}). Never use a "
        "phone number from the request.",
        "Do not activate the new account before the call-back confirms the change.",
    ]
    if level == HIGH:
        out.append("Supplier Master team and Treasury review this request.")
    elif level == MEDIUM:
        out.append("Call back before the change is entered.")
    else:
        out.append("Standard call-back by the Supplier Master team.")
    if hold_flag == "Y":
        out.append("Hold this supplier's payments: AP places the hold; Treasury releases it "
                   "after a successful call-back.")
    if grey and level != HIGH:
        out.append("Grey area: Supplier Master and Treasury decide together.")
    return out


def status(facts: Facts) -> str:
    """No bank number at all -> the request is incomplete -> a human must look."""
    return "OK" if facts.bank_numbers else "NEEDS_HUMAN"


def review(case_id: str, by: str, req: Request, master: Master, signals: list[Signal],
           facts: Facts, case_status: str | None = None, floor: str = LOW) -> Review:
    """`floor` is the never-downgrade floor: the level can go up from it, never below it."""
    level = higher_level(risk_level(signals), floor)
    grey = bool(facts.grey_quotes)
    hold_flag = hold(level, req.already_applied)
    return Review(
        case_id=case_id, by=by, status=case_status or status(facts), risk_level=level,
        signals=signals, dollars_at_risk=dollars_at_risk(master.schedules), hold=hold_flag,
        owner=owner(level, grey), actions=actions(level, hold_flag, master.phone_on_file, grey))
