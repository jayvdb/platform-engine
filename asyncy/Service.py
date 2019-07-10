# -*- coding: utf-8 -*-
import asyncio
import os
import signal
import sys

import click

import prometheus_client

import tornado
from tornado import web

from . import Version
from .Apps import Apps
from .Config import Config
from .Logger import Logger
from .http_handlers.StoryEventHandler import StoryEventHandler
from .processing.Services import Services
from .processing.internal import File, Http, Json, Log
from .reporting.Reporter import Reporter

_ONE_DAY_IN_SECONDS = 60 * 60 * 24

config = Config()
server = None
logger = Logger(config)
logger.start()
logger.adapt('engine', Version.version)


class Service:

    @click.group()
    def main():
        pass

    @staticmethod
    @main.command()
    @click.option('--port',
                  help='Set the port on which the HTTP server binds to',
                  default=os.getenv('PORT', '8084'))
    @click.option('--prometheus_port',
                  help='Set the port on which metrics are exposed',
                  default=os.getenv('METRICS_PORT', '8085'))
    @click.option('--sentry_dsn',
                  help='Sentry DNS for bug collection.',
                  default=os.getenv('SENTRY_DSN'))
    @click.option('--release',
                  help='The version being released (provide a Git commit ID)',
                  default=os.getenv('RELEASE_VER'))
    @click.option('--debug',
                  help='Sets the engine into debug mode',
                  default=False)
    def start(port, debug, sentry_dsn, release, prometheus_port):
        global server

        # allow the dsn to be set via the cli as a legacy option
        if sentry_dsn is not None:
            config.REPORTING_SENTRY_DSN = sentry_dsn

        Services.set_logger(logger)

        # initialize the engine's exception reporting system
        Reporter.init(config={
            'sentry': {
                'dsn': config.REPORTING_SENTRY_DSN,
            },
            'slack': {
                'webhook': config.REPORTING_SLACK_WEBHOOK
            },
            'clevertap': {
                'account': config.REPORTING_CLEVERTAP_ACCOUNT,
                'pass': config.REPORTING_CLEVERTAP_PASS
            },
            'user_reporting': config.USER_REPORTING_ENABLED,
            'user_reporting_stacktrace': config.USER_REPORTING_STACKTRACE
        }, release=release)

        # Init internal services.
        File.init()
        Log.init()
        Http.init()
        Json.init()
        Services.log_internal()

        logger.log('service-init', Version.version)
        signal.signal(signal.SIGTERM, Service.sig_handler)
        signal.signal(signal.SIGINT, Service.sig_handler)

        web_app = tornado.web.Application([
            (r'/story/event', StoryEventHandler, {'logger': logger})
        ], debug=debug)

        config.ENGINE_PORT = port

        server = tornado.httpserver.HTTPServer(web_app)
        server.listen(port)

        prometheus_client.start_http_server(port=int(prometheus_port))

        logger.log('http-init', port)

        loop = asyncio.get_event_loop()
        loop.create_task(Service.init_wrapper())

        tornado.ioloop.IOLoop.current().start()

        logger.info('Shutdown complete!')

    @staticmethod
    async def init_wrapper():
        try:
            await Apps.init_all(config, logger)
        except BaseException as e:
            logger.error(f'Failed to init apps!', exc=e)
            sys.exit(1)

    @staticmethod
    def sig_handler(*args, **kwargs):
        logger.info(f'Signal {args[0]} received.')
        tornado.ioloop.IOLoop.instance().add_callback(Service.shutdown)

    @classmethod
    async def shutdown_app(cls):
        logger.info('Unregistering with the gateway...')
        await Apps.destroy_all()  # All exceptions are handled inside.

        io_loop = tornado.ioloop.IOLoop.instance()
        io_loop.stop()
        loop = asyncio.get_event_loop()
        loop.stop()

    @classmethod
    def shutdown(cls):
        logger.info('Shutting down...')

        server.stop()
        loop = asyncio.get_event_loop()
        loop.create_task(cls.shutdown_app())
