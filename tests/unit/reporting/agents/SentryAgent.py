from asyncy.Exceptions import StoryscriptError
from asyncy.reporting.ReportingAgent import ReportingAgent
from asyncy.reporting.agents.SentryAgent import SentryAgent
from asyncy.utils.HttpUtils import HttpUtils

from pytest import mark

from raven import Client


def test_create_agent(patch, magic):
    logger = magic()
    patch.init(Client)

    sentry_agent = SentryAgent(
        dsn='sentry_dsn',
        release='release',
        logger=logger
    )

    Client.__init__.assert_called_with(
        dsn='sentry_dsn',
        enable_breadcrumbs=False,
        install_logging_hook=False,
        hook_libraries=[],
        release='release'
    )

    assert sentry_agent._logger == logger
    assert sentry_agent._release == 'release'


@mark.parametrize('suppress_stacktrace', [False, True])
@mark.parametrize('full_stacktrace', [False, True])
@mark.asyncio
async def test_publish_exc(patch, magic, async_mock,
                           suppress_stacktrace, full_stacktrace):
    logger = magic()

    patch.init(Client)
    patch.object(Client, 'user_context')
    patch.object(Client, 'captureMessage')
    patch.object(Client, 'context')

    patch.object(HttpUtils, 'fetch_with_retry', new=async_mock())
    patch.object(ReportingAgent, 'format_tb_error',
                 return_value='traceback_err')

    sentry_agent = SentryAgent(
        dsn='sentry_dsn',
        release='release',
        logger=logger
    )

    try:
        story = magic()
        story.app.app_name = 'app_name'
        story.app.app_id = 'app_id'
        story.app.version = 'app_version'
        story.name = 'story_name'
        line = magic()
        line['ln'] = '28'

        if full_stacktrace is True:
            try:
                raise BaseException('An exception happened.')
            except BaseException as e1:
                raise StoryscriptError(
                    message='A second exception happened.',
                    root=e1
                )
        else:
            raise StoryscriptError(message='foo', story=story, line=line)

    except BaseException as e:
        exc_data = {
            'app_uuid': 'app_uuid',
            'app_name': 'app_name',
            'app_version': 'app_version',
            'story_line': '28',
            'story_name': 'story_name',
            'platform_release': 'release'
        }

        await sentry_agent.publish_exc(
            exc_info=e,
            exc_data=exc_data,
            agent_config={
                'suppress_stacktrace': suppress_stacktrace,
                'full_stacktrace': full_stacktrace
            })

        ReportingAgent.format_tb_error.assert_called_with(
            exc_info=e,
            full_stacktrace=full_stacktrace,
            suppress_stacktrace=suppress_stacktrace
        )

        Client.user_context.assert_called_with({
            'platform_release': 'release',
            'app_uuid': 'app_uuid',
            'app_name': 'app_name',
            'app_version': 'app_version',
            'story_name': 'story_name',
            'story_line': '28'
        })

        Client.captureMessage.assert_called_with(message='traceback_err')
        Client.context.clear.assert_called()
