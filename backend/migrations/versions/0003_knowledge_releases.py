"""Immutable knowledge snapshots, publication CAS, and pinned chat versions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT

revision = "0003_knowledge_releases"
down_revision = "0002_conversation_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("knowledge_release",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("collection_name", sa.String(128), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(64)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False))
    op.create_table("knowledge_snapshot_document",
        sa.Column("release_id", sa.String(32), sa.ForeignKey("knowledge_release.id"), primary_key=True),
        sa.Column("doc_id", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content", LONGTEXT(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False))
    op.create_table("knowledge_chunk_snapshot",
        sa.Column("release_id", sa.String(32), sa.ForeignKey("knowledge_release.id"), primary_key=True),
        sa.Column("chunk_id", sa.String(64), primary_key=True),
        sa.Column("doc_id", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("vector_digest", sa.String(64), nullable=False))
    op.create_table("knowledge_head",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("release_id", sa.String(32), sa.ForeignKey("knowledge_release.id")),
        sa.Column("generation", sa.Integer(), nullable=False))
    op.execute("INSERT INTO knowledge_head (id, release_id, generation) VALUES (1, NULL, 0)")
    op.create_table("knowledge_publication",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("release_id", sa.String(32), sa.ForeignKey("knowledge_release.id"), nullable=False),
        sa.Column("previous_release_id", sa.String(32)),
        sa.Column("previous_generation", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("operator", sa.String(128), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False))
    op.add_column("operation", sa.Column("knowledge_version", sa.String(32)))


def downgrade():
    raise RuntimeError("知识版本迁移不提供破坏性降级")
