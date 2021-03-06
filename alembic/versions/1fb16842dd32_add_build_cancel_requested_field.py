"""Add build.cancel_requested field

Revision ID: 1fb16842dd32
Revises: 448688cfd5b6
Create Date: 2016-04-12 13:15:38.884619

"""

# revision identifiers, used by Alembic.
revision = '1fb16842dd32'
down_revision = '448688cfd5b6'

from alembic import op
import sqlalchemy as sa


def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.add_column('build', sa.Column('cancel_requested', sa.Boolean(), server_default=sa.text(u'false'), nullable=False))
    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('build', 'cancel_requested')
    ### end Alembic commands ###
