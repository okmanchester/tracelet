"""持久化操作、上下文序号和可恢复附属任务；历史 DDL 不读取当前模型。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0002_conversation_operations"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("context_version", "control_version"):
        op.add_column("conversation", sa.Column(name, sa.Integer(), nullable=False,
                                                server_default="0"))
    op.add_column("conversation", sa.Column("active_operation_id", sa.String(64)))
    op.add_column("conversation", sa.Column("lease_until", DATETIME(fsp=6)))
    op.add_column("conversation", sa.Column("handoff_ticket_id", sa.String(64)))
    op.add_column("message", sa.Column("sequence", sa.Integer()))
    op.execute("""UPDATE message m JOIN (
        SELECT id, ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY created_at, id) AS seq
        FROM (SELECT id, conversation_id, created_at FROM message) source_messages
    ) ranked ON ranked.id=m.id SET m.sequence=ranked.seq""")
    op.execute("""UPDATE conversation c LEFT JOIN (
        SELECT conversation_id, MAX(sequence) AS version FROM message GROUP BY conversation_id
    ) m ON m.conversation_id=c.id SET c.context_version=COALESCE(m.version,0)""")
    op.create_unique_constraint("uq_message_sequence", "message", ["conversation_id", "sequence"])
    op.add_column("ticket", sa.Column("operation_id", sa.String(64)))
    op.create_unique_constraint("uq_ticket_operation", "ticket", ["operation_id"])
    op.create_table("operation",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("conversation_id", sa.String(64)),
        sa.Column("user_message_id", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=False),
        sa.Column("owner_token", sa.String(32), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("control_version", sa.Integer(), nullable=False),
        sa.Column("lease_until", DATETIME(fsp=6)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "kind", "request_id", name="uq_operation_identity"))
    op.create_index("ix_operation_tenant_id", "operation", ["tenant_id"])
    op.create_index("ix_operation_conversation_id", "operation", ["conversation_id"])
    op.create_table("effect_task",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("operation_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("owner_token", sa.String(32), nullable=False),
        sa.Column("available_at", DATETIME(fsp=6), nullable=False),
        sa.Column("lease_until", DATETIME(fsp=6)),
        sa.Column("error", sa.String(64)),
        sa.UniqueConstraint("operation_id", "kind", name="uq_effect_task"))
    op.create_index("ix_effect_task_operation_id", "effect_task", ["operation_id"])
    op.create_index("ix_effect_task_status", "effect_task", ["status"])


def downgrade():
    raise RuntimeError("降级会丢失操作和恢复记录；请使用前向修复迁移")
