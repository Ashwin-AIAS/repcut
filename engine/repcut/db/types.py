"""Column types that hold an invariant SQLite will not hold for us.

``DateTime(timezone=True)`` is asymmetric on SQLite: the driver serialises an
aware value to a string carrying no offset, and hands back a **naive**
``datetime`` on read. Nothing fails at the write, nothing fails at the read - it
fails at the first ``stored < utcnow()``, with ``TypeError: can't compare
offset-naive and offset-aware datetimes``, in whichever module happens to need
that comparison first. Prompt 03's Gemini cache is a stored-vs-now comparison,
so that module inherits a bug written here.

``UTCDateTime`` closes the asymmetry at the boundary where it opens, so every
consumer above it can assume aware UTC without checking.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """A datetime that is timezone-aware UTC on both sides of a round trip.

    Writes are **rejected** if naive rather than coerced. Coercion would assume
    the caller meant UTC, and a caller that passed ``datetime.now()`` on a CET
    machine did not - it would store a value an hour off and read back something
    that compares fine and is simply wrong. That is the failure this type
    exists to prevent, so it is a loud error, not a quiet fix.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Normalise to UTC on the way in, refusing a value with no offset."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "a naive datetime cannot be stored: its UTC offset is unknown. "
                "Use repcut.db.models.utcnow(), or attach a tzinfo before writing."
            )
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Re-attach the offset SQLite dropped.

        The value was written as UTC by ``process_bind_param``, so a naive value
        read back *is* UTC - it has only lost the label. Backends that do return
        an offset (a future Postgres migration, per Prompt 13) are converted
        rather than relabelled.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
