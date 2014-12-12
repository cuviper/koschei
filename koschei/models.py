# Copyright (C) 2014  Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Author: Michael Simacek <msimacek@redhat.com>

import koji

from sqlalchemy import (create_engine, Column, Integer, String, Boolean,
                        ForeignKey, DateTime, Index, DDL)
from sqlalchemy.sql.expression import extract, func, select, false, join
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, column_property, mapper
from sqlalchemy.engine.url import URL
from sqlalchemy.event import listens_for, listen
from datetime import datetime

from .util import config
from .event import EventQueue

Base = declarative_base()

db_url = URL(**config['database_config'])
engine = create_engine(db_url, echo=False, pool_size=10)

Session = sessionmaker(bind=engine, autocommit=False)


def hours_since(since):
    return extract('EPOCH', datetime.now() - since) / 3600


def external_id():
    raise AssertionError("ID needs to be supplied")


class User(Base):
    __tablename__ = 'user'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=False)
    timezone = Column(String)
    admin = Column(Boolean, nullable=False, server_default=false())


class Package(Base):
    __tablename__ = 'package'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    static_priority = Column(Integer, nullable=False, default=0)
    manual_priority = Column(Integer, nullable=False, default=0)
    added = Column(DateTime, nullable=False, default=datetime.now)

    build_opts = Column(String)

    # cached value, populated by scheduler
    current_priority = Column(Integer)

    # denormalized field, updated by trigger on inser/update (no delete)
    last_complete_build_id = \
        Column(Integer, ForeignKey('build.id', use_alter=True,
                                   name='fkey_package_last_build_id'),
               nullable=True)
    # denormalized field, updated manually by resolver
    resolved = Column(Boolean)
    # denormalized field, updated manually by resolver
    last_resolution_id = Column(Integer)

    ignored = Column(Boolean, nullable=False, server_default=false())

    @property
    def state_string(self):
        if self.ignored:
            return 'ignored'
        if self.resolved is False:
            return 'unresolved'
        build = self.last_complete_build
        if build:
            # pylint: disable=E1101
            return {Build.COMPLETE: 'ok',
                    Build.FAILED: 'failing',
                    }.get(build.state)
        return 'ignored'

    def __repr__(self):
        return '{0.id} (name={0.name})'.format(self)


class KojiTask(Base):
    __tablename__ = 'koji_task'

    build_id = Column(ForeignKey('build.id', ondelete='CASCADE'),
                      nullable=False, index=True)
    task_id = Column(Integer, primary_key=True, default=external_id)
    arch = Column(String(16))
    state = Column(Integer)
    started = Column(DateTime)
    finished = Column(DateTime)

    @property
    def state_string(self):
        return [state for state, num in koji.TASK_STATES.items()
                if num == self.state][0].lower()


class PackageGroupRelation(Base):
    __tablename__ = 'package_group_relation'
    group_id = Column(Integer, ForeignKey('package_group.id',
                                          ondelete='CASCADE'),
                      primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id', ondelete='CASCADE'),
                        primary_key=True)


class PackageGroup(Base):
    __tablename__ = 'package_group'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)

    # pylint: disable=E1101
    packages = relationship(Package, secondary=PackageGroupRelation.__table__,
                            order_by=Package.name)


class Build(Base):
    __tablename__ = 'build'

    STATE_MAP = {'running': 2,
                 'complete': 3,
                 'canceled': 4,
                 'failed': 5}
    RUNNING = STATE_MAP['running']
    COMPLETE = STATE_MAP['complete']
    CANCELED = STATE_MAP['canceled']
    FAILED = STATE_MAP['failed']
    REV_STATE_MAP = {v: k for k, v in STATE_MAP.items()}

    FINISHED_STATES = [COMPLETE, FAILED, CANCELED]
    STATES = [RUNNING] + FINISHED_STATES

    KOJI_STATE_MAP = {'CLOSED': COMPLETE,
                      'CANCELED': CANCELED,
                      'FAILED': FAILED}

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id', ondelete='CASCADE'))
    state = Column(Integer, nullable=False, default=RUNNING)
    task_id = Column(Integer, unique=True, index=True)
    started = Column(DateTime)
    finished = Column(DateTime)
    epoch = Column(Integer)
    version = Column(String)
    release = Column(String)
    repo_id = Column(Integer)
    deps_processed = Column(Boolean, nullable=False, server_default=false())
    build_arch_tasks = relationship(KojiTask, backref='build',
                                    order_by=KojiTask.arch)
    # was the build done by koschei or was it real build done by packager
    real = Column(Boolean, nullable=False, server_default=false())

    @property
    def state_string(self):
        return self.REV_STATE_MAP[self.state]

    @property
    def triggers(self):
        return [change.get_trigger() for change in self.dependency_changes]

    def __repr__(self):
        return '{0.id} (name={0.package.name}, state={0.state_string})'\
               .format(self)


class ResolutionResult(Base):
    __tablename__ = 'resolution_result'
    id = Column(Integer, primary_key=True)
    package_id = Column(ForeignKey('package.id', ondelete='CASCADE'),
                        index=True)
    repo_id = Column(Integer, nullable=False)
    resolved = Column(Boolean, nullable=False, server_default=false())
    generated = Column(DateTime, nullable=False, default=datetime.now)
    problems = relationship('ResolutionProblem')


class ResolutionProblem(Base):
    __tablename__ = 'resolution_result_element'
    id = Column(Integer, primary_key=True)
    resolution_id = Column(Integer, ForeignKey(ResolutionResult.id,
                                               ondelete='CASCADE'),
                           index=True)
    problem = Column(String, nullable=False)


class RepoGenerationRequest(Base):
    __tablename__ = 'repo_generation_request'

    id = Column(Integer, primary_key=True)
    repo_id = Column(Integer, nullable=False)
    requested = Column(DateTime, nullable=False, default=datetime.now)


class Dependency(Base):
    __tablename__ = 'dependency'
    id = Column(Integer, primary_key=True)
    repo_id = Column(Integer, nullable=False)
    package_id = Column(ForeignKey('package.id', ondelete='CASCADE'))
    name = Column(String, nullable=False)
    epoch = Column(Integer)
    version = Column(String, nullable=False)
    release = Column(String, nullable=False)
    arch = Column(String, nullable=False)
    distance = Column(Integer)

    nevr = (name, epoch, version, release)
    nevra = (name, epoch, version, release, arch)

Index('ix_dependency_composite', Dependency.package_id, Dependency.repo_id)
Index('build_package_id_ordered', Build.package_id.asc())


class DependencyChange(Base):
    __tablename__ = 'dependency_change'
    id = Column(Integer, primary_key=True)
    package_id = Column(ForeignKey('package.id', ondelete='CASCADE'),
                        nullable=False)
    applied_in_id = Column(ForeignKey('build.id', ondelete='CASCADE'),
                           nullable=True, default=None)
    dep_name = Column(String, nullable=False)
    prev_epoch = Column(Integer)
    prev_version = Column(String)
    prev_release = Column(String)
    curr_epoch = Column(Integer)
    curr_version = Column(String)
    curr_release = Column(String)
    distance = Column(Integer)

    @property
    def prev_evr(self):
        return self.prev_epoch, self.prev_version, self.prev_release

    @property
    def curr_evr(self):
        return self.curr_epoch, self.curr_version, self.curr_release


@listens_for(Session, "after_begin")
def session_begin(db, transaction, connection):
    db._event_queue = EventQueue()


@listens_for(Session, "before_commit")
def session_commit(db):
    if hasattr(db, '_event_queue'):
        if not db._event_queue.empty():
            db.flush()
        db._event_queue.flush()


@listens_for(Session, "after_rollback")
def session_rollback(db):
    if hasattr(db, '_event_queue'):
        db._event_queue.rollback()

# Triggers

trigger = DDL("""
              CREATE OR REPLACE FUNCTION update_last_complete_build()
                  RETURNS TRIGGER AS $$
              BEGIN
                  UPDATE package
                  SET last_complete_build_id = lcb.id
                  FROM (SELECT id, task_id, state, started
                        FROM build
                        WHERE package_id = NEW.package_id
                              AND (state = 3 OR state = 5)
                        ORDER BY task_id DESC
                        LIMIT 1) AS lcb
                  WHERE package.id = NEW.package_id;
                  RETURN NEW;
              END $$ LANGUAGE plpgsql;

              DROP TRIGGER IF EXISTS update_last_complete_build_trigger
                    ON build;
              CREATE TRIGGER update_last_complete_build_trigger
                  AFTER INSERT ON build FOR EACH ROW
                  WHEN (NEW.state = 3 OR NEW.state = 5)
                  EXECUTE PROCEDURE update_last_complete_build();
              DROP TRIGGER IF EXISTS update_last_complete_build_trigger_up
                    ON build;
              CREATE TRIGGER update_last_complete_build_trigger_up
                  AFTER UPDATE ON build FOR EACH ROW
                  WHEN (OLD.state != NEW.state)
                  EXECUTE PROCEDURE update_last_complete_build();
              """)

listen(Base.metadata, 'after_create', trigger.execute_if(dialect='postgresql'))

# Relationships

Package.last_complete_build = \
    relationship(Build,
                 primaryjoin=(Build.id == Package.last_complete_build_id),
                 uselist=False)

Package.resolution_problems = \
    relationship(ResolutionProblem,
                 primaryjoin=(ResolutionProblem.resolution_id ==
                              Package.last_resolution_id),
                 foreign_keys=[Package.last_resolution_id],
                 uselist=True)

Package.all_builds = relationship(Build, order_by=Build.task_id.desc(),
                                  primaryjoin=(Build.package_id == Package.id),
                                  backref='package')
Package.unapplied_changes = \
    relationship(DependencyChange,
                 primaryjoin=((DependencyChange.package_id == Package.id)
                              & (DependencyChange.applied_in_id == None)),
                 order_by=DependencyChange.distance)
Build.dependency_changes = relationship(DependencyChange, backref='applied_in',
                                        order_by=DependencyChange.distance
                                        .nullslast())

PackageGroup.package_count = column_property(
    select([func.count(PackageGroupRelation.group_id)],
           PackageGroupRelation.group_id == PackageGroup.id)
    .correlate(PackageGroup).as_scalar(),
    deferred=True)
# pylint: disable=E1101
Package.groups = relationship(PackageGroup,
                              secondary=PackageGroupRelation.__table__,
                              order_by=PackageGroup.name)
def _last_build():
    max_expr = select([func.max(Build.task_id).label('mx')])\
               .group_by(Build.package_id).alias()
    joined = select([Build]).select_from(join(Build, max_expr,
                                              Build.task_id == max_expr.c.mx))\
             .alias()
    return relationship(mapper(Build, joined, non_primary=True), uselist=False,
                        primaryjoin=(Package.id == joined.c.package_id))
Package.last_build = _last_build()
