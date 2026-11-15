import os
from decimal import Decimal

import click
from argon2 import PasswordHasher

from .extensions import db
from .models import Client, Company, Unit, User

COMPANIES = [("شركة النخيل العقارية", "nakheel"), ("شركة الواحة للتطوير", "waha")]
PROJECTS = ["برج النخيل", "واحة الياسمين", "مجمع الريان"]
TYPES = ["شقة", "دوبلكس", "فيلا"]
ROLES = [("manager", 1, "مدير"), ("accountant", 1, "محاسب"),
         ("sales", 1, "مندوب"), ("sales", 2, "مندوب")]


@click.command("seed")
def seed_command():
    """بيانات تجريبية. آمن للتشغيل أكثر من مرة."""
    if db.session.query(Company).count():
        click.echo("seed: already seeded, skipping")
        return
    pw_hash = PasswordHasher().hash(os.environ.get("SEED_PASSWORD", "Passw0rd!2026"))

    for name, slug in COMPANIES:
        company = Company(name=name)
        db.session.add(company)
        db.session.flush()

        sales_users = []
        for role, n, label in ROLES:
            user = User(company_id=company.id, email=f"{role}{n}@{slug}.test",
                        password_hash=pw_hash, full_name=f"{label} {n} - {name}", role=role)
            db.session.add(user)
            if role == "sales":
                sales_users.append(user)
        db.session.flush()

        for i in range(1, 13):
            db.session.add(Unit(
                company_id=company.id, project=PROJECTS[i % 3], number=f"A-{100 + i}",
                type=TYPES[i % 3], area_sqm=Decimal("90") + Decimal("7.5") * i, rooms=2 + i % 3,
                price_minor=(900_000 + i * 85_000) * 100,  # بالهللات
            ))

        for s_idx, sales in enumerate(sales_users, start=1):
            for c in range(1, 4):
                db.session.add(Client(
                    company_id=company.id, owner_id=sales.id,
                    name=f"عميل {s_idx}-{c} ({slug})",
                    phone=f"05{s_idx}{c}00000{c}", notes="عميل تجريبي"))

    db.session.commit()
    click.echo("seed: done. manager1 / accountant1 / sales1 / sales2 @ nakheel.test أو waha.test")