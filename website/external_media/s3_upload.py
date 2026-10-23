"""Bounded direct-to-S3 uploads. File bytes never pass through Django."""
import math
import time
import uuid
from pathlib import PurePosixPath

MIB = 1024 * 1024
MULTIPART_THRESHOLD = 100 * MIB
PART_SIZE = 64 * MIB
SESSION_SECONDS = 24 * 3600


class UploadError(ValueError):
    pass


class S3DirectUpload:
    def __init__(self, storage, now=time.time):
        self.storage = storage
        self.client = storage.connection.meta.client
        self.now = now

    def target(self, key):
        location = str(getattr(self.storage, 'location', '') or '').strip('/')
        key = key.lstrip('/')
        if location and not key.startswith(f'{location}/'):
            key = f'{location}/{key}'
        return {'Bucket': self.storage.bucket_name, 'Key': key}

    def begin(self, key, size, content_type):
        if size <= 0:
            raise UploadError('O arquivo está vazio ou tem tamanho inválido.')
        if size < MULTIPART_THRESHOLD:
            return {'storage_key': key, 'upload_url': self.client.generate_presigned_url(
                'put_object', Params=self.target(key), ExpiresIn=3600,
            )}
        # Positions are allocated before registration. A unique key prevents
        # simultaneous large uploads to the same position overwriting each other.
        token = uuid.uuid4().hex
        path = PurePosixPath(key)
        key = str(path.with_name(f'{path.stem}-{token}{path.suffix}'))
        part_size = max(PART_SIZE, math.ceil(size / (10000 * MIB)) * MIB)
        if part_size > 5 * 1024 * MIB:
            raise UploadError('Arquivo grande demais para este fluxo de upload.')
        result = self.client.create_multipart_upload(
            **self.target(key), ContentType=content_type,
            Metadata={'connect-upload-token': token},
        )
        return {'storage_key': key, 'multipart': {
            'upload_id': result['UploadId'], 'part_size': part_size,
            'token': token, 'started_at': int(self.now()),
        }}

    def session(self, allocation, *, allow_expired=False):
        session = allocation.get('multipart')
        if not isinstance(session, dict):
            raise UploadError('Sessão de upload em partes inválida.')
        if not allow_expired and self.now() - session['started_at'] > SESSION_SECONDS:
            raise UploadError('O prazo deste upload expirou. Selecione o arquivo novamente.')
        return session

    def part_url(self, allocation, number):
        session = self.session(allocation)
        count = math.ceil(allocation['size'] / session['part_size'])
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= count:
            raise UploadError('Número de parte inválido.')
        # Sign on demand, rather than expiring all URLs one hour after begin().
        return self.client.generate_presigned_url(
            'upload_part', Params={**self.target(allocation['storage_key']),
                'UploadId': session['upload_id'], 'PartNumber': number},
            ExpiresIn=3600,
        )

    def _verify_object(self, allocation):
        result = self.client.head_object(**self.target(allocation['storage_key']))
        if (result.get('ContentLength') != allocation['size']
                or result.get('Metadata', {}).get('connect-upload-token') != allocation['multipart']['token']):
            raise UploadError('O tamanho ou a identificação do arquivo no S3 não corresponde ao upload.')

    def complete(self, allocation):
        session = self.session(allocation)
        target = self.target(allocation['storage_key'])
        expected_count = math.ceil(allocation['size'] / session['part_size'])
        parts, marker = [], 0
        try:
            while True:
                result = self.client.list_parts(**target, UploadId=session['upload_id'], PartNumberMarker=marker)
                parts.extend(result.get('Parts', []))
                if len(parts) > expected_count:
                    raise UploadError('O upload contém mais partes do que o esperado.')
                if not result.get('IsTruncated'):
                    break
                next_marker = result.get('NextPartNumberMarker', 0)
                if next_marker <= marker:
                    raise UploadError('Não foi possível verificar todas as partes do upload.')
                marker = next_marker
        except Exception as error:
            if getattr(error, 'response', {}).get('Error', {}).get('Code') != 'NoSuchUpload':
                raise
            # A prior completion may have succeeded while its HTTP response was
            # lost. Check the immutable token and size before accepting a retry.
            self._verify_object(allocation)
            return
        if len(parts) != expected_count:
            raise UploadError('O upload está incompleto. Nem todas as partes chegaram ao S3.')
        for number, part in enumerate(parts, 1):
            expected_size = min(session['part_size'], allocation['size'] - (number - 1) * session['part_size'])
            if part.get('PartNumber') != number or part.get('Size') != expected_size or not part.get('ETag'):
                raise UploadError('Uma parte do arquivo está incompleta ou tem tamanho incorreto.')
        self.client.complete_multipart_upload(
            **target, UploadId=session['upload_id'],
            MultipartUpload={'Parts': [{'PartNumber': part['PartNumber'], 'ETag': part['ETag']} for part in parts]},
        )
        self._verify_object(allocation)

    def abort(self, allocation):
        session = self.session(allocation, allow_expired=True)
        try:
            self.client.abort_multipart_upload(**self.target(allocation['storage_key']), UploadId=session['upload_id'])
        except Exception as error:
            if getattr(error, 'response', {}).get('Error', {}).get('Code') != 'NoSuchUpload':
                raise
