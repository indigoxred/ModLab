"""Small strict parser for Steam's quoted Valve KeyValues app manifests."""

from collections.abc import Iterator


class SteamManifestError(ValueError):
    """Raised when a Steam app manifest is malformed or ambiguous."""


def parse_keyvalues(text: str) -> dict[str, object]:
    if not isinstance(text, str):
        raise SteamManifestError("manifest content must be text")
    tokens = tuple(_tokens(text))
    if not tokens:
        raise SteamManifestError("manifest is empty")
    result, index = _object(tokens, 0, nested=False)
    if index != len(tokens):
        raise SteamManifestError("manifest contains trailing tokens")
    return result


def _object(
    tokens: tuple[tuple[str, str | None], ...],
    index: int,
    *,
    nested: bool,
) -> tuple[dict[str, object], int]:
    result: dict[str, object] = {}
    seen: set[str] = set()
    while index < len(tokens):
        token_type, token_value = tokens[index]
        if token_type == "close":
            if not nested:
                raise SteamManifestError("unexpected closing brace")
            return result, index + 1
        if token_type != "string":
            raise SteamManifestError("manifest key must be quoted text")
        key = token_value
        folded = key.casefold()
        if folded in seen:
            raise SteamManifestError(f"duplicate manifest key: {key}")
        seen.add(folded)
        index += 1
        if index >= len(tokens):
            raise SteamManifestError(f"missing value for manifest key: {key}")
        value_type, value = tokens[index]
        if value_type == "string":
            result[key] = value
            index += 1
        elif value_type == "open":
            nested_value, index = _object(tokens, index + 1, nested=True)
            result[key] = nested_value
        else:
            raise SteamManifestError(f"missing value for manifest key: {key}")
    if nested:
        raise SteamManifestError("manifest object is missing a closing brace")
    return result, index


def _tokens(text: str) -> Iterator[tuple[str, str | None]]:
    index = 0
    while index < len(text):
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if character == "/" and index + 1 < len(text) and text[index + 1] == "/":
            newline = text.find("\n", index + 2)
            index = len(text) if newline == -1 else newline + 1
            continue
        if character == "{":
            yield "open", None
            index += 1
            continue
        if character == "}":
            yield "close", None
            index += 1
            continue
        if character != '"':
            raise SteamManifestError(
                f"unexpected unquoted manifest token at character {index}"
            )
        index += 1
        value: list[str] = []
        while index < len(text):
            character = text[index]
            if character == '"':
                index += 1
                yield "string", "".join(value)
                break
            if character == "\\":
                if index + 1 >= len(text):
                    raise SteamManifestError("unterminated manifest escape")
                escaped = text[index + 1]
                if escaped in {'"', "\\"}:
                    value.append(escaped)
                else:
                    value.extend(("\\", escaped))
                index += 2
                continue
            value.append(character)
            index += 1
        else:
            raise SteamManifestError("unterminated quoted manifest string")
