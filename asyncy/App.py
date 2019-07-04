# -*- coding: utf-8 -*-
import asyncio
import json
from collections import namedtuple

from requests.structures import CaseInsensitiveDict

from tornado.httpclient import AsyncHTTPClient

from .AppConfig import AppConfig, Expose
from .Config import Config
from .Containers import Containers
from .Exceptions import StoryscriptError
from .Logger import Logger
from .Stories import Stories
from .Types import StreamingService
from .constants.ServiceConstants import ServiceConstants
from .entities.Release import Release
from .processing import Story
from .processing.Services import Command, Service, Services
from .utils import Dict
from .utils.HttpUtils import HttpUtils

Subscription = namedtuple('Subscription',
                          ['streaming_service', 'id', 'payload', 'event'])

AppData = namedtuple('AppData', {
    'app_config': AppConfig,
    'config': Config,
    'logger': Logger,
    'services': dict,
    'release': Release
})


class App:
    app_config: AppConfig = None
    """
    The contents of asyncy.yaml.
    """

    config: Config = None
    """
    The runtime config for this app.
    """

    def __init__(self, app_data: AppData):
        self._subscriptions = {}
        release = app_data.release
        self.app_id = release.app_uuid
        self.app_name = release.app_name
        self.app_dns = release.app_dns
        self.config = app_data.config
        self.app_config = app_data.app_config
        self.version = release.version
        self.logger = app_data.logger
        self.owner_uuid = release.owner_uuid
        self.owner_email = release.owner_email
        self.environment = release.environment
        if release.environment is None:
            self.environment = {}
        else:
            self.environment = release.environment

        self.environment = CaseInsensitiveDict(data=self.environment)
        self.stories = release.stories['stories']
        self.entrypoint = release.stories['entrypoint']
        self.services = app_data.services
        self.always_pull_images = release.always_pull_images
        secrets = CaseInsensitiveDict()
        for k, v in self.environment.items():
            if not isinstance(v, dict):
                secrets[k] = v
        self.app_context = {
            'secrets': secrets,
            'hostname': f'{self.app_dns}.{self.config.APP_DOMAIN}',
            'version': self.version
        }

    def image_pull_policy(self):
        if self.always_pull_images is True:
            return 'Always'
        else:
            return 'IfNotPresent'

    async def bootstrap(self):
        """
        Executes all stories found in stories.json.
        This enables the story to listen to pub/sub,
        register with the gateway, and queue cron jobs.
        """
        await self.start_services()
        await self.expose_services()
        await self.run_stories()

    async def expose_services(self):
        for expose in self.app_config.get_expose_config():
            await self._expose_service(expose)

    async def _expose_service(self, e: Expose):
        self.logger.info(f'Exposing service {e.service}/'
                         f'{e.service_expose_name} '
                         f'on {e.http_path}')
        conf = Dict.find(self.services,
                         f'{e.service}'
                         f'.{ServiceConstants.config}'
                         f'.expose.{e.service_expose_name}')
        if conf is None:
            raise StoryscriptError(
                message=f'Configuration for expose "{e.service_expose_name}" '
                f'not found in service "{e.service}"')

        target_path = Dict.find(conf, 'http.path')
        target_port = Dict.find(conf, 'http.port')

        if target_path is None or target_port is None:
            raise StoryscriptError(
                message=f'http.path or http.port is null '
                f'for expose {e.service}/{e.service_expose_name}')

        await Containers.expose_service(self, e)

    async def start_services(self):
        tasks = []
        reusable_services = set()
        for story_name in self.stories.keys():
            story = Stories(self, story_name, self.logger)
            line = story.first_line()
            while line is not None:
                line = story.line(line)
                method = line['method']

                try:
                    if method != 'execute':
                        continue

                    chain = Services.resolve_chain(story, line)
                    assert isinstance(chain[0], Service)
                    assert isinstance(chain[1], Command)

                    if Containers.is_service_reusable(story.app, line):
                        # Simple cache to not unnecessarily make more calls to
                        # Kubernetes. It's okay if we don't have this check
                        # though, since the underlying API handles this.
                        service = chain[0].name
                        if service in reusable_services:
                            continue

                        reusable_services.add(service)

                    if not Services.is_internal(chain[0].name, chain[1].name):
                        tasks.append(Services.start_container(story, line))
                finally:
                    line = line.get('next')

        if len(tasks) > 0:
            completed, pending = await asyncio.wait(tasks)
            # Pending must never be greater than zero.
            assert len(pending) == 0

            for task in completed:
                exc = task.exception()
                if exc is not None:
                    raise exc

    async def run_stories(self):
        """
        Executes all the stories.
        This enables the story to listen to pub/sub,
        register with the gateway, and queue cron jobs.
        """
        for story_name in self.entrypoint:
            await Story.run(self, self.logger, story_name)

    def add_subscription(self, sub_id: str,
                         streaming_service: StreamingService,
                         event: str, payload: dict):
        self._subscriptions[sub_id] = Subscription(streaming_service,
                                                   sub_id, payload, event)

    def get_subscription(self, sub_id: str):
        return self._subscriptions.get(sub_id)

    def remove_subscription(self, sub_id: str):
        self._subscriptions.pop(sub_id)

    async def clear_subscriptions_synapse(self):
        url = f'http://{self.config.ASYNCY_SYNAPSE_HOST}:' \
              f'{self.config.ASYNCY_SYNAPSE_PORT}/clear_all'
        kwargs = {
            'method': 'POST',
            'body': json.dumps({
                'app_id': self.app_id
            }),
            'headers': {
                'Content-Type': 'application/json; charset=utf-8'
            }
        }
        client = AsyncHTTPClient()
        response = await HttpUtils.fetch_with_retry(3, self.logger, url,
                                                    client, kwargs)
        if int(response.code / 100) == 2:
            self.logger.debug(f'Unsubscribed all with Synapse!')
            return True
        else:
            self.logger.error(f'Failed to unsubscribe with Synapse!')
            return False

    async def unsubscribe_all(self):
        for sub_id, sub in self._subscriptions.items():
            assert isinstance(sub, Subscription)
            assert isinstance(sub.streaming_service, StreamingService)
            conf = Dict.find(
                self.services, f'{sub.streaming_service.name}'
                               f'.{ServiceConstants.config}'
                               f'.actions.{sub.streaming_service.command}'
                               f'.events.{sub.event}.http')

            http_conf = conf.get('unsubscribe')
            if not http_conf:
                self.logger.debug(f'No unsubscribe call required for {sub}')
                continue

            url = f'http://{sub.streaming_service.hostname}' \
                  f':{http_conf.get("port", conf.get("port", 80))}' \
                  f'{http_conf["path"]}'

            client = AsyncHTTPClient()
            self.logger.debug(f'Unsubscribing {sub}...')

            method = http_conf.get('method', 'post')

            kwargs = {
                'method': method.upper(),
                'body': json.dumps(sub.payload['sub_body']),
                'headers': {
                    'Content-Type': 'application/json; charset=utf-8'
                }
            }

            response = await HttpUtils.fetch_with_retry(3, self.logger, url,
                                                        client, kwargs)
            if int(response.code / 100) == 2:
                self.logger.debug(f'Unsubscribed!')
            else:
                self.logger.error(f'Failed to unsubscribe {sub}!')

    async def destroy(self):
        """
        Unsubscribe from all existing subscriptions,
        and delete the namespace.
        """
        await self.clear_subscriptions_synapse()
        await self.unsubscribe_all()
