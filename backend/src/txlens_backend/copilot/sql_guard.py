"""The copilot's SQL guard: only one aggregate SELECT over the de-identified copilot views.

Generated SQL is parsed with sqlglot (Postgres) and accepted only if:
- it is one SELECT (CTEs allowed), with no set operations;
- every node of its syntax tree is of an allowed kind (an allow-list: functions, locks,
  INTO, window functions, FILTER, LATERAL, parameters ... are rejected by default);
- every table is a copilot view or vocab.concept / concept_ancestor (or a CTE), and every
  column exists there (sqlglot `qualify` against SCHEMA);
- it returns aggregates only: the outermost SELECT has count(DISTINCT person_id) (the cell
  size, used for suppression), every other output is an aggregate or a GROUP BY column, and
  GROUP BY keys are bare columns of the views, never person_id, another row id or a measured
  value (each distinct value would be a row);
- aggregates appear only in that outermost SELECT and take a bare column (count takes * or
  person_id), so no inner query or expression can carry a small count out (e.g.
  `sum(CASE ...)`, or a CTE's count shown as a group key);
- min and max are not allowed: they would show one patient's value.
The LIMIT is forced to at most MAX_ROWS. The guard also describes the query's filters for
the differencing guard. It never trusts the model: the DB role and RLS stand behind it.
"""

from dataclasses import dataclass, field
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.qualify import qualify

from txlens_backend.services import disclosure

MAX_ROWS = 200
PERSON = "person_id"
INT, NUM, TEXT, BOOL = "INT", "DOUBLE", "TEXT", "BOOLEAN"
EVENT = {PERSON: INT, "day": INT, "year": INT}
SCHEMA: dict[str, dict[str, dict[str, str]]] = {
    "copilot": {
        "patient": {PERSON: INT, "sex": TEXT, "organ": TEXT, "donor_type": TEXT,
                    "transplant_year": INT, "age_at_transplant": INT, "graft_loss": BOOL,
                    "graft_loss_day": INT, "died": BOOL, "death_day": INT},
        "measurement": EVENT | {"measurement_concept_id": INT, "lab": TEXT,
                                "value_as_number": NUM, "value_as_concept_id": INT,
                                "unit_concept_id": INT},
        "condition": EVENT | {"condition_concept_id": INT, "condition_type_concept_id": INT},
        "procedure": EVENT | {"procedure_concept_id": INT},
        "drug_exposure": EVENT | {"drug_concept_id": INT, "days_supply": INT},
        "observation": EVENT | {"observation_concept_id": INT, "value_as_concept_id": INT,
                                "value_as_number": NUM},
        "visit": EVENT | {"visit_concept_id": INT},
    },
    "vocab": {
        "concept": {"concept_id": INT, "concept_name": TEXT, "domain_id": TEXT,
                    "vocabulary_id": TEXT, "concept_class_id": TEXT,
                    "standard_concept": TEXT, "concept_code": TEXT},
        "concept_ancestor": {"ancestor_concept_id": INT, "descendant_concept_id": INT,
                             "min_levels_of_separation": INT,
                             "max_levels_of_separation": INT},
    },
}  # fmt: skip
# Measured values: as GROUP BY keys they would list individual results one per row.
CONTINUOUS = frozenset({"value_as_number"})
AGGREGATES: tuple[type[exp.Expression], ...] = (
    exp.Count, exp.Avg, exp.Stddev, exp.StddevSamp, exp.StddevPop, exp.PercentileCont,
)  # fmt: skip
ALLOWED_NODES: tuple[type[exp.Expression], ...] = (
    *AGGREGATES, exp.Select, exp.With, exp.CTE, exp.Subquery, exp.From, exp.Join, exp.Where,
    exp.Group, exp.Having, exp.Order, exp.Ordered, exp.Limit, exp.Offset, exp.Column,
    exp.Identifier, exp.Table, exp.TableAlias, exp.Alias, exp.Literal, exp.Boolean, exp.Null,
    exp.Paren, exp.And, exp.Or, exp.Not, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
    exp.Between, exp.In, exp.Is, exp.Like, exp.ILike, exp.Add, exp.Sub, exp.Mul, exp.Div,
    exp.Neg, exp.Case, exp.If, exp.Coalesce, exp.Cast, exp.DataType, exp.Round, exp.Abs,
    exp.Distinct, exp.WithinGroup, exp.Exists, exp.Star, exp.Tuple,
)  # fmt: skip
CAST_TYPES = frozenset({"INT", "INTEGER", "BIGINT", "SMALLINT", "DOUBLE PRECISION", "REAL",
                        "DECIMAL", "NUMERIC", "TEXT", "VARCHAR", "BOOLEAN"})  # fmt: skip
# Comparison -> (the column's lower end is bounded, inclusive); and with the sides swapped.
COMPARISONS: dict[type[exp.Expression], tuple[bool, bool]] = {
    exp.GT: (True, False),
    exp.GTE: (True, True),
    exp.LT: (False, False),
    exp.LTE: (False, True),
}
FLIPPED: dict[type[exp.Expression], type[exp.Expression]] = {
    exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE}  # fmt: skip


class SqlRejectedError(ValueError):
    """The SQL breaks a guard rule; the message says which, so the model can fix it."""


@dataclass(frozen=True)
class Output:
    """One output column: its name, and what it is."""

    name: str
    kind: str  # "group" (a GROUP BY column), "n" (count of distinct patients), "count", "value"
    dim: str = ""  # group columns: the filter dimension its value pins down
    cell: str = ""  # counts: what is counted, for the differencing guard


@dataclass(frozen=True)
class Checked:
    """Accepted SQL with what the executor and the differencing guard need."""

    sql: str
    outputs: list[Output]
    base: str
    constraints: disclosure.Constraints = field(default_factory=dict)


def parse(sql: str) -> exp.Select:
    """Exactly one SELECT statement."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as error:
        raise SqlRejectedError(f"not valid SQL: {error.errors[0]['description']}") from error
    if len(statements) != 1:
        raise SqlRejectedError("exactly one statement is allowed")
    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise SqlRejectedError(f"only SELECT is allowed, not {statement.key.upper()}")
    return statement


def type_name(node: exp.DataType) -> str:
    """'NUMERIC' for NUMERIC(10, 2): the type without its parameters."""
    return node.sql("postgres").upper().split("(")[0]


def check_nodes(tree: exp.Expression) -> None:
    """Every node is of an allowed kind; casts only to plain types; * only in count(*)."""
    for node in tree.walk():
        if not isinstance(node, ALLOWED_NODES):
            name = node.sql("postgres") if isinstance(node, exp.Anonymous) else node.key
            raise SqlRejectedError(f"not allowed in copilot SQL: {name}")
        if isinstance(node, exp.DataType) and type_name(node) not in CAST_TYPES:
            raise SqlRejectedError(f"casts to {node.sql('postgres')} are not allowed")
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            raise SqlRejectedError("SELECT * is not allowed; name the columns")


def check_tables(tree: exp.Expression) -> None:
    """Only copilot views, two vocabulary tables, and the query's own CTEs."""
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SqlRejectedError("table functions are not allowed")
        if not table.db and table.name in ctes:
            continue
        if table.name not in SCHEMA.get(table.db, {}):
            allowed = ", ".join(f"{s}.{t}" for s, ts in SCHEMA.items() for t in ts)
            raise SqlRejectedError(f"table {table.sql('postgres')} is not allowed ({allowed})")


def qualified(tree: exp.Select) -> exp.Select:
    """Columns resolved to their tables; unknown columns rejected."""
    try:
        schema = cast(dict[str, object], SCHEMA)
        result = qualify(tree, schema=schema, dialect="postgres", validate_qualify_columns=True,
                         quote_identifiers=False)  # fmt: skip
    except sqlglot.errors.OptimizeError as error:
        raise SqlRejectedError(str(error).split(". Line")[0]) from error
    assert isinstance(result, exp.Select)
    return result


def check_aggregate(agg: exp.Expression, outer: exp.Select) -> None:
    """Aggregates only in the outermost SELECT, over a bare column (count: * or person_id)."""
    if agg.find_ancestor(exp.Select) is not outer:
        raise SqlRejectedError("aggregates are allowed only in the outermost SELECT")
    if isinstance(agg, exp.PercentileCont):
        within = agg.parent
        ordered = within.expression.expressions if isinstance(within, exp.WithinGroup) else []
        if (
            not isinstance(agg.this, exp.Literal)
            or len(ordered) != 1
            or not isinstance(ordered[0].this, exp.Column)
        ):
            raise SqlRejectedError("use percentile_cont(p) WITHIN GROUP (ORDER BY column)")
        return
    arg = agg.this
    inner = arg.expressions if isinstance(arg, exp.Distinct) else [arg]
    if len(inner) != 1 or not isinstance(inner[0], exp.Column | exp.Star):
        raise SqlRejectedError(f"aggregate arguments must be a plain column: {agg.sql()}")
    if isinstance(agg, exp.Count) and isinstance(inner[0], exp.Column) and inner[0].name != PERSON:
        raise SqlRejectedError("count only patients (count(DISTINCT person_id)) or rows (*)")


def table_names(tree: exp.Expression) -> dict[str, str]:
    """Table alias -> table name (schema-qualified for views)."""
    return {t.alias_or_name: f"{t.db}.{t.name}" if t.db else t.name
            for t in tree.find_all(exp.Table)}  # fmt: skip


def column_key(column: exp.Column, names: dict[str, str]) -> str:
    """'copilot.patient.organ' for a column, whatever alias the query gave its table."""
    return f"{names.get(column.table, column.table)}.{column.name}"


def is_n(projection: exp.Expression) -> bool:
    """count(DISTINCT person_id): the number of patients behind a row."""
    count = projection.unalias()
    return (
        isinstance(count, exp.Count)
        and isinstance(count.this, exp.Distinct)
        and [c.name for c in count.this.expressions] == [PERSON]
    )


def outputs(outer: exp.Select, names: dict[str, str]) -> list[Output]:
    """What each output column is; rejects person-level output."""
    groups = outer.args.get("group")
    keys = groups.expressions if groups else []
    for key in keys:
        if not isinstance(key, exp.Column) or key.table not in names or "." not in names[key.table]:
            raise SqlRejectedError("GROUP BY only plain columns of the copilot views")
        if key.name == PERSON or (key.name.endswith("_id") and not key.name.endswith("concept_id")):
            raise SqlRejectedError(f"grouping by {key.name} would return one row per record")
        if key.name in CONTINUOUS:
            raise SqlRejectedError(f"grouping by {key.name} would list individual values; "
                                   "use avg, stddev or percentile_cont instead")  # fmt: skip
    found: list[Output] = []
    for projection in outer.expressions:
        value = projection.unalias()
        if isinstance(value, exp.Column) and value in keys:
            found.append(Output(projection.alias_or_name, "group", column_key(value, names)))
            continue
        if not value.find(*AGGREGATES) or any(
            not c.find_ancestor(*AGGREGATES, exp.WithinGroup) for c in value.find_all(exp.Column)
        ):
            raise SqlRejectedError(
                f"output {projection.sql()} is neither an aggregate nor a GROUP BY column"
            )
        kind = "n" if is_n(projection) else "count" if isinstance(value, exp.Count) else "value"
        cell = normalized(value, names) if kind != "value" else ""
        found.append(Output(projection.alias_or_name, kind, cell=cell))
    if not any(o.kind == "n" for o in found):
        raise SqlRejectedError("include count(DISTINCT person_id) so cell sizes can be checked")
    return found


def constraint(
    conjunct: exp.Expression, names: dict[str, str]
) -> tuple[str, disclosure.Constraint]:
    """One WHERE condition as (dimension, constraint); anything unusual is an opaque atom."""
    cls = type(conjunct)
    left, right = conjunct.args.get("this"), conjunct.args.get("expression")
    if cls in FLIPPED and isinstance(left, exp.Literal) and isinstance(right, exp.Column):
        cls, left, right = FLIPPED[cls], right, left
    if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
        dim, value = column_key(left, names), literal(right)
        if cls is exp.EQ:
            return dim, ({"eq": value} if isinstance(value, str)
                         else disclosure.interval(value, value))  # fmt: skip
        if cls in COMPARISONS:
            lower, inclusive = COMPARISONS[cls]
            bounds = {"lo": value, "lo_inclusive": inclusive} if lower else {
                "hi": value, "hi_inclusive": inclusive}  # fmt: skip
            return dim, disclosure.interval(**bounds)  # type: ignore[arg-type]
    if isinstance(conjunct, exp.Between) and isinstance(left, exp.Column):
        low, high = conjunct.args["low"], conjunct.args["high"]
        if isinstance(low, exp.Literal) and isinstance(high, exp.Literal):
            return column_key(left, names), disclosure.interval(literal(low), literal(high))
    if isinstance(conjunct, exp.In) and isinstance(left, exp.Column):
        values = conjunct.expressions
        if values and all(isinstance(v, exp.Literal) for v in values):
            return column_key(left, names), {"any": sorted({literal(v) for v in values}, key=str)}
    return f"atom:{normalized(conjunct, names)}", {"atom": True}


def literal(node: exp.Literal) -> float | str:
    """A literal's value: numbers as float, strings as they are."""
    return float(node.this) if node.is_number else str(node.this)


def normalized(node: exp.Expression, names: dict[str, str]) -> str:
    """SQL text with table aliases replaced by table names, for comparing queries."""
    copy = node.copy()
    for column in copy.find_all(exp.Column):
        if column.table:
            column.set("table", exp.to_identifier(names.get(column.table, column.table)))
    for table in copy.find_all(exp.Table):
        table.set("alias", None)
    return copy.sql("postgres")


def conjuncts(condition: exp.Expression) -> list[exp.Expression]:
    """The AND-ed parts of a condition."""
    parts = list(condition.flatten()) if isinstance(condition, exp.And) else [condition]
    return [cast(exp.Expression, cast(exp.Expression, p).unnest()) for p in parts]


def is_join_key(condition: exp.Expression) -> bool:
    """column = column: how tables are joined, not a filter on patients."""
    return isinstance(condition, exp.EQ) and all(
        isinstance(side, exp.Column) for side in (condition.this, condition.expression)
    )


def filters(tree: exp.Select, names: dict[str, str]) -> tuple[str, disclosure.Constraints]:
    """(base, constraints): the query without its row filters, and those filters. Every WHERE
    (outer and in CTEs) and inner-join ON is split into AND-ed conditions; join keys stay in
    the base; subqueries stay whole conditions."""
    shape = tree.copy()
    found: list[exp.Expression] = []
    for where in list(shape.find_all(exp.Where)):
        if where.find_ancestor(exp.Where):
            continue  # inside another WHERE's subquery: part of that condition
        found += conjuncts(where.this)
        where.pop()
    for join in shape.find_all(exp.Join):
        on = join.args.get("on")
        if on is None or join.side:  # outer join: ON does not filter the preserved side
            continue
        parts = conjuncts(on)
        found += [p for p in parts if not is_join_key(p)]
        keys = [p for p in parts if is_join_key(p)]
        join.set("on", exp.and_(*keys) if keys else exp.true())
    constraints: disclosure.Constraints = {}
    for part in found:
        dim, value = constraint(part, names)
        constraints = disclosure.add(constraints, dim, value)
    for clause in ("group", "order", "limit", "offset"):
        shape.set(clause, None)
    shape.set("expressions", [p.unalias() for p in shape.expressions if p.find(*AGGREGATES)])
    return disclosure.base_key(sql=normalized(shape, names)), constraints


def check(sql: str) -> Checked:
    """The accepted query (with a LIMIT of at most MAX_ROWS), or SqlRejectedError."""
    tree = parse(sql)
    if tree.find(exp.SetOperation):
        raise SqlRejectedError("UNION, INTERSECT and EXCEPT are not allowed")
    check_nodes(tree)
    check_tables(tree)
    tree = qualified(tree)
    for agg in tree.find_all(*AGGREGATES):
        check_aggregate(agg, tree)
    names = table_names(tree)
    found = outputs(tree, names)
    limit = tree.args.get("limit")
    rows = limit.expression if limit else None
    if not (isinstance(rows, exp.Literal) and rows.is_int and int(rows.this) <= MAX_ROWS):
        tree = tree.limit(MAX_ROWS)
    base, constraints = filters(tree, names)
    return Checked(tree.sql("postgres"), found, base, constraints)
