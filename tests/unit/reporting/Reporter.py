# -*- coding: utf-8 -*-
from asyncy.Exceptions import StoryscriptError
from asyncy.reporting.Reporter import Reporter
from asyncy.reporting.ReportingAgent import ReportingAgentOptions
from asyncy.reporting.agents.CleverTapAgent import CleverTapAgent
from asyncy.reporting.agents.SentryAgent import SentryAgent
from asyncy.reporting.agents.SlackAgent import SlackAgent

from pytest import mark


def test_init(patch):
    patch.init(SentryAgent)
    patch.init(SlackAgent)
    patch.init(CleverTapAgent)

    Reporter.init({
        'sentry': {
            'dsn': 'sentry_dsn'
        },
        'slack': {
            'webhook': 'slack_webhook'
        },
        'clevertap': {
            'account': 'account',
            'pass': 'pass'
        },
        'user_reporting': False,
        'user_reporting_stacktrace': False
    }, 'release')

    logger = Reporter._logger

    SentryAgent.__init__.assert_called_with(
        dsn='sentry_dsn',
        release='release',
        logger=logger
    )

    SlackAgent.__init__.assert_called_with(
        webhook='slack_webhook',
        release='release',
        logger=logger
    )

    CleverTapAgent.__init__.assert_called_with(
        account_id='account',
        account_pass='pass',
        release='release',
        logger=logger
    )


@mark.asyncio
async def test_capture_msg(patch, async_mock):
    Reporter.init({
        'sentry': {
            'dsn': 'https://foo:foo@sentry.io/123'
        },
        'slack': {
            'webhook': 'slack_webhook'
        },
        'clevertap': {
            'account': 'account',
            'pass': 'pass'
        },
        'user_reporting': False,
        'user_reporting_stacktrace': False
    }, 'release')

    patch.object(SentryAgent, 'publish_msg',
                 new=async_mock())
    patch.object(SlackAgent, 'publish_msg',
                 new=async_mock())
    patch.object(CleverTapAgent, 'publish_msg',
                 new=async_mock())

    await Reporter._capture_msg(
        message='hello world',
        agent_options=ReportingAgentOptions(
            agent_config={'hello': 'world'}
        ))

    SentryAgent.publish_msg.mock.assert_not_called()
    CleverTapAgent.publish_msg.mock.assert_not_called()

    SlackAgent.publish_msg.mock.assert_called_with(
        Reporter.get_agent('slack'),
        message='hello world',
        agent_config={'hello': 'world'})


@mark.asyncio
async def test_capture_evt(patch, async_mock, magic):
    Reporter.init({
        'sentry': {
            'dsn': 'https://foo:foo@sentry.io/123'
        },
        'slack': {
            'webhook': 'slack_webhook'
        },
        'clevertap': {
            'account': 'account',
            'pass': 'pass'
        },
        'user_reporting': False,
        'user_reporting_stacktrace': False
    }, 'release')

    patch.object(SentryAgent, 'publish_evt',
                 new=async_mock())
    patch.object(SlackAgent, 'publish_evt',
                 new=async_mock())
    patch.object(CleverTapAgent, 'publish_evt',
                 new=async_mock())

    story = magic()
    story.app.app_name = 'app_name'
    story.app.app_id = 'user_app_id'
    story.app.version = 'app_version'
    story.name = 'story_name'
    line = '28'

    agent_config = {
        'clever_ident': 'foo@foo.com',
        'clever_event': 'Event'
    }

    evt_data = {
        'app_name': story.app.app_name,
        'app_uuid': story.app.app_id,
        'app_version': story.app.version,
        'event_data': {'my_event': 'my_event'},
        'platform_release': 'release',
        'story_line': line,
        'story_name': story.name
    }

    await Reporter._capture_evt(
        evt_name='my-event',
        evt_data={
            'my_event': 'my_event'
        },
        agent_options=ReportingAgentOptions(
            agent_config=agent_config,
            app_uuid=story.app.app_id,
            app_name=story.app.app_name,
            app_version=story.app.version,
            story_name=story.name,
            story_line='28'
        ))

    SentryAgent.publish_evt.mock.assert_not_called()

    SlackAgent.publish_evt.mock.assert_called_with(
        Reporter.get_agent('slack'),
        evt_name='my-event',
        evt_data=evt_data,
        agent_config=agent_config)

    CleverTapAgent.publish_evt.mock.assert_called_with(
        Reporter.get_agent('clevertap'),
        evt_name='my-event',
        evt_data=evt_data,
        agent_config=agent_config)


@mark.asyncio
async def test_capture_evt_with_user_reporting(patch, async_mock, magic):
    Reporter.init({
        'sentry': {
            'dsn': None
        },
        'slack': {
            'webhook': 'slack_webhook'
        },
        'clevertap': {
            'account': None,
            'pass': None
        },
        'user_reporting': True,
        'user_reporting_stacktrace': False
    }, 'release')

    user_app_agent = {
        'slack': {
            'webhook': 'user_webhook'
        }
    }

    Reporter.init_app_agents('user_app_id', user_app_agent)

    assert Reporter.app_agents('user_app_id') == user_app_agent

    patch.object(SlackAgent, 'publish_evt',
                 new=async_mock())

    story = magic()
    story.app.app_name = 'app_name'
    story.app.app_id = 'user_app_id'
    story.app.version = 'app_version'
    story.name = 'story_name'
    line = '28'

    evt_data = {
        'app_name': story.app.app_name,
        'app_uuid': story.app.app_id,
        'app_version': story.app.version,
        'event_data': {'my_event': 'my_event'},
        'platform_release': 'release',
        'story_line': line,
        'story_name': story.name
    }

    await Reporter._capture_evt(
        evt_name='my-event',
        evt_data={
            'my_event': 'my_event'
        },
        agent_options=ReportingAgentOptions(
            app_uuid=story.app.app_id,
            app_name=story.app.app_name,
            app_version=story.app.version,
            story_name=story.name,
            story_line=line,
            allow_user_events=True
        ))

    SlackAgent.publish_evt.mock.assert_any_call(
        Reporter.get_agent('slack'),
        evt_name='my-event',
        evt_data=evt_data,
        agent_config=None)

    SlackAgent.publish_evt.mock.assert_any_call(
        Reporter.get_agent('slack'),
        evt_name='my-event',
        evt_data=evt_data,
        agent_config={
            'webhook': 'user_webhook'
        })


@mark.asyncio
async def test_capture_exc(patch, async_mock, magic):
    Reporter.init({
        'sentry': {
            'dsn': 'https://foo:foo@sentry.io/123'
        },
        'slack': {
            'webhook': 'slack_webhook'
        },
        'clevertap': {
            'account': 'account',
            'pass': 'pass'
        },
        'user_reporting': False,
        'user_reporting_stacktrace': False
    }, 'release')

    patch.object(SentryAgent, 'publish_exc',
                 new=async_mock())
    patch.object(SlackAgent, 'publish_exc',
                 new=async_mock())
    patch.object(CleverTapAgent, 'publish_exc',
                 new=async_mock())

    story = magic()
    story.app.app_name = 'app_name'
    story.app.app_id = 'app_id'
    story.app.version = 'app_version'
    story.name = 'story_name'
    line = magic()
    line['ln'] = '28'

    try:
        raise StoryscriptError(message='foo', story=story, line=line)
    except StoryscriptError as e:
        agent_config = {
            'clever_ident': 'foo@foo.com',
            'clever_event': 'Event',
            'full_stacktrace': True
        }
        await Reporter._capture_exc(
            exc_info=e,
            agent_options=ReportingAgentOptions(
                agent_config=agent_config
            ))

        exc_data = {
            'app_uuid': story.app.app_id,
            'app_name': story.app.app_name,
            'app_version': story.app.version,
            'story_line': line['ln'],
            'story_name': story.name,
            'platform_release': 'release'
        }

        SentryAgent.publish_exc.mock.assert_called_with(
            Reporter.get_agent('sentry'),
            exc_info=e,
            exc_data=exc_data,
            agent_config=agent_config)

        SlackAgent.publish_exc.mock.assert_called_with(
            Reporter.get_agent('slack'),
            exc_info=e,
            exc_data=exc_data,
            agent_config=agent_config)

        CleverTapAgent.publish_exc.mock.assert_called_with(
            Reporter.get_agent('clevertap'),
            exc_info=e,
            exc_data=exc_data,
            agent_config=agent_config)


@mark.asyncio
async def test_capture_exc_with_user_reporting(patch, async_mock, magic):
    Reporter.init({
        'slack': {
            'webhook': 'non_user_webhook'
        },
        'user_reporting': True,
        'user_reporting_stacktrace': False
    }, 'release')

    user_app_agent = {
        'slack': {
            'webhook': 'user_webhook'
        }
    }

    Reporter.init_app_agents('user_app_id', user_app_agent)

    assert Reporter.app_agents('user_app_id') == user_app_agent

    patch.object(SlackAgent, 'publish_exc', new=async_mock())

    story = magic()
    story.app.app_name = 'app_name'
    story.app.app_id = 'user_app_id'
    story.app.version = 'app_version'
    story.name = 'story_name'
    line = magic()
    line['ln'] = '28'

    patch.object(SentryAgent, 'publish_exc',
                 new=async_mock())
    patch.object(SlackAgent, 'publish_exc',
                 new=async_mock())
    patch.object(CleverTapAgent, 'publish_exc',
                 new=async_mock())
    try:
        raise StoryscriptError(message='foo', story=story, line=line)
    except StoryscriptError as e:
        # capture_exc is a simple wrapper for _capture_exc which is async
        await Reporter._capture_exc(
            exc_info=e,
            agent_options=ReportingAgentOptions(
                allow_user_events=True
            )
        )

        exc_data = {
            'app_uuid': story.app.app_id,
            'app_name': story.app.app_name,
            'app_version': story.app.version,
            'story_line': line['ln'],
            'story_name': story.name,
            'platform_release': 'release'
        }
        slack_agent = Reporter.get_agent('slack')

        SlackAgent.publish_exc.mock.assert_any_call(
            slack_agent,
            exc_info=e,
            exc_data=exc_data,
            agent_config=None)

        # the user agent call
        SlackAgent.publish_exc.mock.assert_any_call(
            slack_agent,
            exc_info=e,
            exc_data=exc_data,
            agent_config={
                'full_stacktrace': False,
                'suppress_stacktrace': True,
                'webhook': 'user_webhook'
            })
