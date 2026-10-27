"""One legacy stdlib alias for this offline Woodwork evaluation environment.

CPython3.9 exposes exactly this ABC under collections.Hashable. Python3.10
removed the deprecated alias. No project/test function or exception is changed.
"""
import collections
import collections.abc

if not hasattr(collections, 'Hashable'):
    collections.Hashable = collections.abc.Hashable
