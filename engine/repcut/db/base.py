"""Declarative base and the constraint naming convention.

SQLite cannot ALTER a constraint, so Alembic rewrites the whole table in "batch"
mode instead - and it can only drop a constraint it can name. Auto-generated
names differ between backends and SQLAlchemy versions, so every constraint is
named by convention here, once, before the first migration exists. Retrofitting
this after tables ship costs a data migration.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every Repcut ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
