"""A `vector(N)`-on-Postgres / `TEXT`-on-SQLite column type for embeddings.

Why not `JSON().with_variant(Vector(dim), "postgresql")` (the pattern used by
`PgJson` in app/models/db.py)? `with_variant` returns a copy of the *base*
type, so the column's comparator would be JSON's, not pgvector's -- methods
like `.cosine_distance()` simply wouldn't exist. A `TypeDecorator` avoids that
trap entirely.

Core invariant enforced by this module: **asyncpg must never see a `vector`
value bound or fetched through the ORM.** asyncpg has no codec for the
`vector` OID, and registering one (via `pgvector.asyncpg.register_vector`)
costs a type-introspection round trip per *connection* -- which, combined
with `NullPool` + Supabase's Supavisor transaction-mode pooler (see
app/core/database.py), means a round trip per request. So:

  - Every `Memory.embedding` column is declared `deferred=True` -- a plain
    `select(Memory)` never fetches it.
  - The only code that reads or writes the column is app/memory/store.py,
    which uses `sqlalchemy.text()` with an explicit `CAST(:embedding AS
    vector)`, so the bound parameter's wire type is `text`, which asyncpg
    already knows how to handle.

On SQLite (used only by the test suite -- see tests/conftest.py) the column
is a plain TEXT column holding the same "[0.1,0.2,...]" string representation,
so `Base.metadata.create_all` never has to emit real vector DDL.
"""
from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, UserDefinedType


class _PgVector(UserDefinedType):
    """DDL-only stand-in for pgvector's `vector(N)` type.

    Deliberately does not implement bind/result processing or comparator
    overrides -- see the module docstring for why the ORM never touches this
    column's values directly.
    """

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: object) -> str:
        return f"vector({self.dim})"


class Embedding(TypeDecorator):
    """Represents an embedding as `list[float]` in Python.

    Compiles to `vector(N)` on Postgres and `TEXT` on every other dialect
    (SQLite, for tests). The wire/storage representation is always pgvector's
    text literal, e.g. "[0.1,0.2,0.3]".
    """

    impl = Text
    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PgVector(self.dim))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: list[float] | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return "[" + ",".join(repr(float(x)) for x in value) + "]"

    def process_result_value(self, value: str | None, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        stripped = value.strip("[]")
        if not stripped:
            return []
        return [float(p) for p in stripped.split(",")]
