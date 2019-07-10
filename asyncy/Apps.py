# -*- coding: utf-8 -*-
import asyncio
import os
import select
import signal
import threading

import psycopg2

from .App import App, AppData
from .AppConfig import AppConfig, KEY_EXPOSE
from .Config import Config
from .Containers import Containers
from .DeploymentLock import DeploymentLock
from .Exceptions import TooManyActiveApps, TooManyServices, \
    TooManyVolumes
from .GraphQLAPI import GraphQLAPI
from .Logger import Logger
from .constants.ServiceConstants import ServiceConstants
from .db.Database import Database
from .entities.Release import Release
from .enums.ReleaseState import ReleaseState
from .reporting.Reporter import Reporter
from .reporting.ReportingAgent import ReportingAgentOptions
from .utils.Dict import Dict

MAX_VOLUMES_BETA = 15
MAX_SERVICES_BETA = 15
MAX_ACTIVE_APPS = 5
DEPLOYMENT_BATCH_SIZE = 100


class Apps:
    """
    Globals used: glogger - the global logger (used for the engine)
    """
    internal_services = ['http', 'log', 'crontab', 'file', 'event']

    deployment_lock = DeploymentLock()

    apps = {}
    """
    Keeps a reference to all apps. Keyed by their app_id,
    with their value being asyncy.App
    """

    @classmethod
    def get_app_config(cls, raw):
        return AppConfig(raw)

    @classmethod
    async def deploy_release(cls, config: Config, release: Release):
        app_id = release.app_uuid
        stories = release.stories
        version = release.version

        logger = cls.make_logger_for_app(config, app_id, version)

        logger.log('app-deploying', app_id, version,
                   ReportingAgentOptions(
                       app_name=release.app_name,
                       app_uuid=app_id,
                       app_version=version,
                       agent_config={
                           'clever_ident': release.owner_email,
                           'clever_event': 'App Deploy Initiated'
                       },
                       allow_user_events=True
                   ))

        if release.maintenance:
            logger.warn(f'Not updating deployment, app put in maintenance'
                        f'({app_id}@{release.version})')
            return

        if release.deleted:
            Database.update_release_state(logger, config, app_id,
                                          release.version,
                                          ReleaseState.NO_DEPLOY)
            logger.warn(f'Deployment halted {app_id}@{version}; '
                        f'deleted={release.deleted}; '
                        f'maintenance={release.maintenance}')
            logger.warn(f'State changed to NO_DEPLOY for {app_id}@'
                        f'{version}')
            return

        Database.update_release_state(logger, config, app_id,
                                      release.version,
                                      ReleaseState.DEPLOYING)

        try:
            if release.environment is not None:
                # allow user based reporting in the engine. This makes it
                # possible for users to retrieve deploy errors via slack
                if 'REPORTING_SLACK_WEBHOOK' in release.environment and \
                        config.USER_REPORTING_ENABLED is not False:
                    Reporter.init_app_agents(app_id, {
                        'slack': {
                            'webhook': release.environment[
                                'REPORTING_SLACK_WEBHOOK'
                            ]
                        }
                    })

            # Check for the currently active apps by the same owner.
            # Note: This is a super inefficient method, but is OK
            # since it'll last only during beta.
            active_apps = 0
            for app in cls.apps.values():
                if app is not None and app.owner_uuid == release.owner_uuid:
                    active_apps += 1

            if active_apps >= MAX_ACTIVE_APPS:
                raise TooManyActiveApps(active_apps, MAX_ACTIVE_APPS)

            services_count = len(stories.get('services', []))
            if services_count > MAX_SERVICES_BETA:
                raise TooManyServices(services_count, MAX_SERVICES_BETA)

            services = await cls.get_services(
                stories.get('yaml', {}), logger, stories)

            volume_count = 0
            for service in services.keys():
                omg = services[service][ServiceConstants.config]
                volume_count += len(omg.get('volumes', {}).keys())

            if volume_count > MAX_VOLUMES_BETA:
                raise TooManyVolumes(volume_count, MAX_VOLUMES_BETA)

            app_config = cls.get_app_config(raw=stories.get('yaml', {}))

            app = App(
                app_data=AppData(
                    app_config=app_config,
                    config=config,
                    logger=logger,
                    services=services,
                    release=release
                )
            )

            await Containers.clean_app(app)
            await Containers.init(app)
            await app.bootstrap()

            cls.apps[app_id] = app
            Database.update_release_state(logger, config, app_id,
                                          version,
                                          ReleaseState.DEPLOYED)

            logger.log('app-deployed', app_id, version,
                       ReportingAgentOptions(
                           app_name=release.app_name,
                           app_uuid=app_id,
                           app_version=version,
                           agent_config={
                               'clever_ident': release.owner_email,
                               'clever_event': 'App Deployed'
                           },
                           allow_user_events=True
                       ))

        except BaseException as e:
            Database.update_release_state(
                logger, config,
                app_id, release.version,
                ReleaseState.FAILED
            )
            logger.error(f'Failed to bootstrap app ({e})', exc=e,
                         reporting_agent_options=ReportingAgentOptions(
                             app_name=release.app_name,
                             app_uuid=app_id,
                             app_version=release.version,
                             agent_config={
                                 'clever_ident': release.owner_email,
                                 'clever_event': 'App Deploy Failed'
                             },
                             allow_user_events=True
                         ))

    @classmethod
    def make_logger_for_app(cls, config, app_id, version):
        logger = Logger(config)
        logger.start()
        logger.adapt(app_id, version)
        return logger

    @classmethod
    async def reload_apps(cls, config, glogger):
        """
        Split apps in batches, where all apps in a batch
        are deployed together in parallel
        and subsequent batches are deployed sequentially
        """
        apps = Database.get_all_app_uuids_for_deployment(config)
        for i in range(0, len(apps), DEPLOYMENT_BATCH_SIZE):
            current_batch = apps[i: i + DEPLOYMENT_BATCH_SIZE]
            await asyncio.gather(*[
                cls.reload_app(config, glogger, app['uuid'])
                for app in current_batch
            ])

    @classmethod
    async def init_all(cls, config: Config, glogger: Logger):
        # We must start listening for releases straight away,
        # before an app is even deployed.
        # If we start listening after all the apps are deployed,
        # then we might miss some notifications about releases.
        loop = asyncio.get_event_loop()
        release_listener = threading.Thread(target=cls.listen_to_releases,
                                            args=[config, glogger, loop],
                                            daemon=True)
        release_listener.start()

        await cls.reload_apps(config, glogger)

    @classmethod
    def get(cls, app_id: str):
        return cls.apps[app_id]

    @classmethod
    async def get_services(cls, asyncy_yaml, glogger: Logger,
                           stories: dict):
        services = {}
        all_services = stories.get('services', [])

        expose = asyncy_yaml.get(KEY_EXPOSE, {})
        for expose_conf in expose:
            all_services.append(expose_conf['service'])

        for service in all_services:
            conf = Dict.find(asyncy_yaml, f'services.{service}', {})
            # query the Hub for the OMG
            tag = conf.get('tag', 'latest')

            if '/' in service:
                pull_url, omg = await GraphQLAPI.get_by_slug(
                    glogger, service, tag)
            else:
                pull_url, omg = await GraphQLAPI.get_by_alias(
                    glogger, service, tag)

            if conf.get('image') is not None:
                image = f'{conf.get("image")}:{tag}'
            else:
                image = f'{pull_url}:{tag}'

            omg['image'] = image

            services[service] = {
                'tag': tag,
                'configuration': omg
            }

        return services

    @classmethod
    async def destroy_app(cls, app: App, silent=False,
                          update_db_state=False):
        app.logger.info(f'Destroying app {app.app_id}')
        try:
            if update_db_state:
                Database.update_release_state(app.logger, app.config,
                                              app.app_id, app.version,
                                              ReleaseState.TERMINATING)

            await app.destroy()

            await Containers.clean_app(app)
        except BaseException as e:
            if not silent:
                raise e
            app.logger.error(
                f'Failed to destroy app {app.app_id}@{app.version}; '
                f'will eat exception (silent=True)',
                exc=e)
        finally:
            if update_db_state:
                Database.update_release_state(app.logger, app.config,
                                              app.app_id, app.version,
                                              ReleaseState.TERMINATED)

        app.logger.log('app-destroyed', app.app_id)
        cls.apps[app.app_id] = None

    @classmethod
    async def reload_app(cls, config: Config, glogger: Logger, app_id: str):
        glogger.info(f'Reloading app {app_id}')
        if cls.apps.get(app_id) is not None:
            await cls.destroy_app(cls.apps[app_id], silent=True,
                                  update_db_state=True)

        can_deploy = False
        release = None
        try:
            can_deploy = await cls.deployment_lock.try_acquire(app_id)
            if not can_deploy:
                glogger.warn(f'Another deployment for app {app_id} is in '
                             f'progress. Will not reload.')
                return
            release = Database.get_release_for_deployment(config, app_id)
            if release.state == ReleaseState.FAILED.value:
                glogger.warn(f'Cowardly refusing to deploy app '
                             f'{app_id}@{release.version} as it\'s '
                             f'last state is FAILED')
                return

            if release.stories is None:
                glogger.info(f'No story found for deployment for '
                             f'app {app_id}@{release.version}. '
                             f'Halting deployment.')
                return
            await asyncio.wait_for(
                cls.deploy_release(
                    config=config,
                    release=release
                ),
                timeout=5 * 60)
            glogger.info(f'Reloaded app {app_id}@{release.version}')
        except BaseException as e:
            reporting_options = None
            if release is not None:
                reporting_options = ReportingAgentOptions(
                    app_uuid=release.app_uuid,
                    app_name=release.app_name,
                    app_version=release.version,
                    agent_config={
                        'clever_ident': release.owner_email,
                        'clever_event': 'App Reload Failed'
                    }
                )
                if isinstance(e, asyncio.TimeoutError):
                    logger = cls.make_logger_for_app(config, app_id,
                                                     release.version)
                    Database.update_release_state(logger, config, app_id,
                                                  release.version,
                                                  ReleaseState.TIMED_OUT)

            glogger.error(
                f'Failed to reload app {app_id}', exc=e,
                reporting_agent_options=reporting_options
            )
        finally:
            if can_deploy:
                # If we did acquire the lock, then we must release it.
                await cls.deployment_lock.release(app_id)

    @classmethod
    async def destroy_all(cls):
        copy = cls.apps.copy()
        for app in copy.values():
            try:
                await cls.destroy_app(app)
            except BaseException as e:
                app.logger.error(
                    f'Failed to destroy app ({e})',
                    exc=e,
                    reporting_agent_options=ReportingAgentOptions(
                        app_name=app.app_name,
                        app_uuid=app.app_id,
                        app_version=app.version
                    )
                )

    @classmethod
    def listen_to_releases(cls, config: Config, glogger: Logger, loop):
        glogger.info('Listening for new releases...')
        conn = Database.new_pg_conn(config)
        conn.set_isolation_level(
            psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)

        cur = conn.cursor()
        cur.execute('listen release;')

        while True:
            try:
                if select.select([conn], [], [], 5) == ([], [], []):
                    continue
                else:
                    conn.poll()
                    while conn.notifies:
                        notify = conn.notifies.pop(0)
                        asyncio.run_coroutine_threadsafe(
                            cls.reload_app(config, glogger, notify.payload),
                            loop)
            except (psycopg2.InterfaceError, psycopg2.OperationalError):
                glogger.error('Connection to the DB has failed. Exiting.')
                # Because _thread.interrupt_main() doesn't work.
                os.kill(os.getpid(), signal.SIGINT)
                break
