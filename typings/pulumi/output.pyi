"""Type stubs for pulumi.output - fixes ty union resolution bug with Output.apply()."""

import json
from typing import (
    Any,
    Generic,
    Optional,
    TypeVar,
    Union,
    overload,
)
from collections.abc import Callable, Awaitable, Mapping

T = TypeVar("T")
T1 = TypeVar("T1")
T2 = TypeVar("T2")
T3 = TypeVar("T3")
T_co = TypeVar("T_co", covariant=True)
U = TypeVar("U")

Input = Union[T, Awaitable[T], "Output[T]"]
Inputs = Mapping[str, Input[Any]]
InputType = Union[T, Mapping[str, Any]]

class Output(Generic[T_co]):
    _is_known: Awaitable[bool]
    _is_secret: Awaitable[bool]
    _future: Awaitable[T_co]

    def __init__(
        self,
        resources: Union[Awaitable[set[Any]], set[Any]],
        future: Awaitable[T_co],
        is_known: Awaitable[bool],
        is_secret: Optional[Awaitable[bool]] = None,
    ) -> None: ...

    def resources(self) -> Awaitable[set[Any]]: ...
    def future(self, with_unknowns: Optional[bool] = None) -> Awaitable[Optional[T_co]]: ...
    def is_known(self) -> Awaitable[bool]: ...
    def is_secret(self) -> Awaitable[bool]: ...
    def get(self) -> T_co: ...

    def apply(
        self, func: Callable[[T_co], Input[U]], run_with_unknowns: bool = False
    ) -> "Output[U]": ...

    def __getattr__(self, item: str) -> "Output[Any]": ...
    def __getitem__(self, key: Any) -> "Output[Any]": ...
    def __iter__(self) -> Any: ...
    def __str__(self) -> str: ...

    @staticmethod
    def from_input(val: Input[U]) -> "Output[U]": ...

    @staticmethod
    def _from_input_shallow(val: Input[U]) -> "Output[U]": ...

    @staticmethod
    def unsecret(val: "Output[U]") -> "Output[U]": ...

    @staticmethod
    def secret(val: Input[U]) -> "Output[U]": ...

    @overload
    @staticmethod
    def all(*args: Input[Any]) -> "Output[list[Any]]": ...
    @overload
    @staticmethod
    def all(**kwargs: Input[Any]) -> "Output[dict[str, Any]]": ...

    @staticmethod
    def concat(*args: Input[str]) -> "Output[str]": ...

    @staticmethod
    def format(
        format_string: Input[str], *args: Input[object], **kwargs: Input[object]
    ) -> "Output[str]": ...

    @staticmethod
    def json_dumps(
        obj: Input[Any],
        *,
        skipkeys: bool = False,
        ensure_ascii: bool = True,
        check_circular: bool = True,
        allow_nan: bool = True,
        cls: Optional[type[json.JSONEncoder]] = None,
        indent: Optional[Union[int, str]] = None,
        separators: Optional[tuple[str, str]] = None,
        default: Optional[Callable[[Any], Any]] = None,
        sort_keys: bool = False,
        **kw: Any,
    ) -> "Output[str]": ...

    @staticmethod
    def json_loads(
        s: Input[Union[str, bytes, bytearray]],
        *,
        cls: Optional[type[json.JSONDecoder]] = None,
        object_hook: Optional[Callable[[dict[Any, Any]], Any]] = None,
        parse_float: Optional[Callable[[str], Any]] = None,
        parse_int: Optional[Callable[[str], Any]] = None,
        parse_constant: Optional[Callable[[str], Any]] = None,
        object_pairs_hook: Optional[Callable[[list[tuple[Any, Any]]], Any]] = None,
        **kwds: Any,
    ) -> "Output[Any]": ...


class Unknown:
    def __init__(self) -> None: ...


UNKNOWN: Unknown

def contains_unknowns(val: Any) -> bool: ...
def _is_prompt(value: Input[T]) -> bool: ...
def _map_output(o: Output[T], transform: Callable[[T], U]) -> Output[U]: ...
def _map2_output(o1: Output[T1], o2: Output[T2], transform: Callable[[T1, T2], U]) -> Output[U]: ...
def _map3_output(o1: Output[T1], o2: Output[T2], o3: Output[T3], transform: Callable[[T1, T2, T3], U]) -> Output[U]: ...
