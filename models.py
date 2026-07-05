from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")  # "admin" | "user"
    api_key = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    portfolio_items = db.relationship(
        "PortfolioItem", backref="user", cascade="all, delete-orphan"
    )
    alerts = db.relationship("Alert", backref="user", cascade="all, delete-orphan")

    @property
    def is_admin(self):
        return self.role == "admin"


class PortfolioItem(db.Model):
    __tablename__ = "portfolio_items"
    __table_args__ = (db.UniqueConstraint("user_id", "ticker", name="uq_user_ticker"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ticker = db.Column(db.String(16), nullable=False)
    qty = db.Column(db.Float, default=0)
    buy_price = db.Column(db.Float, default=0)
    name = db.Column(db.String(128), default="")
    current_price = db.Column(db.Float)
    target_price = db.Column(db.Float)
    change_pct = db.Column(db.Float, default=0)

    def to_dict(self):
        return {
            "ticker": self.ticker,
            "qty": self.qty,
            "buyPrice": self.buy_price,
            "name": self.name,
            "currentPrice": self.current_price,
            "targetPrice": self.target_price,
            "changePct": self.change_pct,
        }


class Alert(db.Model):
    __tablename__ = "alerts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ticker = db.Column(db.String(16), nullable=False)
    price = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False, default="above")
    triggered = db.Column(db.Boolean, default=False)
    triggered_at = db.Column(db.String(20))
    created = db.Column(db.String(20))

    def to_dict(self):
        return {
            "id": self.id,
            "ticker": self.ticker,
            "price": self.price,
            "type": self.type,
            "triggered": self.triggered,
            "triggeredAt": self.triggered_at,
            "created": self.created,
        }
