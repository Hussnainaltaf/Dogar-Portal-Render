"""One-off ETL: import the legacy MySQL dump (backup-06-06-2026.sql) into the
current portal's SQLite schema via SQLAlchemy models.

Legacy system: old PHP/MySQL overseas-employment app (data 2015-2026).
Target: this FastAPI/SQLAlchemy portal (data/dogar_trading.db, single tenant).

Mappings:
    parties             -> clients            (1243 rows)
    passport_recevings  -> candidates         (2675 rows)
    visa_categories     -> visa_categories    (199 rows)
    cities              -> cities             (38 rows)
    medical_centers     -> medical_centers    (39 rows)
    embassies           -> embassies          (4 rows)

Only structurally-meaningful, non-sensitive columns are mapped. Legacy user
password hashes are NOT imported (different, weaker auth model) — the portal's
own bootstrap admin is kept. Arabic mojibake (double-encoded UTF-8 in the older
2015 rows) is repaired best-effort.

Usage:
    venv/bin/python scripts/import_legacy_backup.py /path/to/backup.sql
"""
from __future__ import annotations

import csv
import io
import re
import sys
from datetime import datetime

# --- portal imports -------------------------------------------------------
sys.path.insert(0, ".")
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Candidate,
    City,
    Client,
    Embassy,
    MedicalCenter,
    VisaCategory,
)

# ==========================================================================
# 1. Parse the MySQL dump into {table: [row-tuples]}
# ==========================================================================
INSERT_RE = re.compile(r"^INSERT INTO (\w+) VALUES\((.*)\);\s*$")


def parse_dump(path: str) -> dict[str, list[list[str]]]:
    tables: dict[str, list[list[str]]] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = INSERT_RE.match(line.rstrip("\n"))
            if not m:
                continue
            table, values = m.group(1), m.group(2)
            # Values are comma-separated, double-quoted MySQL fields. csv with
            # doublequote handling parses them robustly (handles commas inside
            # quoted values). MySQL escapes " as \" — normalise to "".
            values = values.replace('\\"', '""')
            reader = csv.reader(io.StringIO(values), quotechar='"',
                                doublequote=True, skipinitialspace=True)
            row = next(reader)
            tables.setdefault(table, []).append(row)
    return tables


# ==========================================================================
# 2. Helpers
# ==========================================================================
def fix_mojibake(s: str) -> str:
    """Repair double-encoded UTF-8 (latin1-decoded UTF-8) seen in 2015 rows."""
    if not s:
        return s
    # Heuristic: mojibake for Arabic shows up as Ã/Ù/Ø sequences.
    if any(ch in s for ch in ("Ù", "Ø", "Ã", "Â")):
        try:
            return s.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return s


def clean(s) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    if s in ("????", "NULL", "null"):
        return ""
    return fix_mojibake(s)


def norm_date(s: str):
    """Return an ISO date string the portal's FlexibleDate accepts, or None.
    Legacy dates come as dd/mm/yyyy, dd-mm-yyyy, or already ISO. Unparseable /
    empty values become None (NULL) rather than poisoning the DATE column."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
                "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None  # unparseable → leave NULL rather than poison the column


# ==========================================================================
# 3. Import
# ==========================================================================
def main(path: str) -> None:
    print(f"Parsing dump: {path}")
    tables = parse_dump(path)
    for t, rows in sorted(tables.items()):
        print(f"  legacy {t}: {len(rows)} rows")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    stats: dict[str, int] = {}

    try:
        # ---- Lookups: cities (name, name_arabic, status) -----------------
        seen_cities = {c.name.lower() for c in db.query(City).all()}
        n = 0
        for r in tables.get("cities", []):
            name = clean(r[1])
            if not name or name.lower() in seen_cities:
                continue
            db.add(City(name=name, province="", country="Pakistan"))
            seen_cities.add(name.lower()); n += 1
        stats["cities"] = n

        # ---- visa_categories (id, name, name_arabic, status) -------------
        seen_vc = {v.name.lower() for v in db.query(VisaCategory).all()}
        n = 0
        for r in tables.get("visa_categories", []):
            name = clean(r[1])
            if not name or name.lower() in seen_vc:
                continue
            db.add(VisaCategory(name=name, name_arabic=clean(r[2]),
                                is_active=(clean(r[3]).lower() == "active")))
            seen_vc.add(name.lower()); n += 1
        stats["visa_categories"] = n

        # ---- medical_centers (id, name, code, status) --------------------
        seen_mc = {m.name.lower() for m in db.query(MedicalCenter).all()}
        n = 0
        for r in tables.get("medical_centers", []):
            name = clean(r[1])
            if not name or name.lower() in seen_mc:
                continue
            db.add(MedicalCenter(name=name, city="", address="", phone=""))
            seen_mc.add(name.lower()); n += 1
        stats["medical_centers"] = n

        # ---- embassies (id, name, city_code, status) ---------------------
        seen_emb = {e.name.lower() for e in db.query(Embassy).all()}
        n = 0
        for r in tables.get("embassies", []):
            name = clean(r[1])
            if not name or name.lower() in seen_emb:
                continue
            db.add(Embassy(name=name, country="", city=clean(r[2]), address="", phone=""))
            seen_emb.add(name.lower()); n += 1
        stats["embassies"] = n

        db.commit()

        # ---- parties -> clients ------------------------------------------
        # legacy: id,name,phone,mobile,address,city,country,name_two,
        #         address_two,tel_two,status
        seen_clients = {c.company_name.lower() for c in db.query(Client).all()}
        n = 0
        for r in tables.get("parties", []):
            name = clean(r[1])
            if not name:
                continue
            key = name.lower()
            if key in seen_clients:
                continue
            db.add(Client(
                company_name=name,
                client_type="sponsor",
                status="active" if clean(r[10]).lower() == "active" else "inactive",
                phone=clean(r[2]) or clean(r[3]),
                sponsor_name=name,
                sponsor_address=clean(r[4]),
                city=clean(r[5]),
                country=clean(r[6]) or "Saudi Arabia",
                contact_person=clean(r[7]),
                sponsor_alt_phone=clean(r[9]),
            ))
            seen_clients.add(key); n += 1
            if n % 300 == 0:
                db.commit()
        db.commit()
        stats["clients (from parties)"] = n

        # ---- passport_recevings -> candidates ----------------------------
        # Column order per CREATE TABLE (0-indexed):
        # 0 id,1 vid,2 rid,3 datetime,4 name,5 name_arabic,6 fname,
        # 7 fname_arabic,8 mname,9 gender,10 address,11 dob,12 pob,
        # 13 pob_arabic,14 pnum,15 nationality,16 religion,17 pid,18 ped,
        # 19 issueauth,20 issueauth_arabic,21 cnic,22 marstatus,
        # 23 qualification,24 phone,25 nokinname,26 nokinnic,27 nokinrel,
        # 28 tehsil,29 district,30 per_no,31 per_date,32 destination,
        # 33 car_name,34 province,35 date,36 nadra,37 visa_stamp_date,
        # 38 protector_no,39 d_of_departure,40 flight_no,41 ticket_no,
        # 42 p_send_d,43 p_consign_no,44 p_courrier_n,45 p_receiv_d,
        # 46 medical_send_d,47 medical_consign_no,48 medical_courier_n,
        # 49 age,50 enumber,51 visa_category_id,52 mcenter_id,53 gamcano,
        # 54 medicaldate,55 photo,56 recv_by,57 status
        def g(r, i):
            return clean(r[i]) if i < len(r) else ""

        n = 0
        for r in tables.get("passport_recevings", []):
            full_name = g(r, 4)
            if not full_name:
                continue
            age_raw = g(r, 49)
            try:
                age_val = int(age_raw) if age_raw.isdigit() else None
            except ValueError:
                age_val = None
            gender = g(r, 9).capitalize() or ""
            marital = g(r, 22).capitalize() or ""
            cand = Candidate(
                full_name=full_name,
                name_arabic=g(r, 5),
                mother_name=g(r, 8),
                father_name=g(r, 6),
                father_name_arabic=g(r, 7),
                gender=gender,
                marital_status=marital,
                religion=g(r, 16) or "Islam",
                date_of_birth=norm_date(g(r, 11)),
                place_of_birth=g(r, 12),
                place_of_birth_arabic=g(r, 13),
                nationality=g(r, 15) or "PAKISTANI",
                address=g(r, 10),
                phone=g(r, 24),
                tehsil=g(r, 28),
                district=g(r, 29),
                province=g(r, 34),
                passport_no=g(r, 14),
                passport_issue_date=norm_date(g(r, 17)),
                passport_expiry_date=norm_date(g(r, 18)),
                issuing_authority=g(r, 19) or "PAKISTAN",
                issuing_authority_arabic=g(r, 20),
                cnic=g(r, 21),
                nadra_token_no=g(r, 36),
                permission_no=g(r, 30),
                permission_date=norm_date(g(r, 31)),
                qualification=g(r, 23),
                age_employee=age_val,
                next_of_kin_name=g(r, 25),
                next_of_kin_nic=g(r, 26),
                next_of_kin_relation=g(r, 27),
                protector_no=g(r, 38),
                protector_date=None,
                medical_center=g(r, 52),
                gamca_number=g(r, 53),
                medical_date=norm_date(g(r, 54)),
                medical_consignment_no=g(r, 47),
                medical_send_date=norm_date(g(r, 46)),
                medical_courier_name=g(r, 48),
                e_number=g(r, 50),
                visa_stamp_date=norm_date(g(r, 37)),
                date_of_departure=norm_date(g(r, 39)),
                flight_no=g(r, 40),
                destination=g(r, 32),
                ticket_no=g(r, 41),
                ticket_included="No",
                status="deployed" if g(r, 39) else "documents_pending",
                notes=f"Imported from legacy backup (legacy id {g(r, 0)}).",
            )
            db.add(cand)
            n += 1
            if n % 250 == 0:
                db.commit()
                print(f"    …candidates committed: {n}")
        db.commit()
        stats["candidates (from passport_recevings)"] = n

        print("\n=== IMPORT COMPLETE ===")
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
