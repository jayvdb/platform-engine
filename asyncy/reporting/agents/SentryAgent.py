from raven import Client

from ..ReportingAgent import ReportingAgent
from ...Logger import Logger


class SentryAgent(ReportingAgent):
    _sentry_client = None

    def __init__(self, dsn: str, release: str, logger: Logger):
        self._release = release
        self._logger = logger

        if dsn is None:
            return

        self._sentry_client = Client(
            dsn=dsn,
            enable_breadcrumbs=False,
            install_logging_hook=False,
            hook_libraries=[],
            release=release)

    async def publish_msg(self, message: str, agent_config: dict = None):
        # sentry does not need to track generic messages
        pass

    async def publish_evt(
            self, evt_name: str, evt_data: dict, agent_config: dict = None):
        # sentry does not need to track events
        pass

    async def publish_exc(self, exc_info: BaseException,
                          exc_data: dict, agent_config: dict = None):
        if self._sentry_client is None:
            return

        self._sentry_client.context.clear()

        app_uuid = None
        app_version = None
        app_name = None
        story_name = None
        story_line = None

        if 'app_name' in exc_data:
            app_name = exc_data['app_name']

        if 'app_uuid' in exc_data:
            app_uuid = exc_data['app_uuid']

        if 'app_version' in exc_data:
            app_version = exc_data['app_version']

        if 'story_line' in exc_data:
            story_line = exc_data['story_line']

        if 'story_name' in exc_data:
            story_name = exc_data['story_name']

        full_stacktrace = True
        suppress_stacktrace = False

        if agent_config is not None:
            if agent_config.get('full_stacktrace', True) is False:
                full_stacktrace = False

            if agent_config.get('suppress_stacktrace', False) is True:
                suppress_stacktrace = True

        err_str = ReportingAgent.format_tb_error(
            exc_info=exc_info,
            full_stacktrace=full_stacktrace,
            suppress_stacktrace=suppress_stacktrace
        )

        try:
            self._sentry_client.user_context({
                'platform_release': self._release,
                'app_uuid': app_uuid,
                'app_name': app_name,
                'app_version': app_version,
                'story_name': story_name,
                'story_line': story_line
            })
            # we utilize captureMessage because captureException
            # will not properly work 100% of the time
            # unless this is always called within try/catch block
            self._sentry_client.captureMessage(message=err_str)
        finally:
            self._sentry_client.context.clear()
