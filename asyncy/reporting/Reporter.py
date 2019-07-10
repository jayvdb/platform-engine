# -*- coding: utf-8 -*-
import asyncio
from collections import namedtuple

from .ReportingAgent import ReportingAgent, ReportingAgentOptions
from .agents.CleverTapAgent import CleverTapAgent
from .agents.SentryAgent import SentryAgent
from .agents.SlackAgent import SlackAgent
from ..Config import Config
from ..Exceptions import StoryscriptError
from ..Logger import Logger

RegisteredAgent = namedtuple('RegisteredAgent', {
    'id': str,
    'name': str,
    'agent': ReportingAgent,
    'exceptions': True,
    'events': True,
    'messages': True
})


class Reporter:
    _config = None
    _release = None
    _logger = None

    # agents
    _sentry_agent = None
    _slack_agent = None
    _clever_agent = None

    _registered_agents = {}

    _app_agents = {}

    @classmethod
    def init(cls, config: dict, release: str):
        cls._config = config
        cls._release = release

        # ensure that reporting is never utilized on this logger,
        # otherwise it will cause errors
        cls._logger = Logger(config=Config(), reporting_enabled=False)
        cls._logger.start()

        default_slack_config = config.get('slack', {
            'webhook': None
        })
        if 'slack' in config and \
                default_slack_config.get('webhook', None) is not None:
            cls._registered_agents['slack'] = (RegisteredAgent(
                id='slack',
                name='SlackAgent',
                agent=SlackAgent(
                    webhook=config['slack']['webhook'],
                    release=release,
                    logger=cls._logger
                ),
                exceptions=True,
                events=True,
                messages=True
            ))

        default_sentry_config = config.get('sentry', {
            'dsn': None
        })

        if 'sentry' in config and \
                default_sentry_config.get('dsn', None) is not None:
            cls._registered_agents['sentry'] = (RegisteredAgent(
                id='sentry',
                name='SentryAgent',
                agent=SentryAgent(
                    dsn=config['sentry']['dsn'],
                    release=release,
                    logger=cls._logger
                ),
                exceptions=True,
                events=False,
                messages=False
            ))

        default_clever_config = config.get('clevertap', {
            'account': None,
            'pass': None
        })
        if 'clevertap' in config and \
                default_clever_config.get('account') is not None and \
                default_clever_config.get('pass', None) is not None:
            cls._registered_agents['clevertap'] = (RegisteredAgent(
                id='clevertap',
                name='CleverTapAgent',
                agent=CleverTapAgent(
                    account_id=default_clever_config['account'],
                    account_pass=default_clever_config['pass'],
                    release=release,
                    logger=cls._logger
                ),
                exceptions=True,
                events=True,
                messages=False
            ))

    @classmethod
    def init_app_agents(cls, app_uuid: str, config: dict = {}):
        """
        Allows you to define agent configurations for a specific
        app. This makes it easy to enable custom reporting routes
        for Storyscript users.

        :param app_uuid: the app identifier you wish to define a config for
        :param config: the configuration for the built-in reporting agents.
        :return:
        """
        cls._app_agents[app_uuid] = {
            'slack': config.get('slack', {
                'webhook': None
            })
        }
        return

    @classmethod
    def app_agents(cls, app_uuid: str) -> dict:
        """
        Retrieve any reporting agent configurations based on the
        app identifier.

        :param app_uuid: the app identifier
        :return: returns a dictionary with the app defined configuration
        """
        return cls._app_agents.get(app_uuid, None)

    @classmethod
    def get_agent(cls, agent_id: str):
        """
        This allows you to retrieve a reporting agent based on it's
        id. eg. "slack" will return the SlackAgent.

        :param agent_id:
        :return:
        """
        registered_agent = cls._registered_agents.get(agent_id, None)
        if registered_agent is not None:
            return registered_agent.agent

        return None

    @classmethod
    def get_agent_ids(cls):
        """
        Returns a list of registered agent ids.

        :return:
        """
        return list(cls._registered_agents.keys())

    @classmethod
    def capture_evt(cls, evt_name: str, evt_data: dict,
                    agent_options: ReportingAgentOptions = None):
        """
        This allows you to capture a generic reporting event.

        :param evt_name: the event name
        :param evt_data: the data you wish to send to the reporting agents
        :param agent_options:
        :return:
        """

        task = cls._capture_evt(
            evt_name=evt_name,
            evt_data=evt_data,
            agent_options=agent_options
        )
        asyncio.get_event_loop().create_task(task)

    @classmethod
    async def _capture_evt(cls, evt_name: str, evt_data: dict,
                           agent_options: ReportingAgentOptions = None):
        if cls._registered_agents is None or \
                len(cls._registered_agents) == 0:
            return

        logger = cls._logger

        app_name = None
        app_uuid = None
        app_version = None
        story_name = None
        story_line = None

        agent_config = None
        suppress_agents = []

        if agent_options is not None:
            if agent_options.story_name is not None:
                story_name = agent_options.story_name

            if agent_options.story_line is not None:
                story_line = agent_options.story_line

            if agent_options.app_name is not None:
                app_name = agent_options.app_name

            if agent_options.app_uuid is not None:
                app_uuid = agent_options.app_uuid

            if agent_options.app_version is not None:
                app_version = agent_options.app_version

            if agent_options.agent_config is not None:
                agent_config = agent_options.agent_config

            suppress_agents = agent_options.suppress_agents

        _evt_data = {
            'platform_release': cls._release,
            'event_data': evt_data
        }

        if story_name is not None:
            _evt_data['story_name'] = story_name

        if story_line is not None:
            _evt_data['story_line'] = story_line

        if app_name is not None:
            _evt_data['app_name'] = app_name

        if app_uuid is not None:
            _evt_data['app_uuid'] = app_uuid

        if app_version is not None:
            _evt_data['app_version'] = app_version

        if story_name is not None:
            _evt_data['story_name'] = story_name

        for agent_id, agent_data in cls._registered_agents.items():
            if agent_data.events is False or \
                    agent_id in suppress_agents:
                continue

            if agent_data.agent is not None:
                try:
                    await agent_data.agent.publish_evt(
                        evt_name=evt_name,
                        evt_data=_evt_data,
                        agent_config=agent_config
                    )
                except Exception as e:
                    logger.error(
                        message=f'Unhandled {agent_data.name} '
                        f'reporting agent error: '
                        f'{str(e)}', exc=e
                    )

        # this is disabled at the top level
        if cls._config.get('user_reporting', False) is False:
            return

        if agent_options.allow_user_events is True:
            if app_uuid is not None and \
                    app_uuid in cls._app_agents:
                app_agent_config = cls._app_agents[app_uuid]
                slack_agent = cls.get_agent('slack')

                if slack_agent is not None and \
                        'slack' in app_agent_config:
                    try:
                        user_agent_config = {}
                        user_webhook = app_agent_config['slack']. \
                            get('webhook', None)

                        if user_webhook is None:
                            return

                        user_agent_config['webhook'] = user_webhook

                        await slack_agent.publish_evt(
                            evt_name=evt_name,
                            evt_data=_evt_data,
                            agent_config=user_agent_config
                        )
                    except Exception as e:
                        logger.error(
                            f'Unhandled app reporting'
                            f' agent error: {str(e)}', e
                        )

    @classmethod
    def capture_msg(cls, message: str,
                    agent_options: ReportingAgentOptions = None):
        task = cls._capture_msg(
            message=message,
            agent_options=agent_options
        )
        asyncio.get_event_loop().create_task(task)

    @classmethod
    async def _capture_msg(cls, message: str,
                           agent_options: ReportingAgentOptions = None):
        if cls._registered_agents is None or \
                len(cls._registered_agents) == 0:
            return

        logger = cls._logger

        suppress_agents = []

        agent_config = None
        if agent_options is not None:
            suppress_agents = agent_options.suppress_agents
            agent_config = agent_options.agent_config

        for agent_id, agent_data in cls._registered_agents.items():
            if agent_data.messages is False or \
                    agent_id in suppress_agents:
                continue
            if agent_data.agent is not None:
                try:
                    await agent_data.agent.publish_msg(
                        message=message,
                        agent_config=agent_config
                    )
                except Exception as e:
                    logger.error(
                        message=f'Unhandled {agent_data.name} '
                        f'reporting agent error: '
                        f'{str(e)}', exc=e
                    )

    @classmethod
    def capture_exc(cls, exc_info: BaseException,
                    agent_options: ReportingAgentOptions = None):
        """
        This allows you to capture and publish an exception sending
        it off to any reporting agents.

        :param exc_info: the exception you wish to capture
        :param agent_options:
        :return:
        """
        task = cls._capture_exc(
            exc_info=exc_info,
            agent_options=agent_options
        )
        asyncio.get_event_loop().create_task(task)

    @classmethod
    async def _capture_exc(cls, exc_info: BaseException,
                           agent_options: ReportingAgentOptions = None):
        if cls._registered_agents is None or \
                len(cls._registered_agents) == 0:
            return

        logger = cls._logger

        app_name = None
        app_uuid = None
        app_version = None
        story_name = None
        story_line = None

        agent_config = None

        # we allow the agent options to define any event
        # information.
        if agent_options is not None:
            if agent_options.story_name is not None:
                story_name = agent_options.story_name

            if agent_options.story_line is not None:
                story_line = agent_options.story_line

            if agent_options.app_name is not None:
                app_name = agent_options.app_name

            if agent_options.app_uuid is not None:
                app_uuid = agent_options.app_uuid

            if agent_options.app_version is not None:
                app_version = agent_options.app_version

            if agent_options.agent_config is not None:
                agent_config = agent_options.agent_config

        if isinstance(exc_info, StoryscriptError) and \
                hasattr(exc_info, 'story') and \
                hasattr(exc_info, 'line'):
            story = exc_info.story

            if story.app.app_name is not None:
                app_name = story.app.app_name

            if story.app.app_id is not None:
                app_uuid = story.app.app_id

            if story.app.version is not None:
                app_version = story.app.version

            story_name = story.name

            if exc_info.line is not None:
                story_line = exc_info.line['ln']

        exc_data = {
            'platform_release': cls._release
        }

        if story_name is not None:
            exc_data['story_name'] = story_name

        if story_line is not None:
            exc_data['story_line'] = story_line

        if app_name is not None:
            exc_data['app_name'] = app_name

        if app_uuid is not None:
            exc_data['app_uuid'] = app_uuid

        if app_version is not None:
            exc_data['app_version'] = app_version

        for agent_id, agent_data in cls._registered_agents.items():
            if agent_data.exceptions is False:
                continue
            if agent_data.agent is not None:
                try:
                    await agent_data.agent.publish_exc(
                        exc_info=exc_info,
                        exc_data=exc_data,
                        agent_config=agent_config
                    )
                except Exception as e:
                    logger.error(
                        message=f'Unhandled {agent_data.name} '
                        f'reporting agent error: '
                        f'{str(e)}', exc=e
                    )

        # check this is disabled at the top level. if so we
        # won't report anything to agents with app based
        # configurations
        if cls._config.get('user_reporting', False) is False:
            return

        if agent_options.allow_user_events is True:
            if app_uuid is not None and \
                    app_uuid in cls._app_agents:
                app_agent_config = cls._app_agents[app_uuid]

                # currently slack is only allowed to push events/exceptions
                # for users.
                slack_agent = cls.get_agent('slack')

                if slack_agent is not None and \
                        'slack' in app_agent_config:
                    try:
                        user_agent_config = {}

                        user_webhook = app_agent_config['slack']. \
                            get('webhook', None)

                        if user_webhook is None:
                            return

                        user_agent_config['webhook'] = user_webhook

                        if 'full_stacktrace' in app_agent_config:
                            user_agent_config['full_stacktrace'] = \
                                app_agent_config['full_stacktrace']

                        if 'suppress_stacktrace' in app_agent_config:
                            user_agent_config['suppress_stacktrace'] = \
                                app_agent_config['suppress_stacktrace']

                        # only set defaults if they're not already defined
                        if 'suppress_stacktrace' not \
                                in user_agent_config and \
                                'full_stacktrace' not in user_agent_config:
                            # by default want to disable user reporting
                            if cls._config. \
                                    get('user_reporting_stacktrace',
                                        False) is False:
                                user_agent_config['full_stacktrace'] = False
                                user_agent_config['suppress_stacktrace'] = \
                                    True
                            else:
                                user_agent_config['full_stacktrace'] = True

                        await slack_agent.publish_exc(
                            exc_info=exc_info,
                            exc_data=exc_data,
                            agent_config=user_agent_config
                        )
                    except Exception as e:
                        logger.error(
                            f'Unhandled app reporting'
                            f' agent error: {str(e)}', e
                        )
