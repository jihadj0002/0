import copy as _copy
from django.template.context import BaseContext


def _patched_base_copy(self):
    cls = self.__class__
    new = cls.__new__(cls)
    new.__dict__.update(self.__dict__)
    new.dicts = self.dicts[:]
    return new
BaseContext.__copy__ = _patched_base_copy