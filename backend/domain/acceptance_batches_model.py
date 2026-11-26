from sqlalchemy import Boolean, CheckConstraint, Column, Float, ForeignKey, Integer, JSON, String, Table

from core.db import metadata


acceptance_batches = Table('acceptance_batches', metadata,
    Column('code', String(64), primary_key=True),
    Column('family', String(160), nullable=False),
    Column('state', String(160), nullable=False),
    Column('current_version', Integer, nullable=False),
    CheckConstraint("state IN ('开放','已封批')"),
    CheckConstraint('current_version >= 0'),
)


batch_slots = Table('batch_slots', metadata,
    Column('code', String(64), primary_key=True),
    Column('batch', String(64), ForeignKey('acceptance_batches.code'), nullable=False),
    Column('study', String(160), ForeignKey('shade_studies.code'), nullable=False),
    Column('site_key', String(160), nullable=False, unique=True),
    Column('x_mm', Integer, nullable=False),
    Column('y_mm', Integer, nullable=False),
    Column('lower_de', Float, nullable=False),
    Column('upper_de', Float, nullable=False),
    Column('first_de', Float, nullable=True),
    Column('first_receipt', String(160), nullable=True),
    Column('first_hit', Boolean, nullable=True),
    CheckConstraint('x_mm >= 0'),
    CheckConstraint('y_mm >= 0'),
    CheckConstraint('lower_de >= 0'),
    CheckConstraint('upper_de >= lower_de'),
    CheckConstraint('(first_de IS NULL) = (first_receipt IS NULL)'),
)


batch_readings = Table('batch_readings', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('batch', String(64), ForeignKey('acceptance_batches.code'), nullable=False),
    Column('site_key', String(160), nullable=False),
    Column('reading_kind', String(160), nullable=False),
    Column('delta_e', Float, nullable=False),
    Column('receipt', String(160), nullable=False, unique=True),
    CheckConstraint("reading_kind IN ('首测','复样')"),
    CheckConstraint('delta_e >= 0'),
)


batch_versions = Table('batch_versions', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('batch', String(64), ForeignKey('acceptance_batches.code'), nullable=False),
    Column('version_no', Integer, nullable=False),
    Column('sealed_at', String(40), nullable=False),
    Column('total_slots', Integer, nullable=False),
    Column('measured_slots', Integer, nullable=False),
    Column('missing_slots', Integer, nullable=False),
    Column('hits', Integer, nullable=False),
    Column('misses', Integer, nullable=False),
    Column('coverage', Float, nullable=True),
    Column('missing_site_keys', JSON, nullable=False),
    CheckConstraint('version_no >= 1'),
    CheckConstraint('total_slots >= 0'),
    CheckConstraint('measured_slots >= 0'),
    CheckConstraint('missing_slots >= 0'),
    CheckConstraint('hits >= 0'),
    CheckConstraint('misses >= 0'),
)
