import json

from asyncy.Exceptions import StoryscriptError
from asyncy.reporting.ReportingAgent import ReportingAgent
from asyncy.reporting.agents.SlackAgent import SlackAgent
from asyncy.utils.HttpUtils import HttpUtils

from pytest import mark

from tornado.httpclient import AsyncHTTPClient


def test_create_agent(patch, magic):
    logger = magic()

    patch.init(AsyncHTTPClient)

    slack_agent = SlackAgent(
        webhook='slack_webhook',
        release='release',
        logger=logger
    )

    AsyncHTTPClient.__init__.assert_called()

    assert slack_agent._logger == logger
    assert slack_agent._release == 'release'


@mark.parametrize('suppress_stacktrace', [False, True])
@mark.parametrize('full_stacktrace', [False, True])
@mark.asyncio
async def test_publish_exc(patch, magic, async_mock,
                           suppress_stacktrace, full_stacktrace):
    logger = magic()

    patch.init(AsyncHTTPClient)

    slack_agent = SlackAgent(
        webhook='slack_webhook',
        release='release',
        logger=logger
    )

    patch.object(HttpUtils, 'fetch_with_retry', new=async_mock())
    patch.object(ReportingAgent, 'format_tb_error',
                 return_value='traceback_err')
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
                raise BaseException('Something initially went wrong!')
            except BaseException as e1:
                raise StoryscriptError(
                    message='A Storyscript error happened', root=e1
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

        await slack_agent.publish_exc(
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

        err_str = 'traceback_err'

        if suppress_stacktrace is True:
            traceback_line = f'*Error*: {err_str}'
        else:
            traceback_line = f'```{err_str}```'

        err_msg = f'An exception occurred with' \
            f' the following information:\n\n' \
            f'*Platform Engine Release*: release\n' \
            f'*App Name*: app_name\n' \
            f'*App UUID*: app_uuid\n' \
            f'*App Version*: app_version\n' \
            f'*Story Name*: story_name\n' \
            f'*Story Line Number*: 28\n\n' \
            f'{traceback_line}'

        HttpUtils.fetch_with_retry.mock.assert_called_with(
            tries=3, logger=slack_agent._logger,
            url='slack_webhook', http_client=slack_agent._http_client,
            kwargs={
                'method': 'POST',
                'body': json.dumps({'text': err_msg}),
                'headers': {'Content-Type': 'application/json'}
            })
