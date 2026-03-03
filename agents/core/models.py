"""
Core memory SQLAlchemy models representing the 3-Tier Cognitive Memory System.
"""

from __future__ import annotations

import time

from sqlalchemy import Column, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class IdentityCore(Base):
    """
    Tier 1 - Core Memory: Immutable directives, system rules, user personas.
    """

    __tablename__ = "identity_core"

    id = Column(String, primary_key=True)
    directive_hash = Column(String, nullable=False)
    immutable_constraint_blob = Column(Text, nullable=False)


class RollingContext(Base):
    """
    Tier 2 - Rolling History: High-fidelity recent operational memory.
    """

    __tablename__ = "rolling_context"

    session_id = Column(String, primary_key=True)
    timestamp = Column(Float, nullable=False, default=time.time)
    fifo_blob = Column(Text, nullable=False)
    token_count = Column(Integer, nullable=False, default=0)


class FactStore(Base):
    """
    Tier 3 - Fact Store: Long-term extracted entities/facts (RAG).
    """

    __tablename__ = "fact_store"

    entity_id = Column(String, primary_key=True)
    vector_embedding = Column(Text)  # Stored as JSON string of float array
    semantic_relationship = Column(Text)
    confidence_score = Column(Float, nullable=False, default=0.0)
