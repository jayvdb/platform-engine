# -*- coding: utf-8 -*-


class StoryscriptError(Exception):

    def __init__(self, message='', story=None, line=None, root=None):
        self.message = message
        self.story = story
        self.line = line
        self.root = root
        super().__init__(f'{type(self).__name__}: {self.message}')

    def __str__(self):
        if self.story is None:
            return super().__str__()

        stack = self.story.get_stack()
        trace = f'An exception has occurred:\n\n{self.message}'

        if self.root is not None:
            trace += f': {str(self.root)}'

        for item in stack:
            line = self.story.line(item)
            src = line.get('src')

            if src is None:
                src = f'method={line["method"]} (auto generated frame)'

            src = src.strip()

            trace += f'\n    at line {item}: {src} (in {self.story.name})'

        return trace


class StoryscriptRuntimeError(StoryscriptError):
    pass


class TypeAssertionRuntimeError(StoryscriptRuntimeError):
    def __init__(self, type_expected, type_received, value):
        super().__init__(
            message=f'Incompatible type assertion: '
            f'Received {value} ({type_received}), but '
            f'expected {type_expected}')


class TypeValueRuntimeError(StoryscriptRuntimeError):
    def __init__(self, type_expected, type_received, value):
        super().__init__(
            message=f'Type conversion failed from '
            f'{type_received} to '
            f'{type_expected} with `{value}`')


class InvalidKeywordUsage(StoryscriptError):
    def __init__(self, story, line, keyword):
        super().__init__(message=f'Invalid usage of keyword "{keyword}".',
                         story=story, line=line)


class ContainerSpecNotRegisteredError(StoryscriptError):
    def __init__(self, container_name, story=None, line=None):
        super().__init__(
            message=f'Service {container_name} not registered!',
            story=story, line=line
        )


class TooManyVolumes(StoryscriptError):
    def __init__(self, volume_count, max_volumes):
        super().__init__(
            message=f'Your app makes use of {volume_count} volumes. '
            f'The total permissible limit during Storyscript Beta is '
            f'{max_volumes} volumes. Please see '
            f'https://docs.storyscript.io/faq/ for more information.')


class TooManyActiveApps(StoryscriptError):
    def __init__(self, active_apps, max_apps):
        super().__init__(
            message=f'Only {max_apps} active apps are allowed during '
            'Storyscript Beta. '
            'Please see https://docs.storyscript.io/faq/ '
            'for more information.')


class TooManyServices(StoryscriptError):
    def __init__(self, service_count, max_services):
        super().__init__(
            message=f'Your app makes use of {service_count} services. '
            f'The total permissible limit during Storyscript Beta is '
            f'{max_services} services. Please see '
            f'https://docs.storyscript.io/faq/ for more information.')


class ArgumentNotFoundError(StoryscriptError):

    def __init__(self, story=None, line=None, name=None):
        message = None
        if name is not None:
            message = name + ' is required, but not found'

        super().__init__(message, story=story, line=line)


class ArgumentTypeMismatchError(StoryscriptError):

    def __init__(self, arg_name: str, omg_type: str, story=None, line=None):
        message = f'The argument "{arg_name}" does not match the expected ' \
            f'type "{omg_type}"'
        super().__init__(message, story=story, line=line)


class InvalidCommandError(StoryscriptError):

    def __init__(self, name, story=None, line=None):
        message = None
        if name is not None:
            message = name + ' is not implemented'

        super().__init__(message, story=story, line=line)


class K8sError(StoryscriptError):

    def __init__(self, story=None, line=None, message=None):
        super().__init__(message, story=story, line=line)


class ServiceNotFound(StoryscriptError):

    def __init__(self, service, tag, story=None, line=None):
        assert service is not None
        assert tag is not None
        super().__init__(
            message=f'The service "{service}:{tag}" '
            'was not found in the Storyscript Hub. '
            'Hint: 1. Check with the Storyscript team if this service has '
            'been made public; 2. Service names are case sensitive',
            story=story, line=line)


class ActionNotFound(StoryscriptError):

    def __init__(self, story=None, line=None, service=None, action=None):
        super().__init__(
            f'The action "{action}" was not found in the service "{service}". '
            f'Hint: Check the Storyscript Hub for a list of supported '
            f'actions for this service.',
            story=story, line=line)


class EnvironmentVariableNotFound(StoryscriptError):
    def __init__(self, service=None, variable=None, story=None, line=None):
        assert service is not None
        assert variable is not None
        super().__init__(
            f'The service "{service}" requires an environment variable '
            f'"{variable}" which was not specified. '
            f'Please set it by running '
            f'"$ story config set {service}.{variable}=<value>" '
            f'in your Storyscript app directory', story, line)
