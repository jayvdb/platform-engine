# -*- coding: utf-8 -*-
import asyncio
import os
import select
import signal
from threading import Thread
from unittest import mock

from asyncy.App import App, AppData
from asyncy.AppConfig import AppConfig
from asyncy.Apps import Apps
from asyncy.Containers import Containers
from asyncy.Exceptions import StoryscriptError, TooManyActiveApps, \
    TooManyServices, TooManyVolumes
from asyncy.GraphQLAPI import GraphQLAPI
from asyncy.Kubernetes import Kubernetes
from asyncy.Logger import Logger
from asyncy.constants.ServiceConstants import ServiceConstants
from asyncy.db.Database import Database
from asyncy.entities.Release import Release
from asyncy.enums.ReleaseState import ReleaseState
from asyncy.reporting.Reporter import Reporter
from asyncy.reporting.ReportingAgent import ReportingAgentOptions

import psycopg2

import pytest
from pytest import fixture, mark


def exc():
    def foo(*args, **kwargs):
        raise Exception()

    return foo


def asyncio_timeout_exc():
    def foo(*args, **kwargs):
        raise asyncio.TimeoutError()

    return foo


def asyncy_exc():
    def foo(*args, **kwargs):
        raise StoryscriptError()

    return foo


@fixture
def db(patch, magic):
    def get():
        db = magic()
        patch.object(psycopg2, 'connect', return_value=db)
        return db

    return get


def test_listen_to_releases(patch, db, magic, config, logger):
    conn = db()

    def exc(*args, **kwargs):
        raise psycopg2.InterfaceError()

    patch.object(asyncio, 'run_coroutine_threadsafe', side_effect=exc)
    patch.object(select, 'select', side_effect=[([], [], []), ([], [], []),
                                                False])
    patch.object(Apps, 'reload_app')
    patch.many(os, ['kill', 'getpid'])
    notif = magic()
    notif.payload = 'app_id'
    conn.notifies = [notif]
    loop = magic()

    Apps.listen_to_releases(config, logger, loop)

    conn.cursor().execute.assert_called_with('listen release;')
    Apps.reload_app.assert_called_with(config, logger, 'app_id')
    conn.set_isolation_level.assert_called_with(
        psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    asyncio.run_coroutine_threadsafe.assert_called_with(
        Apps.reload_app(), loop)

    os.kill.assert_called_with(os.getpid.return_value, signal.SIGINT)


@mark.asyncio
async def test_destroy_all(patch, async_mock, magic):
    patch.object(Containers, 'clean_app', new=async_mock())
    app = magic()
    app.destroy = async_mock()
    Apps.apps = {'app_id': app}
    app.app_id = 'app_id'
    await Apps.destroy_all()
    app.destroy.mock.assert_called()
    assert Apps.apps['app_id'] is None
    Containers.clean_app.mock.assert_called()


@mark.asyncio
async def test_destroy_all_exc(magic):
    app = magic()

    err = BaseException()

    async def exc():
        raise err

    app.destroy = exc
    Apps.apps = {'app_id': app}
    app.app_id = 'app_id'
    app.app_name = 'app_name'
    app.logger = magic()
    await Apps.destroy_all()

    app.logger.error.assert_called()


@mark.asyncio
async def test_init_all(patch, magic, async_mock, config, logger, db):
    db()
    patch.init(Thread)
    patch.object(Thread, 'start')

    apps = [{
        'uuid': 'my_app_uuid'
    }]
    patch.object(Database, 'get_all_app_uuids_for_deployment',
                 return_value=apps)
    patch.object(Apps, 'reload_app', new=async_mock())

    await Apps.init_all(config, logger)
    Apps.reload_app.mock.assert_called_with(
        config, logger, 'my_app_uuid')

    loop = asyncio.get_event_loop()
    Thread.__init__.assert_called_with(target=Apps.listen_to_releases,
                                       args=[config, logger, loop],
                                       daemon=True)
    Thread.start.assert_called()


def test_get(magic):
    app = magic()
    Apps.apps['app_id'] = app
    assert Apps.get('app_id') == app


@mark.asyncio
async def test_reload_app_ongoing_deployment(config, logger, patch):
    app_id = 'my_app'
    patch.object(Database, 'new_pg_conn')

    await Apps.deployment_lock.try_acquire(app_id)

    await Apps.reload_app(config, logger, app_id)

    logger.warn.assert_called()
    Database.new_pg_conn.assert_not_called()


@mark.asyncio
async def test_reload_app_no_story(patch, config, logger, db, async_mock):
    app_id = 'app_id'
    app_name = 'app_name'

    patch.object(Apps, 'destroy_app', new=async_mock())
    patch.object(Apps, 'deploy_release', new=async_mock())

    release = Release(
        app_uuid=app_id,
        app_name=app_name,
        version=1,
        environment={},
        stories=None,
        maintenance=False,
        always_pull_images=False,
        app_dns='app_dns',
        state='QUEUED',
        deleted=True,
        owner_uuid='owner_uuid',
        owner_email='owner_email'
    )
    patch.object(Database, 'get_release_for_deployment', return_value=release)

    await Apps.reload_app(config, logger, app_id)

    Apps.deploy_release.mock.assert_not_called()


@mark.parametrize('raise_exc', [None, exc, asyncio_timeout_exc])
@mark.parametrize('previous_state', ['QUEUED', 'FAILED'])
@mark.asyncio
async def test_reload_app(patch, config, logger, async_mock,
                          magic, raise_exc, previous_state):
    old_app = magic()
    app_id = 'app_id'
    app_name = 'app_name'
    app_dns = 'app_dns'
    Apps.apps = {app_id: old_app}
    app_logger = magic()
    patch.object(Apps, 'make_logger_for_app', return_value=app_logger)
    patch.object(Database, 'update_release_state')

    patch.object(Apps, 'destroy_app', new=async_mock())
    if raise_exc:
        patch.object(Apps, 'deploy_release',
                     new=async_mock(side_effect=raise_exc()))
    else:
        patch.object(Apps, 'deploy_release', new=async_mock())

    release = Release(
        app_uuid=app_id,
        app_name=app_name,
        version=1,
        environment={},
        stories={},
        maintenance=False,
        always_pull_images=False,
        app_dns=app_dns,
        state=previous_state,
        deleted=True,
        owner_uuid='owner_uuid',
        owner_email='example@example.com'
    )
    patch.object(Database, 'get_release_for_deployment', return_value=release)

    await Apps.reload_app(config, logger, app_id)

    Apps.destroy_app.mock.assert_called_with(old_app, silent=True,
                                             update_db_state=True)
    if previous_state == 'FAILED':
        Apps.deploy_release.mock.assert_not_called()
        logger.warn.assert_called()
        logger.error.assert_not_called()
        return

    Apps.deploy_release.mock.assert_called_with(
        config=config, release=release
    )

    if raise_exc:
        logger.error.assert_called()
    else:
        logger.error.assert_not_called()

    if raise_exc == asyncio_timeout_exc:
        Database.update_release_state.assert_called_with(
            app_logger, config, 'app_id', 1, ReleaseState.TIMED_OUT)


@mark.asyncio
async def test_deploy_release_many_services(patch):
    patch.object(Apps, 'make_logger_for_app')
    patch.object(Database, 'update_release_state')
    patch.init(TooManyServices)
    patch.object(TooManyServices, '__str__', return_value='too_many_services')

    stories = {'services': {}}

    for i in range(20):
        stories['services'][f'service_{i}'] = {}

    await Apps.deploy_release(
        config={},
        release=Release(
            app_uuid='app_id',
            app_name='app_name',
            version='app_version',
            environment={},
            stories=stories,
            maintenance=False,
            always_pull_images=False,
            app_dns='app_dns',
            state='QUEUED',
            deleted=False,
            owner_uuid='owner_uuid',
            owner_email='example@example.com'
        )
    )

    TooManyServices.__init__.assert_called_with(20, 15)
    Database.update_release_state.assert_called()


@mark.asyncio
async def test_deploy_release_many_apps(patch, magic):
    patch.object(Apps, 'make_logger_for_app')
    patch.object(Database, 'update_release_state')
    patch.init(TooManyActiveApps)
    patch.object(TooManyActiveApps, '__str__', return_value='too_many')

    stories = {'services': {}}
    Apps.apps = {}

    try:
        for i in range(20):
            Apps.apps[f'app_{i}'] = magic()
            Apps.apps[f'app_{i}'].owner_uuid = 'owner_uuid'
            stories['services'][f'service_{i}'] = {}

        await Apps.deploy_release(config={}, release=Release(
            app_uuid='app_id',
            app_name='app_name',
            version='app_version',
            environment={},
            stories=stories,
            maintenance=False,
            always_pull_images=False,
            app_dns='app_dns',
            state='QUEUED',
            deleted=False,
            owner_uuid='owner_uuid',
            owner_email='example@example.com'
        ))

        TooManyActiveApps.__init__.assert_called_with(20, 5)
        Database.update_release_state.assert_called()
    finally:
        Apps.apps = {}  # Cleanup.


def test_get_app_config(patch):
    patch.init(AppConfig)
    raw = {'raw': 1}
    ret = Apps.get_app_config(raw)
    assert isinstance(ret, AppConfig)
    AppConfig.__init__.assert_called_with(raw)


@mark.asyncio
async def test_deploy_release_many_volumes(patch, async_mock):
    patch.object(Apps, 'make_logger_for_app')
    patch.object(Database, 'update_release_state')
    patch.init(TooManyVolumes)
    patch.object(TooManyVolumes, '__str__', return_value='too_many_vols')

    stories = {'services': {}}

    for i in range(10):
        stories['services'][f'service_{i}'] = {
            ServiceConstants.config: {
                'volumes': {
                    'a': {},
                    'b': {}
                }
            }
        }

    patch.object(Apps, 'get_services',
                 new=async_mock(return_value=stories['services']))
    await Apps.deploy_release(
        config={},
        release=Release(
            app_uuid='app_id',
            app_name='app_name',
            version='app_version',
            environment={},
            stories=stories,
            maintenance=False,
            always_pull_images=False,
            app_dns='app_dns',
            state='QUEUED',
            deleted=False,
            owner_uuid='owner_uuid',
            owner_email='owner_email'
        )
    )

    TooManyVolumes.__init__.assert_called_with(20, 15)
    Database.update_release_state.assert_called()


@mark.parametrize('raise_exc', [None, exc, asyncy_exc])
@mark.parametrize('maintenance', [True, False])
@mark.parametrize('always_pull_images', [True, False])
@mark.parametrize('deleted', [True, False])
@mark.parametrize(
    'environment',
    [
        None,
        {
            'REPORTING_SLACK_WEBHOOK': 'http://example.com/'
        }
    ]
)
@mark.asyncio
async def test_deploy_release(config, magic, patch, deleted,
                              environment, always_pull_images,
                              async_mock, raise_exc, maintenance):
    patch.object(Reporter, 'capture_exc')
    patch.object(Reporter, 'init_app_agents')
    patch.object(Kubernetes, 'clean_namespace', new=async_mock())
    patch.object(Containers, 'init', new=async_mock())
    patch.object(Database, 'update_release_state')
    app_logger = magic()
    patch.object(Apps, 'make_logger_for_app', return_value=app_logger)
    Apps.apps = {}
    services = magic()

    app_config = magic()
    patch.object(Apps, 'get_app_config', return_value=app_config)

    patch.object(Apps, 'get_services', new=async_mock(return_value=services))
    patch.init(App)
    if raise_exc is not None:
        patch.object(App, 'bootstrap', new=async_mock(side_effect=raise_exc()))
    else:
        patch.object(App, 'bootstrap', new=async_mock())

    release = Release(
        app_uuid='app_id',
        app_name='app_name',
        version='version',
        environment=environment,
        stories={'stories': True},
        maintenance=maintenance,
        always_pull_images=always_pull_images,
        app_dns='app_dns',
        state='QUEUED',
        deleted=deleted,
        owner_uuid='owner_uuid',
        owner_email='owner_email'
    )

    await Apps.deploy_release(
        config=config, release=release
    )

    if maintenance:
        assert Database.update_release_state.call_count == 0
        app_logger.warn.assert_called()
    elif deleted:
        app_logger.warn.assert_called()
        Database.update_release_state.assert_called_with(
            app_logger, config, 'app_id', 'version', ReleaseState.NO_DEPLOY)
    else:
        assert Database.update_release_state.mock_calls[0] == mock.call(
            app_logger, config, 'app_id', 'version', ReleaseState.DEPLOYING)

        App.__init__.assert_called_with(app_data=AppData(
            release=release,
            config=config,
            logger=app_logger,
            services=services,
            app_config=app_config
        ))

        App.bootstrap.mock.assert_called()
        Containers.init.mock.assert_called()

        if environment is not None and \
                'REPORTING_SLACK_WEBHOOK' in environment:
            Reporter.init_app_agents.assert_called_with('app_id', {
                'slack': {
                    'webhook': environment['REPORTING_SLACK_WEBHOOK']
                }
            })

        if raise_exc is not None:
            assert Apps.apps.get('app_id') is None
            if raise_exc == exc:
                app_logger.error.assert_called()
            assert Database.update_release_state.mock_calls[1] == mock.call(
                app_logger, config, 'app_id', 'version', ReleaseState.FAILED)
        else:
            assert Database.update_release_state.mock_calls[1] == mock.call(
                app_logger, config, 'app_id', 'version', ReleaseState.DEPLOYED)
            assert Apps.apps.get('app_id') is not None


def test_make_logger_for_app(patch, config):
    patch.many(Logger, ['start', 'adapt'])
    logger = Apps.make_logger_for_app(config, 'my_awesome_app', '17.1')
    logger.start.assert_called()
    logger.adapt.assert_called_with('my_awesome_app', '17.1')


@mark.asyncio
async def test_get_services(patch, logger, async_mock):
    patch.object(GraphQLAPI, 'get_by_slug',
                 new=async_mock(return_value=('slug_pull', {'slug': True})))
    patch.object(GraphQLAPI, 'get_by_alias',
                 new=async_mock(return_value=('alias_pull', {'alias': True})))

    asyncy_yaml = {
        'services': {
            'microservice/slack':
                {'image': 'microservice/slack', 'tag': 'v1'}
        },
        'expose': [
            {
                'service': 'naked_service'
            }
        ]
    }
    stories = {
        'services': ['microservice/slack', 'http', 'lastfm']
    }
    ret = await Apps.get_services(asyncy_yaml, logger, stories)
    assert ret == {
        'microservice/slack': {
            'tag': 'v1',
            'configuration': {
                'slug': True,
                'image': 'microservice/slack:v1'
            }
        },
        'lastfm': {
            'tag': 'latest',
            'configuration': {
                'alias': True,
                'image': 'alias_pull:latest'
            }
        },
        'http': {
            'configuration': {
                'alias': True,
                'image': 'alias_pull:latest'
            },
            'tag': 'latest'
        },
        'naked_service': {
            'configuration': {
                'alias': True,
                'image': 'alias_pull:latest'
            },
            'tag': 'latest'
        }
    }


@mark.parametrize('silent', [False, True])
@mark.parametrize('update_db', [False, True])
@mark.asyncio
async def test_destroy_app_exc(patch, async_mock, magic,
                               silent, update_db):
    app = magic()
    app.destroy = async_mock(side_effect=exc())
    patch.object(Database, 'update_release_state')

    if silent:
        await Apps.destroy_app(app, silent, update_db_state=update_db)
    else:
        with pytest.raises(Exception):
            await Apps.destroy_app(app, silent, update_db_state=update_db)

    if update_db:
        assert Database.update_release_state.mock_calls == [
            mock.call(app.logger, app.config, app.app_id, app.version,
                      ReleaseState.TERMINATING),
            mock.call(app.logger, app.config, app.app_id, app.version,
                      ReleaseState.TERMINATED)
        ]

    app.destroy.mock.assert_called()
