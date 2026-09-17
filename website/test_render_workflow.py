import sys
from types import ModuleType
from unittest.mock import Mock, patch

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
