"""Small strict readers for the QSettings INI values ModLab observes."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


class Mo2IniError(ValueError):
    """Raised when an observed MO2 INI cannot be interpreted unambiguously."""


@dataclass(frozen=True)
class IniDocument:
    sections: Mapping[str, Mapping[str, str]]

    def get(self, section: str, key: str) -> str | None:
        values = self.sections.get(section.casefold())
        if values is None:
            return None
        return values.get(key.casefold())


def parse_ini_bytes(data: bytes) -> IniDocument:
    text = _decode_text(data)
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("["):
            if not line.endswith("]") or len(line) < 3:
                raise Mo2IniError(f"invalid INI section on line {number}")
            name = line[1:-1].strip()
            if not name or "[" in name or "]" in name:
                raise Mo2IniError(f"invalid INI section on line {number}")
            current = name.casefold()
            sections.setdefault(current, {})
            continue
        if current is None or "=" not in line:
            raise Mo2IniError(f"invalid INI key/value on line {number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or any(character in key for character in "[]=\r\n"):
            raise Mo2IniError(f"invalid INI key on line {number}")
        normalized = key.casefold()
        if normalized in sections[current]:
            raise Mo2IniError(
                f"duplicate INI key {key!r} in section on line {number}"
            )
        sections[current][normalized] = value.strip()

    frozen = {
        name: MappingProxyType(dict(values)) for name, values in sections.items()
    }
    return IniDocument(MappingProxyType(frozen))


def decode_qsettings_path(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("@"):
        prefix = "@ByteArray("
        if not value.startswith(prefix) or not value.endswith(")"):
            raise Mo2IniError("unsupported QSettings path wrapper")
        payload = value[len(prefix) : -1]
        output: list[str] = []
        index = 0
        while index < len(payload):
            character = payload[index]
            if character != "\\":
                output.append(character)
                index += 1
                continue
            if index + 1 >= len(payload) or payload[index + 1] != "\\":
                raise Mo2IniError("broken QSettings ByteArray path escape")
            output.append("\\")
            index += 2
        return "".join(output)
    return value


def parse_qsettings_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise Mo2IniError(f"invalid QSettings boolean: {value!r}")


def _decode_text(data: bytes) -> str:
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16")
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise Mo2IniError("MO2 text must be UTF-8 or BOM-marked UTF-16") from error

