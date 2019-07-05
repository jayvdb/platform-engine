# -*- coding: utf-8 -*-
import typing


Release = typing.NamedTuple('Release', [
    ('app_uuid', str),
    ('app_name', str),
    ('version', int),
    ('environment', dict),
    ('stories', typing.Union[dict, None]),
    ('maintenance', bool),
    ('always_pull_images', bool),
    ('app_dns', str),
    ('state', str),
    ('deleted', bool),
    ('owner_uuid', str),
    ('owner_email', typing.Union[str, None])
])
