import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call as unittest_call, patch

from django.test import SimpleTestCase, override_settings

from website.external_media.render_workflow import RenderWorkflowError, enqueue_project


class RenderWorkflowDispatchTests(SimpleTestCase):
    @override_settings(RENDER_WORKFLOW_ENABLED=False)
    def test_disabled_integration_does_not_start_remote_task(self):
        self.assertIsNone(enqueue_project(17, 'run'))

    @override_settings(RENDER_WORKFLOW_ENABLED=True, USE_S3=False)
    def test_remote_worker_requires_shared_s3_storage(self):
        with self.assertRaisesRegex(RenderWorkflowError, 'USE_S3=TRUE'):
            enqueue_project(17, 'run')

    @override_settings(
        RENDER_WORKFLOW_ENABLED=True,
        USE_S3=True,
        RENDER_API_KEY='rnd_test',
        RENDER_WORKFLOW_TASK='connect-video/process_video_work',
    )
    def test_starts_render_task_with_project_and_operation(self):
        client = Mock()
        client.workflows.start_task.return_value.id = 'run-123'
        fake_sdk = ModuleType('render_sdk')
        fake_sdk.Render = Mock(return_value=client)
        with patch.dict(sys.modules, {'render_sdk': fake_sdk}):
            task_run_id = enqueue_project(17, 'render')

        self.assertEqual(task_run_id, 'run-123')
        fake_sdk.Render.assert_called_once_with(token='rnd_test')
        client.workflows.start_task.assert_called_once_with(
            'connect-video/process_video_work', ['project-render', 17, None],
        )

    @override_settings(RENDER_WORKFLOW_ENABLED=True, USE_S3=True, RENDER_API_KEY='rnd_test',
                       RENDER_WORKFLOW_TASK='connect-video/process_video_work')
    def test_source_analysis_uses_its_own_render_retry_budget(self):
        from website.external_media.render_workflow import enqueue_video_work
        client = Mock()
        fake_sdk = ModuleType('render_sdk')
        fake_sdk.Render = Mock(return_value=client)
        with patch.dict(sys.modules, {'render_sdk': fake_sdk}):
            enqueue_video_work('take-analysis', 17)
        client.workflows.start_task.assert_called_once_with(
            'connect-video/process_take_analysis', ['take-analysis', 17, None],
        )

    @override_settings(RENDER_WORKFLOW_ENABLED=True, USE_S3=True, RENDER_API_KEY='rnd_test',
                       RENDER_WORKFLOW_TASK='connect-video/process_video_work')
    def test_preview_caches_dispatch_to_render_with_original_lease(self):
        from website.external_media.preview_artifacts import PreviewArtifactService
        from website.external_media.thumbnails import TimelineThumbnailService
        from website.external_media.preview import TimelineRevisionService
        client = Mock()
        fake_sdk = ModuleType('render_sdk')
        fake_sdk.Render = Mock(return_value=client)
        immediate_thread = lambda target, **kwargs: SimpleNamespace(start=target)
        with patch.dict(sys.modules, {'render_sdk': fake_sdk}), \
                patch('website.external_media.preview_artifacts.Thread', side_effect=immediate_thread), \
                patch('website.external_media.thumbnails.Thread', side_effect=immediate_thread), \
                patch('website.external_media.preview.Thread', side_effect=immediate_thread), \
                patch('website.external_media.tasks.create_preview_artifact.apply_async') as artifact_celery, \
                patch('website.external_media.tasks.create_timeline_thumbnail.apply_async') as thumbnail_celery, \
                patch('website.external_media.tasks.create_timeline_review_proxy.apply_async') as proxy_celery:
            PreviewArtifactService.enqueue(7, 'artifact-token')
            TimelineThumbnailService.enqueue(8, 'thumbnail-token')
            TimelineRevisionService._enqueue_timeline_review_proxy(9, 10)
        self.assertEqual(client.workflows.start_task.call_args_list, [
            unittest_call('connect-video/process_video_work', ['preview-artifact', 7, 'artifact-token']),
            unittest_call('connect-video/process_video_work', ['timeline-thumbnail', 8, 'thumbnail-token']),
            unittest_call('connect-video/process_video_work', ['timeline-review-proxy', 9, 10]),
        ])
        artifact_celery.assert_not_called()
        thumbnail_celery.assert_not_called()
        proxy_celery.assert_not_called()

    @override_settings(RENDER_WORKFLOW_ENABLED=False)
    def test_preview_caches_keep_local_celery_fallback(self):
        from website.external_media.preview_artifacts import PreviewArtifactService
        from website.external_media.thumbnails import TimelineThumbnailService
        from website.external_media.preview import TimelineRevisionService
        immediate_thread = lambda target, **kwargs: SimpleNamespace(start=target)
        with patch('website.external_media.preview_artifacts.Thread', side_effect=immediate_thread), \
                patch('website.external_media.thumbnails.Thread', side_effect=immediate_thread), \
                patch('website.external_media.preview.Thread', side_effect=immediate_thread), \
                patch('website.external_media.tasks.create_preview_artifact.apply_async') as artifact_celery, \
                patch('website.external_media.tasks.create_timeline_thumbnail.apply_async') as thumbnail_celery, \
                patch('website.external_media.tasks.create_timeline_review_proxy.apply_async') as proxy_celery:
            PreviewArtifactService.enqueue(7, 'artifact-token')
            TimelineThumbnailService.enqueue(8, 'thumbnail-token')
            TimelineRevisionService._enqueue_timeline_review_proxy(9, 10)
        artifact_celery.assert_called_once_with(args=(7, 'artifact-token'), queue='media_previews', retry=False, ignore_result=True)
        thumbnail_celery.assert_called_once_with(args=(8, 'thumbnail-token'), queue='media_previews', retry=False, ignore_result=True)
        proxy_celery.assert_called_once_with(args=(9, 10), queue='media_previews', retry=False, ignore_result=True)

    @override_settings(RENDER_WORKFLOW_ENABLED=True, USE_S3=True, RENDER_API_KEY='rnd_test',
                       RENDER_WORKFLOW_TASK='connect-video/process_video_work')
    def test_preview_operations_require_a_lease(self):
        from website.external_media.render_workflow import enqueue_video_work
        for operation in ('preview-artifact', 'timeline-thumbnail'):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                enqueue_video_work(operation, 7)

    @override_settings(USE_S3=True)
    def test_render_executes_preview_tasks_with_lease(self):
        import importlib.util
        from pathlib import Path
        sdk = ModuleType('render_sdk')
        sdk.Retry = Mock()
        sdk.Workflows = lambda: SimpleNamespace(task=lambda **kwargs: lambda fn: fn)
        spec = importlib.util.spec_from_file_location('test_render_workflow_entry', Path(__file__).resolve().parent.parent / 'render_workflow.py')
        entry = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'render_sdk': sdk}):
            spec.loader.exec_module(entry)
        for operation, task in [('preview-artifact', entry.create_preview_artifact), ('timeline-thumbnail', entry.create_timeline_thumbnail)]:
            with self.subTest(operation=operation), patch.object(task, 'run') as run:
                entry._process_video_work(operation, 7, 'lease-token')
                run.assert_called_once_with(7, 'lease-token')
        with patch.object(entry.create_timeline_review_proxy, 'run') as run:
            entry._process_video_work('timeline-review-proxy', 9, 10)
            run.assert_called_once_with(9, 10)
