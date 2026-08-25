"""Lifecycle-operation kind vocabulary without model import cycles."""

from typing import Literal

LifecycleOperationKind = Literal["closeout", "integrate", "direct-landing"]
LifecycleControlAction = Literal[
    "retry",
    "recover",
    "cancel",
    "revise",
    "retire",
    "supersede",
]
