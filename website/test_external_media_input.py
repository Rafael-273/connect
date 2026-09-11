from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from website.external_media.exceptions import ExternalMediaError
from website.external_media.ffmpeg_runner import FFmpegRunner
from website.external_media.media_input import (
    LocalMediaInput,
    RemoteMediaSource,
    S3MediaInput,
    media_input_factory,
)
from website.external_media.services import AssemblySource, VideoAssemblyService


class MediaInputTests(SimpleTestCase):
    @staticmethod
    def s3_field(size=15 * 1024 ** 3):
        client = Mock()
        client.generate_presigned_url.return_value = (
            'https://bucket.s3.amazonaws.com/private_media/video.mp4'
            '?X-Amz-Credential=temporary&X-Amz-Signature=secret'
        )
        storage = SimpleNamespace(
            location='private_media',
            bucket_name='bucket',
            connection=SimpleNamespace(meta=SimpleNamespace(client=client)),
        )
        field = SimpleNamespace(
            name='external_media/video.mp4',
            size=size,
            storage=storage,
            open=Mock(),
        )
        return field, client

    @override_settings(USE_S3=True, EXTERNAL_MEDIA_S3_URL_EXPIRATION_SECONDS=86400)
    def test_s3_factory_returns_short_lived_remote_ffmpeg_input(self):
        field, client = self.s3_field()

        media_input = media_input_factory(field)
        source = media_input.get_ffmpeg_input()

        self.assertIsInstance(media_input, S3MediaInput)
        self.assertIsInstance(source, RemoteMediaSource)
        self.assertEqual(source.size_bytes, 15 * 1024 ** 3)
        field.open.assert_not_called()
        client.generate_presigned_url.assert_called_once_with(
            'get_object',
            Params={
                'Bucket': 'bucket',
                'Key': 'private_media/external_media/video.mp4',
            },
            ExpiresIn=86400,
        )

    @override_settings(
        USE_S3=True,
        EXTERNAL_MEDIA_STREAMING_WORKSPACE_ESTIMATE_MB=512,
        EXTERNAL_MEDIA_WORKSPACE_SAFETY_FACTOR=1,
    )
    def test_s3_workspace_estimate_does_not_scale_with_original_size(self):
        small, _ = self.s3_field(size=10 * 1024 ** 2)
        large, _ = self.s3_field(size=15 * 1024 ** 3)

        self.assertEqual(
            media_input_factory(small).workspace_estimate(),
            media_input_factory(large).workspace_estimate(),
        )
        self.assertEqual(media_input_factory(large).workspace_estimate(), 512 * 1024 ** 2)

    @override_settings(USE_S3=True, EXTERNAL_MEDIA_REMOTE_MATERIALIZE_MAX_MB=256)
    def test_large_s3_fallback_is_explicitly_rejected(self):
        field, _ = self.s3_field()

        with TemporaryDirectory() as root, self.assertRaisesRegex(
            ExternalMediaError, 'materialização local',
        ):
            media_input_factory(field).materialize(Path(root) / 'video.mp4')
        field.open.assert_not_called()

    @override_settings(USE_S3=False)
    def test_local_factory_keeps_filesystem_path(self):
        field = SimpleNamespace(name='video.mp4', path='/media/video.mp4', size=10)

        media_input = media_input_factory(field)

        self.assertIsInstance(media_input, LocalMediaInput)
        self.assertEqual(media_input.get_ffmpeg_input(), Path('/media/video.mp4'))

    def test_ffmpeg_diagnostics_redact_presigned_query(self):
        value = (
            "failed https://bucket.s3.amazonaws.com/private/video.mp4"
            "?X-Amz-Credential=key&X-Amz-Signature=secret"
        )

        redacted = FFmpegRunner.redact_sensitive_urls(value)

        self.assertNotIn('secret', redacted)
        self.assertNotIn('Credential=key', redacted)
        self.assertIn('[REDACTED]', redacted)

    def test_ffmpeg_input_keeps_remote_observability_metadata(self):
        remote = RemoteMediaSource(
            'https://bucket/video.mp4?X-Amz-Signature=secret',
            size_bytes=15 * 1024 ** 3,
        )

        self.assertIs(FFmpegRunner.input_arg(remote), remote)
        self.assertEqual(FFmpegRunner.input_arg(Path('/tmp/video.mp4')), '/tmp/video.mp4')

    @override_settings(USE_S3=True, EXTERNAL_MEDIA_ASSEMBLY_WORKERS=2)
    def test_s3_assembly_stages_each_normalized_clip_before_the_next(self):
        runner = Mock()
        storage = Mock()
        normalized_on_disk = []

        def normalize(**job):
            # The previous output must already have left scratch before the next
            # large source starts encoding.
            self.assertFalse(any(path.exists() for path in normalized_on_disk))
            job['destination'].write_bytes(b'normalized')
            normalized_on_disk.append(job['destination'])

        staged_index = 0

        def stage(path, _namespace):
            nonlocal staged_index
            size = path.stat().st_size
            path.unlink()
            name = f'external_media/tmp/part-{staged_index}.mp4'
            staged_index += 1
            return RemoteMediaSource(
                f'https://bucket/part-{staged_index}.mp4?X-Amz-Signature=secret',
                storage_name=name,
                size_bytes=size,
            ), name

        storage.stage_temporary.side_effect = stage
        with TemporaryDirectory() as root:
            workdir = Path(root)
            service = VideoAssemblyService(runner=runner, storage=storage)
            service._effective_duration_ms = Mock(return_value=1000)
            service._normalize = Mock(side_effect=normalize)
            service.assemble(
                [
                    AssemblySource(
                        RemoteMediaSource('https://bucket/a?X-Amz-Signature=a'),
                        temporary_storage_name='external_media/tmp/proxy-a.mp4',
                    ),
                    AssemblySource(
                        RemoteMediaSource('https://bucket/b?X-Amz-Signature=b'),
                        temporary_storage_name='external_media/tmp/proxy-b.mp4',
                    ),
                ],
                workdir / 'output.mp4',
                SimpleNamespace(width=1920, height=1080),
                workdir,
            )

        self.assertEqual(storage.stage_temporary.call_count, 2)
        self.assertEqual(storage.delete_temporary.call_count, 4)

    @override_settings(USE_S3=True, EXTERNAL_MEDIA_ASSEMBLY_WORKERS=1)
    def test_s3_assembly_cleans_staged_clips_when_manifest_preparation_fails(self):
        runner = Mock()
        storage = Mock()

        def normalize(**job):
            job['destination'].write_bytes(b'normalized')
            return Mock()

        def stage(path, namespace):
            path.unlink()
            name = f'external_media/tmp/{namespace}.mp4'
            return RemoteMediaSource(
                f'https://bucket/object.mp4?X-Amz-Signature=secret',
                storage_name=name,
            ), name

        storage.stage_temporary.side_effect = stage
        with TemporaryDirectory() as root:
            workdir = Path(root)
            service = VideoAssemblyService(runner=runner, storage=storage)
            service._effective_duration_ms = Mock(return_value=1000)
            service._normalize = Mock(side_effect=normalize)
            service._serialize_reframe_plan = Mock(side_effect=ExternalMediaError('falha'))

            with self.assertRaisesRegex(ExternalMediaError, 'falha'):
                service.assemble(
                    [
                        AssemblySource(RemoteMediaSource('https://bucket/a?X-Amz-Signature=a')),
                        AssemblySource(RemoteMediaSource('https://bucket/b?X-Amz-Signature=b')),
                    ],
                    workdir / 'output.mp4',
                    SimpleNamespace(width=1920, height=1080),
                    workdir,
                    auto_reframe_config={'priority': 'face'},
                )

        self.assertEqual(storage.stage_temporary.call_count, 2)
        self.assertEqual(storage.delete_temporary.call_count, 2)
