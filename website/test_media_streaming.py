import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from website.external_media.streaming import s3_stream_response


class MediaStreamingTests(unittest.TestCase):
    def field(self, result):
        self.client = Mock()
        self.client.get_object.return_value = result
        return SimpleNamespace(name='clips/take.mp4', storage=SimpleNamespace(
            location='private_media', bucket_name='media', connection=SimpleNamespace(meta=SimpleNamespace(client=self.client))))

    def test_full_object_is_read_lazily_in_bounded_chunks(self):
        body = Mock()
        body.iter_chunks.return_value = iter([b'abc', b'def'])
        response = s3_stream_response(SimpleNamespace(META={}), self.field({'Body': body, 'ContentLength': 6}), 'video/mp4', preview=True)
        body.iter_chunks.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'abcdef')
        body.iter_chunks.assert_called_once_with(chunk_size=65536)
        body.close.assert_called()
        self.client.get_object.assert_called_once_with(Bucket='media', Key='private_media/clips/take.mp4')
        response.close()

    def test_range_is_requested_from_storage_without_reading_the_rest_of_the_file(self):
        body = Mock()
        body.iter_chunks.return_value = iter([b'def'])
        response = s3_stream_response(SimpleNamespace(META={'HTTP_RANGE': 'bytes=3-5'}), self.field({'Body': body, 'ContentLength': 3, 'ContentRange': 'bytes 3-5/6'}), 'video/mp4')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response['Content-Range'], 'bytes 3-5/6')
        self.assertEqual(b''.join(response.streaming_content), b'def')
        self.assertEqual(self.client.get_object.call_args.kwargs['Range'], 'bytes=3-5')
        response.close()

    def test_cancel_before_first_chunk_releases_the_s3_connection(self):
        body = Mock()
        response = s3_stream_response(SimpleNamespace(META={}), self.field({'Body': body, 'ContentLength': 6}), 'video/mp4')
        response.close()
        body.close.assert_called_once()
        body.iter_chunks.assert_not_called()

    def test_invalid_range_does_not_download_the_object(self):
        response = s3_stream_response(SimpleNamespace(META={'HTTP_RANGE': 'bytes=0-1,5-6'}), self.field({}), 'video/mp4')
        self.assertEqual(response.status_code, 416)
        self.client.get_object.assert_not_called()

    def test_cancel_mid_transfer_closes_the_network_body(self):
        body = Mock()
        body.iter_chunks.return_value = iter([b'first', b'rest'])
        response = s3_stream_response(SimpleNamespace(META={}), self.field({'Body': body, 'ContentLength': 9}), 'video/mp4')
        self.assertEqual(next(iter(response.streaming_content)), b'first')
        response.close()
        body.close.assert_called()
