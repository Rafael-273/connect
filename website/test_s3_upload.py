"""Direct upload protocol tests; no database, AWS account or large files required."""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from website.external_media.s3_upload import (
    MIB, MULTIPART_THRESHOLD, PART_SIZE, SESSION_SECONDS, S3DirectUpload, UploadError,
)


class S3Error(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}


class S3UploadTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        storage = SimpleNamespace(location='private_media', bucket_name='test-bucket', connection=SimpleNamespace(meta=SimpleNamespace(client=self.client)))
        self.service = S3DirectUpload(storage, now=lambda: 10000)
        self.client.create_multipart_upload.return_value = {'UploadId': 'upload-id'}
        self.client.generate_presigned_url.return_value = 'https://s3.test/signed'

    def begin(self, size=8 * 1024 ** 3):
        result = self.service.begin('external_media/projects/project/b1/001.mp4', size, 'video/mp4')
        allocation = {'storage_key': result['storage_key'], 'size': size, 'multipart': result['multipart']}
        self.client.head_object.return_value = {'ContentLength': size, 'Metadata': {'connect-upload-token': allocation['multipart']['token']}}
        return allocation

    def parts(self, allocation):
        size, part_size = allocation['size'], allocation['multipart']['part_size']
        return [{'PartNumber': n, 'Size': min(part_size, size - (n - 1) * part_size), 'ETag': f'"etag-{n}"'} for n in range(1, math.ceil(size / part_size) + 1)]

    def test_small_upload_keeps_single_put_and_storage_prefix(self):
        result = self.service.begin('a.mp4', 1000, 'video/mp4')
        self.assertNotIn('multipart', result)
        self.client.create_multipart_upload.assert_not_called()
        self.client.generate_presigned_url.assert_called_once_with('put_object', Params={'Bucket': 'test-bucket', 'Key': 'private_media/a.mp4'}, ExpiresIn=3600)
        self.assertEqual(self.service.target('private_media/a.mp4')['Key'], 'private_media/a.mp4')

    def test_eight_gib_uses_128_parts_and_a_unique_storage_key(self):
        allocation = self.begin()
        self.assertEqual(allocation['multipart']['part_size'], PART_SIZE)
        self.assertEqual(len(self.parts(allocation)), 128)
        self.client.generate_presigned_url.assert_not_called()
        self.assertIn(allocation['multipart']['token'], allocation['storage_key'])
        args = self.client.create_multipart_upload.call_args.kwargs
        self.assertEqual(args['ContentType'], 'video/mp4')
        self.assertEqual(args['Metadata']['connect-upload-token'], allocation['multipart']['token'])
        self.assertNotEqual(allocation['storage_key'], self.begin()['storage_key'])

    def test_threshold_and_invalid_sizes(self):
        self.assertIn('multipart', self.service.begin('a.mov', MULTIPART_THRESHOLD, 'video/quicktime'))
        for size in (0, -1):
            with self.assertRaises(UploadError):
                self.service.begin('a.mp4', size, 'video/mp4')

    def test_signs_each_part_on_demand_with_the_bound_key_and_upload_id(self):
        allocation = self.begin()
        self.service.part_url(allocation, 128)
        args = self.client.generate_presigned_url.call_args
        self.assertEqual(args.args, ('upload_part',))
        self.assertEqual(args.kwargs['Params']['PartNumber'], 128)
        self.assertEqual(args.kwargs['Params']['UploadId'], 'upload-id')
        self.assertEqual(args.kwargs['Params']['Key'], 'private_media/' + allocation['storage_key'])
        for number in (0, -1, 129, True, '1', 1.5, None):
            with self.assertRaises(UploadError):
                self.service.part_url(allocation, number)

    def test_lists_all_pages_and_completes_with_server_etags(self):
        allocation = self.begin()
        parts = self.parts(allocation)
        self.client.list_parts.side_effect = [
            {'Parts': parts[:100], 'IsTruncated': True, 'NextPartNumberMarker': 100},
            {'Parts': parts[100:], 'IsTruncated': False},
        ]
        self.service.complete(allocation)
        self.assertEqual(self.client.list_parts.call_args.kwargs['PartNumberMarker'], 100)
        sent = self.client.complete_multipart_upload.call_args.kwargs['MultipartUpload']['Parts']
        self.assertEqual(sent[-1], {'PartNumber': 128, 'ETag': '"etag-128"'})
        self.assertNotIn('Size', sent[0])
        self.client.head_object.assert_called_once()

    def test_incomplete_wrong_sized_and_noncontiguous_parts_cannot_be_completed(self):
        allocation = self.begin(PART_SIZE * 2 + 123)
        original = self.parts(allocation)
        for parts in (original[:-1], [dict(p, Size=p['Size'] - 1) for p in original], [dict(p, PartNumber=9) for p in original], original + [original[0]]):
            with self.subTest(parts=parts):
                self.client.list_parts.return_value = {'Parts': parts, 'IsTruncated': False}
                with self.assertRaises(UploadError):
                    self.service.complete(allocation)
        self.client.complete_multipart_upload.assert_not_called()

    def test_last_short_part_is_accepted(self):
        allocation = self.begin(PART_SIZE * 2 + 123)
        self.client.list_parts.return_value = {'Parts': self.parts(allocation), 'IsTruncated': False}
        self.service.complete(allocation)
        self.client.complete_multipart_upload.assert_called_once()

    def test_lost_completion_response_is_safe_to_retry_only_for_the_same_object(self):
        allocation = self.begin()
        self.client.list_parts.side_effect = S3Error('NoSuchUpload')
        self.service.complete(allocation)
        self.client.complete_multipart_upload.assert_not_called()
        for head in ({'ContentLength': allocation['size'], 'Metadata': {}}, {'ContentLength': 1, 'Metadata': {'connect-upload-token': allocation['multipart']['token']}}):
            self.client.head_object.return_value = head
            with self.assertRaises(UploadError):
                self.service.complete(allocation)

    def test_expired_session_cannot_sign_or_complete_but_can_be_aborted(self):
        allocation = self.begin()
        allocation['multipart']['started_at'] -= SESSION_SECONDS + 1
        with self.assertRaises(UploadError): self.service.part_url(allocation, 1)
        with self.assertRaises(UploadError): self.service.complete(allocation)
        self.service.abort(allocation)
        self.client.abort_multipart_upload.assert_called_once()

    def test_abort_is_idempotent_but_does_not_hide_permission_failures(self):
        allocation = self.begin()
        self.client.abort_multipart_upload.side_effect = S3Error('NoSuchUpload')
        self.service.abort(allocation)
        self.client.abort_multipart_upload.side_effect = S3Error('AccessDenied')
        with self.assertRaises(S3Error): self.service.abort(allocation)

    def test_listing_failures_are_not_mistaken_for_completed_uploads(self):
        allocation = self.begin()
        self.client.list_parts.side_effect = S3Error('AccessDenied')
        with self.assertRaises(S3Error): self.service.complete(allocation)
        self.client.head_object.assert_not_called()

    def test_part_size_grows_to_respect_ten_thousand_parts(self):
        allocation = self.begin(PART_SIZE * 10001)
        self.assertLessEqual(math.ceil(allocation['size'] / allocation['multipart']['part_size']), 10000)


if __name__ == '__main__':
    unittest.main()
