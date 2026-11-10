"""Optional boto3/Moto protocol smoke test. Uses no real AWS account or database."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from website.external_media.s3_upload import MIB, S3DirectUpload


@unittest.skipUnless(importlib.util.find_spec('moto') and importlib.util.find_spec('boto3'), 'Install moto[s3] and boto3 to run the S3 protocol smoke test')
class S3UploadIntegrationTests(unittest.TestCase):
    def test_transfer_assemble_and_retry_completion(self):
        import boto3
        from moto import mock_aws
        with mock_aws():
            client = boto3.client('s3', region_name='us-east-1')
            client.create_bucket(Bucket='filadelfia-upload-qa')
            storage = SimpleNamespace(location='private_media', bucket_name='filadelfia-upload-qa', connection=SimpleNamespace(meta=SimpleNamespace(client=client)))
            service = S3DirectUpload(storage)
            # Keep the actual byte transfer small while preserving S3's 5 MiB
            # minimum for every part except the final one.
            with patch('website.external_media.s3_upload.MULTIPART_THRESHOLD', 1), patch('website.external_media.s3_upload.PART_SIZE', 5 * MIB):
                upload = service.begin('external_media/projects/test/001.mp4', 11 * MIB, 'video/mp4')
            allocation = {'size': 11 * MIB, **upload}
            for number, size in enumerate((5 * MIB, 5 * MIB, MIB), 1):
                service.part_url(allocation, number)
                client.upload_part(**service.target(allocation['storage_key']), UploadId=allocation['multipart']['upload_id'], PartNumber=number, Body=b'x' * size)
            service.complete(allocation)
            service.complete(allocation)
            result = client.head_object(**service.target(allocation['storage_key']))
            self.assertEqual(result['ContentLength'], 11 * MIB)
            self.assertEqual(result['ContentType'], 'video/mp4')
            self.assertEqual(result['Metadata']['connect-upload-token'], allocation['multipart']['token'])


if __name__ == '__main__':
    unittest.main()
