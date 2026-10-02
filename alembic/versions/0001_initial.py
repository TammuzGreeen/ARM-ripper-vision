"""initial schema"""
from alembic import op
import sqlalchemy as sa
revision="0001"; down_revision=None; branch_labels=None; depends_on=None
def upgrade():
    op.create_table("batches",sa.Column("id",sa.Integer,primary_key=True),sa.Column("masterlist_id",sa.String(200),nullable=False),sa.Column("state",sa.String(50),nullable=False),sa.Column("current_index",sa.Integer,nullable=False),sa.Column("previous_ripping_enabled",sa.Boolean,nullable=True),sa.Column("current_job_id",sa.Integer,nullable=True),sa.Column("error_message",sa.Text,nullable=True),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False))
    op.create_table("seen_jobs",sa.Column("id",sa.Integer,primary_key=True),sa.Column("batch_id",sa.Integer,nullable=False),sa.Column("arm_job_id",sa.Integer,nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    op.create_table("audit_events",sa.Column("id",sa.Integer,primary_key=True),sa.Column("batch_id",sa.Integer,nullable=True),sa.Column("event_type",sa.String(100),nullable=False),sa.Column("message",sa.Text,nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
def downgrade():
    op.drop_table("audit_events");op.drop_table("seen_jobs");op.drop_table("batches")
