from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlsplit


class ValidationError(ValueError):
    pass


class Codec(Protocol):
    def validate(self, value: Any) -> Any: ...
    def parse(self, raw: str) -> Any: ...
    def encode(self, value: Any) -> Any: ...
    def decode(self, value: Any) -> Any: ...


class BaseCodec:
    editor = 'input'

    def parse(self, raw):
        return self.validate(raw)

    def encode(self, value):
        return self.validate(value)

    def decode(self, value):
        return self.validate(value)


class BoolCodec(BaseCodec):
    editor = 'toggle'

    def validate(self, value):
        if type(value) is not bool:
            raise ValidationError('Expected boolean')
        return value

    def parse(self, raw):
        raw = raw.strip().lower()
        if raw not in {'true', 'false', '1', '0', 'yes', 'no'}:
            raise ValidationError('Use true/false')
        return raw in {'true', '1', 'yes'}


class IntCodec(BaseCodec):
    def __init__(self, minimum=None, maximum=None):
        self.minimum, self.maximum = minimum, maximum

    def validate(self, value):
        if type(value) is not int:
            raise ValidationError('Expected integer, not boolean')
        if self.minimum is not None and value < self.minimum or self.maximum is not None and value > self.maximum:
            raise ValidationError('Number outside allowed range')
        return value

    def parse(self, raw):
        try:
            return self.validate(int(raw.strip()))
        except ValueError as exc:
            raise ValidationError('Invalid integer or range') from exc


class FloatCodec(IntCodec):
    def validate(self, value):
        if type(value) not in {int, float} or not math.isfinite(value):
            raise ValidationError('Expected finite float')
        if self.minimum is not None and value < self.minimum or self.maximum is not None and value > self.maximum:
            raise ValidationError('Number outside allowed range')
        return float(value)

    def parse(self, raw):
        try:
            return self.validate(float(raw))
        except ValueError as exc:
            raise ValidationError('Invalid float or range') from exc


class DecimalCodec(IntCodec):
    def validate(self, value):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValidationError('Expected finite Decimal; floats forbidden')
        if self.minimum is not None and value < self.minimum or self.maximum is not None and value > self.maximum:
            raise ValidationError('Number outside allowed range')
        return value

    def parse(self, raw):
        try:
            return self.validate(Decimal(raw.strip()))
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError('Invalid Decimal or range') from exc

    def encode(self, value):
        return str(self.validate(value))

    def decode(self, value):
        if not isinstance(value, str):
            raise ValidationError('Stored Decimal must be a string')
        return self.parse(value)


class StringCodec(BaseCodec):
    def __init__(self, *, min_length=0, max_length=None, pattern=None):
        self.min_length, self.max_length = min_length, max_length
        self.pattern = re.compile(pattern) if pattern else None

    def validate(self, value):
        if not isinstance(value, str) or len(value) < self.min_length or self.max_length is not None and len(value) > self.max_length:
            raise ValidationError('Invalid string length/type')
        if self.pattern and not self.pattern.fullmatch(value):
            raise ValidationError('String does not match required pattern')
        return value


class ChoiceCodec(BaseCodec):
    editor = 'choice'

    def __init__(self, options):
        self.options = tuple(options)
        if not self.options or any(not isinstance(x, str) for x in self.options) or len(set(self.options)) != len(self.options):
            raise ValidationError('Choices require unique nonempty string options')

    def validate(self, value):
        if not isinstance(value, str) or value not in self.options:
            raise ValidationError('Unknown choice')
        return value


class JsonCodec(BaseCodec):
    def validate(self, value):
        try:
            return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise ValidationError('Value must be finite JSON data') from exc

    def parse(self, raw):
        try:
            return self.validate(json.loads(raw))
        except ValueError as exc:
            raise ValidationError('Invalid JSON') from exc


class ListCodec(JsonCodec):
    def __init__(self, item: Codec | None = None):
        self.item = item or JsonCodec()

    def validate(self, value):
        if not isinstance(value, list):
            raise ValidationError('Expected list')
        return [self.item.validate(x) for x in value]

    def encode(self, value):
        return [self.item.encode(x) for x in self.validate(value)]

    def decode(self, value):
        if not isinstance(value, list):
            raise ValidationError('Stored list is invalid')
        return self.validate([self.item.decode(x) for x in value])

    def parse(self, raw):
        try:
            return self.decode(json.loads(raw))
        except ValueError as exc:
            raise ValidationError('Invalid list input') from exc


class DurationCodec(IntCodec):
    def __init__(self, minimum=0, maximum=None):
        super().__init__(minimum, maximum)

    def parse(self, raw):
        match = re.fullmatch(r'\s*(\d+)\s*([smhd]?)\s*', raw)
        if not match:
            raise ValidationError('Use seconds or 5m/2h/1d')
        return self.validate(int(match[1]) * {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}[match[2]])


class EmojiCodec(StringCodec):
    def __init__(self):
        super().__init__(pattern=r'\d+')


class MediaCodec(JsonCodec):
    editor = 'media'

    def validate(self, value):
        from tg_templates import Media
        if isinstance(value, Media):
            return value
        if not isinstance(value, dict) or set(value) - {'kind', 'source', 'has_spoiler'}:
            raise ValidationError('Media requires kind/source/has_spoiler')
        try:
            return Media(**value)
        except (TypeError, ValueError) as exc:
            raise ValidationError('Invalid media') from exc

    def encode(self, value):
        from dataclasses import asdict
        return asdict(self.validate(value))


class URLCodec(StringCodec):
    def validate(self, value):
        value = super().validate(value)
        url = urlsplit(value)
        if url.scheme not in {'https', 'http'} or not url.netloc:
            raise ValidationError('Expected http(s) URL')
        return value


class TierCodec(ListCodec):
    """Strict ascending Decimal bounds and Decimal rates, never floats."""
    def validate(self, value):
        if not isinstance(value, list):
            raise ValidationError('Expected tiers list')
        previous = Decimal('-Infinity')
        result = []
        for tier in value:
            if not isinstance(tier, dict) or set(tier) != {'max', 'rate'}:
                raise ValidationError('Each tier requires max and rate')
            bound = DecimalCodec().validate(tier['max'])
            rate = DecimalCodec(minimum=Decimal('0')).validate(tier['rate'])
            if bound <= previous:
                raise ValidationError('Tier bounds must be strictly ascending')
            previous = bound
            result.append({'max': bound, 'rate': rate})
        return result

    def encode(self, value):
        return [{k: str(v) for k, v in tier.items()} for tier in self.validate(value)]

    def decode(self, value):
        try:
            return self.validate([{k: DecimalCodec().decode(v) for k, v in tier.items()} for tier in value])
        except (AttributeError, TypeError) as exc:
            raise ValidationError('Invalid encoded tiers') from exc
