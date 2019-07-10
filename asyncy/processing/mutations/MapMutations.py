# -*- coding: utf-8 -*-


class MapMutations:

    # DEPRECATED: removed in SS 16.0
    @classmethod
    def size(cls, mutation, value, story, line, operator):
        return len(value)

    @classmethod
    def length(cls, mutation, value, story, line, operator):
        return len(value)

    @classmethod
    def keys(cls, mutation, value, story, line, operator):
        return list(value.keys())

    @classmethod
    def values(cls, mutation, value, story, line, operator):
        return list(value.values())

    @classmethod
    def pop(cls, mutation, value, story, line, operator):
        key = story.argument_by_name(mutation, 'key')
        return value.pop(key, None)

    @classmethod
    def flatten(cls, mutation, value, story, line, operator):
        out = []
        for k, v in value.items():
            out.append([k, v])
        return out

    @classmethod
    def get(cls, mutation, value, story, line, operator):
        key = story.argument_by_name(mutation, 'key')
        default = story.argument_by_name(mutation, 'default')
        # DEPRECATED: 'default' is always required to exist
        if default is None:
            value.get(key)
        return value.get(key, default)

    @classmethod
    def contains(cls, mutation, value, story, line, operator):
        key = story.argument_by_name(mutation, 'key')
        if key is not None:
            return key in value
        item = story.argument_by_name(mutation, 'value')
        for v in value.values():
            if v == item:
                return True
        return False
