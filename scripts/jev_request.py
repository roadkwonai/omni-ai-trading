"""TypeSafe request validation and response usage shared with isolated replay."""
from dataclasses import dataclass
import base64
import io
import json
import math
import struct

MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_IMAGE_BYTES = MAX_BODY_BYTES // 2
MAX_DEPTH = 64
IMAGE_MIN, IMAGE_MAX = 280, 1568
MAX_FINITE_INT = int(float.fromhex('0x1.fffffffffffffp+1023'))


class JevRequestRejected(ValueError):
    """Safe failure reason for audit before the request reaches the network."""


class JevResponseRejected(ValueError):
    """Safe failure reason for an unusable provider choice."""


@dataclass(frozen=True)
class PreparedRequest:
    payload: dict
    body: bytes
    image_pixels: tuple[int, int]


def jev_json_safe(value, depth=0):
    if isinstance(value, (dict, list, tuple)) and depth >= MAX_DEPTH:
        raise JevRequestRejected('json_depth')
    if isinstance(value, dict):
        return {key: jev_json_safe(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jev_json_safe(item, depth + 1) for item in value]
    if type(value) is int and not -MAX_FINITE_INT <= value <= MAX_FINITE_INT:
        raise JevRequestRejected('json_number_range')
    return None if isinstance(value, float) and not math.isfinite(value) else value


def png_pixels(image):
    if (len(image) < 45 or image[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
            or not image.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82')):
        raise JevRequestRejected('image_format')
    width, height = struct.unpack('>II', image[16:24])
    if not (IMAGE_MIN <= width <= IMAGE_MAX and IMAGE_MIN <= height <= IMAGE_MAX):
        raise JevRequestRejected('image_dimensions')
    if len(image) > MAX_IMAGE_BYTES:
        raise JevRequestRejected('image_bytes')
    from PIL import Image

    try:
        with Image.open(io.BytesIO(image)) as picture:
            animated = picture.get_format_mimetype() == 'image/apng' or getattr(picture, 'n_frames', 1) > 1
            picture.verify()
    except (OSError, SyntaxError, ValueError) as exc:
        raise JevRequestRejected('image_format') from exc
    if animated:
        raise JevRequestRejected('image_animation')
    return width, height


def prepare_request(payload, image, image_ref):
    dimensions = png_pixels(image)
    if payload['state'].get('chart') != image_ref or set(payload['assets']) != {image_ref}:
        raise JevRequestRejected('image_reference')
    asset = {'type': 'image', 'format': 'png', 'data_base64': base64.b64encode(image).decode('ascii')}
    normalized = {**payload, 'assets': {image_ref: asset}, 'state': jev_json_safe(payload['state']), 'questions': {
        name: {**question, **{field: jev_json_safe(question[field]) for field in ('instructions', 'criteria') if field in question}}
        for name, question in payload['questions'].items()}}
    try:
        body = json.dumps(normalized, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise JevRequestRejected('json_encoding') from exc
    if len(body) > MAX_BODY_BYTES:
        raise JevRequestRejected('request_bytes')
    return PreparedRequest(normalized, body, dimensions)


def encode_request(payload, image, image_ref):
    return prepare_request(payload, image, image_ref).body


def usage_tokens(result):
    usage = result.get('usage') if isinstance(result, dict) else None
    count = usage.get('input_tokens') if isinstance(usage, dict) else None
    return count if type(count) is int and count >= 0 else None


def validate_choice(result, question, criteria, model):
    answers = result.get('answers') if isinstance(result, dict) else None
    item = answers.get(question) if isinstance(answers, dict) else None
    if not isinstance(item, dict) or result.get('model') != model:
        raise JevResponseRejected('response_choice')
    choice, confidence = item.get('choice'), item.get('confidence')
    if (item.get('type') != 'choice' or not isinstance(choice, str) or choice not in criteria
            or type(confidence) not in (int, float) or not 0 <= confidence <= 1):
        raise JevResponseRejected('response_choice')
    answer = {'type': 'choice', 'choice': choice, 'confidence': confidence}
    probabilities = item.get('probabilities')
    if probabilities is not None:
        if (not isinstance(probabilities, dict) or set(probabilities) != set(criteria)
                or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in probabilities.values())
                or abs(math.fsum(probabilities.values()) - 1) > 0.001
                or probabilities[choice] != max(probabilities.values())):
            raise JevResponseRejected('response_probabilities')
        answer['probabilities'] = probabilities
    return answer
