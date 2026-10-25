"""Protected S3 delivery without buffering an entire media file in Django."""
from pathlib import Path

from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header

from .s3_upload import S3DirectUpload


def s3_stream_response(request, field_file, content_type, *, preview=False):
    storage = field_file.storage
    source = S3DirectUpload(storage)
    parameters = source.target(field_file.name)
    range_header = (request.META.get('HTTP_RANGE') or '').strip()
    if range_header:
        import re
        if not re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)', range_header):
            return HttpResponse(status=416)
        parameters['Range'] = range_header
    try:
        result = source.client.get_object(**parameters)
    except Exception as error:
        code = getattr(error, 'response', {}).get('Error', {}).get('Code')
        if code in ('NoSuchKey', '404', 'NotFound'):
            raise Http404('Arquivo não encontrado.') from error
        if code in ('InvalidRange', '416'):
            return HttpResponse(status=416)
        raise
    body = result['Body']

    def chunks():
        try:
            # S3Storage.open() downloads into a spooled file. Read the network
            # body directly instead, bounding each active response to 64 KiB.
            yield from body.iter_chunks(chunk_size=64 * 1024)
        finally:
            body.close()

    response = StreamingHttpResponse(chunks(), status=206 if result.get('ContentRange') else 200, content_type=content_type)
    response['Content-Length'] = str(result['ContentLength'])
    response['Accept-Ranges'] = 'bytes'
    if result.get('ContentRange'):
        response['Content-Range'] = result['ContentRange']
    disposition = content_disposition_header(not preview, Path(field_file.name).name)
    if disposition:
        response['Content-Disposition'] = disposition
    # Also close a response cancelled before its iterator is first consumed.
    response._resource_closers.append(body.close)
    return response
