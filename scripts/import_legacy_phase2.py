"""
Phase-2 legacy migration — the tables the first pass (import_legacy_backup.py)
did NOT cover. Run AFTER the first pass so clients already exist.

Legacy → Portal mapping:
    visa_receipts        -> demands           (the "demand files")   ~2267
    visa_receipts_items  -> job_categories    (trades under a demand) ~2383
    phone_book           -> contacts                                  (12)

Idempotent: dedup by natural keys (demand file_number, contact
name+phone) so re-running does not duplicate.

Run:
    TESTING=true venv/bin/python scripts/import_legacy_phase2.py /home/user/uploaded_files/backup.sql
"""
import csv
import io
import re
import sys
from datetime import datetime

sys.path.insert(0, ".")
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Client,
    Contact,
    Demand,
    JobCategory,
)

INSERT_RE = re.compile(r"^INSERT INTO (\w+) VALUES\((.*)\);\s*$")


def parse_dump(path):
    tables = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = INSERT_RE.match(line.rstrip("\n"))
            if not m:
                continue
            table, values = m.group(1), m.group(2)
            values = values.replace('\\"', '""')
            reader = csv.reader(io.StringIO(values), quotechar='"',
                                doublequote=True, skipinitialspace=True)
            row = next(reader)
            tables.setdefault(table, []).append(row)
    return tables


def fix_mojibake(s):
    if not s:
        return s
    if any(ch in s for ch in ("Ù", "Ø", "Ã", "Â")):
        try:
            return s.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return s


def clean(s):
    if s is None:
        return ""
    s = str(s).strip()
    if s in ("????", "NULL", "null"):
        return ""
    return fix_mojibake(s)


def g(r, i):
    return clean(r[i]) if i < len(r) else ""


def norm_date(s):
    """ISO date string for FlexibleDate columns, or None."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
                "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def norm_datetime(s):
    """Python datetime object for plain SQLAlchemy DateTime columns, or None."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
                "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def to_float(s):
    s = clean(s).replace(",", "")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def to_int(s):
    s = clean(s)
    try:
        return int(float(s)) if s else None
    except ValueError:
        return None


def main(path):
    print(f"Parsing dump: {path}")
    tables = parse_dump(path)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    stats = {}
    try:
        # --- Build lookup maps from legacy dump -----------------------------
        # legacy party_id -> party name (parties: 0=id, 1=name)
        party_name_by_id = {}
        for r in tables.get("parties", []):
            party_name_by_id[g(r, 0)] = clean(g(r, 1))

        # portal client by (lowercase) company_name
        client_by_name = {}
        for c in db.query(Client).all():
            if c.company_name:
                client_by_name[c.company_name.lower()] = c

        # legacy visa_category id -> name (visa_categories: 0=id, 1=name)
        vc_name_by_legacy_id = {}
        for r in tables.get("visa_categories", []):
            vc_name_by_legacy_id[g(r, 0)] = clean(g(r, 1))

        # legacy embassy id -> embassy display name (embassies: 0=id,1=name,2=city_code)
        # This drives WHICH visa application form is available for a demand /
        # its candidates (Islamabad vs Karachi). visa_receipts.emb_id points
        # here. Without it the UI shows "No embassy" and no visa form.
        emb_name_by_legacy_id = {}
        for r in tables.get("embassies", []):
            emb_name_by_legacy_id[g(r, 0)] = clean(g(r, 1))

        # ---- visa_receipts -> demands ------------------------------------
        # 0 id,1 file_number,2 date,3 visa_issue_date_h,4 party_id,
        # 5 sponsor_name,6 sponsor_name_arabic,7 sponsor_address,
        # 8 sponsor_address_arabic,9 sponsor_phone_1,10 sponsor_phone_2,
        # 11 visa_number,12 bataka_number,...,19 visa_country_id,20 emb_id,
        # 24 total_visas,25 permission_date,26 permission_number,27 status
        existing_files = {d.file_number for d in db.query(Demand).all()
                          if d.file_number}
        demand_by_legacy_id = {}
        n = 0
        for r in tables.get("visa_receipts", []):
            legacy_id = g(r, 0)
            file_number = g(r, 1) or f"VR-{legacy_id}"
            if file_number in existing_files:
                # still map it so items can attach
                dem = db.query(Demand).filter_by(file_number=file_number).first()
                if dem:
                    demand_by_legacy_id[legacy_id] = dem
                continue
            party_id = g(r, 4)
            client = client_by_name.get(
                (party_name_by_id.get(party_id, "") or "").lower())
            sponsor = g(r, 5)
            # client_id is NOT nullable. For the handful of demands whose
            # legacy party_id doesn't resolve to a client, fall back to a
            # client created from the demand's own sponsor_name (or a shared
            # "Unknown Sponsor (Legacy)" placeholder as last resort).
            if client is None:
                fb_name = sponsor or "Unknown Sponsor (Legacy)"
                client = client_by_name.get(fb_name.lower())
                if client is None:
                    client = Client(
                        company_name=fb_name,
                        client_type="sponsor",
                        status="inactive",
                        sponsor_name=sponsor,
                        sponsor_name_arabic=g(r, 6),
                        sponsor_address=g(r, 7),
                        country="Saudi Arabia",
                    )
                    db.add(client)
                    db.flush()
                    client_by_name[fb_name.lower()] = client
            total_visas = to_int(g(r, 24))
            dem = Demand(
                file_number=file_number,
                client_id=client.id,
                receiving_date=norm_date(g(r, 2)),
                permission_no=g(r, 26),
                permission_date=norm_date(g(r, 25)),
                sponsor_name=sponsor,
                sponsor_name_arabic=g(r, 6),
                sponsor_address=g(r, 7),
                sponsor_address_arabic=g(r, 8),
                sponsor_phone=g(r, 9),
                sponsor_alt_phone=g(r, 10),
                visa_number=g(r, 11),
                bataka_number=g(r, 12),
                visa_issue_date_hijri=g(r, 3),
                country="Saudi Arabia",
                # emb_id is column 20 -> embassies.name (Islamabad/Karachi/...).
                # This is the real-time "which visa form is available" driver.
                embassy=emb_name_by_legacy_id.get(g(r, 20), ""),
                visa_quota=total_visas,
                status=(g(r, 27).lower() or "active"),
                notes=f"Imported from legacy visa_receipts (id {legacy_id}).",
            )
            db.add(dem)
            db.flush()  # get dem.id
            demand_by_legacy_id[legacy_id] = dem
            existing_files.add(file_number)
            n += 1
            if n % 300 == 0:
                db.commit()
                print(f"    …demands committed: {n}")
        db.commit()
        stats["demands (from visa_receipts)"] = n

        # ---- visa_receipts_items -> job_categories -----------------------
        # 0 id,1 visa_receipt_id,2 category_id,3 total,4 salary,5 sold
        n = 0
        for r in tables.get("visa_receipts_items", []):
            vr_id = g(r, 1)
            dem = demand_by_legacy_id.get(vr_id)
            if not dem:
                continue
            trade = vc_name_by_legacy_id.get(g(r, 2), "") or f"Category {g(r, 2)}"
            db.add(JobCategory(
                demand_id=dem.id,
                trade=trade,
                quantity=to_int(g(r, 3)) or 0,
                salary=to_float(g(r, 4)),
                salary_currency="SAR",
            ))
            n += 1
            if n % 400 == 0:
                db.commit()
        db.commit()
        stats["job_categories (from visa_receipts_items)"] = n

        # REMOVED 2026-07-30 (operator request): the `agents` -> agents and
        # `agents_cash` -> agent_cash import steps. The Sub-Agents feature and
        # its Agent / AgentCash models have been deleted from the portal, so
        # there are no tables left to import those legacy rows into. The rows
        # remain in the dump file if they are ever needed again.

        # ---- phone_book -> contacts --------------------------------------
        # 0 id,1 name,2 phone,3 mobile,4 status
        existing_contacts = {(c.name or "").lower() for c in db.query(Contact).all()}
        n = 0
        for r in tables.get("phone_book", []):
            name = g(r, 1)
            if not name or name.lower() in existing_contacts:
                continue
            db.add(Contact(
                name=name,
                phone=g(r, 2) or g(r, 3),
                note="Imported from legacy phone_book.",
            ))
            existing_contacts.add(name.lower())
            n += 1
        db.commit()
        stats["contacts (from phone_book)"] = n

        print("\n=== PHASE-2 IMPORT COMPLETE ===")
        for k, v in stats.items():
            print(f"  {k}: +{v}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "/home/user/uploaded_files/backup.sql"
    main(src)
