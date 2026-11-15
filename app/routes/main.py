from flask import Blueprint, g, render_template

from ..extensions import db
from ..models import Company
from ..security import login_required

bp = Blueprint("main", __name__)


@bp.get("/")
@login_required
def home():
    company = db.session.get(Company, g.user.company_id)
    return render_template("index.html", company=company)