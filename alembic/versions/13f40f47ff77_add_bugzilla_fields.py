"""Add bugzilla fields

Revision ID: 13f40f47ff77
Revises: 37de714441fd
Create Date: 2016-04-28 17:17:31.198764

"""

# revision identifiers, used by Alembic.
revision = '13f40f47ff77'
down_revision = '37de714441fd'

from alembic import op
import sqlalchemy as sa


def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.add_column('collection', sa.Column('bugzilla_product', sa.String(), nullable=True))
    op.add_column('collection', sa.Column('bugzilla_version', sa.String(), nullable=True))
    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('collection', 'bugzilla_version')
    op.drop_column('collection', 'bugzilla_product')
    ### end Alembic commands ###
