import os
import re
import traceback
import typing
from collections import namedtuple

from asyncy.Exceptions import StoryscriptError

ReportingAgentOptions = namedtuple('ReportingAgentOptions', {
    'story_name': typing.Union[str, None],
    'story_line': typing.Union[str, None],
    'app_name': typing.Union[str, None],
    'app_uuid': typing.Union[str, None],
    'app_version': typing.Union[str, None],
    'agent_config': typing.Union[dict, None],
    'allow_user_events': bool,
    'suppress_events': bool,
    'suppress_agents': typing.Union[list]
})

# set some defaults
ReportingAgentOptions.__new__.__defaults__ = \
    (None, None, None, None, None, None, True, False, []) * \
    len(ReportingAgentOptions._fields)


class ReportingAgent:

    @staticmethod
    def _cleanup_stacktrace(traceback_str: str):
        cwd = os.path.normpath(os.getcwd())
        return re.sub(cwd, '', traceback_str)

    @staticmethod
    def format_tb_error(exc_info,
                        full_stacktrace: bool = False,
                        suppress_stacktrace: bool = False):
        """
        This is used to easily format exceptions into a string that
        can be easily passed into a reporting agent. This allows you to
        format the entire stacktrace including the root.

        :param exc_info:
        :param full_stacktrace: include the full stacktrace and the root
        :param suppress_stacktrace: do not include the system stacktrace
        :return:
        """
        _traceback = ReportingAgent._cleanup_stacktrace(
            ''.join(traceback.format_tb(exc_info.__traceback__)))

        # check if we are allowed to include the stacktrace in
        # this event. If not, let's just include the error messages
        if suppress_stacktrace is True:
            if isinstance(exc_info, StoryscriptError) and \
                    exc_info.root is not None:
                return f'{exc_info}: {exc_info.root}'
            else:
                return f'{exc_info}'

        err_str = f'{type(exc_info).__qualname__}: {exc_info}'

        _root_traceback = None
        if full_stacktrace is True and \
                isinstance(exc_info, StoryscriptError) and \
                exc_info.root is not None:
            _root_traceback = ReportingAgent._cleanup_stacktrace(
                ''.join(traceback.format_tb(exc_info.root.__traceback__)))

        if _root_traceback is not None:
            root_err_str = f'{type(exc_info.root).__qualname__}:' \
                f' {exc_info.root}'
            return f'{err_str}\n\nStacktrace:\n{_traceback}\n\n' \
                f'{root_err_str}\n\nRoot Stacktrace:\n' \
                f'{_root_traceback}'
        else:
            return f'{err_str}\n\nStacktrace:\n{_traceback}'

    async def publish_exc(
            self, exc_info: BaseException,
            exc_data: dict, agent_config: dict = None):
        return

    async def publish_evt(
            self, evt_name: str, evt_data: dict, agent_config: dict = None):
        return

    async def publish_msg(self, message: str, agent_config: dict = None):
        pass
