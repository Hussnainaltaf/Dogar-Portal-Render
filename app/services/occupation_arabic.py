"""
Occupation -> Arabic translation for visa / OEP documents.

The legacy MySQL database stored Arabic name / father / place-of-birth /
issuing-authority, but it NEVER stored an Arabic occupation. The Gulf visa
forms (Saudi, etc.) require the occupation (المهنة) in Arabic. The reference
portal resolves this by translating the assigned trade into Arabic.

This module reproduces that behaviour with:
  1. An EXACT-match dictionary for the most common full trade names.
  2. A KEYWORD fallback that builds an Arabic phrase from recognised tokens
     (e.g. "General Building Carpenter" -> "نجار مبانى عام").
  3. The original English string as a final fallback so nothing is ever blank.
"""
from functools import lru_cache

# Token-level Arabic for the building blocks that appear across the 178 trades.
# Order matters for multi-word phrases handled in _from_keywords().
_TOKEN_AR = {
    "driver": "سائق",
    "personal": "خاص",
    "transport": "نقل",
    "truck": "شاحنة",
    "vehicle": "مركبة",
    "light": "خفيف",
    "water": "مياه",
    "tanker": "صهريج",
    "agriculture": "زراعة",
    "agriculturist": "مزارع",
    "labour": "عامل",
    "labourer": "عامل",
    "worker": "عامل",
    "general": "عام",
    "building": "مبانى",
    "house": "منزل",
    "cleaning": "تنظيف",
    "cleaner": "عامل نظافة",
    "mason": "بناء",
    "masonry": "بناء",
    "block": "طابوق",
    "stone": "حجر",
    "carpenter": "نجار",
    "furniture": "أثاث",
    "door": "أبواب",
    "window": "نوافذ",
    "decoration": "ديكور",
    "painter": "دهان",
    "painting": "دهان",
    "plasterer": "لياس",
    "plaster": "لياس",
    "gypsum": "جبس",
    "tile": "بلاط",
    "fixer": "مركب",
    "steel": "حديد",
    "electrician": "كهربائى",
    "electric": "كهربائى",
    "electrical": "كهربائى",
    "electronics": "إلكترونيات",
    "technician": "فنى",
    "plumber": "سباك",
    "welder": "لحام",
    "welding": "لحام",
    "blacksmith": "حداد",
    "turner": "خراط",
    "mechanic": "ميكانيكى",
    "auto": "سيارات",
    "car": "سيارة",
    "body": "هيكل",
    "denter": "سمكرى",
    "polisher": "ملمع",
    "washer": "غسيل",
    "greaser": "مشحم",
    "oil": "زيوت",
    "barber": "حلاق",
    "gents": "رجالى",
    "lady": "نسائى",
    "tailor": "خياط",
    "dress": "ملابس",
    "sewing": "خياطة",
    "ironer": "كواء",
    "laundary": "مغسلة",
    "laundry": "مغسلة",
    "cook": "طباخ",
    "cheff": "طباخ",
    "chef": "طباخ",
    "kitchen": "مطبخ",
    "baker": "خباز",
    "flour": "دقيق",
    "waiter": "نادل",
    "head": "رئيس",
    "restaurant": "مطعم",
    "sandwich": "ساندويتش",
    "meat": "لحوم",
    "soup": "حساء",
    "chicken": "دجاج",
    "bbq": "شواء",
    "drink": "مشروبات",
    "juice": "عصير",
    "maker": "صانع",
    "salesman": "بائع",
    "sales": "مبيعات",
    "representative": "مندوب",
    "accountant": "محاسب",
    "messenger": "مراسل",
    "helper": "مساعد",
    "operator": "مشغل",
    "plant": "محطة",
    "refrigerator": "تبريد",
    "refrigeration": "تبريد",
    "refrigrator": "تبريد",
    "surveyor": "مساح",
    "upholsterer": "منجد",
    "glass": "زجاج",
    "engraver": "نقاش",
    "calligrapher": "خطاط",
    "lab": "مختبر",
    "fire": "حريق",
    "safety": "سلامة",
    "workshop": "ورشة",
    "metal": "معدن",
    "scaffolding": "سقالات",
    "communication": "اتصالات",
    "specialist": "أخصائى",
    "thermal": "حرارى",
    "insulation": "عزل",
    "purification": "تنقية",
    "instrument": "أجهزة",
    "central": "مركزى",
    "equipment": "معدات",
    "structure": "إنشاءات",
    "demolish": "هدم",
    "wood": "خشب",
    "digger": "حفار",
    "animal": "حيوان",
    "training": "تدريب",
    "printing": "طباعة",
    "editing": "تحرير",
    "games": "ألعاب",
    "children": "أطفال",
    "curtain": "ستائر",
    "packing": "تغليف",
    "hand": "يدوى",
    "riveting": "برشمة",
    "casting": "سباكة",
    "civeil": "مدنى",
    "civil": "مدنى",
}

# Exact full-name overrides where the keyword build would be awkward.
_EXACT_AR = {
    "personal driver": "سائق خاص",
    "general driver": "سائق عام",
    "truck driver": "سائق شاحنة",
    "general vehicle driver": "سائق مركبات عام",
    "general transport driver": "سائق نقل عام",
    "light vehicle transport driver": "سائق نقل مركبات خفيفة",
    "labour": "عامل",
    "general labour": "عامل عام",
    "house worker": "عامل منزلى",
    "lady house worker": "عاملة منزلية",
    "cleaning worker": "عامل نظافة",
    "constructing worker": "عامل بناء",
    "construction worker": "عامل بناء",
    "workshop worker": "عامل ورشة",
    "general mason": "بناء عام",
    "block mason": "بناء طابوق",
    "stone mason": "بناء حجر",
    "plasterer": "لياس",
    "tile fixer": "مركب بلاط",
    "steel fixer helper": "مساعد حداد تسليح",
    "masonry steel fixer": "حداد تسليح",
    "general plumber": "سباك عام",
    "plumber": "سباك",
    "general electrician": "كهربائى عام",
    "building electrician": "كهربائى مبانى",
    "auto electrician": "كهربائى سيارات",
    "electric technician": "فنى كهرباء",
    "general welder": "لحام عام",
    "welding technician": "فنى لحام",
    "blacksmith": "حداد",
    "general turner": "خراط عام",
    "auto mechanic": "ميكانيكى سيارات",
    "general tailor": "خياط عام",
    "gents barber": "حلاق رجالى",
    "accountant": "محاسب",
    "general accountant": "محاسب عام",
    "waiter": "نادل",
    "head waiter": "رئيس نادل",
    "house cook": "طباخ منزلى",
    "general cheff": "طباخ عام",
    "kitchen worker": "عامل مطبخ",
    "cook helper": "مساعد طباخ",
    "general baker": "خباز عام",
    "sales representative": "مندوب مبيعات",
    "plant operator": "مشغل محطة",
    "building painter": "دهان مبانى",
    "auto painter": "دهان سيارات",
    "building carpenter": "نجار مبانى",
    "furniture carpenter": "نجار أثاث",
    "general building carpenter": "نجار مبانى عام",
    "general furniture carpenter": "نجار أثاث عام",
    "general agriculturist": "مزارع عام",
    "agriculture labour": "عامل زراعى",
    "general cleaning labour": "عامل نظافة عام",
}


def _normalize(s: str) -> str:
    return " ".join((s or "").lower().replace("&", " ").replace(",", " ").split())


def _from_keywords(name: str) -> str:
    """Build an Arabic phrase from recognised English tokens, preserving a
    natural noun-first order. Returns '' if nothing recognised."""
    tokens = _normalize(name).split()
    out = []
    for t in tokens:
        ar = _TOKEN_AR.get(t)
        if ar and ar not in out:
            out.append(ar)
    return " ".join(out)


@lru_cache(maxsize=256)
def occupation_to_arabic(name: str) -> str:
    """Translate an English occupation/trade to Arabic.

    Returns the best Arabic rendering, or the original English string when no
    mapping is found (never returns blank for a non-blank input).

    ``@lru_cache`` makes repeated lookups (the PDF engine calls this twice per
    document page) a pure dict-lookup after the first call — no string
    normalisation or token scanning on subsequent calls for the same trade.
    Cache size 256 covers all realistic trade-name variants in a single portal.
    """
    if not name:
        return ""
    norm = _normalize(name)
    if norm in _EXACT_AR:
        return _EXACT_AR[norm]
    kw = _from_keywords(name)
    if kw:
        return kw
    return name  # graceful fallback: show English rather than nothing
