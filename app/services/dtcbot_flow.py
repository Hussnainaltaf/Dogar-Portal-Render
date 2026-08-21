"""DtcBot conversational FLOW engine -- Google-AI-assistant-style.

This module powers the rebuilt chatbot.  It mimics the conversational
style of Google's "Ask Google Help" assistant: short, professional bot
turns that always offer **actionable options** (clickable chips / option
lists) so the operator can drive a real task to completion *inside the
chat*.

Design
------
The server is stateless.  The whole flow state lives in a small dict
that travels back and forth with every message:

    flow_state = {
        "flow":  "add_demand" | "find_demand" | "find_candidate" | "print_doc" | None,
        "step":  "<current step id>",
        "slots": { ...collected values... },
    }

The client posts ``{message, flow_state}``; we return a response dict
that *includes the next* ``flow_state``.  Each response can carry:

    {
      "type": "flow",
      "text": "markdown body",
      "chips":   [{"label","value"}],     # inline pill buttons (Yes / No / option)
      "options": [{"label","value","sub"}],# stacked option cards
      "card":    {...},                    # a result/confirmation card
      "print":   {tpl_id, record_id, title},# triggers in-chat print
      "flow_state": {...},
      "done": bool,
    }

Every flow can be *started* either by a chip (value == "start:add_demand")
or by natural language ("add a new demand", "find candidate by passport").
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Candidate, Client, Demand, DocumentTemplate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _state(flow=None, step=None, slots=None) -> Dict[str, Any]:
    return {"flow": flow, "step": step, "slots": slots or {}}


def _clear_state() -> Dict[str, Any]:
    return _state()


def _norm(s: Any) -> str:
    return (str(s or "")).strip()


def _parse_date(text: str) -> Optional[date]:
    """Parse a user-typed date: dd-mm-yyyy, dd/mm/yyyy, yyyy-mm-dd, or 'today'."""
    t = _norm(text).lower()
    if not t or t in ("skip", "none", "-"):
        return None
    if t in ("today", "now"):
        return date.today()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", t)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


# The "home" menu chips shown on greeting + after every completed flow.
HOME_CHIPS = [
    {"label": "Add new demand", "value": "start:add_demand"},
    {"label": "Add new client", "value": "start:add_client"},
    {"label": "Find demand by file no.", "value": "start:find_demand"},
    {"label": "Find candidate by passport", "value": "start:find_candidate"},
    {"label": "Print a document", "value": "start:print_doc"},
]


def welcome(user=None) -> Dict[str, Any]:
    name = ""
    try:
        name = (getattr(user, "full_name", None) or getattr(user, "name", None) or "").strip()
    except Exception:
        name = ""
    hi = f"Hi {name}," if name else "Hi there,"
    return {
        "type": "flow",
        "greeting": hi,
        "text": "I'm here to help. What would you like to do?",
        "chips": HOME_CHIPS,
        "flow_state": _clear_state(),
    }


# ---------------------------------------------------------------------------
# Intent router -- decides if a free-text message should *start* a flow.
# Returns a flow id or None.
# ---------------------------------------------------------------------------
def detect_flow(message: str) -> Optional[str]:
    t = message.lower()
    if re.search(r"\b(add|new|create|make|register)\b.*\bclient\b", t) or re.search(r"\bclient\b.*\b(add|new|create|register)\b", t):
        return "add_client"
    if re.search(r"\b(add|new|create|make)\b.*\bdemand\b", t) or re.search(r"\bdemand\b.*\b(add|new|create)\b", t):
        return "add_demand"
    if "demand" in t and any(w in t for w in ("file", "find", "fetch", "open", "show", "lookup", "search", "detail")):
        return "find_demand"
    if "passport" in t and any(w in t for w in ("find", "fetch", "candidate", "search", "lookup", "by", "show", "detail")):
        return "find_candidate"
    if any(w in t for w in ("print", "document", "doc", "visa form", "letter")) and \
       any(w in t for w in ("print", "generate", "make", "create", "doc", "document", "visa")):
        return "print_doc"
    return None


# ===========================================================================
# FLOW: Add new demand
# ===========================================================================
_ADD_DEMAND_STEPS = [
    "client", "country", "sponsor", "trade", "quantity", "salary",
    "embassy", "visa_number", "bataka_number", "permission_no",
    "permission_date", "receiving_date", "confirm",
]


def _find_client(db: Session, ref: str) -> Optional[Client]:
    ref = _norm(ref)
    if not ref:
        return None
    if ref.isdigit():
        c = db.query(Client).filter(Client.id == int(ref)).first()
        if c:
            return c
    return (
        db.query(Client)
        .filter(Client.company_name.ilike(f"%{ref}%"))
        .order_by(Client.id.asc())
        .first()
    )


def _next_file_number(db: Session) -> str:
    rows = db.query(Demand.file_number).all()
    mx = 0
    for (fn,) in rows:
        m = re.search(r"(\d+)\s*$", str(fn or ""))
        if m:
            try:
                mx = max(mx, int(m.group(1)))
            except ValueError:
                pass
    return str(mx + 1)


def _flow_add_demand(db: Session, message: str, st: Dict[str, Any], user=None) -> Dict[str, Any]:
    step = st.get("step") or "client"
    slots = st.get("slots") or {}
    msg = _norm(message)

    # ---- step: client -------------------------------------------------
    if step == "client":
        if not msg or msg.startswith("start:"):
            return {
                "type": "flow",
                "text": "Let's create a **new demand file**. Which client (sponsor company) is this demand for? "
                        "Please type the company name.",
                "flow_state": _state("add_demand", "client", slots),
            }
        client = _find_client(db, msg)
        if not client:
            return {
                "type": "flow",
                "text": f"I couldn't find a client matching **{msg}**. Please type the exact company name.",
                "flow_state": _state("add_demand", "client", slots),
            }
        slots["client_id"] = client.id
        slots["client_name"] = client.company_name
        return {
            "type": "flow",
            "text": f"Client set to **{client.company_name}**.\n\nWhich **country** is this demand for?",
            "chips": [
                {"label": "Saudi Arabia", "value": "Saudi Arabia"},
                {"label": "UAE", "value": "UAE"},
                {"label": "Qatar", "value": "Qatar"},
                {"label": "Kuwait", "value": "Kuwait"},
                {"label": "Oman", "value": "Oman"},
            ],
            "flow_state": _state("add_demand", "country", slots),
        }

    # ---- step: country ------------------------------------------------
    if step == "country":
        slots["country"] = msg or "Saudi Arabia"
        return {
            "type": "flow",
            "text": f"Country: **{slots['country']}**.\n\nWhat is the **sponsor name** (the employer in the destination country)? "
                    "Enter it below, or choose Skip.",
            "chips": [{"label": "Skip", "value": "skip"}],
            "flow_state": _state("add_demand", "sponsor", slots),
        }

    # ---- step: sponsor ------------------------------------------------
    if step == "sponsor":
        slots["sponsor_name"] = "" if msg.lower() == "skip" else msg
        return {
            "type": "flow",
            "text": "Got it. What **trade / profession** is required? (e.g. Driver, Mason, Labour)",
            "chips": [
                {"label": "Driver", "value": "Driver"},
                {"label": "Labour", "value": "Labour"},
                {"label": "Mason", "value": "Mason"},
                {"label": "Electrician", "value": "Electrician"},
            ],
            "flow_state": _state("add_demand", "trade", slots),
        }

    # ---- step: trade --------------------------------------------------
    if step == "trade":
        slots["trade"] = msg or "Labour"
        return {
            "type": "flow",
            "text": f"Trade: **{slots['trade']}**.\n\nHow many **workers** (quantity) are required?",
            "chips": [
                {"label": "5", "value": "5"},
                {"label": "10", "value": "10"},
                {"label": "20", "value": "20"},
                {"label": "50", "value": "50"},
            ],
            "flow_state": _state("add_demand", "quantity", slots),
        }

    # ---- step: quantity -----------------------------------------------
    if step == "quantity":
        m = re.search(r"\d+", msg)
        slots["quantity"] = int(m.group(0)) if m else 1
        return {
            "type": "flow",
            "text": f"Quantity: **{slots['quantity']}**.\n\nWhat is the **monthly salary** (in SAR)? Enter an amount, or choose Skip.",
            "chips": [
                {"label": "1000 SAR", "value": "1000"},
                {"label": "1200 SAR", "value": "1200"},
                {"label": "1500 SAR", "value": "1500"},
                {"label": "Skip", "value": "skip"},
            ],
            "flow_state": _state("add_demand", "salary", slots),
        }

    # ---- step: salary -> embassy --------------------------------------
    if step == "salary":
        if msg.lower() == "skip":
            slots["salary"] = 0
        else:
            m = re.search(r"\d+", msg.replace(",", ""))
            slots["salary"] = int(m.group(0)) if m else 0
        return {
            "type": "flow",
            "text": f"Salary: **{slots.get('salary') or '—'} SAR**.\n\nWhich **embassy** does this demand go through? Pick one or type it, or Skip.",
            "chips": [
                {"label": "Saudi Embassy Islamabad", "value": "Saudi Embassy Islamabad"},
                {"label": "Saudi Consulate Karachi", "value": "Saudi Consulate Karachi"},
                {"label": "UAE Embassy", "value": "UAE Embassy"},
                {"label": "Skip", "value": "skip"},
            ],
            "flow_state": _state("add_demand", "embassy", slots),
        }

    # ---- step: embassy -------------------------------------------------
    if step == "embassy":
        slots["embassy"] = "" if msg.lower() == "skip" else msg
        return {
            "type": "flow",
            "text": "What is the **visa number**? Enter it, or choose Skip.",
            "chips": [{"label": "Skip", "value": "skip"}],
            "flow_state": _state("add_demand", "visa_number", slots),
        }

    # ---- step: visa_number ----------------------------------------------
    if step == "visa_number":
        slots["visa_number"] = "" if msg.lower() == "skip" else msg
        return {
            "type": "flow",
            "text": "What is the **Bataka / ID number**? Enter it, or choose Skip.",
            "chips": [{"label": "Skip", "value": "skip"}],
            "flow_state": _state("add_demand", "bataka_number", slots),
        }

    # ---- step: bataka_number ---------------------------------------------
    if step == "bataka_number":
        slots["bataka_number"] = "" if msg.lower() == "skip" else msg
        return {
            "type": "flow",
            "text": "What is the **permission number** (protectorate permission no.)? Enter it, or Skip.",
            "chips": [{"label": "Skip", "value": "skip"}],
            "flow_state": _state("add_demand", "permission_no", slots),
        }

    # ---- step: permission_no ---------------------------------------------
    if step == "permission_no":
        slots["permission_no"] = "" if msg.lower() == "skip" else msg
        return {
            "type": "flow",
            "text": "What is the **permission date**? Type it as dd-mm-yyyy (e.g. 15-06-2026), or 'today', or Skip.",
            "chips": [{"label": "Today", "value": "today"}, {"label": "Skip", "value": "skip"}],
            "flow_state": _state("add_demand", "permission_date", slots),
        }

    # ---- step: permission_date -------------------------------------------
    if step == "permission_date":
        if msg.lower() != "skip":
            dt = _parse_date(msg)
            if dt is None and msg.lower() not in ("skip", ""):
                return {
                    "type": "flow",
                    "text": "I couldn't read that date. Please use **dd-mm-yyyy** (e.g. 15-06-2026), or choose Skip.",
                    "chips": [{"label": "Today", "value": "today"}, {"label": "Skip", "value": "skip"}],
                    "flow_state": _state("add_demand", "permission_date", slots),
                }
            if dt:
                slots["permission_date"] = dt.isoformat()
        return {
            "type": "flow",
            "text": "And the **receiving date** (when the demand was received)? dd-mm-yyyy, 'today', or Skip.",
            "chips": [{"label": "Today", "value": "today"}, {"label": "Skip", "value": "skip"}],
            "flow_state": _state("add_demand", "receiving_date", slots),
        }

    # ---- step: receiving_date -> confirm ----------------------------------
    if step == "receiving_date":
        if msg.lower() != "skip":
            dt = _parse_date(msg)
            if dt is None and msg.lower() not in ("skip", ""):
                return {
                    "type": "flow",
                    "text": "I couldn't read that date. Please use **dd-mm-yyyy** (e.g. 15-06-2026), or choose Skip.",
                    "chips": [{"label": "Today", "value": "today"}, {"label": "Skip", "value": "skip"}],
                    "flow_state": _state("add_demand", "receiving_date", slots),
                }
            if dt:
                slots["receiving_date"] = dt.isoformat()
        fn = _next_file_number(db)
        slots["file_number"] = fn
        review = (
            "Please review the new demand:\n\n"
            f"- **File No.:** {fn}\n"
            f"- **Client:** {slots.get('client_name')}\n"
            f"- **Country:** {slots.get('country')}\n"
            f"- **Sponsor:** {slots.get('sponsor_name') or '—'}\n"
            f"- **Trade:** {slots.get('trade')} × {slots.get('quantity')}\n"
            f"- **Salary:** {slots.get('salary') or '—'} SAR\n"
            f"- **Embassy:** {slots.get('embassy') or '—'}\n"
            f"- **Visa No.:** {slots.get('visa_number') or '—'}\n"
            f"- **Bataka No.:** {slots.get('bataka_number') or '—'}\n"
            f"- **Permission No.:** {slots.get('permission_no') or '—'}\n"
            f"- **Permission Date:** {slots.get('permission_date') or '—'}\n"
            f"- **Receiving Date:** {slots.get('receiving_date') or '—'}\n\n"
            "Shall I create this demand?"
        )
        return {
            "type": "flow",
            "text": review,
            "chips": [
                {"label": "Yes, create it", "value": "yes"},
                {"label": "No, cancel", "value": "no"},
            ],
            "flow_state": _state("add_demand", "confirm", slots),
        }

    # ---- step: confirm ------------------------------------------------
    if step == "confirm":
        if msg.lower() in ("no", "cancel", "n"):
            return {
                "type": "flow",
                "text": "No problem — I cancelled that demand. Is there anything else?",
                "chips": HOME_CHIPS,
                "flow_state": _clear_state(),
                "done": True,
            }
        # CREATE the demand for real
        try:
            from app.models.demand import JobCategory  # noqa
            d = Demand(
                file_number=slots["file_number"],
                client_id=slots["client_id"],
                country=slots.get("country") or "Saudi Arabia",
                sponsor_name=slots.get("sponsor_name") or "",
                status="active",
                embassy=slots.get("embassy") or "",
                visa_number=slots.get("visa_number") or "",
                bataka_number=slots.get("bataka_number") or "",
                permission_no=slots.get("permission_no") or "",
                permission_date=_parse_date(slots.get("permission_date") or ""),
                receiving_date=_parse_date(slots.get("receiving_date") or ""),
            )
            try:
                if user is not None:
                    d.created_by_id = user.id
                    d.updated_by_id = user.id
            except Exception:
                pass
            db.add(d)
            db.flush()
            # add the job category (trade line)
            try:
                jc = JobCategory(
                    demand_id=d.id,
                    trade=slots.get("trade") or "Labour",
                    quantity=int(slots.get("quantity") or 1),
                    salary=float(slots.get("salary") or 0),
                )
                db.add(jc)
            except Exception:
                pass
            db.commit()
            db.refresh(d)
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            return {
                "type": "flow",
                "text": f"⚠️ I couldn't create the demand: {e}",
                "chips": HOME_CHIPS,
                "flow_state": _clear_state(),
                "done": True,
            }
        return {
            "type": "flow",
            "text": f"✅ **Demand created successfully!** File No. **{d.file_number}** for "
                    f"{slots.get('client_name')} ({slots.get('country')}).",
            "card": {
                "kind": "demand",
                "title": f"Demand {d.file_number}",
                "rows": [
                    ["File No.", d.file_number],
                    ["Client", slots.get("client_name")],
                    ["Country", d.country],
                    ["Trade", f"{slots.get('trade')} × {slots.get('quantity')}"],
                ],
                "href": f"/demands/{d.id}",
            },
            "chips": [
                {"label": "Open demand", "value": f"open:/demands/{d.id}"},
                {"label": "Add another demand", "value": "start:add_demand"},
                {"label": "Done", "value": "start:home"},
            ],
            "flow_state": _clear_state(),
            "done": True,
        }

    # safety fall-through
    return {"type": "flow", "text": "Let's start again.", "chips": HOME_CHIPS, "flow_state": _clear_state()}


# ===========================================================================
# FLOW: Add new client
# ===========================================================================
# Streamlined client-registration flow (per user spec):
#   name -> individual/company -> phone -> country -> active status -> confirm
_ADD_CLIENT_STEPS = ["name", "type", "phone", "country", "status", "confirm"]

_CLIENT_COUNTRIES = ["Pakistan", "Saudi Arabia", "UAE", "Qatar", "Kuwait", "Oman", "Bahrain"]


def _flow_add_client(db: Session, message: str, st: Dict[str, Any], user=None) -> Dict[str, Any]:
    step = st.get("step") or "name"
    slots = st.get("slots") or {}
    msg = _norm(message)

    # ---- step: name ----------------------------------------------------
    if step == "name":
        if not msg or msg.startswith("start:"):
            return {
                "type": "flow",
                "text": "Let's register a **new client**. What is the client's **name** "
                        "(company name or full name)?",
                "flow_state": _state("add_client", "name", slots),
            }
        if msg.startswith("force:"):
            msg = msg.split("force:", 1)[1]
        else:
            existing = db.query(Client).filter(Client.company_name.ilike(msg)).first()
            if existing:
                return {
                    "type": "flow",
                    "text": f"⚠️ A client named **{existing.company_name}** already exists (ID {existing.id}). "
                            "Create a new one anyway, or open the existing client?",
                    "chips": [
                        {"label": "Create anyway", "value": f"force:{msg}"},
                        {"label": "Open existing", "value": f"open:/clients/{existing.id}"},
                        {"label": "Cancel", "value": "start:home"},
                    ],
                    "flow_state": _state("add_client", "name", slots),
                }
        slots["company_name"] = msg
        return {
            "type": "flow",
            "text": f"Client name: **{msg}**.\n\nIs this an **Individual** or a **Company**?",
            "chips": [
                {"label": "Individual", "value": "Individual"},
                {"label": "Company", "value": "Company"},
            ],
            "flow_state": _state("add_client", "type", slots),
        }

    # ---- step: type (individual / company) --------------------------------
    if step == "type":
        slots["client_type"] = "Company" if "comp" in msg.lower() else "Individual"
        return {
            "type": "flow",
            "text": f"Type: **{slots['client_type']}**.\n\nWhat is the client's **phone number**? Enter it, or Skip.",
            "chips": [{"label": "Skip", "value": "skip"}],
            "flow_state": _state("add_client", "phone", slots),
        }

    # ---- step: phone -------------------------------------------------------
    if step == "phone":
        slots["phone"] = "" if msg.lower() == "skip" else msg
        return {
            "type": "flow",
            "text": "Which **country** is the client based in?",
            "chips": [{"label": c, "value": c} for c in _CLIENT_COUNTRIES]
                     + [{"label": "Skip", "value": "skip"}],
            "flow_state": _state("add_client", "country", slots),
        }

    # ---- step: country -----------------------------------------------------
    if step == "country":
        slots["country"] = "Pakistan" if msg.lower() == "skip" else msg
        return {
            "type": "flow",
            "text": f"Country: **{slots['country']}**.\n\nShould this client be **Active** or **Inactive**?",
            "chips": [
                {"label": "Active", "value": "active"},
                {"label": "Inactive", "value": "inactive"},
            ],
            "flow_state": _state("add_client", "status", slots),
        }

    # ---- step: status -> confirm ------------------------------------------
    if step == "status":
        slots["status"] = "inactive" if "inact" in msg.lower() else "active"
        review = (
            "Please review the new client:\n\n"
            f"- **Name:** {slots.get('company_name')}\n"
            f"- **Type:** {slots.get('client_type')}\n"
            f"- **Phone:** {slots.get('phone') or '—'}\n"
            f"- **Country:** {slots.get('country') or '—'}\n"
            f"- **Status:** {slots.get('status').title()}\n\n"
            "Shall I create this client?"
        )
        return {
            "type": "flow",
            "text": review,
            "chips": [
                {"label": "Yes, create it", "value": "yes"},
                {"label": "No, cancel", "value": "no"},
            ],
            "flow_state": _state("add_client", "confirm", slots),
        }

    # ---- step: confirm --------------------------------------------------------------------
    if step == "confirm":
        if msg.lower() in ("no", "cancel", "n"):
            return {
                "type": "flow",
                "text": "No problem — I cancelled that client. Is there anything else?",
                "chips": HOME_CHIPS,
                "flow_state": _clear_state(),
                "done": True,
            }
        try:
            c = Client(
                company_name=slots.get("company_name") or "",
                client_type=slots.get("client_type") or "Individual",
                phone=slots.get("phone") or "",
                country=slots.get("country") or "Pakistan",
                status=slots.get("status") or "active",
            )
            db.add(c)
            db.commit()
            db.refresh(c)
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            return {
                "type": "flow",
                "text": f"⚠️ I couldn't create the client: {e}",
                "chips": HOME_CHIPS,
                "flow_state": _clear_state(),
                "done": True,
            }
        return {
            "type": "flow",
            "text": f"✅ **Client created successfully!** **{c.company_name}** (ID {c.id}).",
            "card": {
                "kind": "client",
                "title": c.company_name,
                "rows": [
                    ["Type", c.client_type or "—"],
                    ["Phone", c.phone or "—"],
                    ["Country", c.country or "—"],
                    ["Status", (c.status or "active").title()],
                ],
                "href": f"/clients/{c.id}",
            },
            "chips": [
                {"label": "Open client", "value": f"open:/clients/{c.id}"},
                {"label": "Add a demand for this client", "value": "start:add_demand"},
                {"label": "Add another client", "value": "start:add_client"},
                {"label": "Done", "value": "start:home"},
            ],
            "flow_state": _clear_state(),
            "done": True,
        }

    return {"type": "flow", "text": "Let's start again.", "chips": HOME_CHIPS, "flow_state": _clear_state()}


# ===========================================================================
# FLOW: Find demand by file no.
# ===========================================================================
def _flow_find_demand(db: Session, message: str, st: Dict[str, Any], user=None) -> Dict[str, Any]:
    step = st.get("step") or "ask"
    msg = _norm(message)

    if step == "ask" or not msg or msg.startswith("start:"):
        return {
            "type": "flow",
            "text": "Sure — what is the **demand file number**? (e.g. 8203)",
            "flow_state": _state("find_demand", "lookup", {}),
        }

    # lookup
    raw = re.sub(r"[^0-9A-Za-z]", "", msg)
    d = (
        db.query(Demand)
        .filter(or_(Demand.file_number == msg, Demand.file_number.ilike(f"%{raw}%")))
        .order_by(Demand.id.desc())
        .first()
    )
    if not d:
        return {
            "type": "flow",
            "text": f"I couldn't find a demand with file no. **{msg}**. Try another number?",
            "chips": [{"label": "Back to menu", "value": "start:home"}],
            "flow_state": _state("find_demand", "lookup", {}),
        }
    client = db.query(Client).filter(Client.id == d.client_id).first()
    # count assigned candidates
    cand_count = 0
    try:
        from app.models.demand import JobCategory
        jc_ids = [j.id for j in db.query(JobCategory).filter(JobCategory.demand_id == d.id).all()]
        if jc_ids:
            from app.models.candidate import CandidateAssignment
            cand_count = (
                db.query(CandidateAssignment)
                .filter(CandidateAssignment.job_category_id.in_(jc_ids))
                .count()
            )
    except Exception:
        cand_count = 0
    return {
        "type": "flow",
        "text": f"Here's demand **{d.file_number}**:",
        "card": {
            "kind": "demand",
            "title": f"Demand {d.file_number}",
            "rows": [
                ["Client", client.company_name if client else "—"],
                ["Country", d.country or "—"],
                ["Sponsor", d.sponsor_name or "—"],
                ["Status", d.status or "—"],
                ["Candidates", str(cand_count)],
            ],
            "href": f"/demands/{d.id}",
        },
        "chips": [
            {"label": "Open demand", "value": f"open:/demands/{d.id}"},
            {"label": "Find another", "value": "start:find_demand"},
            {"label": "Done", "value": "start:home"},
        ],
        "flow_state": _clear_state(),
        "done": True,
    }


# ===========================================================================
# FLOW: Find candidate by passport no.
# ===========================================================================
def _flow_find_candidate(db: Session, message: str, st: Dict[str, Any], user=None) -> Dict[str, Any]:
    step = st.get("step") or "ask"
    msg = _norm(message)

    if step == "ask" or not msg or msg.startswith("start:"):
        return {
            "type": "flow",
            "text": "Of course — what is the candidate's **passport number**?",
            "flow_state": _state("find_candidate", "lookup", {}),
        }

    c = (
        db.query(Candidate)
        .filter(or_(Candidate.passport_no == msg, Candidate.passport_no.ilike(f"%{msg}%")))
        .order_by(Candidate.id.desc())
        .first()
    )
    if not c:
        return {
            "type": "flow",
            "text": f"No candidate found with passport **{msg}**. Want to try again?",
            "chips": [{"label": "Back to menu", "value": "start:home"}],
            "flow_state": _state("find_candidate", "lookup", {}),
        }
    return {
        "type": "flow",
        "text": f"Found candidate **{c.full_name}**:",
        "card": {
            "kind": "candidate",
            "title": c.full_name,
            "rows": [
                ["Passport", c.passport_no or "—"],
                ["CNIC", c.cnic or "—"],
                ["Father", c.father_name or "—"],
                ["Profession", c.profession or "—"],
                ["Phone", getattr(c, "phone", "") or "—"],
            ],
            "href": f"/candidates?open={c.id}&flyout=1",
        },
        "chips": [
            {"label": "Open profile", "value": f"open:/candidates?open={c.id}&flyout=1"},
            {"label": "Print visa form", "value": f"print:17:{c.id}"},
            {"label": "Find another", "value": "start:find_candidate"},
            {"label": "Done", "value": "start:home"},
        ],
        "flow_state": _clear_state(),
        "done": True,
    }


# ===========================================================================
# FLOW: Print a document (in-chat printout)
# ===========================================================================
_PRINT_TEMPLATES = [
    (17, "Visa Application Form (Karachi)"),
    (18, "Visa Application Form (Islamabad)"),
    (2, "Demand Letter"),
    (10, "Passport Submission Letter"),
]


def _flow_print_doc(db: Session, message: str, st: Dict[str, Any], user=None) -> Dict[str, Any]:
    step = st.get("step") or "pick_doc"
    slots = st.get("slots") or {}
    msg = _norm(message)

    # ---- step: pick a document ---------------------------------------
    if step == "pick_doc" or msg.startswith("start:"):
        opts = []
        for tid, _name in _PRINT_TEMPLATES:
            tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == tid).first()
            if tpl:
                opts.append({"label": tpl.name, "value": f"tpl:{tid}", "sub": "PDF template"})
        return {
            "type": "flow",
            "text": "Which document would you like to print? Pick one:",
            "options": opts or [{"label": "Visa Application Form (Karachi)", "value": "tpl:17"}],
            "flow_state": _state("print_doc", "pick_record", slots),
        }

    # ---- step: got the template, ask for candidate -------------------
    if step == "pick_record":
        m = re.search(r"tpl:(\d+)", msg)
        if m:
            slots["tpl_id"] = int(m.group(1))
        if not slots.get("tpl_id"):
            slots["tpl_id"] = 17
        tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == slots["tpl_id"]).first()
        slots["tpl_name"] = tpl.name if tpl else "Document"
        return {
            "type": "flow",
            "text": f"**{slots['tpl_name']}** selected. Which candidate is this for? "
                    "Type the **passport number** (or candidate name).",
            "flow_state": _state("print_doc", "resolve", slots),
        }

    # ---- step: resolve candidate + confirm ---------------------------
    if step == "resolve":
        c = (
            db.query(Candidate)
            .filter(or_(
                Candidate.passport_no.ilike(f"%{msg}%"),
                Candidate.full_name.ilike(f"%{msg}%"),
                Candidate.cnic.ilike(f"%{msg}%"),
            ))
            .order_by(Candidate.id.desc())
            .first()
        )
        if not c:
            return {
                "type": "flow",
                "text": f"I couldn't find a candidate matching **{msg}**. Try the passport number again.",
                "flow_state": _state("print_doc", "resolve", slots),
            }
        slots["record_id"] = c.id
        slots["cand_name"] = c.full_name
        return {
            "type": "flow",
            "text": f"Ready to print **{slots['tpl_name']}** for **{c.full_name}** "
                    f"(Passport {c.passport_no or '—'}). Print now?",
            "chips": [
                {"label": "Print now", "value": "print_now"},
                {"label": "Cancel", "value": "no"},
            ],
            "flow_state": _state("print_doc", "do_print", slots),
        }

    # ---- step: trigger print -----------------------------------------
    if step == "do_print":
        if msg.lower() in ("no", "cancel"):
            return {
                "type": "flow",
                "text": "Cancelled. Anything else?",
                "chips": HOME_CHIPS,
                "flow_state": _clear_state(),
                "done": True,
            }
        return {
            "type": "flow",
            "text": f"🖨️ Printing **{slots.get('tpl_name')}** for **{slots.get('cand_name')}**… "
                    "the print preview is opening now.",
            "print": {
                "tpl_id": slots.get("tpl_id"),
                "record_id": slots.get("record_id"),
                "title": f"{slots.get('tpl_name')} — {slots.get('cand_name')}",
            },
            "chips": [
                {"label": "Print another", "value": "start:print_doc"},
                {"label": "Done", "value": "start:home"},
            ],
            "flow_state": _clear_state(),
            "done": True,
        }

    return {"type": "flow", "text": "Let's start again.", "chips": HOME_CHIPS, "flow_state": _clear_state()}


# ===========================================================================
# Dispatcher
# ===========================================================================
_FLOWS = {
    "add_demand": _flow_add_demand,
    "add_client": _flow_add_client,
    "find_demand": _flow_find_demand,
    "find_candidate": _flow_find_candidate,
    "print_doc": _flow_print_doc,
}


def handle(db: Session, message: str, flow_state: Optional[Dict[str, Any]] = None, user=None) -> Optional[Dict[str, Any]]:
    """Main flow entry point.

    Returns a flow response dict, or ``None`` if this message is not part
    of (and does not start) any known flow -- in which case the caller
    should fall back to the LLM / rule-based brain.
    """
    message = _norm(message)
    fs = flow_state or _clear_state()
    active = fs.get("flow")

    # ---- global chip commands ----------------------------------------
    if message == "start:home":
        return welcome(user)
    if message.startswith("open:"):
        href = message.split("open:", 1)[1]
        return {
            "type": "flow",
            "text": "Opening that for you…",
            "open": href,
            "chips": HOME_CHIPS,
            "flow_state": _clear_state(),
            "done": True,
        }
    if message.startswith("print:"):
        # print:<tpl>:<record_id>
        parts = message.split(":")
        try:
            tpl_id = int(parts[1])
            rec = parts[2] if len(parts) > 2 else ""
        except Exception:
            tpl_id, rec = 17, ""
        return {
            "type": "flow",
            "text": "🖨️ Opening the print preview…",
            "print": {"tpl_id": tpl_id, "record_id": rec, "title": "Document"},
            "chips": HOME_CHIPS,
            "flow_state": _clear_state(),
            "done": True,
        }
    if message.startswith("start:"):
        flow = message.split("start:", 1)[1]
        if flow in _FLOWS:
            return _FLOWS[flow](db, "start:", _state(flow, None, {}), user=user)

    # ---- continue an active flow -------------------------------------
    if active in _FLOWS:
        return _FLOWS[active](db, message, fs, user=user)

    # ---- maybe START a flow from natural language --------------------
    flow = detect_flow(message)
    if flow:
        return _FLOWS[flow](db, "start:", _state(flow, None, {}), user=user)

    return None
