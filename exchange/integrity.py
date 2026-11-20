from hashlib import sha256


def digest_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def verify_body_bytes(body: bytes, byte_length: int, body_digest: str) -> None:
    if type(body) is not bytes or type(byte_length) is not int or byte_length <= 0:
        raise ValueError('无效正文或正文长度')
    if len(body) != byte_length or digest_bytes(body) != body_digest:
        raise ValueError('原页字节与封定清册不符')
