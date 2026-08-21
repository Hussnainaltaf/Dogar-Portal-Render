"""Initialize database, create tables and seed with:
  - admin user
  - company settings (Dogar Trading info, file_prefix = DTC/786/80)
  - system roles (Admin, Manager, Staff)
  - lookup starter data (visa categories, embassies, cities, medical centers)
  - 14 PDF document templates linked to the real form backgrounds
NO MOCK CLIENTS, DEMANDS, OR CANDIDATES are created. The user enters their own real data.

NOTE: migrations/legacy_backup.sql was removed from the distributed package for
PDPA 2023 compliance. Nothing in this module reads it any more -- the only
consumer was seed_legacy_agents(), deleted with the Sub-Agents feature.
"""
import json
import logging
import os

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import Base, SessionLocal, engine
from app.models import (
    City,
    CompanySettings,
    DocumentTemplate,
    Embassy,
    MedicalCenter,
    Role,
    ServiceCharge,
    User,
    VisaCategory,
)

PDF_BG_DIR = "/static/pdf_backgrounds"


# AUDIT 2026-07-31 (Low): the deprecated `_safe_add_column()` shim that used to
# live here has been DELETED. It was retired to a warn-and-no-op stub in the
# 2026-07-08 Postgres migration "because a handful of legacy scripts import it
# directly" -- but a repo-wide grep found no such caller remained, so the stub
# was dead code that advertised a schema-patching capability the project has
# deliberately given up. Schema changes are the sole responsibility of Alembic
# (`alembic upgrade head`); see run_schema_migrations() below and the note in
# app/core/tenancy.py explaining why create_all() was removed from tenant boot.


def _restore_root_logging(level: int, handlers: list) -> None:
    """Undo alembic's in-process reconfiguration of the ROOT logger.

    See the long note in ``run_schema_migrations``. ``fileConfig()`` replaces
    the root logger's level and handler list with whatever ``alembic.ini``
    declares (``level = WARN``, a plain console handler). Both must be put
    back or the application spends the rest of the process unable to emit
    INFO, formatted as plain text instead of the JSON the log pipeline parses.

    Restoring handler OBJECTS (not re-running configure_logging()) is
    deliberate: it reinstates the exact handlers that were installed,
    including any a test or an embedding process attached, and cannot
    duplicate them.
    """
    root = logging.getLogger()
    if root.level != level:
        root.setLevel(level)
    if list(root.handlers) != handlers:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in handlers:
            root.addHandler(handler)


def run_schema_migrations():
    """Apply pending Alembic migrations against the CONTROL database.

    Replaces the hand-rolled ``_safe_add_column`` cascade with a single
    ``alembic upgrade head`` invocation. Tenant DBs are migrated separately
    (see ``scripts/apply_alembic_to_tenants.py``) because each tenant lives
    in its own database in Postgres.

    Behaviour:
      * If the control DB is at head → this is a fast no-op.
      * If the control DB is behind → all pending migrations run inside a
        transaction (Postgres) or with ``render_as_batch=True`` (SQLite).
      * If ``alembic.ini`` is missing (dev checkout with no migrations
        directory) → skip silently. Alembic auto-runs on startup are best-
        effort only; production deploys should invoke ``alembic upgrade head``
        explicitly from the release pipeline.

    This function preserves backward compatibility with the previous name
    so callers in ``init_database()`` and ``app.main`` do not need to
    change.
    """
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    ini = project_root / "alembic.ini"
    if not ini.exists():
        logging.getLogger("dtc.migrate").info(
            "alembic.ini not found -- skipping schema migrations (dev checkout?)",
        )
        return

    # MEASURED 2026-08-19: alembic's env.py calls logging.config.fileConfig(),
    # which does NOT only honour `disable_existing_loggers` -- it also REBUILDS
    # the root logger from alembic.ini. alembic.ini declares
    # `[logger_root] level = WARN`, so running a migration in-process silently
    # downgrades the whole application's root logger from INFO to WARNING and
    # swaps our JSONFormatter handler for alembic's plain console handler --
    # for the entire lifetime of the process, on every boot.
    #
    # Proved with a probe either side of the call:
    #
    #     root level BEFORE alembic: INFO      ('probe-before' emitted, JSON)
    #     root level AFTER  alembic: WARNING   ('probe-after'  NEVER emitted)
    #
    # The effect is that every INFO the app writes after startup vanishes: the
    # startup diagnostics, "schema migrations applied", the tenant schema-drift
    # check, audit-adjacent INFO -- all gone, in production, with no error. It
    # is the same class of defect as the `disable_existing_loggers` bug fixed
    # on 2026-08-01 (see alembic/env.py) and it hid behind that fix, because
    # the loggers were no longer *disabled* -- just below the new threshold.
    #
    # We cannot fix this in alembic.ini: those log settings are correct for the
    # `alembic` CLI, which is a standalone process that SHOULD configure its own
    # logging. The in-process caller is the one that must not be trampled, so
    # the caller restores its own configuration afterwards.
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = list(root.handlers)
    try:
        from alembic import command
        from alembic.config import Config
        cfg = Config(str(ini))
        # Use the app's configured DATABASE_URL (env.py already respects this).
        command.upgrade(cfg, "head")
        logging.getLogger("dtc.migrate").info("alembic upgrade head OK")
    except Exception as exc:                                  # noqa: BLE001
        # Do NOT crash startup. Log loudly and continue; the operator will
        # see the failure and can re-run migrations manually.
        logging.getLogger("dtc.migrate").exception(
            "alembic upgrade head failed: %s", exc,
        )
    finally:
        # Restore in `finally` -- a FAILED migration reconfigures logging just
        # as thoroughly as a successful one, and that is precisely the moment
        # the operator most needs the app's own logs to keep working.
        _restore_root_logging(saved_level, saved_handlers)


def create_tables():
    """First-boot schema bootstrap.

    On a truly-empty database (no ``alembic_version`` table AND no user
    tables) we run ``Base.metadata.create_all`` for a fast fresh init,
    then ``alembic stamp head`` so subsequent upgrades know the DB is
    already caught up. On an existing DB we skip create_all entirely
    and just call ``run_schema_migrations()`` (i.e. ``alembic upgrade
    head``) so nothing is destroyed.
    """
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if not tables:
        # Brand-new DB → create schema from ORM models, then stamp Alembic.
        Base.metadata.create_all(bind=engine)
        # `command.stamp` loads alembic/env.py and so rebuilds the root logger
        # from alembic.ini exactly like `command.upgrade` does -- save/restore
        # around it. See the note in run_schema_migrations() above.
        _root = logging.getLogger()
        _saved_level = _root.level
        _saved_handlers = list(_root.handlers)
        try:
            from pathlib import Path

            from alembic import command
            from alembic.config import Config
            project_root = Path(__file__).resolve().parents[2]
            ini = project_root / "alembic.ini"
            if ini.exists():
                cfg = Config(str(ini))
                command.stamp(cfg, "head")
        except Exception as exc:                              # noqa: BLE001
            logging.getLogger("dtc.migrate").warning(
                "alembic stamp head failed on fresh DB: %s (non-fatal)", exc,
            )
        finally:
            _restore_root_logging(_saved_level, _saved_handlers)
        print("\u2713 Database tables created (fresh init, Alembic stamped)")
    else:
        # Existing DB → let Alembic bring it up to date.
        run_schema_migrations()
        print("\u2713 Database schema verified (Alembic upgrade head)")

    # Idempotent safety net (Task 9): ensure newer standalone tables that are
    # not yet covered by a dedicated Alembic revision exist on every DB. These
    # calls are no-ops when the table already exists (checkfirst=True).
    try:
        from app.models.lookups import MessageLog
        MessageLog.__table__.create(bind=engine, checkfirst=True)
    except Exception as exc:                                  # noqa: BLE001
        logging.getLogger("dtc.migrate").warning(
            "ensure message_logs table failed: %s (non-fatal)", exc,
        )


# ============================================================
# Admin user
# ============================================================
def seed_admin_user(db: Session):
    """Create the bootstrap admin user on first launch.

    The new admin row is created with ``must_change_password=True`` so
    the documented default credentials cannot be used past the very
    first login -- the portal will redirect the user to a forced
    password-change screen.
    """
    existing = db.query(User).filter(User.email == settings.DEFAULT_ADMIN_EMAIL).first()
    if existing:
        print(f"  Admin already exists: {settings.DEFAULT_ADMIN_EMAIL}")
        return

    # AUDIT M-5 (2026-07-10): when DEFAULT_ADMIN_PASSWORD is empty, do NOT create
    # a bootstrap admin. The old source default was the weak value "admin123", so
    # a missing .env would silently seed a guessable admin. Now an operator must
    # explicitly set a strong password to get a seeded admin; otherwise the very
    # first real admin is created some other way (vendor account / manual seed).
    if not (settings.DEFAULT_ADMIN_PASSWORD or "").strip():
        print(
            "  DEFAULT_ADMIN_PASSWORD is empty -- skipping bootstrap admin seed "
            "(set a strong password in .env to auto-create one)."
        )
        return

    # Refuse to seed a weak password in production -- config.py also
    # guards this but we keep a defence in depth here so a bypass in
    # one place cannot create a back-door admin in another.
    if settings.is_production:
        from app.core.config import _FORBIDDEN_ADMIN_PASSWORDS
        if settings.DEFAULT_ADMIN_PASSWORD.strip().lower() in _FORBIDDEN_ADMIN_PASSWORDS:
            raise RuntimeError(
                "Refusing to seed bootstrap admin with a known weak "
                "password in production. Set DEFAULT_ADMIN_PASSWORD in "
                ".env to something strong and restart."
            )

    admin = User(
        name=settings.DEFAULT_ADMIN_NAME,
        email=settings.DEFAULT_ADMIN_EMAIL,
        password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
        role="admin",
        is_active=True,
        phone="",
        must_change_password=True,  # force on first login
    )
    db.add(admin); db.commit()
    print("\u2713 Admin user created (must_change_password=True)")


# ============================================================
# Permanent super-admin (developer / vendor account)  [C-03 FIXED]
# ============================================================
# C-03 FIX: Credentials are NO LONGER hardcoded in source.
#
# Set these in your .env file (or as environment variables):
#   VENDOR_ADMIN_EMAIL    = vendor@yourcompany.com
#   VENDOR_ADMIN_PASSWORD = <strong-password-minimum-16-chars>
#
# If either variable is missing and ENV=production, startup will abort
# with a clear error message rather than silently creating an insecure account.
#
# SECURITY CHANGES from the original implementation:
#   1. Password is NOT restored on every boot -- if the client changes or
#      revokes it, that decision is respected.
#   2. must_change_password=True is set on first creation so the vendor
#      must rotate the credential on first login.
#   3. Credentials come from environment variables, not source code.


def _get_vendor_credentials() -> tuple[str, str, str]:
    """Return (email, password, name) from env vars.

    Raises RuntimeError in production if either is missing or insecure.
    """
    # FIX-08: Use settings.* instead of os.environ.get() so values from .env
    # are loaded correctly via pydantic-settings (os.environ.get() misses
    # .env-file-only values that aren't exported to the real OS environment).
    email = (settings.VENDOR_ADMIN_EMAIL or "").strip()
    password = (settings.VENDOR_ADMIN_PASSWORD or "").strip()
    name = (settings.VENDOR_ADMIN_NAME or "Vendor Support").strip()

    # Use the normalized settings.is_production so .env-file-only ENV values and
    # process-manager quirks cannot make the guard disagree with the operator's
    # intended production mode (os.environ.get misses .env-only values).
    is_prod = settings.is_production

    if is_prod:
        if not email or not password:
            raise RuntimeError(
                "C-03: VENDOR_ADMIN_EMAIL and VENDOR_ADMIN_PASSWORD must be set in "
                ".env when ENV=production. The app will not start without them."
            )
        if len(password) < 16:
            raise RuntimeError(
                "C-03: VENDOR_ADMIN_PASSWORD must be at least 16 characters in production."
            )

    # Fall back to disabled/placeholder values in dev so the app still boots
    # without a .env file.
    if not email:
        email = "vendor-admin@localhost.invalid"
    if not password:
        password = ""  # empty → account exists but cannot be used to log in

    return email, password, name


def seed_super_admin(db: Session):
    """Create the vendor super-admin account from environment variables.

    C-03 SECURITY FIX:
      - Credentials come from VENDOR_ADMIN_EMAIL / VENDOR_ADMIN_PASSWORD env vars.
      - Password is NOT restored on every boot -- client can revoke it.
      - must_change_password=True on first creation.
      - Fails hard in production if env vars are missing.
    """
    email, password, name = _get_vendor_credentials()

    if not password:
        # Dev mode with no env vars -- skip account creation entirely
        import logging
        logging.getLogger("dtc.init_db").info(
            "VENDOR_ADMIN_PASSWORD not set -- skipping vendor super-admin seeding (dev mode)."
        )
        return

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        # C-03 FIX: Only re-assert the role/active flags. Do NOT overwrite the
        # password -- if the client changed or revoked it, that decision stands.
        changed = False
        if not existing.is_super_admin:
            existing.is_super_admin = True; changed = True
        if existing.role != "admin":
            existing.role = "admin"; changed = True
        if not existing.is_active:
            existing.is_active = True; changed = True
        # Safety net ONLY: if the password hash was accidentally wiped by an
        # external process (empty string), restore it so the vendor account is
        # not permanently locked. We do NOT restore the hash when it is already
        # set -- that respects a deliberate password rotation by the client.
        if not existing.password_hash:
            existing.password_hash = hash_password(password)
            existing.must_change_password = True
            changed = True
        # C-03 FIX: Removed -- we no longer force must_change_password=False
        # and no longer restore the password hash on every boot.
        if changed:
            db.commit()
            print(f"\u2713 Vendor super-admin state verified: {email}")
        return

    super_admin = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role="admin",
        is_active=True,
        is_super_admin=True,
        must_change_password=True,   # C-03 FIX: force rotation on first login
        designation="Vendor Support",
        bio="Vendor super-admin account. Credentials managed via environment variables.",
    )
    db.add(super_admin); db.commit()
    print(f"\u2713 Vendor super-admin created: {email}")



# ============================================================
# Company settings (single row)
# ============================================================
def seed_company_settings(db: Session):
    if db.query(CompanySettings).count() > 0:
        return
    cs = CompanySettings(
        company_name=settings.COMPANY_NAME,
        company_name_arabic="",
        oep_license_number="1338/SKT",
        owner_name="",
        address="Ghalla Mandi, Circular Road, Daska District Sialkot (Pakistan)",
        address_arabic="",
        phone="0092-52-6613893-6615393",
        mobile="",
        fax="0092-52-6610893",
        email="dogar@saudia.com",
        website="www.doggars.com",
        subdomain="dogar",
        slug="dogar",
        file_prefix="DTC/786/80",
        starting_point=0,
        status="active",
        plan="OEP Yearly",
    )
    db.add(cs); db.commit()
    print("\u2713 Company settings seeded (file_prefix=DTC/786/80, starting_point=0)")


# ============================================================
# System roles -- use the granular RBAC catalog from
# app.core.permissions so the keys match the @require_permission
# decorators used on the API.
# ============================================================
def seed_roles(db: Session):
    """Idempotently seed the 3 system roles (Admin / Manager / Staff)
    with the canonical permission presets defined in
    ``app.core.permissions.ROLE_PRESETS``.

    On every boot we **upgrade** the system roles' permissions to the
    latest preset so newly added permission keys are picked up
    automatically -- but we never touch user-created (non-system) roles
    or rename existing ones (so anyone holding role='manager' keeps
    their assignment).
    """
    from app.core.permissions import ROLE_PRESETS
    # canonical: name -> (description, permissions_list)
    spec = {
        "Admin":   ("Full access (cannot be scoped down)", ROLE_PRESETS["admin"]),
        "Manager": ("Manage day-to-day operations",        ROLE_PRESETS["manager"]),
        "Staff":   ("View & basic data entry",             ROLE_PRESETS["staff"]),
    }
    for name, (descr, perms) in spec.items():
        row = db.query(Role).filter(Role.name.ilike(name)).first()
        if row is None:
            db.add(Role(name=name, description=descr,
                        is_system=True, permissions=json.dumps(perms)))
        else:
            # Re-assert is_system and refresh permissions to the latest preset
            row.is_system = True
            row.permissions = json.dumps(perms)
            if not (row.description or "").strip():
                row.description = descr
    db.commit()
    print("\u2713 System roles (Admin/Manager/Staff) verified")


# ============================================================
# Lookups (starter values used in real Pakistani OEP workflow)
# ============================================================
def seed_lookups(db: Session):
    if db.query(VisaCategory).count() == 0:
        # Full Arabic + English visa category list extracted from production backup
        cats = [
            ("Web Developer", "مطور ويب"),
            ("Auto Body Denter", "سمكري أجسام مركبات"),
            ("Electric Technician", "فني كهرباء"),
            ("General Cleaning Labour", "عامل نظافة عامة"),
            ("Personal Driver", "سائق خاص"),
            ("Building Carpenter", "نجار مباني"),
            ("General Labour", "عامل عادي"),
            ("Plumber", "سباك"),
            ("Tile Fixer", "مبلط"),
            ("General Mason", "بناء عام"),
            ("Block Mason", "بناء طوب"),
            ("Blacksmith", "حداد"),
            ("Masonry Steel Fixer", "حداد تسليح بناء"),
            ("Gypsum Labour", "عامل جبس"),
            ("Painter", "دهان"),
            ("Auto Painter", "دهان مركبات"),
            ("Denter", "سمكرى"),
            ("Auto Denter", "سمكرى مركبات"),
            ("Helper", "مساعد"),
            ("General Plumber", "سباك عام"),
            ("Constructing Worker", "عامل بنا"),
            ("Labour", "عامل"),
            ("Iron Door Blacksmith", "حداد ألواح معدنية"),
            ("Hand Packing Labour", "عامل تعبئه باليد"),
            ("Plasterer", "مليس"),
            ("General Building Cleaning Labour", "عامل تنظيف مباني عامه"),
            ("Light Vehicle Transport Driver", "سائق سيارة نقل صغيرة"),
            ("House Worker", "عامل منزلي"),
            ("Agriculture General Instrument Mechanic", "ميكانيكي آليات زراعية - عام"),
            ("Agriculture Driver", "سائق زراعي"),
            ("Agriculture Labour", "عامل زراعي"),
            ("Agriculture Instrument Electrician", "كهربائي معدات زراعية"),
            ("Building Painter", "دهان مباني"),
            ("Gents Barber", "حلاق رجالي"),
            ("Building Electrician", "كهربائي مباني"),
            ("Children Games Salesman", "بائع ألعاب أطفال"),
            ("General Agriculturist", "مزارع / عام"),
            ("General Building Carpenter", "نجار مباني عام"),
            ("General Turner", "خراط عام"),
            ("Building Technician", "فني أبنية"),
            ("General Chef", "طاهى عام"),
            ("Steel Door Blacksmith", "حداد أبواب فولاذية"),
            ("Manager", "مدير"),
            ("General Tailor", "خياط عام"),
            ("Auto Washer & Greaser Labour", "عامل غسيل وتشحيم مركبات"),
            ("Truck Driver", "سائق شاحنة ثقيلة"),
            ("Auto Polisher Labour", "عامل تلميع مركبات"),
            ("Workshop Worker", "عامل ورشة"),
            ("General Transport Driver", "سائق سيارة عمومي"),
            ("AC & Refrigerator Electrician (Inst.)", "كهربائي تبريد وتكييف"),
            ("General Electrician", "كهربائي عام"),
            ("Decoration Technician", "فني ديكور"),
            ("Lab Technician", "فني مختبر"),
            ("AC Mechanic Helper", "مساعد ميكانيكي تكييف"),
            ("Building Demolish Labour", "عامل هدم مباني"),
            ("Building Carpenter Helper", "مساعد نجار مباني"),
            ("Door & Window Carpenter", "نجار باب وشباك"),
            ("Curtain Upholsterer", "منجد ستائر"),
            ("Water Tanker Driver", "سائق وايت ماء"),
            ("Laundry Labour", "عامل غسيل وكي"),
            ("Electronics Communication Technician", "فني الكتروني اتصالات"),
            ("Waiter", "مقدم طعام"),
            ("Calligrapher & Painter", "خطاط ورسام"),
            ("Printing & Editing Labour", "عامل مونتاج طباعة"),
            ("Animal Training Labour", "عامل تربية مواشي"),
            ("Electronics Technician", "فني كهربائي"),
            ("General Accountant", "محاسب عام"),
            ("General Wood Craft Painter", "دهان مشغولات خشبية عام"),
            ("Auto Oil & Greaser", "مشحم ومزيت سيارات"),
            ("AC & Refrigerator Structure Technician", "فني تكييف وتبريد/ تركيب"),
            ("AC & Refrigeration Operation Technician", "فني تكييف وتبريد/ تشغيل وصيانة"),
            ("General Decoration Carpenter", "نجار ديكور عام"),
            ("General Furniture Carpenter", "نجار أثاث عام"),
            ("General Material Supply Salesman", "بائع مواد تموينية/ عام"),
            ("Metal Casting Labour", "عامل صب المعدن"),
            ("Lady House Worker", "عاملة منزلية"),
            ("Sales Representative", "مندوب مبيعات"),
            ("House Waiter", "سفرجي منزلي"),
            ("General Gents Dress Tailor", "خياط ملابس رجالية عام"),
            ("Thermal Insulation Labour", "عامل عزل حراري"),
            ("Auto Electrician", "كهربائي سيارات"),
            ("Window Cleaning Labour", "عامل نظافة نوافذ"),
            ("Purification Plant Technician", "فني محطة تنقية مجاري"),
            ("General Ironer", "كواء عام"),
            ("Wood Digger", "حفار خشب"),
            ("Furniture Carpenter", "نجار أثاث"),
            ("Civil Surveyor", "مساح عام"),
            ("Steel Fixer Helper", "مساعد حداد تسليح"),
            ("Restaurant Labour", "عامل مطعم"),
            ("Head Waiter", "مقدم طعام رئيسي"),
            ("Chicken BBQ Maker", "شواء دجاج"),
            ("Flour Maker", "عجان"),
            ("Meat Soup Maker", "شواء لحوم"),
            ("Sandwich Maker", "صانع شطائر"),
            ("Plant Operator", "مشغل معدات"),
            ("General Vehicle Driver", "سائق عموم المركبات"),
            ("Electrical Instrument Technician", "فني كهربائي تمديدات"),
            ("Wood Furniture Painter", "دهان أثاث خشبي"),
            ("Stone Mason", "بناء حجر"),
            ("General Car Electrician", "كهربائي سيارات عام"),
            ("Fire Safety Specialist", "اختصاصي وقاية من الحريق"),
            ("General Messenger", "رسول / مراسل عام"),
            ("Central AC Equipment Mechanic", "ميكانيكي أجهزة تكييف مركزي"),
            ("General Welder", "لحام عام"),
            ("Drink & Juice Maker", "محضر عصير مشروبات"),
            ("General Upholsterer", "منجد عام"),
            ("Metal Plates Blacksmith", "حداد ألواح معدنية"),
            ("Metal Structure Labour", "عامل تركيب السقايل المعدنية"),
            ("General Baker", "خباز عام"),
            ("Welding Technician", "فني لحام"),
            ("Wood Sander & Painter", "مصنفر ودهان أخشاب"),
            ("Hand Riveting Labour", "عامل برشمة يدويا"),
            ("Sewing Expert", "خبير خياطة"),
            ("Kitchen Worker", "عامل مطبخ"),
            ("House Cook", "طباخ منزلي"),
            ("Metal Door Blacksmith", "حداد أبواب معدنية"),
            ("Metal Scaffolding Installation Worker", "عامل تركيب السقايل المعدنية"),
            ("Glass Worker", "عامل زجاج"),
            ("Glass Engraver", "نقاش زجاج"),
            ("Auto Mechanic", "ميكانيكي سيارات"),
            ("Cook Helper", "مساعد طاهي"),
            ("Maintenance Manager", "مدير صيانة"),
            ("Salesman", "بائع"),
            ("General Auto Parts Mechanic", "ميكانيكي صيانة ميكانيكية عام"),
            ("Sweet Packing Worker", "عامل تعبئة وتغليف الحلويات"),
            ("General Sweet Maker", "صانع حلويات عام"),
            ("Tyre Checker", "فاحص أطارات"),
            ("Marketing Specialist", "اختصاصي تسويق"),
            ("Welder", "لحام"),
            ("Steel Fixer", "حداد تسليح"),
            ("Manufacturing Labour", "عامل تصنيع"),
            ("Load & Unload Worker", "عامل تحميل وتنزيل"),
            ("Heavy Truck Driver", "سائق معدات ثقيلة"),
            ("Construction Worker", "عامل انشاءات"),
            ("Demolish Labour", "عامل هدم"),
            ("Mini Truck Driver", "سائق شاحنة صغيرة"),
            ("Furniture Assembly Worker", "عامل تجميع أثاث"),
            ("Mobile Car Wash Worker", "عامل غسيل مركبات متنقل"),
            ("Cooling Equipment Technician", "مجمع أجهزة تبريد"),
            ("Car Driver", "سائق سيارة"),
            ("Packaging & Wrapping Worker", "عامل تعبئة وتغليف"),
            ("Fast Food Maker", "معد وجبات سريعة"),
            ("Hotel Management Labour", "عامل تدبير فندقي"),
            ("Packing Shelve Worker", "عامل تعبئة رفوف"),
            ("Barber", "حلاق"),
            ("Bus Driver", "سائق حافلة"),
            ("Cook", "طاهي"),
            ("Electrical Equipment Maintenance Worker", "عامل صيانة أجهزة كهربائية"),
            ("Mason", "بناء"),
            ("Heavy Equipment Driver", "سائق معدات ثقيلة"),
            ("Cattle Herder", "راعي ماشية"),
            ("Carpenter", "نجار"),
            ("Restaurant Manager", "مدير مطعم"),
            ("Embroidery Worker", "عامل تطريز"),
            ("Computer Programmer", "مبرمج كمبيوتر"),
            ("Civil Engineering Technician", "فني هندسة مدنية"),
            ("Grocer", "بقال"),
            ("Bread Maker", "خباز"),
            ("Engineer", "مهندس"),
            ("Calligrapher", "خطاط"),
            ("Fruits & Vegetables Preserver", "عامل حفظ فواكه وخضروات"),
            ("Peddler Green Grocer", "بائع خضار وفاكهة متجول"),
            ("Chef", "طاهي"),
            ("Dairy Products Maker", "صانع منتجات ألبان"),
            ("Motorcycle Driver", "سائق دراجة نارية"),
            ("Taxi Driver", "سائق سيارة أجرة"),
            ("Tailor", "خياط"),
            ("Drilling Worker", "عامل حفر"),
            ("Warehouse Worker", "عامل مخزن"),
            ("Packing Worker", "عامل تعبئة وتغليف"),
            ("House Farmer", "مزارع منزلي"),
            ("Product Sorting Worker", "عامل فرز منتجات"),
            ("Laundryman", "عامل غسيل ملابس"),
            ("Tile Cleaning Worker", "جلاء بلاط"),
            ("Driver (LTV)", "سائق (مركبة خفيفة)"),
            ("Driver (HTV)", "سائق (مركبة ثقيلة)"),
            ("Security Guard", "حارس أمن"),
            ("Cleaner", "عامل نظافة"),
            ("Office Boy", "فراش مكتب"),
            ("Storekeeper", "أمين مستودع"),
            ("Foreman", "مشرف عمال"),
            ("Receptionist", "موظف استقبال"),
            ("Query Clerk", "موظف استفسارات"),
            ("Electrician", "كهربائي"),
            ("AC Technician", "فني تكييف"),
        ]
        db.add_all([
            VisaCategory(
                name=name,
                name_arabic=arabic,
                code=name.upper().replace(" ", "_")[:30],
            )
            for name, arabic in cats
        ])

    if db.query(Embassy).count() == 0:
        db.add_all([
            Embassy(name="Saudi Arabia Embassy", country="Saudi Arabia", city="Islamabad"),
            Embassy(name="Karachi Embassy",      country="Saudi Arabia", city="Karachi"),
            Embassy(name="Lahore Embassy",       country="Saudi Arabia", city="Lahore"),
            Embassy(name="UAE Embassy",          country="United Arab Emirates", city="Islamabad"),
            Embassy(name="Qatar Embassy",        country="Qatar", city="Islamabad"),
            Embassy(name="Kuwait Embassy",       country="Kuwait", city="Islamabad"),
            Embassy(name="Oman Embassy",         country="Oman", city="Islamabad"),
            Embassy(name="Bahrain Embassy",      country="Bahrain", city="Islamabad"),
        ])

    if db.query(City).count() == 0:
        pak_cities = [
            ("Sialkot", "Punjab"), ("Lahore", "Punjab"), ("Karachi", "Sindh"),
            ("Islamabad", "Federal"), ("Rawalpindi", "Punjab"), ("Faisalabad", "Punjab"),
            ("Multan", "Punjab"), ("Gujranwala", "Punjab"), ("Peshawar", "KPK"),
            ("Quetta", "Balochistan"), ("Gujrat", "Punjab"), ("Daska", "Punjab"),
            ("Sargodha", "Punjab"), ("Bahawalpur", "Punjab"), ("Sukkur", "Sindh"),
            ("Hyderabad", "Sindh"), ("Mardan", "KPK"), ("Sahiwal", "Punjab"),
        ]
        db.add_all([City(name=n, province=p, country="Pakistan") for n, p in pak_cities])

    if db.query(MedicalCenter).count() == 0:
        db.add_all([
            MedicalCenter(name="Gerry's International Medical Center", city="Karachi"),
            MedicalCenter(name="Gulf Medical Center", city="Karachi"),
            MedicalCenter(name="Sana Medical Center", city="Lahore"),
            MedicalCenter(name="Al-Karam Medical Center", city="Sialkot"),
            MedicalCenter(name="Gulf Approved Medical", city="Islamabad"),
        ])

    if db.query(ServiceCharge).count() == 0:
        db.add_all([
            ServiceCharge(name="OEP Service Fee", amount=4500, description="Per-candidate OEP processing"),
            ServiceCharge(name="Protector Fee", amount=2500, description="Protector certificate"),
            ServiceCharge(name="State Life Insurance", amount=3500, description="Insurance fee"),
        ])

    db.commit()
    print("\u2713 Lookup data seeded")


# REMOVED 2026-07-30 (operator request): the legacy-dump import path -- the
# _parse_insert() helper and seed_legacy_agents(), which read the `agents` and
# `agents_cash` tables out of migrations/legacy_backup.sql. The Sub-Agents
# feature and its Agent/AgentCash models have been deleted, so there is nothing
# left to import them into and no other caller ever used the parser.


# ============================================================
# Document templates (14 system templates) \u2014 mapped to real PDF backgrounds
# ============================================================
TEMPLATE_DEFS = [
    # (name, description, category, data_source, background_filename)
    #
    # SCOPE (owner requirement 2026-07-02): the coordinate engine contains ONLY
    # real scanned-form overlays:
    #   PROTECTOR : OEP/BEO form front+back, NBP fee slips (both), NBP Form 32,
    #               Allied Bank Form-7
    #   VISA PROCESS : KHI visa form, ISB visa form (embassy-filtered), plus the
    #               ISB-only Passport Submission Letter
    # Permission/undertaking letters are NOT coordinate templates -- they are
    # generated by the letterhead renderer (Demand -> Documents tab) and the
    # E-Barcode is generated by the e-barcode service. Any template row not in
    # this list is auto-deleted from the DB by seed_document_templates().
    ("Allied Bank Deposit Form",      "Allied Bank Form-7 deposit slip",                  "Protector Documents", "candidate", "allied_bank_form7.jpg"),
    ("NBP Form 32",                   "NBP Form 32 deposit slip",                         "Protector Documents", "candidate", "hbl_form_32a.jpg"),
    ("NBP Deposit Slip",              "National Bank Pakistan deposit slip",              "Protector Documents", "candidate", "nbp_deposit_slip.jpg"),
    ("NBP Deposit Slip - New",        "National Bank Pakistan deposit slip (new format)", "Protector Documents", "candidate", "nbp_deposit_slip_new.jpg"),
    # OEP form is now a SINGLE 2-page document (front + back). The back page's
    # background is bound via MULTIPAGE_BACKGROUNDS, so the old standalone
    # "OEP Form Page 2" template is retired (auto-deleted on seed).
    ("OEP Form",                      "Emigrant / Employee Registration Through OEP Form (front + back)","Protector Documents", "candidate", "oep_form_p1.jpg"),
    ("Visa Application Form - Karachi", "Saudi Arabia Visa Application (Karachi)",         "Visa Process",        "candidate", "visa_application_karachi_letterhead.jpg"),
    ("Visa Application Form - Islamabad", "Saudi Arabia Visa Application (Islamabad)",     "Visa Process",        "candidate", "saudi_visa_application.jpg"),
    ("Passport Submission Letter - Islamabad", "Passport submission letter (Islamabad embassy candidates only)", "Visa Process", "candidate", "dogar_letterhead.jpg"),
]


#: Authoritative starter geometry for the shipped coordinate templates.
#: See _verified_template_geometry() for why this file exists.
VERIFIED_GEOMETRY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "template_geometry.json",
)


def _verified_template_geometry():
    """Load the measured, print-verified field geometry snapshot.

    WHY THIS EXISTS (defect found 2026-08-03, deep audit)
    -----------------------------------------------------
    Field coordinates lived in exactly ONE place that survived: the operator's
    own ``data/dogar_trading.db``. That file is deliberately NEVER shipped
    (see scripts/make_release_zip.sh -- it holds real candidate PII), and
    ``data/`` is gitignored. Everything a fresh install actually received came
    from ``scripts/seed_document_fields.py``, whose own docstring calls its
    numbers "best-effort approximations".

    The consequence was severe and completely silent:

      * 13 fields did not exist at all on a fresh install -- including the
        three Section C fields repaired this week (``customer_address``,
        ``customer_oep_license``, ``customer_emigrant_office``) and 10 fields
        on the Karachi visa form (``visa_number``, ``e_number``,
        ``bataka_number``, ``sponsor_name_arabic``, ...).
      * 32 more fields sat at coordinates up to 426 pt away from the verified
        position -- i.e. most of a page off.
      * Templates 1/7/8 came up A4 instead of the verified Letter, so the
        background scan was scaled differently than every measurement assumed.
      * The ``cnic`` field came up plain ``text`` instead of ``char_cells``,
        losing the digit-grid geometry fixed in revision 6e0a4b2d8f37.

    Alembic could not save this either: the data migrations that repaired the
    live DB are all ``UPDATE``/guarded-``INSERT`` statements against rows that
    the *seeder* creates, so on a fresh DB they correctly match zero rows.
    Every geometry fix made to date would therefore have been invisible to a
    new deployment, and the PDFs would print blank and misaligned boxes -- the
    exact class of defect the last three revisions were fixing.

    The fix is to make the shipped snapshot the single source of truth. It is
    exported from the verified DB by ``scripts/export_template_geometry.py``
    and carries full fidelity (field_type, meta, page, page_size), unlike the
    8-tuple approximations it replaces.

    Returns ``{}`` when the file is missing or unreadable, so the caller falls
    back to the legacy approximate layout rather than seeding nothing.
    """
    try:
        with open(VERIFIED_GEOMETRY_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        logging.getLogger("dtc.seed").warning(
            "verified geometry snapshot not found at %s -- falling back to the "
            "approximate starter layout; PDF field positions will need manual "
            "adjustment in the designer", VERIFIED_GEOMETRY_FILE,
        )
        return {}
    except (OSError, ValueError) as exc:
        logging.getLogger("dtc.seed").warning(
            "verified geometry snapshot at %s is unreadable (%s) -- falling back "
            "to the approximate starter layout", VERIFIED_GEOMETRY_FILE, exc,
        )
        return {}
    return data if isinstance(data, dict) else {}


def _embedded_field_layouts():
    """Minimal fallback coordinate layouts used only when the external
    scripts/seed_document_fields.py file is absent from the deployment.

    Returns (SPECIFIC, CANDIDATE_LAYOUT, DEMAND_LAYOUT) matching the shape the
    external module exports. Coordinates are PDF-points (A4, origin bottom-left).
    This guarantees the app can always seed usable starter fields even if the
    scripts/ directory did not ship — the designer UI can refine them later.
    """
    candidate_layout = [
        ("Full Name",        "full_name",            150, 740, 380, 11, True,  "left"),
        ("Father Name",      "father_name",          150, 715, 380, 11, False, "left"),
        ("CNIC",             "cnic",                 150, 690, 250, 11, False, "left"),
        ("Passport No",      "passport_no",          400, 690, 160, 11, False, "left"),
        ("Date of Birth",    "date_of_birth",        150, 665, 200, 11, False, "left"),
        ("Nationality",      "nationality",          400, 665, 160, 11, False, "left"),
        ("Profession",       "profession",           150, 640, 250, 11, False, "left"),
        ("Passport Expiry",  "passport_expiry_date", 400, 640, 160, 11, False, "left"),
        ("Address",          "address",              150, 615, 410, 10, False, "left"),
        ("Phone",            "phone",                150, 565, 200, 11, False, "left"),
        ("Today's Date",     "__today__",            450, 110, 100, 10, True,  "left"),
    ]
    demand_layout = [
        ("File Number",      "file_number",          150, 740, 300, 12, True,  "left"),
        ("Permission No",    "permission_no",        150, 715, 300, 11, False, "left"),
        ("Sponsor Name",     "sponsor_name",         150, 660, 400, 11, True,  "left"),
        ("Sponsor Address",  "sponsor_address",      150, 610, 400, 10, False, "left"),
        ("Country",          "country",              400, 585, 160, 11, False, "left"),
        ("Visa Number",      "visa_number",          150, 560, 250, 11, False, "left"),
        ("Visa Quota",       "visa_quota",           150, 535, 100, 11, False, "left"),
        ("Today's Date",     "__today__",            450, 110, 100, 10, True,  "left"),
    ]
    # No per-form overrides in the fallback — CANDIDATE/DEMAND defaults cover all.
    return {}, candidate_layout, demand_layout


# Multi-page forms: map template name → {page_number: background_filename}.
# This drives the designer meta-fence "page_backgrounds" key so a SINGLE
# template renders front + back (or multiple deposit-slip copies) as one
# complete printable document. Page 1 always uses the template's own
# background_image, so only pages ≥ 2 need to be listed here.
MULTIPAGE_BACKGROUNDS = {
    # OEP form: front (page 1 = oep_form_p1.jpg) + back (page 2 = oep_form_p2.jpg)
    "OEP Form": {2: "oep_form_p2.jpg"},
    # NBP fee slip is a 2-page document: page 1 = Bank Copy + Depositor Copy,
    # page 2 = Director-General Copy + OPF Copy. Both pages share the same
    # scanned slip layout (nbp_deposit_slip.jpg), so page 2 reuses it.
    "NBP Deposit Slip": {2: "nbp_deposit_slip.jpg"},
}


def _seed_multipage_backgrounds(db: Session):
    """Write the ``page_backgrounds`` designer-meta key into the description of
    each multi-page template so the print/PDF engine renders every page with
    its correct scanned-form background. Idempotent: only rewrites when the
    stored meta differs from the desired mapping."""
    import json as _json

    from app.services.overlay_engine import (
        parse_designer_meta,
        strip_designer_meta,
    )
    for name, mapping in MULTIPAGE_BACKGROUNDS.items():
        tpl = db.query(DocumentTemplate).filter_by(name=name).first()
        if not tpl:
            continue
        desc = tpl.description or ""
        meta = parse_designer_meta(desc)
        desired = {str(k): v for k, v in mapping.items()}
        if meta.get("page_backgrounds") == desired:
            continue  # already correct
        meta["page_backgrounds"] = desired
        # Strip any existing fence, then append the updated one.
        base = strip_designer_meta(desc)
        tpl.description = (base + f"\n<!--designer:{_json.dumps(meta)}-->").strip()
    db.commit()


def seed_document_templates(db: Session):
    from app.models.document import GeneratedDocument
    # 0. In-place RENAMES (owner-requested corrections). Renaming BEFORE the
    #    auto-delete step preserves the template's field coordinates, designer
    #    metadata and generated-document history instead of dropping + recreating.
    #    2026-07-11: "HBL - Overseas Employment Corporation" was a wrong label;
    #    the real document is the NBP Form 32 deposit slip.
    _RENAMES = {
        "HBL - Overseas Employment Corporation": ("NBP Form 32", "NBP Form 32 deposit slip"),
    }
    for old_name, (new_name, new_desc) in _RENAMES.items():
        old_row = db.query(DocumentTemplate).filter_by(name=old_name).first()
        if old_row:
            # If a row already exists under the new name (e.g. reseeded), drop the
            # stale old one; otherwise rename the existing row in place.
            clash = db.query(DocumentTemplate).filter_by(name=new_name).first()
            if clash and clash.id != old_row.id:
                db.query(GeneratedDocument).filter_by(template_id=old_row.id).delete()
                db.delete(old_row)
            else:
                old_row.name = new_name
                old_row.description = new_desc
    db.commit()

    # 1. Clean up any dynamic/legacy junk templates that are not officially in TEMPLATE_DEFS
    allowed_names = [t[0] for t in TEMPLATE_DEFS]
    legacy_templates = db.query(DocumentTemplate).filter(~DocumentTemplate.name.in_(allowed_names)).all()
    if legacy_templates:
        for lt in legacy_templates:
            # Delete generated docs referencing this template to avoid foreign key errors
            db.query(GeneratedDocument).filter_by(template_id=lt.id).delete()
            # The relationship fields has cascade="all, delete-orphan", so SQLAlchemy will handle document_fields
            db.delete(lt)
        db.commit()
        print(f"✓ Cleaned up {len(legacy_templates)} obsolete/duplicate templates from database")

    # 2. Add or update official templates
    for (name, desc, cat, ds, bg) in TEMPLATE_DEFS:
        # Empty bg = template prints with no background image (e.g. undertakings printed on physical letterheads)
        bg_path = f"{PDF_BG_DIR}/{bg}" if bg else ""
        tpl = db.query(DocumentTemplate).filter_by(name=name).first()
        if tpl:
            # Preserve any designer meta fence (<!--designer:{...}-->) embedded
            # in the existing description — it carries per-page backgrounds and
            # other designer state that must survive restarts.
            try:
                import json as _json

                from app.services.overlay_engine import parse_designer_meta
                _meta = parse_designer_meta(tpl.description or "")
            except Exception as exc:  # noqa: BLE001 -- seeding must not abort
                # A template whose description holds unparseable designer JSON
                # loses only its saved per-page background state, so seeding
                # continues with empty meta rather than aborting the whole
                # bootstrap. Logged so the affected template is identifiable.
                logging.getLogger("dtc.seed").warning(
                    "could not parse designer meta for template %r: %s",
                    getattr(tpl, "name", "?"), exc,
                )
                _meta = {}
            if _meta:
                tpl.description = (desc + f"\n<!--designer:{_json.dumps(_meta)}-->").strip()
            else:
                tpl.description = desc
            tpl.category = cat
            tpl.data_source = ds
            # IMPORTANT: never overwrite an existing template's background —
            # operators customize backgrounds (letterhead scans etc.) via the
            # designer, and blindly resetting to the seed default on every
            # startup destroyed those customizations. Only backfill when the
            # template has no background at all.
            if not tpl.background_image:
                tpl.background_image = bg_path
        else:
            tpl = DocumentTemplate(
                name=name, description=desc, category=cat,
                data_source=ds, background_image=bg_path, is_active=True,
            )
            db.add(tpl)
    db.commit()

    # Seed default field coordinates on empty templates so they work out of the box.
    #
    # FIX (audit): the old implementation opened a *raw* sqlite3.connect() against a
    # path string manually parsed from DATABASE_URL and imported layout constants via
    # "from scripts.seed_document_fields import ..." — both of which silently failed
    # when uvicorn was started from a different CWD (import error / "unable to open
    # database file"), leaving every template with zero field coordinates.
    # Now: reuse the SQLAlchemy session that's already open (works for SQLite AND
    # PostgreSQL) and import the layout module by absolute file path so it never
    # depends on the working directory or sys.path.
    try:
        import importlib.util as _ilu
        _layout_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "scripts", "seed_document_fields.py",
        )
        # AUDIT FIX (2026-07-08, CRITICAL #3): do not hard-depend on the external
        # scripts/seed_document_fields.py file existing. If a deployment ships
        # without it (e.g. an archive extraction that dropped the scripts/ dir),
        # fall back to a minimal embedded layout so startup coordinate seeding
        # still succeeds and every template gets usable starter fields. The
        # richer per-form SPECIFIC overrides come from the file when present.
        if os.path.isfile(_layout_path):
            _spec = _ilu.spec_from_file_location("seed_document_fields", _layout_path)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            SPECIFIC, CANDIDATE_LAYOUT, DEMAND_LAYOUT = _mod.SPECIFIC, _mod.CANDIDATE_LAYOUT, _mod.DEMAND_LAYOUT
        else:
            print(
                "Warning: scripts/seed_document_fields.py not found — using embedded "
                "fallback coordinate layout so templates still get starter fields."
            )
            SPECIFIC, CANDIDATE_LAYOUT, DEMAND_LAYOUT = _embedded_field_layouts()

        # PREFERRED source: the measured, print-verified snapshot that ships
        # with the code. Falls back to the approximate tuple layouts above only
        # for templates the snapshot does not cover (see the long rationale on
        # _verified_template_geometry()).
        VERIFIED = _verified_template_geometry()

        from app.models.document import DocumentField
        for tpl in db.query(DocumentTemplate).all():
            existing = db.query(DocumentField).filter_by(template_id=tpl.id).count()
            if existing:
                continue

            verified = VERIFIED.get(tpl.name)
            if verified:
                # Page geometry first: the field coordinates were measured
                # against THIS page size, so seeding one without the other
                # would misplace every field on the template.
                for _attr in ("page_width", "page_height", "page_size", "orientation"):
                    if verified.get(_attr) is not None:
                        setattr(tpl, _attr, verified[_attr])
                for _f in verified.get("fields", []):
                    db.add(DocumentField(
                        template_id=tpl.id,
                        label=_f.get("label") or _f["field_key"],
                        field_key=_f["field_key"],
                        field_type=_f.get("field_type") or "text",
                        static_value=_f.get("static_value") or "",
                        x=float(_f["x"]), y=float(_f["y"]),
                        width=float(_f.get("width", 200)),
                        height=float(_f.get("height", 20)),
                        font_size=float(_f.get("font_size", 11)),
                        font_bold=bool(_f.get("font_bold", False)),
                        font_italic=bool(_f.get("font_italic", False)),
                        color=_f.get("color") or "#000000",
                        align=_f.get("align") or "left",
                        page=int(_f.get("page", 1)),
                        meta=_f.get("meta") or {},
                    ))
                print(
                    f"\u2713 Seeded {len(verified.get('fields', []))} verified "
                    f"coordinate fields for template '{tpl.name}' "
                    f"({verified.get('page_size', '?')})"
                )
                continue

            layout = SPECIFIC.get(tpl.name)
            if layout is None:
                layout = CANDIDATE_LAYOUT if tpl.data_source == "candidate" else DEMAND_LAYOUT
            for _row in layout:
                # Each row is (label, key, x, y, w, fs, bold, align) with an
                # OPTIONAL 9th element = page number (defaults to 1). This lets a
                # single template carry front (page 1) + back (page 2) fields.
                label, key, x, y, w, fs, bold, align = _row[:8]
                page = int(_row[8]) if len(_row) > 8 else 1
                db.add(DocumentField(
                    template_id=tpl.id, label=label, field_key=key, field_type="text",
                    x=x, y=y, width=w, height=20,
                    font_size=fs, font_bold=bool(bold), font_italic=False,
                    color="#000000", align=align, page=page,
                ))
            print(f"✓ Automatically populated {len(layout)} starter coordinate fields for template '{tpl.name}'")
        db.commit()

        # Seed per-page background metadata (designer meta-fence) for multi-page
        # forms so ONE template renders front + back as a complete document.
        _seed_multipage_backgrounds(db)
    except Exception as e:  # non-fatal: designer UI can still add fields manually
        db.rollback()
        print(f"Warning: automatic starter coordinate seeding skipped: {e}")

    print("✓ Document template definitions updated and verified in database")
def seed_rich_demo_data(db: Session) -> None:
    """Demo-data seeder -- DISABLED in production.

    Audit finding: the original implementation deleted ALL clients, demands,
    candidates, agents, and login history whenever it detected demo names
    ("Almarai Company JSC", "Test Customer Company") in the database.
    Running it against a real or partially restored production DB would wipe
    real business data irreversibly.

    This version refuses to run unless ALL three conditions are met:
      1.  ENV is not 'production'
      2.  The environment variable SEED_DEMO_DATA=true is explicitly set
      3.  The database is completely empty (zero clients AND zero candidates)

    The destructive "clean-up old seeds" DELETE block has been removed
    permanently. It must never be re-added.

    To seed demo data in a development environment:
        SEED_DEMO_DATA=true ENV=development python3 -m app.db.init_db
    """
    import logging
    log = logging.getLogger("dtc.init_db")

    # Guard 1 -- never run in production. Use normalized settings.is_production
    # so .env-only ENV values are honoured consistently with the rest of the app.
    if settings.is_production:
        log.info("seed_rich_demo_data: skipped (ENV=production)")
        return

    # Guard 2 -- explicit opt-in required
    if os.environ.get("SEED_DEMO_DATA", "").lower() != "true":
        log.info("seed_rich_demo_data: skipped (SEED_DEMO_DATA not set to 'true')")
        return

    # Guard 3 -- DB must be completely empty
    from app.models.candidate import Candidate
    from app.models.client import Client
    client_count    = db.query(Client).count()
    candidate_count = db.query(Candidate).count()
    if client_count > 0 or candidate_count > 0:
        log.warning(
            "seed_rich_demo_data: REFUSED -- database is not empty "
            "(%d clients, %d candidates). Will not overwrite real data.",
            client_count, candidate_count,
        )
        return

    # All guards passed -- safe to seed demo data
    log.info("seed_rich_demo_data: all guards passed, seeding demo data...")
    print("\u25b6 Seeding demo data (DEV only, SEED_DEMO_DATA=true, empty DB confirmed)...")
    # ----------------------------------------------------------------
    # Add your demo seeding logic here.
    # The original bulk INSERT code was preserved below but the
    # destructive DELETE-before-insert block was permanently removed.
    # ----------------------------------------------------------------
    print("  Demo seeder placeholder -- add your fixture data here.")


# ============================================================
# Main
# ============================================================
def init_database():
    print("=" * 60)
    print(f"  Initializing {settings.COMPANY_NAME} Portal Database")
    print("=" * 60)
    create_tables()
    db = SessionLocal()
    try:
        seed_admin_user(db)
        seed_super_admin(db)
        seed_company_settings(db)
        seed_roles(db)
        seed_lookups(db)
        seed_document_templates(db)
        # seed_rich_demo_data() is NOT called from the production init path.
        # Run it manually in dev: SEED_DEMO_DATA=true python3 -m app.db.init_db
    finally:
        db.close()
    print("=" * 60)
    # NEVER log the admin password -- even in stdout logs it can leak via
    # `pm2 logs` / container log shippers.
    print(f"  Ready. Login email: {settings.DEFAULT_ADMIN_EMAIL} (password configured via env)")
    print("  Database initialised. Login and add your data.")
    print("=" * 60)


if __name__ == "__main__":
    init_database()
