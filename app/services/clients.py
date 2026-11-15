from sqlalchemy import func, select

from ..extensions import db
from ..models import Client, User
from ..schemas import ClientIn


class ClientNotFound(Exception):
    pass


def _visible(company_id: int, role: str, user_id: int) -> list:
    """المندوب يرى عملاءه فقط. غيره يرى عملاء شركته. الشركة تأتي دائماً من الجلسة."""
    conds = [Client.company_id == company_id]
    if role == "sales":
        conds.append(Client.owner_id == user_id)
    return conds


def list_clients(*, company_id: int, role: str, user_id: int, page: int, per_page: int):
    conds = _visible(company_id, role, user_id)
    total = db.session.execute(select(func.count(Client.id)).where(*conds)).scalar_one()
    rows = db.session.execute(
        select(Client, User.full_name).join(User, User.id == Client.owner_id)
        .where(*conds).order_by(Client.id.desc())
        .limit(per_page).offset((page - 1) * per_page)
    ).all()
    return rows, total


def get_client(*, company_id: int, role: str, user_id: int, client_id: int):
    row = db.session.execute(
        select(Client, User.full_name).join(User, User.id == Client.owner_id)
        .where(*_visible(company_id, role, user_id), Client.id == client_id)
    ).first()
    if row is None:
        raise ClientNotFound()
    return row  # (Client, owner_name)


def create_client(*, company_id: int, owner_id: int, data: ClientIn) -> Client:
    client = Client(company_id=company_id, owner_id=owner_id,
                    name=data.name, phone=data.phone, notes=data.notes)
    db.session.add(client)
    db.session.flush()
    return client


def update_client(*, company_id: int, owner_id: int, client_id: int, data: ClientIn) -> Client:
    client, _ = get_client(company_id=company_id, role="sales",
                           user_id=owner_id, client_id=client_id)
    client.name, client.phone, client.notes = data.name, data.phone, data.notes
    db.session.flush()
    return client