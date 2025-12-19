"""Add story arcs tables

Revision ID: f1a2b3c4d5e6
Revises: d8f9e2a7b5c4
Create Date: 2025-12-19

Story arcs replace the keyword-based topic tracking with a more flexible
narrative-driven approach. Each story arc tracks an evolving news story
across multiple episodes and feeds.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'f1a2b3c4d5e6'
down_revision = 'd8f9e2a7b5c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create story_arcs table
    op.create_table(
        'story_arcs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('arc_name', sa.String(512), nullable=False),
        sa.Column('arc_slug', sa.String(255), nullable=False),
        sa.Column('functional_category', sa.String(50), nullable=False, server_default='other'),
        sa.Column('digest_topic', sa.String(256), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),  # AI-generated summary of the arc
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('source_count', sa.Integer(), nullable=False, server_default='1'),  # Number of unique feeds
        sa.Column('included_in_digest_id', sa.Integer(), nullable=True),
        sa.Column('included_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['included_in_digest_id'], ['digests.id'], ondelete='SET NULL'),
    )

    # Indexes for story_arcs
    op.create_index('ix_story_arcs_slug', 'story_arcs', ['arc_slug'])
    op.create_index('ix_story_arcs_digest_topic', 'story_arcs', ['digest_topic'])
    op.create_index('ix_story_arcs_last_updated', 'story_arcs', ['last_updated_at'])
    op.create_index('ix_story_arcs_category', 'story_arcs', ['functional_category'])
    op.create_unique_constraint('uq_story_arcs_slug_digest', 'story_arcs', ['arc_slug', 'digest_topic'])

    # Create story_arc_events table
    op.create_table(
        'story_arc_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('story_arc_id', sa.Integer(), nullable=False),
        sa.Column('event_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_summary', sa.Text(), nullable=False),  # 1-2 sentence description
        sa.Column('key_points', postgresql.ARRAY(sa.Text()), nullable=False, server_default='{}'),
        sa.Column('source_feed_id', sa.Integer(), nullable=True),
        sa.Column('source_episode_id', sa.Integer(), nullable=True),
        sa.Column('source_episode_guid', sa.String(512), nullable=True),
        sa.Column('source_name', sa.String(256), nullable=True),  # Feed/episode title for display
        sa.Column('perspective', sa.String(50), nullable=True),  # positive, negative, neutral, analytical
        sa.Column('relevance_score', sa.Float(), nullable=True),
        sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['story_arc_id'], ['story_arcs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_feed_id'], ['feeds.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_episode_id'], ['episodes.id'], ondelete='SET NULL'),
    )

    # Indexes for story_arc_events
    op.create_index('ix_story_arc_events_arc', 'story_arc_events', ['story_arc_id'])
    op.create_index('ix_story_arc_events_date', 'story_arc_events', ['event_date'])
    op.create_index('ix_story_arc_events_episode', 'story_arc_events', ['source_episode_id'])

    # Enable RLS on new tables
    op.execute("ALTER TABLE story_arcs ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY "service_role_policy" ON story_arcs
        FOR ALL TO service_role
        USING (true) WITH CHECK (true);
    """)
    op.execute("""
        CREATE POLICY "authenticated_read_policy" ON story_arcs
        FOR SELECT TO authenticated
        USING (true);
    """)

    op.execute("ALTER TABLE story_arc_events ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY "service_role_policy" ON story_arc_events
        FOR ALL TO service_role
        USING (true) WITH CHECK (true);
    """)
    op.execute("""
        CREATE POLICY "authenticated_read_policy" ON story_arc_events
        FOR SELECT TO authenticated
        USING (true);
    """)

    # Add web_settings for story arc retention
    op.execute("""
        INSERT INTO web_settings (category, key, value, value_type, description)
        VALUES (
            'story_arcs',
            'retention_days',
            '14',
            'int',
            'Number of days to keep story arcs active (default: 14)'
        )
        ON CONFLICT (category, key) DO NOTHING;
    """)

    op.execute("""
        INSERT INTO web_settings (category, key, value, value_type, description)
        VALUES (
            'story_arcs',
            'max_events_per_arc',
            '20',
            'int',
            'Maximum events to store per story arc (default: 20)'
        )
        ON CONFLICT (category, key) DO NOTHING;
    """)


def downgrade() -> None:
    # Remove web_settings
    op.execute("DELETE FROM web_settings WHERE category = 'story_arcs';")

    # Drop RLS policies
    op.execute("DROP POLICY IF EXISTS service_role_policy ON story_arc_events;")
    op.execute("DROP POLICY IF EXISTS authenticated_read_policy ON story_arc_events;")
    op.execute("DROP POLICY IF EXISTS service_role_policy ON story_arcs;")
    op.execute("DROP POLICY IF EXISTS authenticated_read_policy ON story_arcs;")

    # Drop tables
    op.drop_table('story_arc_events')
    op.drop_table('story_arcs')
